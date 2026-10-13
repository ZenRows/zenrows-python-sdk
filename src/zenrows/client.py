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
import os
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

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
# extract(mode="auto") retries both once with Autoparse.
_EXTRACT_DOMAIN_UNAVAILABLE = {(403, "REQS007"), (402, "AUTH010")}


def _is_extract_domain_unavailable(response: requests.Response) -> bool:
    """True when the response is Extract saying it can't serve the target
    domain yet (403 REQS007, or the legacy 402 AUTH010)."""
    code = _error_code(response)
    if code is None:
        return False
    return (response.status_code, code.upper()) in _EXTRACT_DOMAIN_UNAVAILABLE


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
        automatically instead of failing with REQS002. Pass
        `adaptive_stealth=False` to disable that and set `js_render`/
        `premium_proxy` yourself.
        """
        final_params = dict(params) if params else {}
        final_params["extract"] = mode
        if adaptive_stealth:
            final_params["mode"] = "auto"
        response = self.fetch(url, final_params, headers, **kwargs)

        if mode == "auto" and fallback_to_autoparse and _is_extract_domain_unavailable(response):
            autoparse_params = dict(params) if params else {}
            autoparse_params["autoparse"] = True
            if adaptive_stealth:
                autoparse_params["mode"] = "auto"
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
        if adaptive_stealth:
            final_params["mode"] = "auto"
        response = await self.fetch_async(url, final_params, headers, **kwargs)

        if mode == "auto" and fallback_to_autoparse and _is_extract_domain_unavailable(response):
            autoparse_params = dict(params) if params else {}
            autoparse_params["autoparse"] = True
            if adaptive_stealth:
                autoparse_params["mode"] = "auto"
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
