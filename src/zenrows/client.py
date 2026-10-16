"""Synchronous Zenrows scraper client (the original SDK surface).

Backward-compatible with the pre-1.4 API: the constructor still takes
`(apikey, retries, concurrency)` positionally, and `get`/`post`/`put`
return a `requests.Response`. What's new in this freshen-up:

  - Modern type hints (3.10+ unions, no Optional/Dict noise)
  - Context-manager support that closes the underlying requests
    session + the thread-pool executor

For the async (job-based) Batch API, see `zenrows.ZenRowsBatchClient`.
"""

import asyncio
import contextvars
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.exceptions import InvalidHeader, MaxRetryError, ResponseError
from urllib3.util.retry import Retry

from zenrows.__version__ import __version__

DEFAULT_SCRAPER_URL = "https://api.zenrows.com/v1/"
DEFAULT_USER_AGENT = f"zenrows/{__version__} python"

# Status codes worth another try. None of them is charged: 429 is a rate
# limit, 503 is the gateway asking to retry later, 502 and 504 never reached
# the target. Every other 4xx/5xx is the final answer.
_RETRY_STATUSES = (429, 502, 503, 504)
DEFAULT_RETRIES = 2
# Longest wait between two tries, in seconds: the cap on `Retry-After` and on
# the jittered backoff. Same cap as the Crawl client's `stop()` retry.
DEFAULT_RETRY_MAX_WAIT = 30.0
# Longest a call may keep retrying, in seconds, counted from its first try:
# no retry starts if the wait before it would end past this.
DEFAULT_RETRY_BUDGET = 90.0
# Shortest jittered wait, so a retry never fires back to back.
_RETRY_MIN_WAIT = 0.25

# Clock for the retry budget; tests replace it.
_monotonic = time.monotonic

# When the current call's first try started. Set per call by
# `_RetryBudgetAdapter.send`; a context variable, so concurrent calls on
# the client's thread pool each see their own.
_call_started: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "zenrows_call_started", default=None
)


class _JitteredRetry(Retry):
    """urllib3 `Retry` with the Zenrows client retry policy.

    Between tries it waits the server's `Retry-After` (delta-seconds or
    HTTP-date), capped at `retry_max_wait`; without one it waits a random time
    in `[0.25, min(retry_max_wait, 2 ** attempt)]` seconds (full jitter,
    `attempt` counting from 0). It never starts a retry whose wait would end
    more than `retry_budget` seconds after the call's first try.

    Only the hooks urllib3 1.26 and 2.x share are overridden, so it works on
    both (no `backoff_jitter` / `retry_after_max`).
    """

    # urllib3 also retries 413 when it carries `Retry-After`; we don't.
    RETRY_AFTER_STATUS_CODES = frozenset({429, 503})

    def __init__(
        self,
        *args: Any,
        retry_max_wait: float = DEFAULT_RETRY_MAX_WAIT,
        retry_budget: float = DEFAULT_RETRY_BUDGET,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self.retry_max_wait = retry_max_wait
        self.retry_budget = retry_budget
        self._started: float | None = None
        # The wait before the next try, drawn once in `increment` so the
        # budget check and the sleep agree on it.
        self._delay: float | None = None

    def new(self, **kw: Any) -> "_JitteredRetry":
        retry = super().new(**kw)
        retry.retry_max_wait = self.retry_max_wait
        retry.retry_budget = self.retry_budget
        started = self._started
        if started is None:
            started = _call_started.get()
        if started is None:
            started = _monotonic()
        retry._started = started
        return retry

    def increment(
        self,
        method: str | None = None,
        url: str | None = None,
        response: Any = None,
        error: Exception | None = None,
        _pool: Any = None,
        _stacktrace: Any = None,
    ) -> "_JitteredRetry":
        retry = super().increment(method, url, response, error, _pool, _stacktrace)
        retry._delay = retry._next_delay(response)
        started = retry._started if retry._started is not None else _monotonic()
        if _monotonic() - started + retry._delay > retry.retry_budget:
            reason = error or ResponseError(f"retry budget of {retry.retry_budget}s spent")
            raise MaxRetryError(_pool, url, reason) from reason
        return retry

    def get_backoff_time(self) -> float:
        if self._delay is not None:
            return self._delay
        return self._jitter()

    def get_retry_after(self, response: Any) -> float | None:
        try:
            retry_after = super().get_retry_after(response)
        except InvalidHeader:
            return None  # unparseable: fall back to the jittered backoff
        if retry_after is None:
            return None
        return min(retry_after, self.retry_max_wait)

    def _next_delay(self, response: Any) -> float:
        # Mirrors `Retry.sleep`: a positive `Retry-After` wins over the backoff.
        if response is not None and self.respect_retry_after_header:
            retry_after = self.get_retry_after(response)
            if retry_after:
                return retry_after
        return self._jitter()

    def _jitter(self) -> float:
        attempt = min(max(len(self.history) - 1, 0), 30)
        ceiling = min(self.retry_max_wait, 2.0**attempt)
        return random.uniform(min(_RETRY_MIN_WAIT, ceiling), ceiling)


class _RetryBudgetAdapter(HTTPAdapter):
    """Marks when each call's first try starts, for the retry budget."""

    def send(self, request: Any, *args: Any, **kwargs: Any) -> requests.Response:
        token = _call_started.set(_monotonic())
        try:
            return super().send(request, *args, **kwargs)
        finally:
            _call_started.reset(token)


# Extract's answer when it knows the kind of page but none of the fields came
# back filled. Retrying returns the same answer, so it is never retried.
_NO_DATA_EXTRACTED = "no_data_extracted"


def _error_code(response: requests.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, str) else None


def is_no_data_extracted(response: requests.Response) -> bool:
    """True when the response is Extract's 422 `no_data_extracted`: Extract
    knows this kind of page but found no data on it. The request is not
    charged, and the client does not retry it."""
    code = _error_code(response)
    return response.status_code == 422 and code is not None and code.lower() == _NO_DATA_EXTRACTED


# Extract answers these when it can't serve the target domain: 403 REQS007
# (domain not prepared yet) or the legacy 402 AUTH010 (domain not enabled).
# extract(mode="auto") retries both once with Autoparse.
_EXTRACT_DOMAIN_UNAVAILABLE = {(403, "REQS007"), (402, "AUTH010")}


def _is_extract_domain_unavailable(response: requests.Response) -> bool:
    """True when the response is Extract saying it can't serve the target
    domain yet (403 REQS007, or the legacy 402 AUTH010)."""
    code = _error_code(response)
    if code is None:
        return False
    return (response.status_code, code.upper()) in _EXTRACT_DOMAIN_UNAVAILABLE


# Adaptive Stealth Mode (wire `mode=auto`) picks js_render / premium_proxy
# itself, so the API rejects it combined with either one set by hand.
_ADAPTIVE_STEALTH_CONFLICTS = ("js_render", "premium_proxy")


def _is_set(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def _with_adaptive_stealth(params: dict, enabled: bool) -> dict:
    """Add wire `mode=auto` to `params` unless the caller already chose a
    `mode`, or forces js_render / premium_proxy, which the API won't combine
    with Adaptive Stealth Mode."""
    if not enabled or "mode" in params:
        return params
    if any(_is_set(params.get(key)) for key in _ADAPTIVE_STEALTH_CONFLICTS):
        return params
    params["mode"] = "auto"
    return params


class ZenRowsClient:
    """Synchronous client for the Zenrows scraping API.

    Example:

        client = ZenRowsClient("zr_...")
        resp = client.get("https://example.com", params={"js_render": "true"})
        print(resp.text)

    `base_url` defaults to the public production endpoint; override via
    constructor arg or `ZENROWS_SCRAPER_BASE_URL` env var.

    `retries` (default 2) retries a 429, 502, 503 or 504, and a failure to
    connect, never any other status or a read timeout. Between tries it waits
    the server's `Retry-After`, or else a random 0.25s to `2 ** attempt`
    seconds; either is capped at `retry_max_wait` (default 30). No retry starts
    if its wait would end more than `retry_budget` seconds (default 90) after
    the first try. Pass `retries=0` to turn retries off.
    """

    # Kept as a class attribute for compatibility — pre-1.4 callers
    # could read `ZenRowsClient.api_url` directly.
    api_url = DEFAULT_SCRAPER_URL

    def __init__(
        self,
        apikey: str,
        retries: int = DEFAULT_RETRIES,
        concurrency: int = 5,
        *,
        base_url: str | None = None,
        retry_max_wait: float = DEFAULT_RETRY_MAX_WAIT,
        retry_budget: float = DEFAULT_RETRY_BUDGET,
    ):
        if not apikey:
            raise ValueError("ZenRowsClient: apikey is required.")
        self.apikey = apikey
        self.api_url = base_url or os.environ.get("ZENROWS_SCRAPER_BASE_URL") or DEFAULT_SCRAPER_URL

        self.retries = retries
        self.executor = ThreadPoolExecutor(max_workers=concurrency)
        self.requests_session = requests.Session()
        self.retry_max_wait = retry_max_wait
        self.retry_budget = retry_budget
        if retries > 0:
            # Retries a 429/502/503/504 and a failure to connect, nothing
            # else: a read error or timeout may come after the server
            # accepted the request, which keeps running and may be charged.
            max_retries = _JitteredRetry(
                total=retries,
                connect=retries,
                read=False,  # re-raise as is, so it stays a ReadTimeout
                status=retries,
                other=0,
                allowed_methods=None,
                status_forcelist=_RETRY_STATUSES,
                raise_on_status=False,
                retry_max_wait=retry_max_wait,
                retry_budget=retry_budget,
            )
            adapter = _RetryBudgetAdapter(max_retries=max_retries)
            self.requests_session.mount("https://", adapter)
            self.requests_session.mount("http://", adapter)

    # ---- sync HTTP verbs ----

    def fetch(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Fetch a URL through Zenrows — the main page-scraping product. This is
        the primary entry point; `get()` remains as a deprecated alias.
        """
        return self._worker("GET", url, params, headers, **kwargs)

    def get(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Deprecated: use `fetch()` instead. Kept for backward compatibility."""
        return self.fetch(url, params, headers, **kwargs)

    def extract(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        mode: str = "auto",
        fallback_to_autoparse: bool = True,
        adaptive_stealth: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """Fetch a URL and run it through Extract, Zenrows' AI-powered structured
        extraction. `mode` is one of "auto" (default), "native",
        or "standard". Thin wrapper over `fetch()` with the `extract` param set:
        no separate endpoint or auth.

        Extract works on any domain once Zenrows has prepared it, with any
        active API key. A domain that isn't prepared yet answers 403 with
        `code: "REQS007"` (prepare it with `POST /v1/extract/prepared-domains`);
        the legacy domain-not-enabled code is a 402 with `code: "AUTH010"`.
        On either, `mode="auto"` retries once with `autoparse=True` instead of
        returning the error response. Pass `fallback_to_autoparse=False` to
        disable that and get the raw error response back.

        A 422 with `code: "no_data_extracted"` means Extract knows this kind
        of page but found no data on it. It is not charged and is never
        retried; check it with `is_no_data_extracted(response)`.

        `adaptive_stealth=True` (default) also sends Adaptive Stealth Mode
        (`mode: "auto"` at the wire-param level - unrelated to this method's own
        `mode` argument) on both the extract attempt and the Autoparse fallback,
        so a target needing `js_render`/`premium_proxy` gets escalated
        automatically instead of failing with REQS002. It is skipped when
        `params` sets `js_render` or `premium_proxy` (the API rejects either
        combined with Adaptive Stealth Mode) or its own wire `mode`. Pass
        `adaptive_stealth=False` to turn it off entirely.
        """
        final_params = dict(params) if params else {}
        final_params["extract"] = mode
        _with_adaptive_stealth(final_params, adaptive_stealth)
        response = self.fetch(url, final_params, headers, **kwargs)

        if mode == "auto" and fallback_to_autoparse and _is_extract_domain_unavailable(response):
            autoparse_params = dict(params) if params else {}
            autoparse_params["autoparse"] = True
            _with_adaptive_stealth(autoparse_params, adaptive_stealth)
            return self.fetch(url, autoparse_params, headers, **kwargs)

        return response

    def post(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        data: Any = None,
        **kwargs: Any,
    ) -> requests.Response:
        return self._worker("POST", url, params, headers, data=data, **kwargs)

    def put(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        data: Any = None,
        **kwargs: Any,
    ) -> requests.Response:
        return self._worker("PUT", url, params, headers, data=data, **kwargs)

    # ---- async-flavoured wrappers (thread-pool offload) ----

    async def fetch_async(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        return await self._offload("GET", url, params, headers, **kwargs)

    async def get_async(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Deprecated: use `fetch_async()` instead. Kept for backward compatibility."""
        return await self.fetch_async(url, params, headers, **kwargs)

    async def extract_async(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        mode: str = "auto",
        fallback_to_autoparse: bool = True,
        adaptive_stealth: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """Async counterpart of `extract()` - see its docstring for the
        REQS007 / AUTH010 -> Autoparse fallback behavior,
        `fallback_to_autoparse`, and `adaptive_stealth`."""
        final_params = dict(params) if params else {}
        final_params["extract"] = mode
        _with_adaptive_stealth(final_params, adaptive_stealth)
        response = await self.fetch_async(url, final_params, headers, **kwargs)

        if mode == "auto" and fallback_to_autoparse and _is_extract_domain_unavailable(response):
            autoparse_params = dict(params) if params else {}
            autoparse_params["autoparse"] = True
            _with_adaptive_stealth(autoparse_params, adaptive_stealth)
            return await self.fetch_async(url, autoparse_params, headers, **kwargs)

        return response

    async def post_async(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        data: Any = None,
        **kwargs: Any,
    ) -> requests.Response:
        return await self._offload("POST", url, params, headers, data=data, **kwargs)

    async def put_async(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        data: Any = None,
        **kwargs: Any,
    ) -> requests.Response:
        return await self._offload("PUT", url, params, headers, data=data, **kwargs)

    # ---- lifecycle ----

    def close(self) -> None:
        """Release the requests session + drain the thread-pool."""
        self.requests_session.close()
        self.executor.shutdown(wait=True)

    def __enter__(self) -> "ZenRowsClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- internal ----

    async def _offload(
        self,
        method: str,
        url: str,
        params: dict | None,
        headers: dict | None,
        **kwargs: Any,
    ) -> requests.Response:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            partial(self._worker, method, url, params, headers, **kwargs),
        )

    def _worker(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        data: Any = None,
        **kwargs: Any,
    ) -> requests.Response:
        final_params: dict = {}
        if params:
            final_params.update(params)
        final_params["url"] = url
        final_params["apikey"] = self.apikey

        final_headers: dict = {"User-Agent": DEFAULT_USER_AGENT}

        if headers:
            # Caller wants their own headers forwarded to the target —
            # opt into the gateway's `custom_headers` mode and clear
            # the requests defaults that would otherwise stomp them.
            final_params["custom_headers"] = True
            final_headers["Accept"] = None
            final_headers["Accept-Encoding"] = urllib3.util.SKIP_HEADER
            final_headers["Connection"] = None
            final_headers.update(headers)

        return self.requests_session.request(
            method,
            self.api_url,
            params=final_params,
            headers=final_headers,
            data=data,
            **kwargs,
        )
