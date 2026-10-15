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
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any
from urllib.parse import quote

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from zenrows.__version__ import __version__

DEFAULT_SCRAPER_URL = "https://api.zenrows.com/v1/"
DEFAULT_USER_AGENT = f"zenrows/{__version__} python"

# Status codes the urllib3 retry layer treats as transient. 422 is retried
# too, but in `_worker`, because whether it is worth retrying depends on the
# response body (see `is_no_data_extracted`).
_RETRY_STATUSES = [429, 500, 502, 503, 504]
_RETRY_BACKOFF_FACTOR = 0.5

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
# extract(mode="auto", fallback_to_autoparse=True) retries both once with the
# deprecated Autoparse; by default they are returned to the caller as is.
_EXTRACT_DOMAIN_UNAVAILABLE = {(403, "REQS007"), (402, "AUTH010")}


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


def _is_extract_domain_unavailable(response: requests.Response) -> bool:
    """True when the response is Extract saying it can't serve the target
    domain yet (403 REQS007, or the legacy 402 AUTH010)."""
    code = _error_code(response)
    if code is None:
        return False
    return (response.status_code, code.upper()) in _EXTRACT_DOMAIN_UNAVAILABLE


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
    """

    # Kept as a class attribute for compatibility — pre-1.4 callers
    # could read `ZenRowsClient.api_url` directly.
    api_url = DEFAULT_SCRAPER_URL

    def __init__(
        self,
        apikey: str,
        retries: int = 0,
        concurrency: int = 5,
        *,
        base_url: str | None = None,
    ):
        if not apikey:
            raise ValueError("ZenRowsClient: apikey is required.")
        self.apikey = apikey
        self.api_url = base_url or os.environ.get("ZENROWS_SCRAPER_BASE_URL") or DEFAULT_SCRAPER_URL

        self.retries = retries
        self.executor = ThreadPoolExecutor(max_workers=concurrency)
        self.requests_session = requests.Session()
        if retries > 0:
            max_retries = Retry(
                total=retries,
                backoff_factor=_RETRY_BACKOFF_FACTOR,
                status_forcelist=_RETRY_STATUSES,
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=max_retries)
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
        A 403 with `code: "REQS007"` means the domain isn't prepared for
        Extract yet: prepare it with `POST /v1/extract/prepared-domains`
        (`prepare_domain()`, then check it with `prepared_domain_status()`),
        or pass `fallback_to_autoparse=True` to retry with deprecated
        autoparse.
        The legacy domain-not-enabled code is a 402 with `code: "AUTH010"`.
        By default both error responses are returned to you as is.

        `fallback_to_autoparse=True` (opt-in, default False) makes
        `mode="auto"` retry once with `autoparse=True` on either code instead
        of returning the error response. Autoparse is deprecated, and the
        retry is a second, separately charged request.

        A 422 with `code: "no_data_extracted"` means Extract knows this kind
        of page but found no data on it. It is not charged and is never
        retried; check it with `is_no_data_extracted(response)`.

        `adaptive_stealth=True` (default) also sends Adaptive Stealth Mode
        (`mode: "auto"` at the wire-param level - unrelated to this method's own
        `mode` argument) on the extract attempt and on the Autoparse fallback
        when `fallback_to_autoparse=True`,
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
        REQS007 / AUTH010 handling, the opt-in `fallback_to_autoparse`, and
        `adaptive_stealth`."""
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

        response = self.requests_session.request(
            method,
            self.api_url,
            params=final_params,
            headers=final_headers,
            data=data,
            **kwargs,
        )
        # Retry a 422 here rather than in urllib3, which can't read the body:
        # Extract's `no_data_extracted` is a final answer, any other 422 is a
        # failure to get the target's content and is worth another try.
        attempt = 0
        while (
            response.status_code == 422
            and attempt < self.retries
            and not is_no_data_extracted(response)
        ):
            attempt += 1
            # Same schedule as urllib3's Retry: no wait before the first retry.
            if attempt > 1:
                time.sleep(_RETRY_BACKOFF_FACTOR * 2 ** (attempt - 1))
            response = self.requests_session.request(
                method,
                self.api_url,
                params=final_params,
                headers=final_headers,
                data=data,
                **kwargs,
            )
        return response
