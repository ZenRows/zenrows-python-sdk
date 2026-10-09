"""Unit tests for ZenRowsCrawlClient.

Covers the wire contract with respx: request shapes (path, method, key
header, bodies carrying only what the caller set), response parsing
with open enums, problem+json error mapping, the results scanner's
stop rule, downloads with their status, retries, and the waiter.
"""

import json

import pytest
import respx
from httpx import Response

from zenrows import ZenRowsCrawlClient
from zenrows.batch._open_enum import is_unknown
from zenrows.crawl import (
    ContentStatus,
    CrawlAPIError,
    CrawlDownloadFile,
    CrawlErrorCode,
    CrawlResult,
    CrawlStatus,
    OutputFormat,
    StopReason,
    WaiterTimeout,
)

BASE_URL = "http://localhost:9100/v1"
API_KEY = "test-key"


def crawl_body(**overrides):
    body = {
        "crawl_id": "c_1",
        "status": "running",
        "url": "https://example.com/",
        "depth": 1,
        "max_items": 10,
        "max_pages": 10,
        "coverage": {"pages_fetched": 0, "pages_failed": 0, "items_found": 0},
        "created_at": "2026-10-08T09:00:00Z",
    }
    body.update(overrides)
    return body


def problem(status: int, code: str, **extra):
    return Response(
        status,
        headers={"Content-Type": "application/problem+json", **extra.pop("headers", {})},
        json={
            "code": code,
            "title": code.replace("_", " ").capitalize(),
            "detail": f"detail for {code}",
            "status": status,
            "instance": "urn:zenrows:request:abc",
            **extra,
        },
    )


@pytest.fixture
def client() -> ZenRowsCrawlClient:
    return ZenRowsCrawlClient(api_key=API_KEY, base_url=BASE_URL, retries=0)


@pytest.fixture
def no_sleep(monkeypatch):
    """Record + swallow sleeps (waiter and retry backoff both call
    `time.sleep`) so tests run instantly."""
    slept: list[float] = []
    monkeypatch.setattr("time.sleep", slept.append)
    return slept


# ----- construction -----


def test_missing_api_key_raises():
    with pytest.raises(ValueError):
        ZenRowsCrawlClient(api_key="")


def test_base_url_is_a_constructor_option_only(monkeypatch):
    monkeypatch.setenv("ZENROWS_CRAWL_BASE_URL", "http://env-set/v1")
    assert ZenRowsCrawlClient(api_key=API_KEY).base_url.rstrip("/") == "https://api.zenrows.com/v1"
    assert ZenRowsCrawlClient(api_key=API_KEY, base_url=BASE_URL).base_url.rstrip("/") == BASE_URL


# ----- create -----


@respx.mock
def test_create_sends_only_required_fields(client: ZenRowsCrawlClient):
    route = respx.post(f"{BASE_URL}/crawls").mock(
        return_value=Response(202, headers={"Location": "/v1/crawls/c_1"}, json=crawl_body())
    )

    crawl = client.create("https://example.com/", depth=1)

    assert crawl.crawl_id == "c_1"
    assert crawl.status is CrawlStatus.RUNNING
    assert not crawl.is_terminal
    sent = route.calls.last.request
    assert sent.headers["X-API-Key"] == API_KEY
    assert sent.headers["Content-Type"] == "application/json"
    assert "Idempotency-Key" not in sent.headers
    assert json.loads(sent.content) == {"url": "https://example.com/", "depth": 1}


@respx.mock
def test_create_sends_every_option_and_idempotency_key(client: ZenRowsCrawlClient):
    route = respx.post(f"{BASE_URL}/crawls").mock(
        return_value=Response(202, json=crawl_body(output_format="html"))
    )

    crawl = client.create(
        "https://example.com/",
        depth=2,
        max_items=3,
        max_pages=5,
        include_patterns=["/product/"],
        exclude_patterns=["/cart"],
        output_format="html",
        idempotency_key="idem-1",
    )

    assert crawl.output_format is OutputFormat.HTML
    sent = route.calls.last.request
    assert sent.headers["Idempotency-Key"] == "idem-1"
    body = json.loads(sent.content)
    assert body == {
        "url": "https://example.com/",
        "depth": 2,
        "max_items": 3,
        "max_pages": 5,
        "include_patterns": ["/product/"],
        "exclude_patterns": ["/cart"],
        "output_format": "html",
    }
    assert "discovery" not in body


def test_create_output_format_accepts_only_html(client: ZenRowsCrawlClient):
    with pytest.raises(ValueError, match="output_format"):
        client.create("https://example.com/", depth=1, output_format="json")  # type: ignore[arg-type]


# ----- get / list / stop -----


@respx.mock
def test_get_parses_results_and_open_enums(client: ZenRowsCrawlClient):
    route = respx.get(f"{BASE_URL}/crawls/c_1").mock(
        return_value=Response(
            200,
            json=crawl_body(
                status="completed",
                stop_reason="max_items",
                output_format="xml",
                finished_at="2026-10-08T09:02:00Z",
                some_new_field=True,
                results=[
                    {
                        "url": "https://example.com/product/a",
                        "content_status": "fetched",
                        "content_url": "/v1/crawls/c_1/contents/ct_a",
                    },
                    {"url": "https://example.com/product/b", "content_status": "archived"},
                ],
                next_cursor=None,
            ),
        )
    )

    page = client.get("c_1", cursor="cur_1", limit=50)

    assert route.calls.last.request.url.params["cursor"] == "cur_1"
    assert route.calls.last.request.url.params["limit"] == "50"
    assert page.status is CrawlStatus.COMPLETED
    assert page.is_terminal
    assert page.stop_reason is StopReason.MAX_ITEMS
    assert is_unknown(page.output_format)  # type: ignore[arg-type]
    assert page.next_cursor is None
    assert page.results[0].content_status is ContentStatus.FETCHED
    assert page.results[0].content_id == "ct_a"
    unknown = page.results[1].content_status
    assert unknown is not None and is_unknown(unknown) and unknown.value == "archived"


@respx.mock
def test_get_parses_failed_crawl_error(client: ZenRowsCrawlClient):
    respx.get(f"{BASE_URL}/crawls/c_1").mock(
        return_value=Response(
            200,
            json=crawl_body(
                status="failed",
                error={"code": "seed_unreachable", "detail": "The site may be blocking it."},
                results=[],
                next_cursor=None,
            ),
        )
    )
    page = client.get("c_1")
    assert page.status is CrawlStatus.FAILED
    assert page.error is not None and page.error.code is CrawlErrorCode.SEED_UNREACHABLE


@respx.mock
def test_get_omits_unset_query_params(client: ZenRowsCrawlClient):
    route = respx.get(f"{BASE_URL}/crawls/c_1").mock(
        return_value=Response(200, json=crawl_body(results=[], next_cursor="cur_0"))
    )
    client.get("c_1")
    assert dict(route.calls.last.request.url.params) == {}


@respx.mock
def test_iter_crawls_paginates_until_cursor_absent(client: ZenRowsCrawlClient):
    route = respx.get(f"{BASE_URL}/crawls").mock(
        side_effect=[
            Response(200, json={"crawls": [crawl_body(crawl_id="c_2")], "next_cursor": "n1"}),
            Response(200, json={"crawls": [crawl_body(crawl_id="c_1")]}),
        ]
    )

    ids = [c.crawl_id for c in client.iter_crawls(page_size=1)]

    assert ids == ["c_2", "c_1"]
    assert route.calls[0].request.url.params["limit"] == "1"
    assert "cursor" not in route.calls[0].request.url.params
    assert route.calls[1].request.url.params["cursor"] == "n1"


@respx.mock
def test_stop_posts_without_body(client: ZenRowsCrawlClient):
    route = respx.post(f"{BASE_URL}/crawls/c_1/stop").mock(
        return_value=Response(
            200,
            json={
                "crawl_id": "c_1",
                "status": "stopped",
                "stop_reason": "user",
                "finished_at": "2026-10-08T09:01:00Z",
            },
        )
    )

    stopped = client.stop("c_1")

    assert stopped.status is CrawlStatus.STOPPED
    assert stopped.stop_reason is StopReason.USER
    assert route.calls.last.request.content == b""


# ----- results scanner -----


@respx.mock
def test_iter_results_stops_on_null_cursor(client: ZenRowsCrawlClient):
    route = respx.get(f"{BASE_URL}/crawls/c_1").mock(
        side_effect=[
            Response(200, json=crawl_body(results=[{"url": "https://a"}], next_cursor="k1")),
            Response(
                200,
                json=crawl_body(
                    status="completed", results=[{"url": "https://b"}], next_cursor=None
                ),
            ),
        ]
    )

    urls = [r.url for r in client.iter_results("c_1")]

    assert urls == ["https://a", "https://b"]
    assert route.call_count == 2
    assert "cursor" not in route.calls[0].request.url.params
    assert route.calls[1].request.url.params["cursor"] == "k1"


@respx.mock
def test_iter_results_returns_at_first_empty_page_of_running_crawl(
    client: ZenRowsCrawlClient, no_sleep
):
    route = respx.get(f"{BASE_URL}/crawls/c_1").mock(
        side_effect=[
            Response(200, json=crawl_body(results=[{"url": "https://a"}], next_cursor="k1")),
            # Caught up with a running crawl: no results, same cursor back.
            Response(200, json=crawl_body(results=[], next_cursor="k1")),
        ]
    )

    urls = [r.url for r in client.iter_results("c_1")]

    assert urls == ["https://a"]
    assert route.call_count == 2
    assert no_sleep == []


# ----- contents / download -----


@respx.mock
def test_get_content_accepts_result_or_id(client: ZenRowsCrawlClient):
    route = respx.get(f"{BASE_URL}/crawls/c_1/contents/ct_a").mock(
        return_value=Response(
            200, headers={"Content-Type": "text/html"}, text="<html>product</html>"
        )
    )
    result = CrawlResult(
        url="https://a", content_status="fetched", content_url="/v1/crawls/c_1/contents/ct_a"
    )  # type: ignore[arg-type]

    assert client.get_content("c_1", result) == "<html>product</html>"
    assert client.get_content("c_1", "ct_a") == "<html>product</html>"
    assert route.call_count == 2


def test_get_content_without_content_url_raises(client: ZenRowsCrawlClient):
    with pytest.raises(ValueError, match="content_url"):
        client.get_content("c_1", CrawlResult(url="https://a", content_status="pending"))  # type: ignore[arg-type]


NDJSON = (
    b'{"url":"https://a","content_status":"fetched","content":"<html>a</html>"}\n'
    b'{"url":"https://b","content_status":"failed"}\n'
    b'{"url":"https://c","content_status":"fetched","content":{"title":"C"}}\n'
)


@respx.mock
def test_iter_download_parses_lines_and_status(client: ZenRowsCrawlClient):
    respx.get(f"{BASE_URL}/crawls/c_1/download").mock(
        return_value=Response(
            200,
            headers={"Content-Type": "application/x-ndjson", "X-Crawl-Status": "completed"},
            content=NDJSON,
        )
    )
    download = client.iter_download("c_1")
    assert download.status is CrawlStatus.COMPLETED
    lines = list(download)
    assert [line.url for line in lines] == ["https://a", "https://b", "https://c"]
    assert lines[0].content == "<html>a</html>"
    assert lines[1].content_status is ContentStatus.FAILED
    assert lines[2].content == {"title": "C"}


@respx.mock
def test_iter_download_without_status_header(client: ZenRowsCrawlClient):
    respx.get(f"{BASE_URL}/crawls/c_1/download").mock(return_value=Response(200, content=b""))
    with client.iter_download("c_1") as download:
        assert download.status is None
        assert list(download) == []


@respx.mock
def test_download_writes_file_and_returns_status(client: ZenRowsCrawlClient, tmp_path):
    respx.get(f"{BASE_URL}/crawls/c_1/download").mock(
        return_value=Response(200, headers={"X-Crawl-Status": "running"}, content=NDJSON)
    )
    target = tmp_path / "out" / "c_1.jsonl"
    saved = client.download("c_1", target)
    assert saved == CrawlDownloadFile(path=target, status=CrawlStatus.RUNNING)
    assert saved.path.read_bytes() == NDJSON


@respx.mock
def test_download_maps_errors(client: ZenRowsCrawlClient):
    respx.get(f"{BASE_URL}/crawls/nope/download").mock(return_value=problem(404, "crawl_not_found"))
    with pytest.raises(CrawlAPIError) as exc_info:
        client.iter_download("nope")
    assert exc_info.value.code == "crawl_not_found"


# ----- errors -----


@respx.mock
def test_not_found_maps_to_crawl_api_error(client: ZenRowsCrawlClient):
    respx.get(f"{BASE_URL}/crawls/nope").mock(return_value=problem(404, "crawl_not_found"))
    with pytest.raises(CrawlAPIError) as exc_info:
        client.get("nope")
    err = exc_info.value
    assert err.status_code == 404
    assert err.code == "crawl_not_found"
    assert err.detail == "detail for crawl_not_found"
    assert not err.not_enabled


@respx.mock
def test_validation_error_maps_to_crawl_api_error(client: ZenRowsCrawlClient):
    respx.post(f"{BASE_URL}/crawls").mock(return_value=problem(422, "invalid_start_url"))
    with pytest.raises(CrawlAPIError) as exc_info:
        client.create("http://10.0.0.1/", depth=1)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "invalid_start_url"


@respx.mock
def test_too_many_crawls_carries_retry_after(client: ZenRowsCrawlClient):
    respx.post(f"{BASE_URL}/crawls").mock(
        return_value=problem(429, "too_many_crawls", headers={"Retry-After": "30"})
    )
    with pytest.raises(CrawlAPIError) as exc_info:
        client.create("https://example.com/", depth=1)
    assert exc_info.value.code == "too_many_crawls"
    assert exc_info.value.retry_after == 30.0


@respx.mock
def test_crawl_not_enabled_has_clear_message(client: ZenRowsCrawlClient):
    respx.post(f"{BASE_URL}/crawls").mock(
        return_value=problem(
            403,
            "REQS008",
            title="Crawl is not enabled for this account.",
            type="https://docs.zenrows.com/api-error-codes#REQS008",
        )
    )
    with pytest.raises(CrawlAPIError) as exc_info:
        client.create("https://example.com/", depth=1)
    err = exc_info.value
    assert err.not_enabled
    assert err.code == "REQS008"
    assert "Crawl is not enabled for this account" in str(err)


@pytest.mark.parametrize(
    "response",
    [Response(502, text="bad gateway"), Response(500, json={"title": "Server error"})],
)
@respx.mock
def test_error_without_code_has_none_code(client: ZenRowsCrawlClient, response):
    respx.get(f"{BASE_URL}/crawls/c_1").mock(return_value=response)
    with pytest.raises(CrawlAPIError) as exc_info:
        client.get("c_1")
    assert exc_info.value.status_code == response.status_code
    assert exc_info.value.code is None


@respx.mock
def test_keyed_create_does_not_retry_too_many_crawls(no_sleep):
    client = ZenRowsCrawlClient(api_key=API_KEY, base_url=BASE_URL, retries=2)
    route = respx.post(f"{BASE_URL}/crawls").mock(
        return_value=problem(429, "too_many_crawls", headers={"Retry-After": "1"})
    )
    with pytest.raises(CrawlAPIError) as exc_info:
        client.create("https://example.com/", depth=1, idempotency_key="idem-1")
    assert exc_info.value.code == "too_many_crawls"
    assert route.call_count == 1
    assert no_sleep == []


@respx.mock
def test_keyed_create_retries_transient_status(no_sleep):
    client = ZenRowsCrawlClient(api_key=API_KEY, base_url=BASE_URL, retries=2)
    route = respx.post(f"{BASE_URL}/crawls").mock(
        side_effect=[Response(503), Response(202, json=crawl_body())]
    )
    client.create("https://example.com/", depth=1, idempotency_key="idem-1")
    assert route.call_count == 2
    assert len(no_sleep) == 1


@respx.mock
def test_get_retries_transient_status(no_sleep):
    client = ZenRowsCrawlClient(api_key=API_KEY, base_url=BASE_URL, retries=2)
    route = respx.get(f"{BASE_URL}/crawls/c_1").mock(
        side_effect=[Response(503), Response(200, json=crawl_body(results=[], next_cursor="k"))]
    )
    client.get("c_1")
    assert route.call_count == 2
    assert len(no_sleep) == 1


# ----- waiter -----


@respx.mock
def test_wait_polls_until_terminal(client: ZenRowsCrawlClient, no_sleep):
    route = respx.get(f"{BASE_URL}/crawls/c_1").mock(
        side_effect=[
            Response(200, json=crawl_body(results=[], next_cursor="k")),
            Response(
                200,
                json=crawl_body(
                    status="completed",
                    finished_at="2026-10-08T09:02:00Z",
                    results=[{"url": "https://a"}],
                    next_cursor="k2",
                ),
            ),
        ]
    )

    crawl = client.wait("c_1", poll_interval=1.0)

    assert crawl.status is CrawlStatus.COMPLETED
    assert not hasattr(crawl, "results")
    assert route.call_count == 2
    assert all(c.request.url.params["limit"] == "1" for c in route.calls)
    assert len(no_sleep) == 1


@respx.mock
def test_wait_times_out(client: ZenRowsCrawlClient, no_sleep, monkeypatch):
    respx.get(f"{BASE_URL}/crawls/c_1").mock(
        return_value=Response(200, json=crawl_body(results=[], next_cursor="k"))
    )
    clock = iter(range(0, 1000, 10))
    monkeypatch.setattr("zenrows.batch._waiters.time.monotonic", lambda: next(clock))

    with pytest.raises(WaiterTimeout):
        client.wait("c_1", timeout=25)
