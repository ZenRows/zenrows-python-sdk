"""Checked by the x-python-typed check under `mypy --strict`; never run."""

from typing import reveal_type

from zenrows import ZenRowsCrawlClient
from zenrows.crawl import (
    Crawl,
    CrawlAPIError,
    CrawlList,
    CrawlResult,
    CrawlStatus,
    CrawlStop,
    CrawlWithResults,
)

client = ZenRowsCrawlClient("k", base_url="https://api.zenrows.com/v1", retries=3, timeout=30)
c: Crawl = client.create(
    "https://example.com/",
    depth=1,
    max_items=5,
    max_pages=10,
    include_patterns=["/p/"],
    output_format="html",
    idempotency_key="k",
)
w: Crawl = client.wait(c.crawl_id, timeout=1)
g: CrawlWithResults = client.get(c.crawl_id, limit=2, cursor=None)
nxt: str | None = g.next_cursor
lst: CrawlList = client.list(limit=2)
for r in client.results(c.crawl_id, limit=2):
    x: CrawlResult = r
    html: str = client.content(c.crawl_id, x)
with client.download(c.crawl_id) as d:
    st: CrawlStatus | None = d.status
    for line in d.lines:
        u: str = line.url
s: CrawlStop = client.stop(c.crawl_id)
try:
    client.get("x")
except CrawlAPIError as e:
    code: str | None = e.code
    status: int = e.status_code
reveal_type(client.create)
