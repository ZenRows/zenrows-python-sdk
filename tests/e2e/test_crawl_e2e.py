"""End-to-end test of ZenRowsCrawlClient against a live Crawl API.

Opt-in: runs only with `make test-e2e` (pytest `-m e2e`), and only when
`ZENROWS_API_KEY` (a key with Crawl access), `ZENROWS_CRAWL_BASE_URL`
(the API base, e.g. https://api.zenrows.com/v1) and
`ZENROWS_E2E_CRAWL_URL` (the page to crawl from) are set. Otherwise it is
skipped, so `make test` stays offline. When `ZENROWS_E2E_CRAWL_INCLUDE`
is set, the crawl keeps only URLs containing it, and the test checks
that every result does. It starts one small crawl (a few credits) and
reads it back every way the client can.
"""

import json
import os
import time

import pytest

from zenrows.crawl import CrawlAPIError, CrawlStatus, ZenRowsCrawlClient

_REQUIRED = ("ZENROWS_API_KEY", "ZENROWS_CRAWL_BASE_URL", "ZENROWS_E2E_CRAWL_URL")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not all(os.environ.get(v) for v in _REQUIRED),
        reason="set " + ", ".join(_REQUIRED) + " to run the Crawl e2e test",
    ),
]

START_URL = os.environ.get("ZENROWS_E2E_CRAWL_URL", "")
INCLUDE = os.environ.get("ZENROWS_E2E_CRAWL_INCLUDE") or None
# An account has a limit of active jobs (3 by default, shared with its
# Batch jobs), so a create can meet 429 too_many_crawls while others finish.
SLOT_WAIT_SECONDS = 300


@pytest.fixture(scope="module")
def client():
    with ZenRowsCrawlClient(
        api_key=os.environ["ZENROWS_API_KEY"], base_url=os.environ["ZENROWS_CRAWL_BASE_URL"]
    ) as c:
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
                include_patterns=[INCLUDE] if INCLUDE else None,
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
    if INCLUDE:
        assert all(INCLUDE in r.url for r in results), [r.url for r in results]

    fetched = [r for r in results if r.content_id]
    assert fetched, "no result has a fetched page"
    html = client.get_content(crawl.crawl_id, fetched[0])
    print(f"content of {fetched[0].url}: {len(html)} chars")
    assert "<html" in html.lower()

    download = client.iter_download(crawl.crawl_id)
    assert download.status is CrawlStatus.COMPLETED
    lines = list(download)
    print(f"download lines: {len(lines)}")
    assert len(lines) == len(results)
    saved = client.download(crawl.crawl_id, tmp_path / f"{crawl.crawl_id}.jsonl")
    assert saved.status is CrawlStatus.COMPLETED
    assert [json.loads(line)["url"] for line in saved.path.read_text().splitlines()] == [
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
