"""HTTP transport for the Batch client.

Owns the httpx Client, the `X-API-Key` auth header, default
User-Agent, automatic retries for transient failures, and the RFC
7807 → `BatchAPIError` mapping. The facade (`client.py`) is thin glue
over this — one method per endpoint, all typed via pydantic v2 models.
"""

import random
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from zenrows.batch._logging import log_error, log_request, logger
from zenrows.batch.errors import BatchAPIError

M = TypeVar("M", bound=BaseModel)

_log = logger("transport")

# Transient statuses worth retrying: 429 (rate limited), 502/503/504
# (gateway / transient upstream — the spec marks 503 "safe to retry").
_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})

# Methods safe to replay without side effects. POST is added only when
# the caller supplied an Idempotency-Key (submit / rerun).
_IDEMPOTENT_METHODS = frozenset({"GET", "PUT", "DELETE", "HEAD", "OPTIONS"})

# Retry tuning (mirrors the Node SDK): ~250ms · 2**attempt, ±20% jitter,
# capped at 10s.
_BACKOFF_BASE_MS = 250
_BACKOFF_CAP_MS = 10_000
_DEFAULT_RETRIES = 3


def _has_idempotency_key(headers: dict[str, str] | None) -> bool:
    return bool(headers) and any(k.lower() == "idempotency-key" for k in headers)


def _backoff_ms(attempt: int) -> float:
    """Jittered exponential backoff for retry `attempt` (0-based)."""
    base = min(_BACKOFF_BASE_MS * 2**attempt, _BACKOFF_CAP_MS)
    return base * (1 + (random.random() * 2 - 1) * 0.2)


def _retry_after_ms(response: httpx.Response) -> float | None:
    """Parse a `Retry-After` header (delta-seconds only) to milliseconds."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        secs = float(raw)
    except ValueError:
        return None
    return secs * 1000 if secs >= 0 else None


class _Transport:
    """Thin wrapper over `httpx.Client` with the Batch API's conventions.

    Why not vanilla httpx? Three reasons we want centralised:
      - Auth header (`X-API-Key`) is set once, not per call site.
      - Every non-2xx is decoded as RFC 7807 and raised — handlers
        never see a raw status code, they see `BatchAPIError.code`.
      - Pydantic encode/decode lives in one place; method bodies in
        `client.py` stay readable.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        user_agent: str,
        timeout: float | httpx.Timeout,
        retries: int = _DEFAULT_RETRIES,
        verify: bool | str = True,
        httpx_args: dict[str, Any] | None = None,
    ):
        self._retries = max(0, retries)
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "X-API-Key": api_key,
                "User-Agent": user_agent,
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(timeout) if isinstance(timeout, (int, float)) else timeout,
            verify=verify,
            **(httpx_args or {}),
        )

    # ----- retrying send -----

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        headers: dict[str, str] | None,
        content: bytes | None = None,
    ) -> httpx.Response:
        """Issue the request, retrying transient failures on idempotent
        requests.

        Retries `{429, 502, 503, 504}` and transient network errors up
        to `retries` times, with jittered exponential backoff (honoring
        `Retry-After` when present). Only idempotent requests are
        replayed — `GET`/`PUT`/`DELETE`/`HEAD`/`OPTIONS`, plus `POST`
        when the caller supplied an `Idempotency-Key`. Our own timeouts
        (`httpx.TimeoutException`) are never retried: the caller set
        that budget.
        """
        idempotent = method.upper() in _IDEMPOTENT_METHODS or (
            method.upper() == "POST" and _has_idempotency_key(headers)
        )
        attempt = 0
        while True:
            try:
                response = self._client.request(
                    method, path, params=params, headers=headers, content=content
                )
            except httpx.TimeoutException:
                # Our own timeout budget — do not retry.
                raise
            except httpx.TransportError:
                # Network-level failure (DNS, connection reset, TLS).
                if idempotent and attempt < self._retries:
                    time.sleep(_backoff_ms(attempt) / 1000)
                    attempt += 1
                    continue
                raise

            if (
                idempotent
                and attempt < self._retries
                and response.status_code in _RETRYABLE_STATUSES
            ):
                wait_ms = _retry_after_ms(response) or _backoff_ms(attempt)
                response.close()
                time.sleep(wait_ms / 1000)
                attempt += 1
                continue

            return response

    # ----- lifecycle -----

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "_Transport":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def base_url(self) -> str:
        return str(self._client.base_url)

    # ----- request helpers -----

    def request_json(
        self,
        method: str,
        path: str,
        *,
        body: BaseModel | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Send a request, parse JSON, raise BatchAPIError on non-2xx.

        Pydantic models in `body` are serialised via
        `model_dump(mode="json", exclude_unset=True)` — the spec marks
        every optional field with `omitempty`, so we send exactly what
        the caller passed and let server defaults fill the rest.
        """
        content: bytes | None = None
        if body:
            payload = body.model_dump(
                mode="json", exclude_unset=True, exclude_none=True, by_alias=True
            )
            import json

            content = json.dumps(payload).encode("utf-8")
            headers = {**(headers or {}), "Content-Type": "application/json"}

        start = time.monotonic()
        response = self._send(
            method,
            path,
            params=_drop_none(params),
            headers=headers,
            content=content,
        )
        elapsed_ms = (time.monotonic() - start) * 1000

        if response.status_code >= 400:
            err = BatchAPIError.from_response(response)
            log_error(
                _log,
                method=method,
                path=path,
                status=response.status_code,
                code=err.code,
                detail=err.problem.detail if err.problem else None,
            )
            raise err

        log_request(
            _log, method=method, path=path, status=response.status_code, elapsed_ms=elapsed_ms
        )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()


def _drop_none(d: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strip None-valued query params; httpx sends them as empty
    strings otherwise, which the server then rejects as malformed."""
    if not d:
        return None
    return {k: v for k, v in d.items() if v is not None}
