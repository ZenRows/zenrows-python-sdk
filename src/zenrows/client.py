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

# Status codes the retry layer treats as transient. The set was inherited
# from the original SDK and matches the gateway's documented retry guidance.
_RETRY_STATUSES = [422, 429, 500, 502, 503, 504]


def _is_auth010(response: requests.Response) -> bool:
    """True when a response's JSON error envelope carries the Extract
    domain-not-enabled code (AUTH010)."""
    try:
        body = response.json()
    except ValueError:
        return False
    code = body.get("code") if isinstance(body, dict) else None
    return isinstance(code, str) and code.upper() == "AUTH010"


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

        self.executor = ThreadPoolExecutor(max_workers=concurrency)
        self.requests_session = requests.Session()
        if retries > 0:
            max_retries = Retry(
                total=retries,
                backoff_factor=0.5,
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
        **kwargs: Any,
    ) -> requests.Response:
        """Fetch a URL and run it through Extract — Zenrows' AI-powered structured
        extraction (beta). `mode` is one of "auto" (default), "native",
        or "standard". Thin wrapper over `fetch()` with the `extract` param set —
        no separate endpoint or auth.

        `mode="auto"` is a domain-gated open beta: when the target domain isn't
        enabled yet, the API returns a 402 with `code: "AUTH010"`. By default
        this retries once with `autoparse=True` instead of returning the error
        response — pass `fallback_to_autoparse=False` to disable that and get
        the raw AUTH010 response back.
        """
        final_params = dict(params) if params else {}
        final_params["extract"] = mode
        response = self.fetch(url, final_params, headers, **kwargs)

        if (
            response.status_code == 402
            and mode == "auto"
            and fallback_to_autoparse
            and _is_auth010(response)
        ):
            autoparse_params = dict(params) if params else {}
            autoparse_params["autoparse"] = True
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
        **kwargs: Any,
    ) -> requests.Response:
        """Async counterpart of `extract()` - see its docstring for the
        AUTH010 -> Autoparse fallback behavior and `fallback_to_autoparse`."""
        final_params = dict(params) if params else {}
        final_params["extract"] = mode
        response = await self.fetch_async(url, final_params, headers, **kwargs)

        if (
            response.status_code == 402
            and mode == "auto"
            and fallback_to_autoparse
            and _is_auth010(response)
        ):
            autoparse_params = dict(params) if params else {}
            autoparse_params["autoparse"] = True
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
