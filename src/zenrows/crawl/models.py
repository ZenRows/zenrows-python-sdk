"""Pydantic v2 models for the Crawl API's responses, and the
`CrawlWebhookDict` that `create` takes.

Hand-written: the surface is a handful of small schemas.

Forward compatible: unknown response fields
are ignored (pydantic's default), and every enum is open: a value the
server adds later parses as an `UNKNOWN` member that keeps the raw
value (see `zenrows.batch._open_enum`).
"""

import sys
from datetime import datetime
from enum import Enum
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

if sys.version_info >= (3, 11):
    from typing import NotRequired
else:
    # `typing.NotRequired` only exists on 3.11+; the SDK floors at 3.10.
    from typing_extensions import NotRequired

from zenrows.batch._open_enum import open_enum_missing


class CrawlStatus(Enum):
    """Where a crawl stands. `running` until it ends; the other values
    are terminal. Clients must accept values not listed here."""

    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"

    _missing_ = classmethod(open_enum_missing)


class StopReason(Enum):
    """What ended the crawl before nothing was left to open: a limit
    (`max_items` / `max_pages`, with `completed`) or the caller (`user`,
    with `stopped`). Absent on a crawl that stopped because the account
    ran out of credits or the API key hit a credit cap: that one carries
    `error` instead."""

    MAX_ITEMS = "max_items"
    MAX_PAGES = "max_pages"
    USER = "user"

    _missing_ = classmethod(open_enum_missing)


class ContentStatus(Enum):
    """Where a kept URL's page stands, when the crawl has an
    `output_format`."""

    PENDING = "pending"
    FETCHED = "fetched"
    FAILED = "failed"

    _missing_ = classmethod(open_enum_missing)


class OutputFormat(Enum):
    """The format the crawl returns each kept URL's page in."""

    HTML = "html"
    MARKDOWN = "markdown"

    _missing_ = classmethod(open_enum_missing)


class DiscoveryStrategy(Enum):
    """How the crawl finds the URLs it follows on each page it walks.

    `links` follows the links in the page's HTML. The server may run a
    crawl with a strategy this version does not list, and it parses as
    an `UNKNOWN` member keeping the raw value."""

    LINKS = "links"

    _missing_ = classmethod(open_enum_missing)


class CrawlErrorCode(Enum):
    """Why a crawl failed, or why it stopped on credits (`Crawl.error.code`).

    `insufficient_credits` and `api_key_cap_reached` come with
    `status` `stopped`: the pages fetched before that stay readable.
    The other codes come with `failed`. `seed_unreachable`: the start
    page could not be reached, was blocked, or came back empty, with
    no visible text and no link. `no_items_found`: the crawl read the
    start page but found no data it could use, so it had nothing to
    follow; start from a listing or product page. A crawl that returns
    nothing for any other reason is not a failure: it ends `completed`
    with no results."""

    INSUFFICIENT_CREDITS = "insufficient_credits"
    API_KEY_CAP_REACHED = "api_key_cap_reached"
    SEED_UNREACHABLE = "seed_unreachable"
    DOMAIN_NOT_ALLOWED = "domain_not_allowed"
    NO_ITEMS_FOUND = "no_items_found"
    INTERNAL_ERROR = "internal_error"

    _missing_ = classmethod(open_enum_missing)


class CrawlEventType(Enum):
    """A crawl event a webhook can be sent. `crawl.started` once the
    crawl is created and runs; `crawl.completed`, `crawl.stopped` or
    `crawl.failed` once it reaches that status, exactly one of them per
    crawl. Clients must accept values not listed here."""

    STARTED = "crawl.started"
    COMPLETED = "crawl.completed"
    STOPPED = "crawl.stopped"
    FAILED = "crawl.failed"

    _missing_ = classmethod(open_enum_missing)


CrawlEventName = Literal["crawl.started", "crawl.completed", "crawl.stopped", "crawl.failed"]
"""The event names `CrawlWebhookDict.events` takes."""


class CrawlWebhookDict(TypedDict):
    """The `webhook` to pass to `create`: where the crawl's events are
    sent, and which ones. It is fixed at create; there is no update.

    - `url` (required): the absolute `https` URL each event is POSTed
      to. Its host must resolve.
    - `events`: the events to send, each named once. Leave it out for
      all four (`crawl.started`, `crawl.completed`, `crawl.stopped`,
      `crawl.failed`).
    - `metadata`: sent back in every event, to match it to your own
      records. Up to 20 keys of 1 to 64 characters; each value any
      JSON value of at most 1,024 bytes as compact JSON.

    Every event is signed with the account's webhook signing key, so
    the account needs one (Settings → API Keys in the dashboard)."""

    url: str
    events: NotRequired[list[CrawlEventName]]
    metadata: NotRequired[dict[str, Any]]


class CrawlWebhook(BaseModel):
    """The webhook a crawl sends its events to, as it runs with it."""

    url: str
    events: list[CrawlEventType]
    """The events the crawl sends: all four when the create left
    `events` out."""
    metadata: dict[str, Any] | None = None
    """Sent back in every event. None when the crawl has none."""


class CrawlEventResource(BaseModel):
    """The crawl a webhook event is about."""

    type: str
    """`crawl`."""
    id: str
    """The crawl's `crawl_id`."""
    url: str | None = None
    """The crawl on the API. Read it with `get` for its status,
    coverage and results."""


class CrawlWebhookEvent(BaseModel):
    """The JSON body POSTed to a crawl's webhook for each event.

    Every delivery of one event carries the same `event_id` (also in
    the `X-ZenRows-Event-Id` header): drop an event you have already
    handled. Events can arrive out of order; order them by
    `occurred_at`, or read the crawl. Verify the `X-Signature` header
    with `verify_webhook_signature` before parsing the body."""

    schema_: str = Field(alias="schema")
    """`zenrows.webhook/v1`."""
    event_id: str
    """Stable across retries, e.g. `ev_01M5AKXYZ_completed`."""
    event_type: CrawlEventType
    occurred_at: datetime
    """When the crawl was created, for `crawl.started`; when it
    finished, for the others."""
    resource: CrawlEventResource
    metadata: dict[str, Any] | None = None
    """The webhook's `metadata`. None when the crawl has none."""


class RunError(BaseModel):
    """Why a crawl failed, or why it stopped because the account ran out
    of credits or the API key hit a credit cap. Present when `status` is
    `failed`, and when it is `stopped` with `code` `insufficient_credits`
    or `api_key_cap_reached`; part of the crawl, not an error response."""

    code: CrawlErrorCode
    detail: str


class Coverage(BaseModel):
    """How far the crawl has got."""

    pages_fetched: int
    """Pages fetched so far, successful or not."""
    pages_failed: int
    """Fetched pages whose fetch failed (not charged)."""
    items_found: int
    """URLs kept so far, bounded by `max_items`."""


class Crawl(BaseModel):
    """One crawl, with the parameters it runs with."""

    crawl_id: str
    status: CrawlStatus
    url: str
    """The page the crawl started from."""
    depth: int | None = None
    """Link hops the crawl follows from `url`. Absent when the crawl was
    created without one: it has no depth limit."""
    max_items: int
    max_pages: int
    discovery: list[DiscoveryStrategy] = Field(default_factory=lambda: [DiscoveryStrategy.LINKS])
    """The strategies the crawl finds URLs with. `["links"]` when the
    create left it out, which is also what this reads when the server
    does not send the field at all."""
    coverage: Coverage
    created_at: datetime
    stop_reason: StopReason | None = None
    error: RunError | None = None
    include_patterns: list[str] | None = None
    exclude_patterns: list[str] | None = None
    output_format: OutputFormat | None = None
    webhook: CrawlWebhook | None = None
    """Where the crawl sends its events. None for a crawl created
    without a webhook."""
    duplicates_removed: int | None = None
    """Links met again and dropped as already seen."""
    finished_at: datetime | None = None
    """When the crawl reached a terminal status. Absent while it runs."""


class CrawlResult(BaseModel):
    """One URL the crawl kept."""

    url: str
    content_status: ContentStatus | None = None
    """Present only when the crawl has an `output_format`."""
    content_url: str | None = None
    """The page's path (e.g. `/v1/crawls/c_x/contents/ct_y`), present
    once `content_status` is `fetched`. Pass the result itself to
    `ZenRowsCrawlClient.content`."""


class CrawlWithResults(Crawl):
    """A crawl and one page of the URLs it has kept (`GET /crawls/{id}`).

    `next_cursor` is never null while the crawl runs: polling with it
    returns only URLs kept since. It is null once the crawl has ended
    and this page holds its last URLs."""

    results: list[CrawlResult]
    next_cursor: str | None = None


class CrawlList(BaseModel):
    """One page of the account's crawls, newest first. `next_cursor` is
    absent on the last page."""

    crawls: list[Crawl]
    next_cursor: str | None = None


class CrawlStop(BaseModel):
    """What `stop` answers: where the crawl stands, without counts. Read
    the coverage and results with `get`."""

    crawl_id: str
    status: CrawlStatus
    stop_reason: StopReason | None = None
    error: RunError | None = None
    finished_at: datetime | None = None


class DownloadLine(BaseModel):
    """One line of a crawl's NDJSON download."""

    url: str
    content_status: ContentStatus | None = None
    content: str | dict[str, Any] | None = None
    """The page, present when `content_status` is `fetched`: HTML text,
    or an object for a crawl whose output format returns parsed data."""


__all__ = [
    "ContentStatus",
    "Coverage",
    "Crawl",
    "CrawlErrorCode",
    "CrawlEventName",
    "CrawlEventResource",
    "CrawlEventType",
    "CrawlList",
    "CrawlResult",
    "CrawlStatus",
    "CrawlStop",
    "CrawlWebhook",
    "CrawlWebhookDict",
    "CrawlWebhookEvent",
    "CrawlWithResults",
    "DiscoveryStrategy",
    "DownloadLine",
    "OutputFormat",
    "RunError",
    "StopReason",
]
