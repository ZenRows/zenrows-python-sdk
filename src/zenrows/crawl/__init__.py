"""Public surface for the ZenRows Crawl API (New).

Crawl is still evolving: new features are coming, limits may be tuned,
and the changelog announces each change.

What's where:
  - `client.ZenRowsCrawlClient` — the typed facade: create, get, list,
    results, content, download, stop, and wait.
  - `models` — hand-written pydantic v2 response models with open enums.
  - `errors.CrawlAPIError` — RFC 9457 problem+json mapping.

`create(output_format="html")` also returns each kept URL's page;
without it a crawl returns URLs only.
"""

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
    "CRAWL_NOT_ENABLED_CODE",
    "ContentStatus",
    "Coverage",
    "Crawl",
    "CrawlAPIError",
    "CrawlDownload",
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
    "ZenRowsCrawlClient",
]
