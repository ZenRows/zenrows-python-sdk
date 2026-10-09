"""11: Crawl a site → wait → read URLs and pages.

Demonstrates the Crawl client (`ZenRowsCrawlClient`):
  - `create(url, depth=..., include_patterns=..., output_format="html")`
    starts a crawl and returns at once, `running`.
  - `wait(crawl_id)` polls until it ends, or 600 s pass, and returns
    the `Crawl` (still `running` on timeout; `error` set when `failed`).
  - `results(crawl_id)` pages through the kept URLs.
  - `content(crawl_id, result)` reads one kept URL's HTML.
  - `download(crawl_id)` reads every result as NDJSON, with the crawl's
    status; the example saves the lines to a file.

Run with:
    export ZENROWS_API_KEY=zr_...
    python examples/11_crawl.py --url https://example.com/products/
"""

import argparse
import json
import os

from zenrows import ZenRowsCrawlClient
from zenrows.crawl import CRAWL_NOT_ENABLED_CODE, CrawlAPIError, CrawlStatus


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="the page to start from")
    parser.add_argument("--depth", type=int, default=1)
    parser.add_argument("--max-items", type=int, default=5)
    parser.add_argument(
        "--include", action="append", help="keep URLs containing this (default /product/)"
    )
    parser.add_argument("--out", default="crawl.jsonl")
    args = parser.parse_args()

    client = ZenRowsCrawlClient(api_key=os.environ["ZENROWS_API_KEY"])

    try:
        crawl = client.create(
            args.url,
            depth=args.depth,
            max_items=args.max_items,
            include_patterns=args.include or ["/product/"],
            output_format="html",
        )
    except CrawlAPIError as exc:
        if exc.code == CRAWL_NOT_ENABLED_CODE:
            raise SystemExit("Crawl is not enabled for this account.") from exc
        raise
    print(f"started {crawl.crawl_id}")

    crawl = client.wait(crawl.crawl_id)
    cov = crawl.coverage
    print(
        f"{crawl.crawl_id} {crawl.status.value}: {cov.items_found} URLs kept, "
        f"{cov.pages_fetched} pages fetched ({cov.pages_failed} failed)"
    )
    if crawl.status is CrawlStatus.FAILED and crawl.error:
        raise SystemExit(f"crawl failed: {crawl.error.code.value}: {crawl.error.detail}")
    if crawl.status is CrawlStatus.RUNNING:
        print("still running: the results below are what it has kept so far")

    for result in client.results(crawl.crawl_id):
        status = result.content_status.value if result.content_status else "-"
        print(f"  [{status}] {result.url}")
        if result.content_url:
            html = client.content(crawl.crawl_id, result)
            print(f"           {len(html)} chars of HTML")

    with client.download(crawl.crawl_id) as download, open(args.out, "w") as f:
        for line in download.lines:
            f.write(json.dumps(line.model_dump(mode="json", exclude_none=True)) + "\n")
    print(f"wrote {args.out} (crawl {download.status.value if download.status else '-'})")


if __name__ == "__main__":
    main()
