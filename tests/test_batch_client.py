"""Smoke tests for ZenRowsBatchClient.

Covers the wire-level contract: auth header, base-URL override, RFC
7807 error mapping, and a round-trip through `submit_job` + the
auto-paginating `iter_results`. We use respx to mock httpx so the
tests stay offline-deterministic.
"""

import io
import json

import httpx
import pytest
import respx
from httpx import Response

from zenrows import ZenRowsBatchClient
from zenrows.batch import BatchAPIError, IngestStatus, JobStatus, JobType, TaskResult, WaiterTimeout

BASE_URL = "http://localhost:9000/v1"
API_KEY = "test-key"


@pytest.fixture
def client() -> ZenRowsBatchClient:
    """A client pointed at a fake local dev URL. Demonstrates the
    base_url override path — same code can hit prod by dropping it."""
    return ZenRowsBatchClient(api_key=API_KEY, base_url=BASE_URL)


@respx.mock
def test_submit_job_sends_api_key_and_returns_typed_response(client: ZenRowsBatchClient):
    """Auth header is set, body is encoded, response parses into a typed model."""
    route = respx.post(f"{BASE_URL}/jobs").mock(
        return_value=Response(
            201,
            json={
                "job_id": "01J0000000000000000000000",
                "status": "closed",
                "accepted_tasks": 1,
            },
        )
    )

    resp = client.submit_job(
        {
            "type": "regular",
            "status": "closed",
            "tasks": [{"url": "https://example.com/a"}],
        }
    )

    assert resp.job_id == "01J0000000000000000000000"
    assert resp.status == JobStatus.CLOSED
    assert resp.accepted_tasks == 1

    sent = route.calls.last.request
    assert sent.headers["X-API-Key"] == API_KEY
    assert sent.headers["Content-Type"] == "application/json"
    assert json.loads(sent.content) == {
        "type": "regular",
        "status": "closed",
        "tasks": [{"url": "https://example.com/a"}],
    }


@respx.mock
def test_problem_response_raises_batch_api_error(client: ZenRowsBatchClient):
    """RFC 7807 problem bodies surface as BatchAPIError with a stable code."""
    respx.get(f"{BASE_URL}/jobs/missing").mock(
        return_value=Response(
            404,
            headers={"Content-Type": "application/problem+json"},
            json={
                "type": "about:blank",
                "title": "Not found",
                "status": 404,
                "code": "not_found",
                "detail": "Job not found.",
            },
        )
    )

    with pytest.raises(BatchAPIError) as exc_info:
        client.get_job("missing")
    assert exc_info.value.code == "not_found"
    assert exc_info.value.status_code == 404
    assert exc_info.value.problem is not None
    assert exc_info.value.problem.title == "Not found"


@respx.mock
def test_iter_results_auto_paginates(client: ZenRowsBatchClient):
    """Auto-pagination uses next_cursor; yields TaskResult instances."""
    page1 = {
        "results": [
            {
                "task_id": "01T000000000000000000A",
                "run_id": "01R000000000000000000A",
                "url": "https://example.com/a",
                "status": "successful",
            }
        ],
        "next_cursor": "abc",
    }
    page2 = {
        "results": [
            {
                "task_id": "01T000000000000000000B",
                "run_id": "01R000000000000000000A",
                "url": "https://example.com/b",
                "status": "successful",
            }
        ],
        "next_cursor": None,
    }

    def _handler(request):
        cursor = request.url.params.get("cursor")
        return Response(200, json=page2 if cursor == "abc" else page1)

    respx.get(f"{BASE_URL}/jobs/J/results").mock(side_effect=_handler)

    rows = list(client.iter_results("J"))
    assert [r.task_id for r in rows] == [
        "01T000000000000000000A",
        "01T000000000000000000B",
    ]


def test_missing_api_key_raises():
    """Constructor refuses to start without auth — no surprise 401s later."""
    with pytest.raises(ValueError, match="api_key is required"):
        ZenRowsBatchClient(api_key=None)


def test_base_url_override_takes_precedence_over_env(monkeypatch):
    """Explicit base_url wins over the env var which wins over the default."""
    monkeypatch.setenv("ZENROWS_BATCH_BASE_URL", "http://env-set/v1")
    c = ZenRowsBatchClient(api_key=API_KEY, base_url="http://kwarg/v1")
    assert c.base_url.rstrip("/") == "http://kwarg/v1"

    c2 = ZenRowsBatchClient(api_key=API_KEY)
    assert c2.base_url.rstrip("/") == "http://env-set/v1"


@respx.mock
def test_enum_filter_serialises_to_string(client: ZenRowsBatchClient):
    """`type=JobType.REGULAR` flattens to a `type=regular` query param."""
    route = respx.get(f"{BASE_URL}/jobs").mock(
        return_value=Response(200, json={"jobs": [], "next_cursor": None})
    )

    client.list_jobs(job_type=JobType.REGULAR, status=JobStatus.OPEN)

    params = route.calls.last.request.url.params
    assert params.get("type") == "regular"
    assert params.get("status") == "open"


# ----- type-specific submit shortcuts -----


@respx.mock
def test_submit_regular_accepts_bare_url_strings(client: ZenRowsBatchClient):
    """`submit_regular(["url1", "url2"])` builds the same wire body as
    the dict form — without the caller having to spell out type/status."""
    route = respx.post(f"{BASE_URL}/jobs").mock(
        return_value=Response(
            201,
            json={"job_id": "J", "status": "closed", "accepted_tasks": 2},
        )
    )

    client.submit_regular(
        ["https://example.com/a", "https://example.com/b"],
        zenrows_params={"js_render": "true"},
    )

    body = json.loads(route.calls.last.request.content)
    assert body == {
        "type": "regular",
        "status": "closed",
        "zenrows_params": {"js_render": "true"},
        "tasks": [
            {"url": "https://example.com/a"},
            {"url": "https://example.com/b"},
        ],
    }


@respx.mock
def test_submit_regular_accepts_inline_dicts_with_external_id(client: ZenRowsBatchClient):
    """Inline dict form lets each URL carry its own external_id."""
    route = respx.post(f"{BASE_URL}/jobs").mock(
        return_value=Response(
            201,
            json={"job_id": "J", "status": "closed", "accepted_tasks": 1},
        )
    )

    client.submit_regular(
        [{"url": "https://example.com/a", "external_id": "order-1"}],
    )

    body = json.loads(route.calls.last.request.content)
    assert body["tasks"] == [{"url": "https://example.com/a", "external_id": "order-1"}]


def test_submit_regular_rejects_urls_and_file_input_together(client: ZenRowsBatchClient):
    """Local validation fires before the round-trip."""
    with pytest.raises(ValueError, match=r"urls.*file_input_id"):
        client.submit_regular(["https://a"], file_input_id="FI")


@respx.mock
def test_submit_open_omits_tasks_for_streaming_jobs(client: ZenRowsBatchClient):
    """`submit_open()` with no args creates an empty open job."""
    route = respx.post(f"{BASE_URL}/jobs").mock(
        return_value=Response(
            201,
            json={"job_id": "J", "status": "open", "accepted_tasks": 0},
        )
    )

    client.submit_open()

    body = json.loads(route.calls.last.request.content)
    # No `tasks` key at all when no urls supplied.
    assert body == {"type": "regular", "status": "open"}


@respx.mock
def test_submit_scheduled_rate(client: ZenRowsBatchClient):
    """rate-shape schedule round-trips on the wire."""
    from zenrows.batch import Rate

    route = respx.post(f"{BASE_URL}/jobs").mock(
        return_value=Response(
            201,
            json={"job_id": "J", "status": "closed", "accepted_tasks": 1},
        )
    )

    client.submit_scheduled(
        Rate(every=15, unit="minute"),
        ["https://example.com/poll"],
    )

    body = json.loads(route.calls.last.request.content)
    assert body["type"] == "scheduled"
    assert body["schedule"] == {"rate": {"every": 15, "unit": "minute"}}


# ----- filename clash handling -----


def test_name_allocator_appends_suffix_on_clash():
    """First claimer gets the bare name; subsequent claimers get
    `_01`, `_02`, … suffixed before the extension."""
    from zenrows.batch._download import _NameAllocator

    a = _NameAllocator()
    assert a.claim("order-1.html") == "order-1.html"
    assert a.claim("order-1.html") == "order-1_01.html"
    assert a.claim("order-1.html") == "order-1_02.html"
    # Different basename — no collision.
    assert a.claim("order-2.html") == "order-2.html"
    # No extension — suffix still goes on the end.
    assert a.claim("README") == "README"
    assert a.claim("README") == "README_01"


@respx.mock
def test_submit_regular_passes_job_external_id_and_metadata(client: ZenRowsBatchClient):
    """Job-level external_id + metadata round-trip on the wire."""
    route = respx.post(f"{BASE_URL}/jobs").mock(
        return_value=Response(
            201,
            json={"job_id": "J", "status": "closed", "accepted_tasks": 1},
        )
    )

    client.submit_regular(
        ["https://example.com/a"],
        external_id="quarterly-crawl-42",
        metadata={"owner": "growth-team", "ticket": "GROW-1234"},
    )

    body = json.loads(route.calls.last.request.content)
    assert body["external_id"] == "quarterly-crawl-42"
    assert body["metadata"] == {"owner": "growth-team", "ticket": "GROW-1234"}


@respx.mock
def test_submit_scheduled_calendar_with_timezone(client: ZenRowsBatchClient):
    """Calendar builder round-trips to the expected wire shape."""
    from zenrows.batch import Calendar, Weekly

    route = respx.post(f"{BASE_URL}/jobs").mock(
        return_value=Response(
            201,
            json={"job_id": "J", "status": "closed", "accepted_tasks": 1},
        )
    )

    client.submit_scheduled(
        Calendar(
            times_of_day=["09:00", "18:00"],
            cadence=Weekly(days=["mon", "wed", "fri"]),
            timezone="Europe/Berlin",
        ),
        ["https://example.com/daily"],
    )

    body = json.loads(route.calls.last.request.content)
    assert body["schedule"] == {
        "calendar": {
            "times_of_day": ["09:00", "18:00"],
            "cadence": {"weekly": {"days": ["mon", "wed", "fri"]}},
        },
        "timezone": "Europe/Berlin",
    }


@respx.mock
def test_submit_scheduled_at_accepts_naive_datetime(client: ZenRowsBatchClient):
    """`At(datetime, ...)` serializes to the ISO string wire form."""
    from datetime import datetime

    from zenrows.batch import At

    route = respx.post(f"{BASE_URL}/jobs").mock(
        return_value=Response(
            201,
            json={"job_id": "J", "status": "closed", "accepted_tasks": 1},
        )
    )

    client.submit_scheduled(
        At(datetime(2026, 9, 1, 9, 0), timezone="Europe/Berlin"),
        ["https://example.com/once"],
    )

    body = json.loads(route.calls.last.request.content)
    assert body["schedule"] == {
        "at": "2026-09-01T09:00:00",
        "timezone": "Europe/Berlin",
    }


def test_at_rejects_aware_datetime():
    """`At(...)` validates in `__post_init__` — aware datetime fails fast."""
    from datetime import datetime, timezone

    from zenrows.batch import At

    with pytest.raises(ValueError, match="naive datetime"):
        At(datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc), timezone="Europe/Berlin")


def test_at_rejects_offset_string():
    """`At(str-with-offset, ...)` rejects in `__post_init__`."""
    from zenrows.batch import At

    with pytest.raises(ValueError, match="tz-naive"):
        At("2026-09-01T09:00:00+02:00", timezone="Europe/Berlin")


def test_at_rejects_z_suffix():
    """`At('...Z', ...)` rejects in `__post_init__`."""
    from zenrows.batch import At

    with pytest.raises(ValueError, match="tz-naive"):
        At("2026-09-01T09:00:00Z", timezone="UTC")


def test_at_requires_timezone():
    from zenrows.batch import At

    with pytest.raises(ValueError, match="timezone is required"):
        At("2026-09-01T09:00:00", timezone="")


def test_at_rejects_bad_timezone():
    from zenrows.batch import At

    with pytest.raises(ValueError, match="not a valid IANA"):
        At("2026-09-01T09:00:00", timezone="Mars/Olympus")


def test_rate_validates():
    from zenrows.batch import Rate

    with pytest.raises(ValueError, match=">= 1"):
        Rate(every=0, unit="minute")
    with pytest.raises(ValueError, match="must be one of"):
        Rate(every=5, unit="week")  # type: ignore[arg-type]


def test_calendar_validates():
    from zenrows.batch import Calendar, Daily, Monthly, Weekly

    # Half-hour rejected.
    with pytest.raises(ValueError, match="on the hour"):
        Calendar(
            times_of_day=["09:30"],
            cadence=Daily(),
            timezone="UTC",
        )

    # Bad weekday name.
    with pytest.raises(ValueError, match="valid day"):
        Calendar(
            times_of_day=["09:00"],
            cadence=Weekly(days=["funday"]),
            timezone="UTC",
        )

    # Monthly day out of range.
    with pytest.raises(ValueError, match="out of range"):
        Calendar(
            times_of_day=["09:00"],
            cadence=Monthly(days=[32]),
            timezone="UTC",
        )

    # Timezone required.
    with pytest.raises(ValueError, match="timezone is required"):
        Calendar(
            times_of_day=["09:00"],
            cadence=Daily(),
            timezone="",
        )


# ----- retry failed (partial rerun) -----


def _rerun_response(run_id: str, *, total: int, successful: int) -> dict:
    return {
        "job_id": "J",
        "status": "closed",
        "latest_run": {
            "run_id": run_id,
            "job_id": "J",
            "run_sequence": 2,
            "status": "running",
            "stats": {
                "total": total,
                "completed": successful,
                "successful": successful,
                "failed": 0,
            },
            "created_at": "2026-06-05T00:00:00Z",
            "updated_at": "2026-06-05T00:00:00Z",
        },
        "rerun_of": "R1",
        "retried_tasks": total - successful,
        "inherited_tasks": successful,
    }


@respx.mock
def test_retry_failed_sends_status_failed(client: ZenRowsBatchClient):
    route = respx.post(f"{BASE_URL}/jobs/J/rerun").mock(
        return_value=Response(201, json=_rerun_response("R2", total=100, successful=90))
    )

    # Act on a ref without a GET round-trip.
    run = client.job("J").retry_failed()

    assert run.run_id == "R2"
    assert dict(route.calls.last.request.url.params) == {"status": "failed"}


@respx.mock
def test_retry_failed_include_pending_sends_both(client: ZenRowsBatchClient):
    route = respx.post(f"{BASE_URL}/jobs/J/rerun").mock(
        return_value=Response(201, json=_rerun_response("R2", total=100, successful=80))
    )

    client.job("J").retry_failed(include_pending=True, idempotency_key="k1")

    req = route.calls.last.request
    assert dict(req.url.params) == {"status": "failed,pending"}
    assert req.headers["Idempotency-Key"] == "k1"


# ----- download all results (export-based zip) -----


@respx.mock
def test_download_all_results_starts_polls_and_streams(client: ZenRowsBatchClient, tmp_path):
    exports = f"{BASE_URL}/jobs/J/runs/R/exports"
    download = "https://s3.example.test/exports/E.zip?sig=abc"

    respx.post(exports).mock(
        return_value=Response(
            202,
            json={
                "export_id": "01J000000000000000000000EX",
                "status": "pending",
                "created_at": "2026-06-05T00:00:00Z",
                "expires_at": "2026-06-05T12:00:00Z",
            },
        )
    )
    respx.get(f"{exports}/01J000000000000000000000EX").mock(
        return_value=Response(
            200,
            json={
                "export_id": "01J000000000000000000000EX",
                "status": "completed",
                "error": None,
                "download_url": download,
                "created_at": "2026-06-05T00:00:00Z",
                "expires_at": "2026-06-05T12:00:00Z",
            },
        )
    )
    respx.get(download).mock(return_value=Response(200, content=b"PK\x03\x04 zip-bytes"))

    out = client.download_all_results("J", "R", tmp_path / "results.zip")

    assert out.read_bytes() == b"PK\x03\x04 zip-bytes"


# ----- scheduled-job management -----


def _scheduled_job(schedule_state: str) -> dict:
    return {
        "job_id": "J",
        "type": "scheduled",
        "status": "closed",
        "schedule_state": schedule_state,
        "created_at": "2026-06-05T00:00:00Z",
        "updated_at": "2026-06-05T00:00:00Z",
    }


def _run_json(
    *, run_id: str = "R", status: str = "running", pause_state: str | None = None
) -> dict:
    run = {
        "run_id": run_id,
        "job_id": "J",
        "run_sequence": 1,
        "status": status,
        "stats": {"total": 10, "completed": 3, "successful": 3, "failed": 0},
        "created_at": "2026-07-07T00:00:00Z",
        "updated_at": "2026-07-07T00:00:00Z",
    }
    if pause_state:
        run["pause_state"] = pause_state
    return run


@respx.mock
def test_run_pause_and_resume_suspend_current_run(client: ZenRowsBatchClient):
    """`job.run.pause()` / `.resume()` hit the run-level endpoints
    (distinct from `job.schedule.*`) and return a fresh RunHandle."""
    pause = respx.post(f"{BASE_URL}/jobs/J/pause").mock(
        return_value=Response(200, json=_run_json(pause_state="paused"))
    )
    resume = respx.post(f"{BASE_URL}/jobs/J/resume").mock(
        return_value=Response(200, json=_run_json(pause_state="active"))
    )

    job = client.job("J")
    paused = job.run.pause()
    assert pause.call_count == 1
    assert paused.run_id == "R"
    assert paused.data.pause_state.value == "paused"

    resumed = job.run.resume()
    assert resume.call_count == 1
    assert resumed.data.pause_state.value == "active"


@respx.mock
def test_run_stop_posts_stop_and_returns_run_handle(client: ZenRowsBatchClient):
    """`job.run.stop()` (and its `cancel()` alias) POST /stop and echo
    the refreshed run."""
    route = respx.post(f"{BASE_URL}/jobs/J/stop").mock(
        return_value=Response(200, json=_run_json(status="stopped"))
    )

    stopped = client.job("J").run.stop()
    assert route.call_count == 1
    assert stopped.run_id == "R"
    assert stopped.data.status.value == "stopped"


@respx.mock
def test_pause_and_resume_post_schedule_state(client: ZenRowsBatchClient):
    route = respx.post(f"{BASE_URL}/jobs/J/schedule/state").mock(
        side_effect=[
            Response(200, json=_scheduled_job("paused")),
            Response(200, json=_scheduled_job("active")),
        ]
    )

    job = client.job("J")
    paused = job.schedule.pause()
    assert json.loads(route.calls[0].request.content) == {"schedule_state": "paused"}
    assert paused.data.schedule_state.value == "paused"

    resumed = job.schedule.resume()
    assert json.loads(route.calls[1].request.content) == {"schedule_state": "active"}
    assert resumed.data.schedule_state.value == "active"


@respx.mock
def test_update_schedule_puts_resolved_body(client: ZenRowsBatchClient):
    from zenrows.batch import Rate

    route = respx.put(f"{BASE_URL}/jobs/J/schedule").mock(
        return_value=Response(200, json=_scheduled_job("active"))
    )

    client.job("J").schedule.update(Rate(every=15, unit="minute"))

    assert json.loads(route.calls.last.request.content) == {"rate": {"every": 15, "unit": "minute"}}


@respx.mock
def test_submit_regular_sends_webhook_and_name(client: ZenRowsBatchClient):
    route = respx.post(f"{BASE_URL}/jobs").mock(
        return_value=Response(201, json={"job_id": "J", "status": "closed", "accepted_tasks": 1})
    )

    client.submit_regular(
        ["https://example.com/a"],
        name="nightly-prices",
        webhook={"url": "https://hooks.example.com/zr", "signature": True},
    )

    body = json.loads(route.calls.last.request.content)
    assert body["name"] == "nightly-prices"
    assert body["webhook"] == {"url": "https://hooks.example.com/zr", "signature": True}


# ----- wait-for-ingest (async 202 submits, COR-358) -----


def _ingest_run_json(ingest_status: str | None, *, status: str = "running") -> dict:
    run = {
        "run_id": "R",
        "job_id": "J",
        "run_sequence": 1,
        "status": status,
        "stats": {"total": 10000, "completed": 0, "successful": 0, "failed": 0},
        "created_at": "2026-07-07T00:00:00Z",
        "updated_at": "2026-07-07T00:00:00Z",
    }
    if ingest_status:
        run["ingest_status"] = ingest_status
    return run


def _ingest_job_json(ingest_status: str | None) -> dict:
    return {
        "job_id": "J",
        "type": "regular",
        "status": "closed",
        "created_at": "2026-07-07T00:00:00Z",
        "updated_at": "2026-07-07T00:00:00Z",
        "latest_run": _ingest_run_json(ingest_status),
    }


@respx.mock
def test_submit_wait_for_ingest_polls_202_until_done(client: ZenRowsBatchClient):
    """A 202 submit body carries `ingest_status: pending`; the flag
    polls GET /jobs/{id} until it leaves `pending`."""
    respx.post(f"{BASE_URL}/jobs").mock(
        return_value=Response(
            202,
            json={
                "job_id": "J",
                "status": "closed",
                "accepted_tasks": 10000,
                "latest_run": _ingest_run_json("pending"),
            },
        )
    )
    get_route = respx.get(f"{BASE_URL}/jobs/J").mock(
        return_value=Response(200, json=_ingest_job_json("done"))
    )

    job = client.submit_regular(["https://example.com/a"], wait_for_ingest=True)

    assert get_route.call_count == 1
    assert job.data.latest_run.ingest_status is IngestStatus.DONE


@respx.mock
def test_submit_wait_for_ingest_201_skips_polling(client: ZenRowsBatchClient):
    """Sync (201) submits never carry `ingest_status` — the flag must
    not cost a follow-up GET."""
    respx.post(f"{BASE_URL}/jobs").mock(
        return_value=Response(
            201,
            json={
                "job_id": "J",
                "status": "closed",
                "accepted_tasks": 1,
                "latest_run": _ingest_run_json(None),
            },
        )
    )
    get_route = respx.get(f"{BASE_URL}/jobs/J").mock(
        return_value=Response(200, json=_ingest_job_json(None))
    )

    client.submit_regular(["https://example.com/a"], wait_for_ingest=True)

    assert get_route.call_count == 0


@respx.mock
def test_wait_for_ingest_standalone_polls_until_done(client: ZenRowsBatchClient):
    """`JobRef.wait_for_ingest()` works on any ref — the path a caller
    uses when acting on a known id. Returns a fresh loaded handle."""
    get_route = respx.get(f"{BASE_URL}/jobs/J").mock(
        side_effect=[
            Response(200, json=_ingest_job_json("pending")),
            Response(200, json=_ingest_job_json("pending")),
            Response(200, json=_ingest_job_json("done")),
        ]
    )

    out = client.job("J").wait_for_ingest(timeout=5.0, poll_interval=0.01)

    assert get_route.call_count == 3
    assert out.data.latest_run.ingest_status is IngestStatus.DONE


@respx.mock
def test_wait_for_ingest_timeout_raises(client: ZenRowsBatchClient):
    respx.get(f"{BASE_URL}/jobs/J").mock(
        return_value=Response(200, json=_ingest_job_json("pending"))
    )

    with pytest.raises(WaiterTimeout):
        client.job("J").wait_for_ingest(timeout=0.05, poll_interval=0.01)


@respx.mock
def test_job_handle_is_get_free(client: ZenRowsBatchClient):
    """`client.job(id)` mints a handle with no network call; acting on it
    (delete) hits only the operation endpoint — no wasted GET."""
    get_route = respx.get(f"{BASE_URL}/jobs/J").mock(return_value=Response(200, json={}))
    del_route = respx.delete(f"{BASE_URL}/jobs/J").mock(return_value=Response(202, json={}))

    client.job("J").delete()

    assert del_route.call_count == 1
    assert get_route.call_count == 0  # no GET just to delete


@respx.mock
def test_run_handle_is_get_free(client: ZenRowsBatchClient):
    """`client.run(job, run)` — same GET-free contract, scoped to one run."""
    get_route = respx.get(f"{BASE_URL}/jobs/J/runs/R").mock(return_value=Response(200, json={}))
    del_route = respx.delete(f"{BASE_URL}/jobs/J/runs/R").mock(return_value=Response(202, json={}))

    client.run("J", "R").delete()

    assert del_route.call_count == 1
    assert get_route.call_count == 0


@respx.mock
def test_job_ref_load_fetches_once(client: ZenRowsBatchClient):
    """A `client.job(id)` ref is GET-free and has no `.data`; `.load()`
    fetches exactly once and returns a loaded `JobHandle`."""
    get_route = respx.get(f"{BASE_URL}/jobs/J").mock(
        return_value=Response(200, json=_ingest_job_json("done"))
    )

    ref = client.job("J")
    assert get_route.call_count == 0  # minting the ref costs nothing
    assert not hasattr(ref, "data")  # a ref carries no snapshot

    handle = ref.load()  # explicit GET
    assert get_route.call_count == 1
    assert handle.data.status is not None


# ===== transport retries =====


@pytest.fixture
def no_sleep(monkeypatch):
    """Record + swallow retry backoff sleeps so tests run instantly.
    Returns the list of sleep durations (seconds) in call order."""
    slept: list[float] = []
    monkeypatch.setattr("zenrows.batch._transport.time.sleep", slept.append)
    return slept


@respx.mock
def test_retry_transient_status_then_succeeds(client: ZenRowsBatchClient, no_sleep):
    """A 503 on an idempotent GET is retried; the eventual 200 wins."""
    route = respx.get(f"{BASE_URL}/jobs/J").mock(
        side_effect=[
            Response(503, json={}),
            Response(503, json={}),
            Response(200, json=_ingest_job_json("done")),
        ]
    )

    job = client.get_job("J")

    assert route.call_count == 3  # two retries, then success
    assert job.data.status is JobStatus.CLOSED
    assert len(no_sleep) == 2  # slept once before each retry


@respx.mock
def test_retry_exhausts_and_raises(client: ZenRowsBatchClient, no_sleep):
    """Default 3 retries → 4 attempts, then the last error surfaces."""
    route = respx.get(f"{BASE_URL}/jobs/J").mock(return_value=Response(503, json={}))

    with pytest.raises(BatchAPIError) as exc:
        client.get_job("J")

    assert exc.value.status_code == 503
    assert route.call_count == 4  # 1 + 3 retries


@respx.mock
def test_no_retry_on_non_retryable_status(client: ZenRowsBatchClient, no_sleep):
    """A 400 is a client error, not transient — no retry."""
    route = respx.get(f"{BASE_URL}/jobs/J").mock(return_value=Response(400, json={}))

    with pytest.raises(BatchAPIError):
        client.get_job("J")

    assert route.call_count == 1


@respx.mock
def test_no_retry_on_non_idempotent_post(client: ZenRowsBatchClient, no_sleep):
    """A plain POST (no Idempotency-Key) is not replayed on 503."""
    route = respx.post(f"{BASE_URL}/jobs").mock(return_value=Response(503, json={}))

    with pytest.raises(BatchAPIError):
        client.submit_regular(["https://a"])

    assert route.call_count == 1


@respx.mock
def test_retry_on_post_with_idempotency_key(client: ZenRowsBatchClient, no_sleep):
    """A POST carrying an Idempotency-Key IS safe to replay."""
    route = respx.post(f"{BASE_URL}/jobs").mock(
        side_effect=[
            Response(503, json={}),
            Response(201, json={"job_id": "J", "status": "closed", "accepted_tasks": 1}),
        ]
    )

    ref = client.submit_regular(["https://a"], idempotency_key="k1")

    assert route.call_count == 2
    assert ref.job_id == "J"


@respx.mock
def test_retry_honors_retry_after(client: ZenRowsBatchClient, no_sleep):
    """`Retry-After: 2` overrides the computed backoff for that wait."""
    respx.get(f"{BASE_URL}/jobs/J").mock(
        side_effect=[
            Response(429, headers={"Retry-After": "2"}, json={}),
            Response(200, json=_ingest_job_json("done")),
        ]
    )

    client.get_job("J")

    assert no_sleep[0] == 2.0  # 2000ms / 1000


@respx.mock
def test_retry_on_network_error_then_succeeds(client: ZenRowsBatchClient, no_sleep):
    """Transient network errors on idempotent requests are retried."""
    route = respx.get(f"{BASE_URL}/jobs/J").mock(
        side_effect=[httpx.ConnectError("reset"), Response(200, json=_ingest_job_json("done"))]
    )

    job = client.get_job("J")

    assert route.call_count == 2
    assert job.data.status is JobStatus.CLOSED


@respx.mock
def test_no_retry_on_timeout(client: ZenRowsBatchClient, no_sleep):
    """Our own timeout budget is not retried — it propagates."""
    route = respx.get(f"{BASE_URL}/jobs/J").mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(httpx.ReadTimeout):
        client.get_job("J")

    assert route.call_count == 1
    assert no_sleep == []


@respx.mock
def test_retries_zero_disables(no_sleep):
    """`retries=0` sends each request exactly once."""
    client = ZenRowsBatchClient(api_key=API_KEY, base_url=BASE_URL, retries=0)
    route = respx.get(f"{BASE_URL}/jobs/J").mock(return_value=Response(503, json={}))

    with pytest.raises(BatchAPIError):
        client.get_job("J")

    assert route.call_count == 1


# ===== webhooks =====

_WEBHOOK_JSON = {"url": "https://hooks.example.com/zr", "signature": True}


@respx.mock
def test_get_job_webhook(client: ZenRowsBatchClient):
    respx.get(f"{BASE_URL}/jobs/J/webhook").mock(return_value=Response(200, json=_WEBHOOK_JSON))

    cfg = client.get_job_webhook("J")

    assert str(cfg.url) == "https://hooks.example.com/zr"
    assert cfg.signature is True


@respx.mock
def test_put_job_webhook_sends_both_fields(client: ZenRowsBatchClient):
    route = respx.put(f"{BASE_URL}/jobs/J/webhook").mock(
        return_value=Response(200, json=_WEBHOOK_JSON)
    )

    client.put_job_webhook("J", {"url": "https://hooks.example.com/zr", "signature": True})

    assert json.loads(route.calls.last.request.content) == {
        "url": "https://hooks.example.com/zr",
        "signature": True,
    }


@respx.mock
def test_delete_job_webhook(client: ZenRowsBatchClient):
    route = respx.delete(f"{BASE_URL}/jobs/J/webhook").mock(return_value=Response(204))

    assert client.delete_job_webhook("J") is None
    assert route.call_count == 1


@respx.mock
def test_test_webhook(client: ZenRowsBatchClient):
    route = respx.post(f"{BASE_URL}/webhook/test").mock(
        return_value=Response(
            200,
            json={"delivered": True, "event_id": "01T", "status_code": 200, "elapsed_ms": 42},
        )
    )

    resp = client.test_webhook({"url": "https://hooks.example.com/zr"})

    assert resp.delivered is True
    assert resp.status_code == 200
    assert json.loads(route.calls.last.request.content)["url"] == "https://hooks.example.com/zr"


@respx.mock
def test_job_ref_webhook_facet_delegates(client: ZenRowsBatchClient):
    """`job.set_webhook()` / `.get_webhook()` hit the job webhook route."""
    put = respx.put(f"{BASE_URL}/jobs/J/webhook").mock(
        return_value=Response(200, json=_WEBHOOK_JSON)
    )

    cfg = client.job("J").set_webhook("https://hooks.example.com/zr", signature=True)

    assert put.call_count == 1
    assert cfg.signature is True


# ===== single-task download =====


def _task_result(
    task_id: str,
    *,
    external_id: str | None = None,
    result_type: str | None = None,
    result_url: str | None = None,
    status: str = "successful",
) -> TaskResult:
    data = {"task_id": task_id, "run_id": "R", "url": "https://example.com", "status": status}
    if external_id:
        data["external_id"] = external_id
    if result_type:
        data["type"] = result_type
    if result_url:
        data["result_url"] = result_url
    return TaskResult.model_validate(data)


@respx.mock
def test_download_task_pulls_from_result_url(client: ZenRowsBatchClient, tmp_path):
    """`run.download_task_to_*` GET the presigned `result_url` directly
    (no /content endpoint); memory returns raw bytes, file writes to a
    path or an open binary file object."""
    result_url = "https://storage.example.test/bodies/T1.html?sig=abc"
    route = respx.get(result_url).mock(return_value=Response(200, content=b"<html>hi</html>"))
    run = client.run("J", "R")
    task = _task_result("T1", result_url=result_url)

    assert run.download_task_to_memory(task) == b"<html>hi</html>"

    out = tmp_path / "nested" / "body.html"  # parent dirs created
    run.download_task_to_file(task, out)
    assert out.read_bytes() == b"<html>hi</html>"

    buf = io.BytesIO()  # also accepts an open binary file object
    run.download_task_to_file(task, buf)
    assert buf.getvalue() == b"<html>hi</html>"

    assert route.call_count == 3  # one GET per download, straight to storage


@respx.mock
def test_download_task_no_result_url_raises(client: ZenRowsBatchClient):
    """A task with no `result_url` (e.g. a failed task) can't be downloaded."""
    task = _task_result("T1", status="failed")
    with pytest.raises(ValueError, match="no result_url"):
        client.run("J", "R").download_task_to_memory(task)


def test_external_id_filename_coerces_to_safe_name():
    """`use_external_id=True` coerces the id into a safe filename —
    unsafe chars → `_`, missing id → task_id fallback."""
    from zenrows.batch._download import _external_id_filename

    ok = _task_result("T1", external_id="order-1", result_type="html")
    assert _external_id_filename(ok) == "order-1.html"

    unsafe = _task_result("T1", external_id="a/b c", result_type="html")
    assert _external_id_filename(unsafe) == "a_b_c.html"  # '/' and ' ' → '_'

    missing = _task_result("T1", result_type="html")
    assert _external_id_filename(missing) == "T1.html"  # falls back to task_id
