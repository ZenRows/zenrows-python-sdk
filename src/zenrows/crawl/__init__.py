"""Public surface for the ZenRows Crawl API (New).

Crawl is still evolving: new features are coming, limits may be tuned,
and the changelog announces each change.

What's where:
  - `client.ZenRowsCrawlClient` — the typed facade: create, get, list,
    results, content, download, stop, and wait.
  - `models` — hand-written pydantic v2 response models with open enums.
  - `errors.CrawlAPIError` — RFC 9457 problem+json mapping.

`create(output_format="html")` or `create(output_format="markdown")` also
returns each kept URL's page; without it a crawl returns URLs only.

`create(webhook={"url": ...})` sends a signed event when the crawl starts
and when it ends; check each one with `verify_webhook_signature` and
parse it as a `CrawlWebhookEvent`.
"""

from zenrows.batch._webhooks import (
    DEFAULT_WEBHOOK_TOLERANCE_SECONDS,
    WEBHOOK_EVENT_ID_HEADER,
    WEBHOOK_SIGNATURE_HEADER,
    verify_webhook_signature,
)
from zenrows.crawl.client import (
    CrawlDownload,
    ZenRowsCrawlClient,
)
from zenrows.crawl.errors import CRAWL_NOT_ENABLED_CODE, CrawlAPIError
from zenrows.crawl.models import (
    ContentStatus,
    Coverage,
    Crawl,
    CrawlErrorCode,
    CrawlEventName,
    CrawlEventResource,
    CrawlEventType,
    CrawlList,
    CrawlResult,
    CrawlStatus,
    CrawlStop,
    CrawlWebhook,
    CrawlWebhookDict,
    CrawlWebhookEvent,
    CrawlWithResults,
    DiscoveryStrategy,
    DownloadLine,
    OutputFormat,
    RunError,
    StopReason,
)

__all__ = [
    "CRAWL_NOT_ENABLED_CODE",
    "DEFAULT_WEBHOOK_TOLERANCE_SECONDS",
    "WEBHOOK_EVENT_ID_HEADER",
    "WEBHOOK_SIGNATURE_HEADER",
    "ContentStatus",
    "Coverage",
    "Crawl",
    "CrawlAPIError",
    "CrawlDownload",
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
    "ZenRowsCrawlClient",
    "verify_webhook_signature",
]
