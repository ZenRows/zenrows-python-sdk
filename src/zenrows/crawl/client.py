"""ZenRowsCrawlClient — the typed facade over the Crawl API (New).

A crawl starts from one URL, follows links up to `depth` hops, and
keeps the URLs that match its patterns, up to `max_items`. It runs as
a job: `create` returns at once with `status=running`, and the URLs
grow as pages finish.

    client = ZenRowsCrawlClient(api_key)
    crawl = client.create("https://example.com/shop/", depth=1,
                          include_patterns=["/product/"])
    client.wait(crawl.crawl_id)
    for result in client.results(crawl.crawl_id):
        print(result.url)

Requests go through the SDK's shared HTTP transport (auth header,
retries, problem+json mapping), which raises `CrawlAPIError`.
"""

import json
import logging
import random
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from typing import Any, Literal

import httpx

from zenrows.__version__ import __version__
from zenrows.batch._transport import _RETRYABLE_STATUSES, _Transport
from zenrows.crawl.errors import CrawlAPIError
from zenrows.crawl.models import (
    Crawl,
    CrawlList,
    CrawlResult,
    CrawlStatus,
    CrawlStop,
    CrawlWithResults,
    DownloadLine,
)

_DEFAULT_BASE_URL = "https://api.zenrows.com/v1"
DEFAULT_USER_AGENT = f"zenrows-crawl-python/{__version__}"

_log = logging.getLogger("zenrows.crawl.transport")

# 429 `too_many_crawls` is a capacity limit, not a transient failure.
_CREATE_RETRY_STATUSES = _RETRYABLE_STATUSES - {429}

_POLL_INTERVAL = 2.0
_POLL_BACKOFF = 1.5
_MAX_POLL_INTERVAL = 15.0


class CrawlDownload:
    """A crawl's NDJSON download, open and ready to read.

    `status` is the crawl's status when the file was read
    (`X-Crawl-Status`): `running` means the file holds only what the
    crawl has kept so far. Iterate `lines` once for the parsed lines;
    the connection closes when the iteration ends. Use it as a context
    manager to close it without reading to the end.
    """

    def __init__(self, response: httpx.Response, close: Callable[[], None]):
        self.status: CrawlStatus | None = _crawl_status(response)
        self._response = response
        self._close = close

    @property
    def lines(self) -> Iterator[DownloadLine]:
        return self._lines()

    def _lines(self) -> Iterator[DownloadLine]:
        try:
            for line in self._response.iter_lines():
                if line.strip():
                    yield DownloadLine.model_validate_json(line)
        finally:
            self.close()

    def close(self) -> None:
        self._close()

    def __enter__(self) -> "CrawlDownload":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class ZenRowsCrawlClient:
    """Synchronous, typed client for the ZenRows Crawl API (New).

    `api_key` is required:

        client = ZenRowsCrawlClient(api_key=os.environ["ZENROWS_API_KEY"])

    Every non-2xx raises `CrawlAPIError`; branch on its `status_code`
    and `code`. An account without Crawl enabled gets 403 `REQS008`
    (`CRAWL_NOT_ENABLED_CODE`).
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout: float | httpx.Timeout = 30.0,
        retries: int = 3,
        verify_ssl: bool | str = True,
        user_agent: str = DEFAULT_USER_AGENT,
        httpx_args: dict[str, Any] | None = None,
    ):
        """`base_url` defaults to `https://api.zenrows.com/v1`. `retries` bounds
        automatic retries of transient failures (HTTP 429/502/503/504
        and network errors) on idempotent requests — GETs, and a
        `create` that carries an `idempotency_key` (never on 429 for
        `create`). Retries use jittered exponential backoff and honor
        `Retry-After`; set `retries=0` to disable."""
        if not api_key:
            raise ValueError("ZenRowsCrawlClient: api_key is required.")
        self._t = _Transport(
            base_url=base_url or _DEFAULT_BASE_URL,
            api_key=api_key,
            user_agent=user_agent,
            timeout=timeout,
            retries=retries,
            verify=verify_ssl,
            httpx_args=httpx_args,
            error_from_response=CrawlAPIError.from_response,
            log=_log,
        )

    # ----- lifecycle -----

    def __enter__(self) -> "ZenRowsCrawlClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._t.close()

    @property
    def base_url(self) -> str:
        return self._t.base_url

    # ===== crawls =====

    def create(
        self,
        url: str,
        *,
        depth: int,
        max_items: int | None = None,
        max_pages: int | None = None,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        output_format: Literal["html"] | None = None,
        idempotency_key: str | None = None,
    ) -> Crawl:
        """`POST /crawls` — start a crawl and return it, `running`.

        - `depth`: link hops to follow from `url` (1 to 100,000).
        - `max_items`: stop after keeping this many URLs (server
          default 10).
        - `max_pages`: stop after fetching this many pages, which
          bounds the cost (server default 10).
        - `include_patterns` / `exclude_patterns`: substrings matched
          against each normalized URL. A URL is kept when it matches
          an include pattern (if any) and no exclude pattern. A crawl
          stays on the start URL's registrable domain; subdomains count.
        - `output_format="html"`: also fetch every kept URL's page,
          readable with `content` / `download`. Omit for URLs only.
        - `idempotency_key`: a retry with the same key and body answers
          with the crawl the first request created.

        Only the arguments you pass are sent. When the account has
        reached its limit of active jobs (3 by default), shared with its
        Batch jobs, `create` raises 429 `too_many_crawls` without
        retrying; retry after the error's `retry_after` seconds.
        """
        body: dict[str, Any] = {"url": url, "depth": depth}
        optional: dict[str, Any] = {
            "max_items": max_items,
            "max_pages": max_pages,
            "include_patterns": include_patterns,
            "exclude_patterns": exclude_patterns,
            "output_format": output_format,
        }
        body.update({k: v for k, v in optional.items() if v is not None})
        headers = {"Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        response = self._t.request(
            "POST",
            "/crawls",
            headers=headers,
            content=json.dumps(body).encode("utf-8"),
            retry_statuses=_CREATE_RETRY_STATUSES,
        )
        return Crawl.model_validate(response.json())

    def get(
        self,
        crawl_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> CrawlWithResults:
        """`GET /crawls/{crawl_id}` — the crawl and one page of its
        results (`limit` 1 to 10,000, server default 1,000). Pass the
        page's `next_cursor` as `cursor` for the URLs kept since. For
        every result, prefer `results`."""
        return CrawlWithResults.model_validate(
            self._t.request_json(
                "GET", f"/crawls/{crawl_id}", params={"cursor": cursor, "limit": limit}
            )
        )

    def list(
        self,
        *,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> CrawlList:
        """`GET /crawls` — one page of the account's crawls, newest
        first (`limit` 1 to 100, server default 20), without results.
        Pass the page's `next_cursor` as `cursor` for the next page; it
        is None on the last page."""
        return CrawlList.model_validate(
            self._t.request_json("GET", "/crawls", params={"cursor": cursor, "limit": limit})
        )

    def results(
        self,
        crawl_id: str,
        *,
        limit: int | None = None,
    ) -> Iterator[CrawlResult]:
        """Yield the URLs the crawl has kept, in order, each once.

        Follows `next_cursor`, `limit` URLs per request, and returns at
        the first empty page or null cursor. It does not poll: on a
        running crawl it yields only what the crawl has kept so far.
        Call `wait` first to read every result.
        """
        cursor: str | None = None
        while True:
            page = self.get(crawl_id, cursor=cursor, limit=limit)
            yield from page.results
            if not page.results or page.next_cursor is None:
                return
            cursor = page.next_cursor

    def stop(self, crawl_id: str) -> CrawlStop:
        """`POST /crawls/{crawl_id}/stop` — stop a running crawl.

        Idempotent: a crawl that already ended answers as it ended.
        The answer carries no counts; read the final coverage and
        results with `get` once pages already in flight have finished.
        """
        return CrawlStop.model_validate(self._t.request_json("POST", f"/crawls/{crawl_id}/stop"))

    # ===== contents =====

    def content(self, crawl_id: str, content: str | CrawlResult) -> str:
        """`GET /crawls/{crawl_id}/contents/{content_id}` — one kept
        URL's page, as HTML text.

        `content` is a `CrawlResult` whose `content_status` is
        `fetched`, its `content_url`, or its content id (the last
        segment of the `content_url`). Only crawls created with an
        `output_format` have contents; anything else is 404
        `content_not_found`.
        """
        if isinstance(content, CrawlResult):
            if not content.content_url:
                raise ValueError(
                    f"content: {content.url} has no content_url "
                    f"(content_status={content.content_status})"
                )
            content = content.content_url
        content_id = content.rstrip("/").rsplit("/", 1)[-1]
        return self._t.request("GET", f"/crawls/{crawl_id}/contents/{content_id}").text

    def download(self, crawl_id: str) -> CrawlDownload:
        """`GET /crawls/{crawl_id}/download` — open the NDJSON file and
        return it as a `CrawlDownload`: iterate its `lines` for parsed
        `DownloadLine`s (url, content_status, content), and read its
        `status`.

        On a running crawl the file holds what the crawl has kept so
        far, and `status` is `running`; call `wait` first for all of it.
        """
        stack = ExitStack()
        response = stack.enter_context(self._t.stream("GET", f"/crawls/{crawl_id}/download"))
        return CrawlDownload(response, stack.close)

    # ===== waiter =====

    def wait(self, crawl_id: str, *, timeout: float = 600.0) -> Crawl:
        """Poll the crawl until it ends (`completed`, `stopped` or
        `failed`) or `timeout` seconds pass, and return it.

        A `failed` crawl is returned, not raised: read `error` on it.
        On timeout the crawl is returned still `running`; call `wait`
        again to keep waiting, or `stop` to end it.
        """
        deadline = time.monotonic() + timeout
        interval = _POLL_INTERVAL
        while True:
            # limit=1 keeps each poll cheap; results are not returned.
            page = self.get(crawl_id, limit=1)
            crawl = Crawl.model_validate(page.model_dump(exclude={"results", "next_cursor"}))
            remaining = deadline - time.monotonic()
            if crawl.status is not CrawlStatus.RUNNING or remaining <= 0:
                return crawl
            time.sleep(min(interval * random.uniform(0.8, 1.2), remaining))
            interval = min(interval * _POLL_BACKOFF, _MAX_POLL_INTERVAL)


def _crawl_status(response: httpx.Response) -> CrawlStatus | None:
    status = response.headers.get("X-Crawl-Status")
    return CrawlStatus(status) if status else None


__all__ = [
    "DEFAULT_USER_AGENT",
    "CrawlDownload",
    "ZenRowsCrawlClient",
]
