"""End-to-end test of ZenRowsCrawlClient against a live Crawl API.

Opt-in: runs only with `make test-e2e` (pytest `-m e2e`), and only when
both `ZENROWS_API_KEY` (a key with Crawl access) and
`ZENROWS_CRAWL_BASE_URL` (the API base, e.g. https://api.zenrows.com/v1)
are set. Otherwise it is skipped, so `make test` stays offline. It
starts one small crawl (a few credits) and reads it back every way the
client can.
"""

import json
import os
import time

import pytest

from zenrows.crawl import CrawlAPIError, CrawlStatus, ZenRowsCrawlClient

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not (os.environ.get("ZENROWS_API_KEY") and os.environ.get("ZENROWS_CRAWL_BASE_URL")),
        reason="set ZENROWS_API_KEY and ZENROWS_CRAWL_BASE_URL to run the Crawl e2e test",
    ),
]

START_URL = "https://www.scrapingcourse.com/ecommerce/"
# An account can run only a few crawls at once, so a create can meet
# 429 too_many_crawls while other runs finish.
SLOT_WAIT_SECONDS = 300


@pytest.fixture(scope="module")
def client():
    with ZenRowsCrawlClient(api_key=os.environ["ZENROWS_API_KEY"]) as c:
        yield c


def create_when_a_slot_frees(client: ZenRowsCrawlClient):
    deadline = time.monotonic() + SLOT_WAIT_SECONDS
    while True:
        try:
            return client.create(
                START_URL,
                depth=1,
                max_items=3,
                max_pages=5,
                include_patterns=["/product/"],
                output_format="html",
            )
        except CrawlAPIError as err:
            if err.code != "too_many_crawls" or time.monotonic() >= deadline:
                raise
            wait = err.retry_after or 30.0
            print(f"too_many_crawls: retrying create in {wait:.0f}s")
            time.sleep(wait)


def test_crawl_end_to_end(client: ZenRowsCrawlClient, tmp_path):
    created = create_when_a_slot_frees(client)
    print(f"created {created.crawl_id} status={created.status.value}")
    assert created.crawl_id

    crawl = client.wait(created.crawl_id, timeout=600)
    print(
        f"finished {crawl.crawl_id} status={crawl.status.value} "
        f"stop_reason={crawl.stop_reason and crawl.stop_reason.value} "
        f"pages_fetched={crawl.coverage.pages_fetched} items_found={crawl.coverage.items_found}"
    )
    assert crawl.status is CrawlStatus.COMPLETED, crawl.error

    results = list(client.iter_results(crawl.crawl_id))
    print(f"results: {len(results)}")
    assert results
    assert all("/product/" in r.url for r in results), [r.url for r in results]

    fetched = [r for r in results if r.content_id]
    assert fetched, "no result has a fetched page"
    html = client.get_content(crawl.crawl_id, fetched[0])
    print(f"content of {fetched[0].url}: {len(html)} chars")
    assert "<html" in html.lower()

    lines = list(client.iter_download(crawl.crawl_id))
    print(f"download lines: {len(lines)}")
    assert len(lines) == len(results)
    target = client.download(crawl.crawl_id, tmp_path / f"{crawl.crawl_id}.jsonl")
    assert [json.loads(line)["url"] for line in target.read_text().splitlines()] == [
        line.url for line in lines
    ]

    listed = next((c for c in client.iter_crawls() if c.crawl_id == crawl.crawl_id), None)
    print(f"listed: {listed is not None}")
    assert listed is not None

    stopped = client.stop(crawl.crawl_id)
    print(f"stop on ended crawl: status={stopped.status.value}")
    assert stopped.status is crawl.status

    with pytest.raises(CrawlAPIError) as exc_info:
        client.get("c_does_not_exist")
    print(f"missing crawl: {exc_info.value.status_code} {exc_info.value.code}")
    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "crawl_not_found"
