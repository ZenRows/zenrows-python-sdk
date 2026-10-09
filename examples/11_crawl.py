"""11: Crawl a site → wait → read URLs and pages. Crawl is in Beta.

Demonstrates the Crawl client (`ZenRowsCrawlClient`):
  - `create(url, depth=..., include_patterns=..., output_format="html")`
    starts a crawl and returns at once, `running`.
  - `wait(crawl_id)` blocks until it ends and returns the final `Crawl`
    (a `failed` crawl is returned, with `error` set).
  - `iter_results(crawl_id)` pages through the kept URLs.
  - `get_content(crawl_id, result)` reads one kept URL's HTML.
  - `download(crawl_id, path)` saves every result as NDJSON and
    returns the path and the crawl's status.

Run with:
    export ZENROWS_API_KEY=zr_...
    python examples/11_crawl.py --url https://example.com/products/
"""

import argparse
import os

from zenrows import ZenRowsCrawlClient
from zenrows.crawl import CrawlAPIError, CrawlStatus


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
        if exc.not_enabled:
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

    for result in client.iter_results(crawl.crawl_id):
        status = result.content_status.value if result.content_status else "-"
        print(f"  [{status}] {result.url}")
        if result.content_id:
            html = client.get_content(crawl.crawl_id, result)
            print(f"           {len(html)} chars of HTML")

    saved = client.download(crawl.crawl_id, args.out)
    print(f"wrote {saved.path}")


if __name__ == "__main__":
    main()
