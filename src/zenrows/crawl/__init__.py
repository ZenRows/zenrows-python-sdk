"""Public surface for the ZenRows Crawl API (Beta).

Crawl is in Beta: its surface may still change.

What's where:
  - `client.ZenRowsCrawlClient` — the typed facade: create, get, list,
    stop, contents, download, and a `wait` helper.
  - `models` — hand-written pydantic v2 response models with open enums.
  - `errors.CrawlAPIError` — RFC 9457 problem+json mapping.

`create(output_format="html")` also returns each kept URL's page;
without it a crawl returns URLs only.
"""

from zenrows.batch._waiters import WaiterTimeout
from zenrows.crawl.client import (
    DEFAULT_BASE_URL,
    CrawlDownload,
    CrawlDownloadFile,
    ZenRowsCrawlClient,
)
from zenrows.crawl.errors import CRAWL_NOT_ENABLED, CrawlAPIError
from zenrows.crawl.models import (
    ContentStatus,
    Coverage,
    Crawl,
    CrawlErrorCode,
    CrawlList,
    CrawlResult,
    CrawlStatus,
    CrawlStop,
    CrawlWithResults,
    DownloadLine,
    OutputFormat,
    RunError,
    StopReason,
)

__all__ = [
    "CRAWL_NOT_ENABLED",
    "DEFAULT_BASE_URL",
    "ContentStatus",
    "Coverage",
    "Crawl",
    "CrawlAPIError",
    "CrawlDownload",
    "CrawlDownloadFile",
    "CrawlErrorCode",
    "CrawlList",
    "CrawlResult",
    "CrawlStatus",
    "CrawlStop",
    "CrawlWithResults",
    "DownloadLine",
    "OutputFormat",
    "RunError",
    "StopReason",
    "WaiterTimeout",
    "ZenRowsCrawlClient",
]
