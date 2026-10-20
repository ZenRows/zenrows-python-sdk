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
import math
import os
import random
import re
import time
import warnings
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.exceptions import MaxRetryError, ResponseError
from urllib3.util.retry import Retry

from zenrows.__version__ import __version__

DEFAULT_SCRAPER_URL = "https://api.zenrows.com/v1/"
DEFAULT_USER_AGENT = f"zenrows/{__version__} python"

# Status codes worth another try, whatever the body says. A 422 is retried
# too, but in `_worker`, because whether it is worth retrying depends on the
# body (see `is_no_data_extracted`), which urllib3 cannot read.
_RETRY_STATUSES = (429, 500, 502, 503, 504)
DEFAULT_RETRIES = 2
# Longest jittered wait between two tries, in seconds. It does not cap
# `Retry-After`, which has its own fixed cap below.
DEFAULT_RETRY_MAX_WAIT = 30.0
# No retry starts after this many seconds, counted from the call's first try.
# It does not cut a running try short.
DEFAULT_RETRY_BUDGET = 90.0
# Shortest wait between two tries, so a retry never fires back to back.
_RETRY_MIN_WAIT = 0.25
# Longest wait a `Retry-After` header can ask for.
_RETRY_AFTER_CAP = 30.0
# `Retry-After` is honoured only as whole seconds. An HTTP date, a fraction,
# a sign or anything else falls back to the jittered wait.
_RETRY_AFTER_SECONDS = re.compile(r"[0-9]+")

# The only methods the client retries. Any other method is sent once: the API
# does not deduplicate a request by `Idempotency-Key`, so a second POST or PUT
# could run twice.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Clock for the retry budget; tests replace it.
_monotonic = time.monotonic


def _retry_after_seconds(value: str | None) -> float | None:
    """The wait a `Retry-After` header asks for, or None when it is not whole
    seconds. Clamped to [0.25, 30] seconds."""
    if value is None:
        return None
    value = value.strip()
    if not _RETRY_AFTER_SECONDS.fullmatch(value):
        return None
    # Three or more significant digits is already past the cap. Return the cap
    # without parsing: int() raises on a string of thousands of digits.
    digits = value.lstrip("0")
    if len(digits) > 2:
        return _RETRY_AFTER_CAP
    return min(max(float(int(digits or "0")), _RETRY_MIN_WAIT), _RETRY_AFTER_CAP)


def _jitter(attempt: int, retry_max_wait: float) -> float:
    """A random wait in [0.25, max(0.25, min(retry_max_wait, 2 ** attempt))]
    seconds (full jitter); `attempt` counts retries from 0."""
    ceiling = max(_RETRY_MIN_WAIT, min(retry_max_wait, 2.0 ** min(attempt, 30)))
    return _RETRY_MIN_WAIT + (ceiling - _RETRY_MIN_WAIT) * random.random()


def _retry_delay(retry_after: str | None, attempt: int, retry_max_wait: float) -> float:
    """The wait before retry number `attempt` (from 0): the `Retry-After`
    header when it is whole seconds, or else the jittered wait."""
    seconds = _retry_after_seconds(retry_after)
    if seconds is not None:
        return seconds
    return _jitter(attempt, retry_max_wait)


def _check_retry_options(retries: Any, retry_max_wait: Any, retry_budget: Any) -> None:
    """Raise ValueError for a retry option the policy cannot use."""
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ValueError(
            f"ZenRowsClient: retries must be an integer of 0 or more, got {retries!r}."
        )
    for name, value in (("retry_max_wait", retry_max_wait), ("retry_budget", retry_budget)):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(
                f"ZenRowsClient: {name} must be a finite number of seconds above 0, got {value!r}."
            )


def _method_can_be_sent_again(method: str | None) -> bool:
    """True for GET, HEAD and OPTIONS, the only methods the client retries."""
    return (method or "").upper() in _SAFE_METHODS


def _body_can_be_sent_again(body: Any) -> bool:
    """True when a request body is none, a string or bytes. A file object, a
    generator, an iterator or a stream is read by the first try, so it is
    never sent again."""
    return body is None or isinstance(body, (bytes, str))


def _data_can_be_sent_again(data: Any) -> bool:
    """True when `requests` builds the same body from `data` every time:
    none, a string, bytes, or a dict or list it encodes. A file object, a
    generator or an iterator is read by the first try."""
    return data is None or isinstance(data, (bytes, str, Mapping, list, tuple))


def _can_send_again(request: requests.PreparedRequest) -> bool:
    """True when urllib3 may send `request` again: a GET, HEAD or OPTIONS
    whose body is none, a string or bytes."""
    return _method_can_be_sent_again(request.method) and _body_can_be_sent_again(request.body)


# The `original_status` values the client reads as true, in any case. The
# docs spell it "true"; requests sends a Python True as "True" and 1 as "1".
_TRUE_VALUES = frozenset({"true", "1"})


@dataclass(frozen=True)
class _TargetStatuses:
    """The statuses of one request that come from the target site, not from
    the API. Another try cannot change them, so they are never retried.

    With `original_status` set, the API answers with the target's own
    status, so every status may be the target's. With
    `allowed_status_codes`, the API passes the listed target statuses
    through."""

    every: bool = False
    listed: frozenset[int] = frozenset()

    def __contains__(self, status: object) -> bool:
        return self.every or status in self.listed

    @classmethod
    def from_url(cls, url: str | None) -> "_TargetStatuses":
        query = parse_qs(urlsplit(url or "").query, keep_blank_values=True)
        every = any(v.strip().lower() in _TRUE_VALUES for v in query.get("original_status", []))
        # A comma-separated list, or the key repeated (requests sends a
        # list that way). A token that is not a status code is ignored.
        listed = frozenset(
            int(token)
            for value in query.get("allowed_status_codes", [])
            for token in (part.strip() for part in value.split(","))
            if token.isdigit()
        )
        return cls(every, listed)


@dataclass
class _CallState:
    """The retry state of one call. urllib3's retries and the 422 retry in
    `_worker` share it, so they share one retry count and one budget."""

    started: float
    retries_used: int = 0
    # Whether urllib3 may send the current request again; set per request.
    can_send_again: bool = True
    # The current request's statuses that come from the target site, which
    # neither urllib3 nor the 422 loop retries; set per request.
    target_statuses: _TargetStatuses = _TargetStatuses()


# The current call's retry state. A context variable, so concurrent calls on
# the client's thread pool each see their own.
_call_state: contextvars.ContextVar[_CallState | None] = contextvars.ContextVar(
    "zenrows_call_state", default=None
)


@contextmanager
def _call_scope() -> Iterator[_CallState]:
    """The current call's retry state, or a new one when there is none yet."""
    state = _call_state.get()
    if state is not None:
        yield state
        return
    state = _CallState(started=_monotonic())
    token = _call_state.set(state)
    try:
        yield state
    finally:
        _call_state.reset(token)


@dataclass(frozen=True)
class _RetryPolicy:
    """How often and how long the client retries. One instance per client."""

    retries: int
    retry_max_wait: float
    retry_budget: float

    def next_delay(self, state: _CallState, retry_after: str | None) -> float | None:
        """The wait before the call's next retry, or None when no retry may
        start: the retries are used up, or the wait would end past the
        budget. Counts the retry when it allows one. The caller first checks
        that the request may be sent again."""
        if state.retries_used >= self.retries:
            return None
        delay = _retry_delay(retry_after, state.retries_used, self.retry_max_wait)
        if _monotonic() - state.started + delay > self.retry_budget:
            return None
        state.retries_used += 1
        return delay


class _JitteredRetry(Retry):
    """urllib3 `Retry` that follows the client's `_RetryPolicy`.

    The policy decides whether a retry starts and how long to wait before
    it. urllib3 still decides which statuses and errors are retryable.

    Only the hooks urllib3 1.26 and 2.x share are overridden, so it works on
    both (no `backoff_jitter` / `retry_after_max`).
    """

    # urllib3 also retries 413 when it carries `Retry-After`; we don't.
    RETRY_AFTER_STATUS_CODES = frozenset({429, 503})

    def __init__(self, *args: Any, policy: _RetryPolicy | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.policy = policy or _RetryPolicy(
            retries=self.total if isinstance(self.total, int) else 0,
            retry_max_wait=DEFAULT_RETRY_MAX_WAIT,
            retry_budget=DEFAULT_RETRY_BUDGET,
        )
        # The call this retry belongs to, when used outside the client's
        # adapter (which sets the context variable).
        self._state: _CallState | None = None
        # The wait before the next try, drawn once in `increment` so the
        # budget check and the sleep agree on it.
        self._delay: float | None = None

    def new(self, **kw: Any) -> "_JitteredRetry":
        retry = super().new(**kw)
        retry.policy = self.policy
        retry._state = self._current_state()
        return retry

    def _current_state(self) -> _CallState:
        state = self._state or _call_state.get()
        return state if state is not None else _CallState(started=_monotonic())

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
        retry_after = response.headers.get("Retry-After") if response is not None else None
        state = retry._current_state()
        from_target = response is not None and response.status in state.target_statuses
        may_retry = state.can_send_again and not from_target
        delay = retry.policy.next_delay(state, retry_after) if may_retry else None
        if delay is None:
            reason = error or ResponseError("no retry left for this call")
            raise MaxRetryError(_pool, url, reason) from reason
        retry._delay = delay
        return retry

    def get_retry_after(self, response: Any) -> float | None:
        return _retry_after_seconds(response.headers.get("Retry-After"))

    def get_backoff_time(self) -> float:
        if self._delay is not None:
            return self._delay
        return _jitter(len(self.history), self.policy.retry_max_wait)

    def sleep(self, response: Any = None) -> None:
        time.sleep(self.get_backoff_time())


class _RetryBudgetAdapter(HTTPAdapter):
    """Gives each request the retry state of the call it belongs to, and
    says whether that request may be sent again and which of its statuses
    come from the target site."""

    def send(self, request: Any, *args: Any, **kwargs: Any) -> requests.Response:
        with _call_scope() as state:
            state.can_send_again = _can_send_again(request)
            state.target_statuses = _TargetStatuses.from_url(request.url)
            return super().send(request, *args, **kwargs)


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


_FALLBACK_DEPRECATED = (
    "fallback_to_autoparse is deprecated and has no effect: extract() no longer "
    "retries with autoparse. On a 403 REQS007, check `reason` in the problem "
    "body; for domain_not_prepared, prepare the domain with prepare_domain()."
)


def _check_domain(domain: str) -> None:
    # An empty domain would request the list endpoint and read its answer as
    # a status, so it never leaves the client.
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError("domain must be a non-empty string")


def _prepared_domains_request(method: str, url: str, **kwargs: Any) -> requests.Response:
    # Not through `requests_session`: its retry adapter would retry a 429 or
    # 503, which these endpoints use for answers to read (out_of_allowance,
    # overloaded). The Go and Node SDKs skip client retries here too.
    with requests.Session() as session:
        return session.request(method, url, **kwargs)


def _prepared_domain_body(response: requests.Response) -> dict:
    if not response.ok:
        raise PreparedDomainError.from_response(response)
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise PreparedDomainError(response.status_code, None)
    return body


class PreparedDomainError(Exception):
    """A non-2xx answer from the prepared-domains endpoints
    (`prepare_domain()` / `prepared_domain_status()`).

    Branch on `status_code` + `code`; display `detail`, don't parse it.
    `code` is the problem's `code` member, for example `invalid_argument`
    (400), `not_found` (404), `in_progress` (409: a preparation is already
    running, read its status instead of resubmitting), `out_of_allowance`
    (429: no preparations left this month), or `overloaded` / `disabled`
    (503); None when the body has no code. `problem` holds the whole body,
    or None when it was not JSON.
    """

    def __init__(
        self,
        status_code: int,
        problem: dict | None,
        *,
        retry_after: float | None = None,
    ):
        self.status_code = status_code
        self.problem = problem
        # Seconds the server asked to wait (`Retry-After`), when it sent one.
        self.retry_after = retry_after

        def member(name: str) -> str | None:
            value = problem.get(name) if problem else None
            return value if isinstance(value, str) else None

        self.code = member("code")
        self.detail = member("detail")
        self.reason = member("reason")
        title = member("title") or "Prepared-domains request failed"
        explanation = self.detail or self.code
        super().__init__(f"{status_code} {title}" + (f": {explanation}" if explanation else ""))

    @classmethod
    def from_response(cls, response: requests.Response) -> "PreparedDomainError":
        try:
            body = response.json()
        except ValueError:
            body = None
        try:
            retry_after: float | None = float(response.headers.get("Retry-After", ""))
        except ValueError:
            retry_after = None
        if retry_after is not None and (not math.isfinite(retry_after) or retry_after < 0):
            retry_after = None
        return cls(
            response.status_code,
            body if isinstance(body, dict) else None,
            retry_after=retry_after,
        )


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

    `retries` (default 2) retries a 429, 500, 502, 503 or 504, a 422 other
    than Extract's `no_data_extracted`, and a failure to connect. It never
    retries any other status, a read timeout, or a connection that fails
    after the request was sent. It retries only a GET, HEAD or OPTIONS whose
    body is none, a string, bytes, or a dict it encodes. A POST or PUT is
    always sent once: the API does not deduplicate a request by
    `Idempotency-Key`, so the client does not retry on it. It retries no
    status when `params` sets `original_status`, and no status listed in
    `allowed_status_codes`: that status comes from the target site.

    Between tries it waits the server's `Retry-After` when that is whole
    seconds (at least 0.25, at most 30), or else a random 0.25 to
    `min(retry_max_wait, 2 ** attempt)` seconds (default `retry_max_wait`
    30). No retry starts after `retry_budget` seconds (default 90) from the
    call's first try. Pass `retries=0` to turn retries off. A call cannot be
    cancelled, so a wait between tries runs to its end.

    `prepare_domain()` and `prepared_domain_status()` never retry: their 429
    and 503 are answers to read.
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
        _check_retry_options(retries, retry_max_wait, retry_budget)
        self.apikey = apikey
        self.api_url = base_url or os.environ.get("ZENROWS_SCRAPER_BASE_URL") or DEFAULT_SCRAPER_URL

        self.retries = retries
        self.executor = ThreadPoolExecutor(max_workers=concurrency)
        self.requests_session = requests.Session()
        self.retry_max_wait = retry_max_wait
        self.retry_budget = retry_budget
        # None when retries are off. `_worker` uses it for the 422 retry.
        self._retry_policy: _RetryPolicy | None = None
        if retries > 0:
            self._retry_policy = _RetryPolicy(retries, retry_max_wait, retry_budget)
            # Retries the statuses above and a failure to connect, nothing
            # else: a read error, a read timeout or a connection that drops
            # after the request was sent may come after the server accepted
            # the request, which keeps running and may be charged.
            max_retries = _JitteredRetry(
                total=retries,
                connect=retries,
                read=False,  # re-raise as is, so it stays a ReadTimeout
                status=retries,
                other=0,
                allowed_methods=None,  # `_can_send_again` decides instead
                status_forcelist=_RETRY_STATUSES,
                raise_on_status=False,
                policy=self._retry_policy,
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
        fallback_to_autoparse: bool = False,
        adaptive_stealth: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """Fetch a URL and run it through Extract, Zenrows' AI-powered structured
        extraction. `mode` is one of "auto" (default), "native",
        or "standard". Thin wrapper over `fetch()` with the `extract` param set:
        no separate endpoint or auth.

        Extract works on any prepared domain (you can prepare one yourself
        with `POST /v1/extract/prepared-domains`), with any active API key.
        A 403 with `code: "REQS007"` means Extract can't extract this page.
        Check `reason` in the problem body (`response.json().get("reason")`)
        when present; older responses may not carry it:

          - `domain_not_prepared`: prepare the domain with `prepare_domain()`,
            poll `prepared_domain_status()` until `status` is `"ready"`, then
            retry.
          - `page_not_recognised`: the domain is served, but this page isn't
            one Extract extracts (a home, search, login or category page).
            Try a product, article or listing page, or retry once in a few
            minutes. Preparing the domain again changes nothing.
          - `domain_not_enabled`: preparing doesn't help. Use
            `css_extractor`, or contact support.

        The legacy domain-not-enabled code is a 402 with `code: "AUTH010"`.
        Both error responses are returned to you as is, in one request.

        `fallback_to_autoparse` is deprecated and has no effect: extract()
        never retries with autoparse. Passing True emits a
        `DeprecationWarning`.

        A 422 with `code: "no_data_extracted"` means Extract knows this kind
        of page but found no data on it. It is not charged and is never
        retried; check it with `is_no_data_extracted(response)`.

        `adaptive_stealth=True` (default) also sends Adaptive Stealth Mode
        (`mode: "auto"` at the wire-param level - unrelated to this method's own
        `mode` argument), so a target needing `js_render`/`premium_proxy` gets escalated
        automatically instead of failing with REQS002. It is skipped when
        `params` sets `js_render` or `premium_proxy` (the API rejects either
        combined with Adaptive Stealth Mode) or its own wire `mode`. Pass
        `adaptive_stealth=False` to turn it off entirely.
        """
        if fallback_to_autoparse:
            warnings.warn(_FALLBACK_DEPRECATED, DeprecationWarning, stacklevel=2)
        final_params = dict(params) if params else {}
        final_params["extract"] = mode
        _with_adaptive_stealth(final_params, adaptive_stealth)
        return self.fetch(url, final_params, headers, **kwargs)

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

    # ---- Extract prepared domains ----

    def prepare_domain(self, domain: str, example_urls: list[str]) -> dict:
        """Submit `domain` for Extract preparation
        (`POST /v1/extract/prepared-domains`) and return the parsed status
        object the API answers with `202`: `domain`, `status` (`preparing`,
        `ready` or `failed`), `state`, `submitted_at`, `updated_at`.

        `domain` is the domain without a scheme. `example_urls` are 1 to 5
        URLs on that domain, one per kind of page you want to extract.
        Submitting a domain that is already prepared returns its current
        status instead of starting a new run.

        Preparation takes minutes. This method does not wait for it: call
        `prepared_domain_status(domain)` until `status` is no longer
        `"preparing"`. These endpoints don't fetch pages and cost no
        credits.

        The client's `retries` setting doesn't apply to these endpoints.
        Raises `ValueError` for an empty `domain`, before any request, and
        `PreparedDomainError` on a non-2xx answer, for example 400
        `invalid_argument`, 409 `in_progress`, 429 `out_of_allowance` or
        503 `overloaded` / `disabled`.
        """
        _check_domain(domain)
        response = _prepared_domains_request(
            "POST",
            self._prepared_domains_url(),
            json={"domain": domain, "example_urls": list(example_urls)},
            headers=self._prepared_domains_headers(),
        )
        return _prepared_domain_body(response)

    def prepared_domain_status(self, domain: str) -> dict:
        """Read the preparation status of `domain`
        (`GET /v1/extract/prepared-domains/{domain}`) and return the parsed
        status object. `status` is `preparing`, `ready` or `failed`; a
        failed preparation carries a `failure` object with `code`, `detail`
        and `slot_consumed`. `state` and `roles` report progress.

        Raises `ValueError` for an empty `domain`, before any request, and
        `PreparedDomainError` on a non-2xx answer, for example 404
        `not_found` when your account never submitted `domain`.
        """
        _check_domain(domain)
        response = _prepared_domains_request(
            "GET",
            f"{self._prepared_domains_url()}/{quote(domain, safe='')}",
            headers=self._prepared_domains_headers(),
        )
        return _prepared_domain_body(response)

    def _prepared_domains_url(self) -> str:
        return self.api_url.rstrip("/") + "/extract/prepared-domains"

    def _prepared_domains_headers(self) -> dict:
        return {"X-API-Key": self.apikey, "User-Agent": DEFAULT_USER_AGENT}

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
        fallback_to_autoparse: bool = False,
        adaptive_stealth: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """Async counterpart of `extract()` - see its docstring for the
        REQS007 `reason` cases, the deprecated `fallback_to_autoparse`, and
        `adaptive_stealth`."""
        if fallback_to_autoparse:
            warnings.warn(_FALLBACK_DEPRECATED, DeprecationWarning, stacklevel=2)
        final_params = dict(params) if params else {}
        final_params["extract"] = mode
        _with_adaptive_stealth(final_params, adaptive_stealth)
        return await self.fetch_async(url, final_params, headers, **kwargs)

    async def prepare_domain_async(self, domain: str, example_urls: list[str]) -> dict:
        """Async counterpart of `prepare_domain()`, run on the client's thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor, partial(self.prepare_domain, domain, example_urls)
        )

    async def prepared_domain_status_async(self, domain: str) -> dict:
        """Async counterpart of `prepared_domain_status()`, run on the client's
        thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor, partial(self.prepared_domain_status, domain)
        )

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

        send = partial(
            self.requests_session.request,
            method,
            self.api_url,
            params=final_params,
            headers=final_headers,
            data=data,
            **kwargs,
        )
        policy = self._retry_policy
        if policy is None:
            return send()
        with _call_scope() as state:
            response = send()
            # This loop builds the request again from `data` and `files`, so it
            # retries only a method it may send again, with a body built the
            # same way each time. A file passed as `files` is read by the
            # first try, so it is never retried here.
            if not (
                _method_can_be_sent_again(method)
                and _data_can_be_sent_again(data)
                and not kwargs.get("files")
            ):
                return response
            # Retry a 422 here rather than in urllib3, which can't read the
            # body: Extract's `no_data_extracted` is a final answer, any other
            # 422 is a failure to get the target's content and is worth
            # another try. Same count, wait and budget as urllib3's retries.
            # A 422 from the target site (`original_status`, or listed in
            # `allowed_status_codes`) is the target's answer: not retried.
            while (
                response.status_code == 422
                and not is_no_data_extracted(response)
                and 422 not in state.target_statuses
            ):
                delay = policy.next_delay(state, response.headers.get("Retry-After"))
                if delay is None:
                    break
                time.sleep(delay)
                response = send()
            return response
