"""ZenRowsCrawlClient — the typed facade over the Crawl API (Beta).

A crawl starts from one URL, follows links up to `depth` hops, and
keeps the URLs that match its patterns, up to `max_items`. It runs as
a job: `create` returns at once with `status=running`, and the URLs
grow as pages finish.

    client = ZenRowsCrawlClient(api_key)
    crawl = client.create("https://example.com/shop/", depth=1,
                          include_patterns=["/product/"])
    client.wait(crawl.crawl_id)
    for result in client.iter_results(crawl.crawl_id):
        print(result.url)

Requests go through the SDK's shared HTTP transport (auth header,
retries, problem+json mapping), which raises `CrawlAPIError`.
"""

import json
import logging
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from zenrows.__version__ import __version__
from zenrows.batch._transport import _RETRYABLE_STATUSES, _Transport
from zenrows.batch._waiters import poll_until
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

DEFAULT_BASE_URL = "https://api.zenrows.com/v1"
DEFAULT_USER_AGENT = f"zenrows-crawl-python/{__version__}"

_log = logging.getLogger("zenrows.crawl.transport")

# 429 `too_many_crawls` is a capacity limit, not a transient failure.
_CREATE_RETRY_STATUSES = _RETRYABLE_STATUSES - {429}


class CrawlDownload:
    """A crawl's NDJSON download, open and ready to read.

    `status` is the crawl's status when the file was read
    (`X-Crawl-Status`): `running` means the file holds only what the
    crawl has kept so far. Iterate it once for the parsed lines; the
    connection closes when the iteration ends. Use it as a context
    manager to close it without reading to the end.
    """

    def __init__(self, response: httpx.Response, close: Callable[[], None]):
        self.status: CrawlStatus | None = _crawl_status(response)
        self._response = response
        self._close = close

    def __iter__(self) -> Iterator[DownloadLine]:
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


@dataclass(slots=True, frozen=True)
class CrawlDownloadFile:
    """A crawl's NDJSON download, saved to `path`. `status` is as on
    `CrawlDownload`."""

    path: Path
    status: CrawlStatus | None


class ZenRowsCrawlClient:
    """Synchronous, typed client for the ZenRows Crawl API (Beta).

    `api_key` is required:

        client = ZenRowsCrawlClient(api_key=os.environ["ZENROWS_API_KEY"])

    Every non-2xx raises `CrawlAPIError`; branch on its `status_code`
    and `code`. An account without Crawl enabled gets 403 `REQS008`
    (`CrawlAPIError.not_enabled`).
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
        """`base_url` defaults to `DEFAULT_BASE_URL`. `retries` bounds
        automatic retries of transient failures (HTTP 429/502/503/504
        and network errors) on idempotent requests — GETs, and a
        `create` that carries an `idempotency_key` (never on 429 for
        `create`). Retries use jittered exponential backoff and honor
        `Retry-After`; set `retries=0` to disable."""
        if not api_key:
            raise ValueError("ZenRowsCrawlClient: api_key is required.")
        self._t = _Transport(
            base_url=base_url or DEFAULT_BASE_URL,
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
          readable with `get_content` / `download`. Omit for URLs only.
        - `idempotency_key`: a retry with the same key and body answers
          with the crawl the first request created.

        Only the arguments you pass are sent. When the account has
        reached its limit of active jobs (3 by default), shared with its
        Batch jobs, `create` raises 429 `too_many_crawls` without
        retrying; retry after the error's `retry_after` seconds.
        """
        if output_format not in (None, "html"):
            raise ValueError(f"create: output_format must be 'html' or None, not {output_format!r}")
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
        every result, prefer `iter_results`."""
        return CrawlWithResults.model_validate(
            self._t.request_json(
                "GET", f"/crawls/{crawl_id}", params={"cursor": cursor, "limit": limit}
            )
        )

    def list_crawls(
        self,
        *,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> CrawlList:
        """`GET /crawls` — one page of the account's crawls, newest
        first (`limit` 1 to 100, server default 20), without results. For
        most uses prefer the auto-paginating `iter_crawls`."""
        return CrawlList.model_validate(
            self._t.request_json("GET", "/crawls", params={"cursor": cursor, "limit": limit})
        )

    def iter_crawls(self, *, page_size: int | None = None) -> Iterator[Crawl]:
        """Auto-paginate `list_crawls`, newest first."""
        cursor: str | None = None
        while True:
            page = self.list_crawls(cursor=cursor, limit=page_size)
            yield from page.crawls
            cursor = page.next_cursor
            if not cursor:
                return

    def iter_results(
        self,
        crawl_id: str,
        *,
        page_size: int | None = None,
    ) -> Iterator[CrawlResult]:
        """Yield the URLs the crawl has kept, in order, each once.

        Follows `next_cursor` and returns at the first empty page or
        null cursor. It does not poll: on a running crawl it yields
        only what the crawl has kept so far. Call `wait` first to read
        every result.
        """
        cursor: str | None = None
        while True:
            page = self.get(crawl_id, cursor=cursor, limit=page_size)
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

    def get_content(self, crawl_id: str, content: str | CrawlResult) -> str:
        """`GET /crawls/{crawl_id}/contents/{content_id}` — one kept
        URL's page, as HTML text.

        `content` is a `CrawlResult` whose `content_status` is
        `fetched`, or its content id (the last segment of its
        `content_url`). Only crawls created with an `output_format`
        have contents; anything else is 404 `content_not_found`.
        """
        if isinstance(content, CrawlResult):
            content_id = content.content_id
            if not content_id:
                raise ValueError(
                    f"get_content: {content.url} has no content_url "
                    f"(content_status={content.content_status})"
                )
        else:
            content_id = content
        return self._t.request("GET", f"/crawls/{crawl_id}/contents/{content_id}").text

    def iter_download(self, crawl_id: str) -> CrawlDownload:
        """`GET /crawls/{crawl_id}/download` — open the NDJSON file and
        return it as a `CrawlDownload`: iterate it for parsed
        `DownloadLine`s (url, content_status, content), and read its
        `status`.

        On a running crawl the file holds what the crawl has kept so
        far, and `status` is `running`; call `wait` first for all of it.
        """
        stack = ExitStack()
        response = stack.enter_context(self._t.stream("GET", f"/crawls/{crawl_id}/download"))
        return CrawlDownload(response, stack.close)

    def download(
        self,
        crawl_id: str,
        target_path: str | Path,
        *,
        chunk_size: int = 64 * 1024,
    ) -> CrawlDownloadFile:
        """`GET /crawls/{crawl_id}/download` — stream the NDJSON file
        (one JSON object per result) to `target_path`. Returns its
        `path` and the crawl's `status` when the file was read.

        On a running crawl the file holds what the crawl has kept so
        far, and `status` is `running`; call `wait` first for all of it.
        """
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with (
            self._t.stream("GET", f"/crawls/{crawl_id}/download") as response,
            target.open("wb") as f,
        ):
            for chunk in response.iter_bytes(chunk_size):
                f.write(chunk)
        return CrawlDownloadFile(path=target, status=_crawl_status(response))

    # ===== waiter =====

    def wait(
        self,
        crawl_id: str,
        *,
        timeout: float = 600.0,
        poll_interval: float = 2.0,
        max_poll_interval: float = 15.0,
    ) -> Crawl:
        """Block until the crawl ends (`completed`, `stopped` or
        `failed`) and return it, polling with jittered exponential
        backoff from `poll_interval` up to `max_poll_interval` seconds.

        A `failed` crawl is returned, not raised: read `error` on it.
        Raises `WaiterTimeout` after `timeout` seconds; the crawl keeps
        running (call `stop` to end it).
        """

        def fetch() -> Crawl:
            # limit=1 keeps each poll cheap; results are not returned.
            page = self.get(crawl_id, limit=1)
            return Crawl.model_validate(page.model_dump(exclude={"results", "next_cursor"}))

        return poll_until(
            fetch=fetch,
            is_done=lambda crawl: crawl.is_terminal,
            timeout=timeout,
            initial_interval=poll_interval,
            max_interval=max_poll_interval,
        )


def _crawl_status(response: httpx.Response) -> CrawlStatus | None:
    status = response.headers.get("X-Crawl-Status")
    return CrawlStatus(status) if status else None


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_USER_AGENT",
    "CrawlDownload",
    "CrawlDownloadFile",
    "ZenRowsCrawlClient",
]
