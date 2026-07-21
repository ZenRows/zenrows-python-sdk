"""ZenRowsBatchClient — the friendly, typed facade.

The **main pattern** is resource-style, in two tiers (see
`_resources.py`): a `JobRef` / `RunRef` — id-only operations, no data —
or a loaded `JobHandle` / `RunHandle` whose `.data` snapshot is
synchronous and guaranteed. `client.job(id)` and the `submit_*` methods
give you a ref; `get_job` / `iter_jobs` and `ref.load()` give you a
loaded handle:

    ref = client.submit_regular([...])        # JobRef — no GET yet
    run = ref.wait()                          # loaded RunHandle
    ref.download_to_dir("./out",              # bulk download…
                        concurrency=8,        # …in parallel…
                        progress=True)        # …with a progress bar

Handles are immutable snapshots: mutating ops (`close`, `stop`, …)
return a *new* loaded handle carrying the server's fresh state rather
than updating in place. The raw, pydantic-typed wire responses are
still accessible — every loaded handle exposes a `.data` (full `Job` /
`Run`), and refs returned by a `submit_*` call carry a
`.submit_response` with the original wire-shape.

Listing endpoints return raw pages (`ListJobsResponse`, etc.); the
scanners (`iter_jobs`, `iter_runs`, `iter_results`) yield handles
(or `TaskResult` for results, since those are terminal data — they
don't have further-actions to chain).
"""

import os
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import IO, Any, Literal, TypeVar

import httpx
from pydantic import BaseModel

from zenrows.__version__ import __version__
from zenrows.batch._download import (
    DEFAULT_MAX_BYTES_PER_FILE,
    DEFAULT_MAX_COUNT_IN_MEMORY,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_TOTAL_BYTES_IN_MEMORY,
    DownloadedResult,
    download_to_dir,
    download_to_memory,
)
from zenrows.batch._estimate import CostEstimate, ParamMap, _estimate_cost
from zenrows.batch._resources import (
    ExportHandle,
    ExportRef,
    JobHandle,
    JobRef,
    RunHandle,
    RunRef,
)
from zenrows.batch._schedule import At, Calendar, Rate, Schedule
from zenrows.batch._transport import _Transport
from zenrows.batch._typed_dicts import (
    AddTasksDict,
    CreateJobInputDict,
    JobScheduleDict,
    SubmitJobDict,
    TaskInputDict,
    WebhookDict,
)
from zenrows.batch._waiters import poll_until
from zenrows.batch.errors import BatchAPIError
from zenrows.batch.models import (
    AddTasksRequest,
    AddTasksResponse,
    CreateJobInputRequest,
    CreateJobInputResponse,
    Csv,
    Export,
    ExportStatus,
    Fields,
    FileInputColumnRef1,
    FileInputColumnRef2,
    HMACKeyCreated,
    HMACKeyFinalized,
    HMACKeyList,
    IngestStatus,
    Job,
    JobSchedule,
    JobStatus,
    JobType,
    ListJobRunsResponse,
    ListJobsResponse,
    ListResultsResponse,
    RerunJobResponse,
    Run,
    RunStatus,
    StartExportResponse,
    SubmitJobRequest,
    SubmitJobResponse,
    TaskHistoryResponse,
    TaskResult,
    TestWebhookRequest,
    TestWebhookResponse,
    UpdateScheduleStateRequest,
    WebhookConfig,
)

# Run states that mean "no further work will happen on this run".
# Used as the default target for waiters; a run that's `completed`,
# `stopped`, or `deleted` never transitions again.
TERMINAL_RUN_STATUSES: frozenset[str] = frozenset(
    {RunStatus.COMPLETED.value, RunStatus.STOPPED.value, RunStatus.DELETED.value}
)

# Export states that don't transition again — `completed` (zip ready)
# or `failed` (size cap exceeded, fetch error, etc.). Used as the
# waiter default.
TERMINAL_EXPORT_STATUSES: frozenset[str] = frozenset(
    {ExportStatus.COMPLETED.value, ExportStatus.FAILED.value}
)

# Production endpoint. Hardcoded so the common path needs no
# configuration. The `base_url=` kwarg + `ZENROWS_BATCH_BASE_URL`
# env var are present for advanced use only.
DEFAULT_BASE_URL = "https://async.api.zenrows.com/v1"
DEFAULT_USER_AGENT = f"zenrows-batch-python/{__version__}"

M = TypeVar("M", bound=BaseModel)


class ZenRowsBatchClient:
    """Synchronous, typed client for the ZenRows Batch API.

    `api_key` is required. Most users read it from a secrets store or
    environment variable at the call site:

        client = ZenRowsBatchClient(api_key=os.environ["ZENROWS_API_KEY"])

    Every non-2xx raises `BatchAPIError`; `BatchAPIError.code` carries
    the stable `code` from the Problem body.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout: float | httpx.Timeout = 30.0,
        retries: int = 3,
        verify_ssl: bool | str = True,
        user_agent: str = DEFAULT_USER_AGENT,
        httpx_args: dict[str, Any] | None = None,
    ):
        """`retries` bounds automatic retries of transient failures
        (HTTP 429/502/503/504 and network errors) on idempotent
        requests — GET/PUT/DELETE and POSTs carrying an
        `Idempotency-Key`. Retries use jittered exponential backoff and
        honor `Retry-After`; set `retries=0` to disable."""
        if not api_key:
            raise ValueError("ZenRowsBatchClient: api_key is required.")
        base_url = base_url or os.environ.get("ZENROWS_BATCH_BASE_URL") or DEFAULT_BASE_URL
        self._t = _Transport(
            base_url=base_url,
            api_key=api_key,
            user_agent=user_agent,
            timeout=timeout,
            retries=retries,
            verify=verify_ssl,
            httpx_args=httpx_args,
        )

    # ----- lifecycle -----

    def __enter__(self) -> "ZenRowsBatchClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._t.close()

    @property
    def base_url(self) -> str:
        return self._t.base_url

    # ===== jobs (resource-returning) =====

    def submit_job(
        self,
        body: SubmitJobRequest | SubmitJobDict,
        *,
        idempotency_key: str | None = None,
        wait_for_ingest: bool = False,
    ) -> JobRef:
        """`POST /jobs` — submit a new scraping job. The general,
        low-level path; most callers prefer the type-specific
        `submit_regular` / `submit_scheduled` which hide the `type=`
        boilerplate and give per-type validation in the signature.

        Large submissions come back `202 Accepted` with task rows
        still streaming into storage; `wait_for_ingest=True` blocks
        until that ingestion finishes, so results pages are complete
        and `add_tasks` won't 409 on return. It costs no extra calls
        on the ordinary 201 path. For custom poll knobs, leave it
        False and call `JobRef.wait_for_ingest()` yourself.

        Returns a `JobRef` whose `.submit_response` carries the
        immediate wire response (job_id, accepted_tasks, etc.). Call
        `.load()` for a `JobHandle` with the full `.data` — or, when
        `wait_for_ingest=True` actually polled, a loaded `JobHandle`
        (with `.data`) is returned directly since the GET was paid
        for anyway."""
        body = _as_model(body, SubmitJobRequest)
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        resp = _parse(
            self._t.request_json("POST", "/jobs", body=body, headers=headers),
            SubmitJobResponse,
        )
        ref = JobRef(self, resp.job_id, submit_response=resp)
        if (
            wait_for_ingest
            and resp.latest_run is not None
            and resp.latest_run.ingest_status is IngestStatus.PENDING
        ):
            # We paid for the poll GET anyway — hand back a loaded
            # JobHandle (still carrying the submit response) rather than
            # a bare ref, so the caller has `.data` for free.
            data = ref.wait_for_ingest().data
            return JobHandle(self, resp.job_id, data, submit_response=resp)
        return ref

    # ----- type-specific submits (preferred over `submit_job` for
    # most callers) -----

    def submit_regular(
        self,
        urls: list[str | TaskInputDict] | None = None,
        *,
        file_input_id: str | None = None,
        zenrows_params: ParamMap | None = None,
        external_id: str | None = None,
        name: str | None = None,
        metadata: dict[str, str] | None = None,
        webhook: WebhookDict | None = None,
        idempotency_key: str | None = None,
        wait_for_ingest: bool = False,
    ) -> JobRef:
        """Submit a one-shot scraping job (closed, all tasks upfront).

        Pass `urls` as either bare strings or inline dicts carrying
        per-task `external_id` / `metadata` / `params`:

            client.submit_regular(["https://a", "https://b"])
            client.submit_regular([
                {"url": "https://a", "external_id": "ord-1"},
                {"url": "https://b", "external_id": "ord-2"},
            ])

        `urls` and `file_input_id` are mutually exclusive — exactly
        one must be set. The job is created with `status=closed`,
        so no further `add_tasks` calls are accepted. For the
        open-and-extend pattern see `submit_open`.
        """
        body = _build_submit_body(
            job_type="regular",
            urls=urls,
            file_input_id=file_input_id,
            status="closed",
            zenrows_params=zenrows_params,
            external_id=external_id,
            name=name,
            metadata=metadata,
            webhook=webhook,
        )
        return self.submit_job(
            body, idempotency_key=idempotency_key, wait_for_ingest=wait_for_ingest
        )

    def submit_open(
        self,
        urls: list[str | TaskInputDict] | None = None,
        *,
        zenrows_params: ParamMap | None = None,
        external_id: str | None = None,
        name: str | None = None,
        metadata: dict[str, str] | None = None,
        webhook: WebhookDict | None = None,
        idempotency_key: str | None = None,
        wait_for_ingest: bool = False,
    ) -> JobRef:
        """Submit a streaming-style job that stays open for more tasks.

        Created with `status=open` — `urls` can be empty (or omitted)
        and tasks are added later via `JobRef.add_tasks`. Close
        the job with `job.close()` once you're done; until then the
        run keeps running and accepting new work.

        File-input upload is not supported here (the server only
        accepts CSV inputs for `closed` regular jobs and scheduled
        jobs).
        """
        body = _build_submit_body(
            job_type="regular",
            urls=urls,
            file_input_id=None,
            status="open",
            zenrows_params=zenrows_params,
            external_id=external_id,
            name=name,
            metadata=metadata,
            webhook=webhook,
        )
        return self.submit_job(
            body, idempotency_key=idempotency_key, wait_for_ingest=wait_for_ingest
        )

    def submit_scheduled(
        self,
        schedule: Schedule | JobScheduleDict,
        urls: list[str | TaskInputDict] | None = None,
        *,
        file_input_id: str | None = None,
        zenrows_params: ParamMap | None = None,
        external_id: str | None = None,
        name: str | None = None,
        metadata: dict[str, str] | None = None,
        webhook: WebhookDict | None = None,
        idempotency_key: str | None = None,
    ) -> JobRef:
        """Submit a scheduled job.

        `schedule` is one of the typed builders (`At`, `Rate`,
        `Calendar`) or a raw `JobScheduleDict` for power users. The
        typed builders validate their inputs in `__post_init__`;
        the dict form is a passthrough — the server validates.

        Examples:

        ```python
        from datetime import datetime
        from zenrows.batch import At, Calendar, Rate, Weekly

        # One-shot at 09:00 Berlin local time
        client.submit_scheduled(
            At(datetime(2026, 9, 1, 9, 0), timezone="Europe/Berlin"),
            ["https://example.com/once"],
        )

        # Every 15 minutes (no timezone needed)
        client.submit_scheduled(
            Rate(every=15, unit="minute"),
            ["https://example.com/poll"],
        )

        # 09:00 + 18:00 Berlin time, Mon/Wed/Fri
        client.submit_scheduled(
            Calendar(
                times_of_day=["09:00", "18:00"],
                cadence=Weekly(days=["mon", "wed", "fri"]),
                timezone="Europe/Berlin",
            ),
            ["https://example.com/recurring"],
        )
        ```

        Like `submit_regular`, `urls` is bare-strings-or-inline-dicts
        and is mutually exclusive with `file_input_id`.
        """
        if isinstance(schedule, (At, Rate, Calendar)):
            schedule = schedule.to_dict()
        else:
            schedule = _normalize_schedule(schedule)
        body = _build_submit_body(
            job_type="scheduled",
            urls=urls,
            file_input_id=file_input_id,
            status="closed",
            zenrows_params=zenrows_params,
            schedule=schedule,
            external_id=external_id,
            name=name,
            metadata=metadata,
            webhook=webhook,
        )
        return self.submit_job(body, idempotency_key=idempotency_key)

    def get_job(self, job_id: str) -> JobHandle:
        """`GET /jobs/{job_id}` — returns a loaded `JobHandle` with
        `.data` already populated.

        **Want the full `Job` in one line?** This is it —
        `client.get_job(job_id).data` gives you the complete pydantic
        `Job`. It's the eager shortcut for `client.job(job_id).load()`
        (same single GET, same handle); reach for the bare
        `client.job(job_id)` ref only when you want to *act* on the id
        without fetching it."""
        data = self._get_job_data(job_id)
        return JobHandle(self, job_id, data)

    def job(self, job_id: str) -> JobRef:
        """A `JobRef` for an existing job with **no network call**.
        Lifecycle operations act on the id directly (`delete`, `close`,
        `rerun`, `add_tasks`); current-run and schedule ops live on the
        `.run` / `.schedule` facets; call `.load()` for a `JobHandle`
        with `.data`. Prefer this over `get_job` when you just want to
        act on a known id without fetching it first::

            client.job(job_id).delete()            # no round-trip
            client.job(job_id).run.stop()          # POST /jobs/{id}/stop
            client.job(job_id).schedule.pause()    # skip future fires
            client.job(job_id).load().data.status  # explicit GET
        """
        return JobRef(self, job_id)

    # ----- cost estimation (local, no API call) -----

    def estimate_cost(self, body: SubmitJobRequest | SubmitJobDict) -> CostEstimate:
        """Estimate the credit cost of a job before submitting it,
        assuming every task succeeds once. **Takes the same body you'd
        hand `submit_job`** — a raw dict or a typed `SubmitJobRequest` —
        so you estimate the exact job you're about to submit.

        Returns a `CostEstimate` with `min`/`max` credits and a per-tier
        `breakdown`; `min == max` (`.exact`) when no task uses
        `mode=auto`. Per-task `zenrows_params` override the job-level
        params on collision (task wins), matching the worker's merge.

        `file_input` bodies estimate as zero tasks — the CSV row count
        isn't known client-side; count the rows first.

        This is the single entry point for estimation. It's currently
        computed client-side from the SDK's rate card (no network call);
        it may move server-side in a future release, so it lives on the
        client to keep call sites stable.
        """
        model = _as_model(body, SubmitJobRequest)
        return _estimate_cost(model.tasks or [], zenrows_params=model.zenrows_params)

    def list_jobs(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        job_type: JobType | str | None = None,
        status: JobStatus | str | None = None,
    ) -> ListJobsResponse:
        """`GET /jobs` — raw page (with `next_cursor`). For most uses
        prefer the auto-paginating `iter_jobs`."""
        return _parse(
            self._t.request_json(
                "GET",
                "/jobs",
                params={
                    "limit": limit,
                    "cursor": cursor,
                    # The wire-level query param is `type`; the
                    # Python kwarg is `job_type` so it doesn't
                    # shadow the builtin.
                    "type": _enum_value(job_type),
                    "status": _enum_value(status),
                },
            ),
            ListJobsResponse,
        )

    def iter_jobs(
        self,
        *,
        job_type: JobType | str | None = None,
        status: JobStatus | str | None = None,
        page_size: int | None = None,
    ) -> Iterator[JobHandle]:
        """Auto-paginate `list_jobs`, yielding loaded `JobHandle`s with
        their `.data` pre-populated from the page."""
        for job in _scan(
            lambda cursor: self.list_jobs(
                limit=page_size, cursor=cursor, job_type=job_type, status=status
            ),
            items_attr="jobs",
        ):
            yield JobHandle(self, job.job_id, job)

    # ===== runs (resource-returning) =====

    def get_run(self, job_id: str, *, run_id: str) -> RunHandle:
        """`GET /jobs/{job_id}/runs/{run_id}` — returns a loaded
        `RunHandle` with `.data` populated."""
        data = self._get_run_data(job_id, run_id)
        return RunHandle(self, job_id, run_id, data)

    def run(self, job_id: str, run_id: str) -> RunRef:
        """A `RunRef` for an existing run with **no network call** — the
        run counterpart of `job`. Acts on `(job_id, run_id)` directly
        (`delete`, `results`, `wait`); call `.load()` for a `RunHandle`
        with `.data`::

            client.run(job_id, run_id).delete()   # scrub one run, no GET
        """
        return RunRef(self, job_id, run_id)

    def list_runs(
        self,
        job_id: str,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> ListJobRunsResponse:
        """`GET /jobs/{job_id}/runs` — raw page. For most uses prefer
        the auto-paginating `JobRef.runs()`."""
        return _parse(
            self._t.request_json(
                "GET",
                f"/jobs/{job_id}/runs",
                params={"limit": limit, "cursor": cursor},
            ),
            ListJobRunsResponse,
        )

    def iter_runs(
        self,
        job_id: str,
        *,
        page_size: int | None = None,
    ) -> Iterator[RunHandle]:
        """Auto-paginate runs of a job, yielding loaded `RunHandle`s."""
        for run in self._iter_runs_raw(job_id, page_size=page_size):
            yield RunHandle(self, job_id, run.run_id, run)

    # ===== results / content (terminal data) =====

    def list_results(
        self,
        job_id: str,
        *,
        run_id: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
    ) -> ListResultsResponse:
        """Raw results page. Prefer `iter_results` or
        `JobRef.results()` for auto-pagination."""
        path = f"/jobs/{job_id}/runs/{run_id}/results" if run_id else f"/jobs/{job_id}/results"
        return _parse(
            self._t.request_json("GET", path, params={"status": status, "cursor": cursor}),
            ListResultsResponse,
        )

    def iter_results(
        self,
        job_id: str,
        *,
        run_id: str | None = None,
        status: str | None = None,
    ) -> Iterator[TaskResult]:
        """Auto-paginate results, yielding `TaskResult` per row."""
        return self._iter_results_raw(job_id, run_id=run_id, status=status)

    # ===== waiter =====

    def wait_for_run(
        self,
        job_id: str,
        *,
        run_id: str | None = None,
        target_statuses: set[str] | frozenset[str] = TERMINAL_RUN_STATUSES,
        failure_statuses: set[str] | frozenset[str] | None = None,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
        max_poll_interval: float = 15.0,
        progress: bool = False,
    ) -> Run:
        """Block until a run reaches one of `target_statuses`, polling
        with jittered exponential backoff.

        `progress=True` shows a tqdm bar with totals as they advance.
        `None` inherits the client-level `progress` setting (which
        itself defaults to off unless `ZENROWS_BATCH_PROGRESS=true`).
        """
        return self._wait_for_run_raw(
            job_id,
            run_id=run_id,
            target_statuses=target_statuses,
            failure_statuses=failure_statuses,
            timeout=timeout,
            poll_interval=poll_interval,
            max_poll_interval=max_poll_interval,
            progress=progress,
        )

    # ===== file inputs (CSV uploads) =====

    def create_job_input(
        self,
        body: CreateJobInputRequest | CreateJobInputDict,
    ) -> CreateJobInputResponse:
        """`POST /job_inputs` — allocate a CSV upload slot. Most
        callers want the higher-level `upload_csv` instead."""
        body = _as_model(body, CreateJobInputRequest)
        return _parse(
            self._t.request_json("POST", "/job_inputs", body=body),
            CreateJobInputResponse,
        )

    def upload_csv(
        self,
        source: str | Path | IO[bytes],
        *,
        fields: dict[str, int | str],
        header: bool = False,
        delimiter: str = ",",
        quote: str = '"',
    ) -> str:
        """Allocate a CSV slot + PUT the body. Returns the
        `file_input_id` you then pass to `submit_job(...)`."""
        ref_fields: dict[str, Any] = {}
        for key in ("url", "external_id"):
            if key not in fields:
                continue
            v = fields[key]
            ref_fields[key] = (
                FileInputColumnRef1(root=v) if isinstance(v, str) else FileInputColumnRef2(root=v)
            )
        request = CreateJobInputRequest(
            type="csv",
            csv=Csv(
                delimiter=delimiter,
                quote=quote,
                header=header,
                fields=Fields.model_validate(ref_fields),
            ),
        )
        created = self.create_job_input(request)

        headers = dict(created.upload.headers or {})
        headers.setdefault("Content-Type", "text/csv")

        data = _read_csv_body(source)

        # The presigned URL lives on a different host (S3 / dev
        # _local route). Use a bare httpx so our auth header doesn't
        # leak there.
        with httpx.Client(timeout=httpx.Timeout(60.0)) as bare:
            r = bare.request(
                created.upload.method, str(created.upload.url), content=data, headers=headers
            )
            if r.status_code >= 400:
                raise BatchAPIError(r.status_code, problem=None, raw=r.content)

        return created.file_input_id

    # ===== Webhooks =====

    def get_job_webhook(self, job_id: str) -> WebhookConfig:
        """`GET /jobs/{job_id}/webhook` — the job's current webhook
        config. Raises `BatchAPIError` (404) when none is set."""
        return _parse(self._t.request_json("GET", f"/jobs/{job_id}/webhook"), WebhookConfig)

    def put_job_webhook(self, job_id: str, config: WebhookConfig | WebhookDict) -> WebhookConfig:
        """`PUT /jobs/{job_id}/webhook` — replace the webhook config
        wholesale. Both `url` and `signature` are required (no
        defaulting at the mutate boundary, so a partial update can't
        silently toggle signing). Returns the persisted config."""
        model = _as_model(config, WebhookConfig)
        return _parse(
            self._t.request_json("PUT", f"/jobs/{job_id}/webhook", body=model), WebhookConfig
        )

    def delete_job_webhook(self, job_id: str) -> None:
        """`DELETE /jobs/{job_id}/webhook` — clear the webhook config.
        Idempotent: 204 whether or not one was set."""
        self._t.request_json("DELETE", f"/jobs/{job_id}/webhook")

    def test_webhook(self, config: TestWebhookRequest | WebhookDict) -> TestWebhookResponse:
        """`POST /webhook/test` — dispatch a synthetic `webhook.test`
        event to a receiver URL and report the outcome, without touching
        any job. Handy to verify a receiver before you wire it to a job.

        `signature` defaults to `false`; set it `true` to exercise the
        HMAC path (requires an active signing key, else 400
        `webhook_signing_requires_active_key`)."""
        model = _as_model(config, TestWebhookRequest)
        return _parse(
            self._t.request_json("POST", "/webhook/test", body=model), TestWebhookResponse
        )

    # ===== HMAC key lifecycle =====

    def list_hmac_keys(self) -> HMACKeyList:
        return _parse(self._t.request_json("GET", "/hmac/keys"), HMACKeyList)

    def rotate_hmac_key(self) -> HMACKeyCreated:
        """Capture the returned `secret` HERE — it is not revealed again."""
        return _parse(self._t.request_json("POST", "/hmac/keys/rotate"), HMACKeyCreated)

    def finalize_hmac_key(self) -> HMACKeyFinalized:
        return _parse(
            self._t.request_json("POST", "/hmac/keys/rotate/finalize"),
            HMACKeyFinalized,
        )

    def cancel_hmac_rotation(self) -> None:
        self._t.request_json("DELETE", "/hmac/keys/rotate")

    # ===== Results exports =====

    def start_results_export(self, job_id: str, run_id: str) -> ExportRef:
        """`POST /jobs/{job_id}/runs/{run_id}/exports` — start an async
        zip of every task body in the run. Returns an `ExportRef`
        carrying the just-issued `export_id`; call `.load()` or block on
        `.wait()` for the download URL.

        Failure modes: 404 if the job/run is missing or not yours.
        The worker fails the export (status=failed,
        error="results are larger then 1 gb") if the pre-zip total
        exceeds the server's 1 GiB cap.
        """
        resp = self._post_export_start(job_id, run_id)
        return ExportRef(self, job_id, run_id, resp.export_id, start_response=resp)

    def get_results_export(self, job_id: str, run_id: str, export_id: str) -> ExportHandle:
        """`GET /jobs/{job_id}/runs/{run_id}/exports/{export_id}` —
        snapshot one export. Returns a loaded `ExportHandle` with `.data`
        pre-populated. 404 covers both "no such id" and "TTL-swept"."""
        data = self._get_export(job_id, run_id, export_id)
        return ExportHandle(self, job_id, run_id, export_id, data)

    def wait_for_export(
        self,
        job_id: str,
        run_id: str,
        export_id: str,
        *,
        target_statuses: set[str] | frozenset[str] = TERMINAL_EXPORT_STATUSES,
        timeout: float = 600.0,
        poll_interval: float = 2.0,
        max_poll_interval: float = 15.0,
    ) -> Export:
        """Block until an export reaches a terminal state. Defaults to
        `{completed, failed}`. Returns the `Export` snapshot — callers
        check `.status` and (on `completed`) `.download_url`.

        `failed` is NOT raised; the caller decides whether
        `error="results are larger then 1 gb"` is fatal or expected.
        """
        return self._wait_for_export_raw(
            job_id,
            run_id,
            export_id,
            target_statuses=target_statuses,
            timeout=timeout,
            poll_interval=poll_interval,
            max_poll_interval=max_poll_interval,
        )

    # ==========================================================
    # ===== "raw" methods (no resource wrapping; used by   =====
    # =====  handles internally and as escape hatches).     =====
    # ==========================================================

    def _get_job_data(self, job_id: str) -> Job:
        return _parse(self._t.request_json("GET", f"/jobs/{job_id}"), Job)

    def _get_run_data(self, job_id: str, run_id: str) -> Run:
        return _parse(self._t.request_json("GET", f"/jobs/{job_id}/runs/{run_id}"), Run)

    def _post_close(self, job_id: str) -> Job:
        return _parse(self._t.request_json("POST", f"/jobs/{job_id}/close"), Job)

    def _post_stop(self, job_id: str) -> Run:
        # `/stop`, `/pause`, `/resume` all act on the current run and
        # echo the refreshed Run object (not the Job).
        return _parse(self._t.request_json("POST", f"/jobs/{job_id}/stop"), Run)

    def _post_pause(self, job_id: str) -> Run:
        return _parse(self._t.request_json("POST", f"/jobs/{job_id}/pause"), Run)

    def _post_resume(self, job_id: str) -> Run:
        return _parse(self._t.request_json("POST", f"/jobs/{job_id}/resume"), Run)

    def _delete(self, job_id: str) -> None:
        self._t.request_json("DELETE", f"/jobs/{job_id}")

    def _delete_run(self, job_id: str, run_id: str) -> None:
        self._t.request_json("DELETE", f"/jobs/{job_id}/runs/{run_id}")

    def _post_rerun(
        self,
        job_id: str,
        *,
        status: str | list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> RerunJobResponse:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        params: dict[str, Any] | None = None
        if status is not None:
            if isinstance(status, list):
                params = {"status": ",".join(status)}
            else:
                params = {"status": status}
        return _parse(
            self._t.request_json("POST", f"/jobs/{job_id}/rerun", params=params, headers=headers),
            RerunJobResponse,
        )

    def _resolve_schedule(self, schedule: "Schedule | JobScheduleDict") -> JobSchedule:
        """Normalise a schedule (typed builder, or dict with a possible
        `datetime` in `at`) into a validated `JobSchedule` model — the
        same resolution `submit_scheduled` does."""
        if isinstance(schedule, (At, Rate, Calendar)):
            schedule = schedule.to_dict()
        else:
            schedule = _normalize_schedule(schedule)
        return _as_model(schedule, JobSchedule)

    def _put_schedule(self, job_id: str, schedule: JobSchedule) -> Job:
        return _parse(
            self._t.request_json("PUT", f"/jobs/{job_id}/schedule", body=schedule),
            Job,
        )

    def _post_schedule_state(self, job_id: str, state: str) -> Job:
        body = UpdateScheduleStateRequest(schedule_state=state)  # type: ignore
        return _parse(
            self._t.request_json("POST", f"/jobs/{job_id}/schedule/state", body=body),
            Job,
        )

    def _post_tasks(self, job_id: str, body: AddTasksRequest | AddTasksDict) -> AddTasksResponse:
        body = _as_model(body, AddTasksRequest)
        return _parse(
            self._t.request_json("POST", f"/jobs/{job_id}/tasks", body=body),
            AddTasksResponse,
        )

    def _iter_runs_raw(self, job_id: str, *, page_size: int | None = None) -> Iterator[Run]:
        yield from _scan(
            lambda cursor: self.list_runs(job_id, limit=page_size, cursor=cursor),
            items_attr="runs",
        )

    def _iter_results_raw(
        self,
        job_id: str,
        *,
        run_id: str | None = None,
        status: str | None = None,
    ) -> Iterator[TaskResult]:
        yield from _scan(
            lambda cursor: self.list_results(job_id, run_id=run_id, status=status, cursor=cursor),
            items_attr="results",
        )

    def _get_task_history_raw(
        self, job_id: str, task_id: str, *, run_id: str | None = None
    ) -> TaskHistoryResponse:
        path = (
            f"/jobs/{job_id}/runs/{run_id}/tasks/{task_id}/history"
            if run_id
            else f"/jobs/{job_id}/tasks/{task_id}/history"
        )
        return _parse(self._t.request_json("GET", path), TaskHistoryResponse)

    def _post_export_start(self, job_id: str, run_id: str) -> StartExportResponse:
        return _parse(
            self._t.request_json("POST", f"/jobs/{job_id}/runs/{run_id}/exports"),
            StartExportResponse,
        )

    def _get_export(self, job_id: str, run_id: str, export_id: str) -> Export:
        return _parse(
            self._t.request_json("GET", f"/jobs/{job_id}/runs/{run_id}/exports/{export_id}"),
            Export,
        )

    def _wait_for_export_raw(
        self,
        job_id: str,
        run_id: str,
        export_id: str,
        *,
        target_statuses: set[str] | frozenset[str],
        timeout: float,
        poll_interval: float,
        max_poll_interval: float = 15.0,
    ) -> Export:
        target = target_statuses or TERMINAL_EXPORT_STATUSES

        def fetch() -> Export:
            return self._get_export(job_id, run_id, export_id)

        def is_done(e: Export) -> bool:
            return e.status.value in target

        return poll_until(
            fetch=fetch,
            is_done=is_done,
            timeout=timeout,
            initial_interval=poll_interval,
            max_interval=max_poll_interval,
        )

    def download_all_results(
        self,
        job_id: str,
        run_id: str,
        target_path: str | Path,
        *,
        wait_timeout: float = 600.0,
        poll_interval: float = 2.0,
        chunk_size: int = 1 << 20,
    ) -> Path:
        """End-to-end helper: start an export, wait for it, and save
        the zip to `target_path`.

        Steps:
          1. `POST .../exports` (start) → `export_id`.
          2. Poll `GET .../exports/{id}` until `completed` or `failed`.
          3. On `completed`, stream the presigned URL to `target_path`.

        Raises `WaiterTimeout` if the export doesn't reach a terminal
        state within `wait_timeout`. Raises `BatchAPIError` with the
        server's error message on `status=failed` (e.g.
        `"results are larger then 1 gb"`). Returns the `Path` written.

        The server-side export is capped at 1 GiB per run. For larger
        runs (or one file per task), use `download_to_dir` instead: it
        fetches each body client-side with no size limit, at the cost
        of being slower (one body at a time, tunable via `concurrency=`).
        """
        export = self.start_results_export(job_id, run_id)
        final = self.wait_for_export(
            job_id,
            run_id,
            export.export_id,
            timeout=wait_timeout,
            poll_interval=poll_interval,
        )
        if final.status != ExportStatus.COMPLETED:
            raise BatchAPIError(
                status_code=0,
                problem=None,
                raw=(final.error or "export failed").encode(),
            )
        if not final.download_url:
            raise BatchAPIError(
                status_code=0,
                problem=None,
                raw=b"export completed but server returned no download_url",
            )

        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Presigned URLs live on a different host (S3 / dev `_local`).
        # Use a bare httpx so our auth header doesn't leak there.
        with (
            httpx.Client(timeout=httpx.Timeout(60.0)) as bare,
            bare.stream("GET", str(final.download_url)) as r,
        ):
            if r.status_code >= 400:
                raise BatchAPIError(r.status_code, problem=None, raw=r.read())
            with target.open("wb") as f:
                for chunk in r.iter_bytes(chunk_size):
                    f.write(chunk)
        return target

    def _wait_for_run_raw(
        self,
        job_id: str,
        *,
        run_id: str | None,
        target_statuses: set[str] | frozenset[str] | None,
        failure_statuses: set[str] | frozenset[str] | None,
        timeout: float,
        poll_interval: float,
        max_poll_interval: float = 15.0,
        progress: bool = False,
    ) -> Run:
        target = target_statuses or TERMINAL_RUN_STATUSES

        def fetch() -> Run:
            if not run_id:
                job = self._get_job_data(job_id)
                if not job.latest_run:
                    return _PendingRun()  # type: ignore
                return job.latest_run
            return self._get_run_data(job_id, run_id)

        def is_done(run: Run) -> bool:
            if isinstance(run, _PendingRun):
                return False
            return run.status.value in target

        def is_failure(run: Run) -> bool:
            if not failure_statuses or isinstance(run, _PendingRun):
                return False
            return run.status.value in failure_statuses

        with _maybe_wait_progress(progress, job_id=job_id) as on_poll:

            def fetch_with_progress() -> Run:
                run = fetch()
                on_poll(run)
                return run

            return poll_until(
                fetch=fetch_with_progress,
                is_done=is_done,
                is_failure=is_failure,
                timeout=timeout,
                initial_interval=poll_interval,
                max_interval=max_poll_interval,
            )

    def _wait_for_ingest_raw(
        self,
        job_id: str,
        *,
        timeout: float,
        poll_interval: float,
        max_poll_interval: float = 15.0,
    ) -> Job:
        """Poll `GET /jobs/{id}` until the latest run's async-carrier
        ingestion is no longer `pending`. Done means: `ingest_status`
        is `done` or absent (the run never ingested asynchronously),
        the run is terminal (a mid-ingest stop flips the field to
        `done` — SPEC §3.1), or there is no run yet (nothing is
        ingesting). Returns the final `Job` snapshot."""

        def fetch() -> Job:
            return self._get_job_data(job_id)

        def is_done(job: Job) -> bool:
            run = job.latest_run
            if run is None or run.status.value in TERMINAL_RUN_STATUSES:
                return True
            return run.ingest_status is not IngestStatus.PENDING

        return poll_until(
            fetch=fetch,
            is_done=is_done,
            timeout=timeout,
            initial_interval=poll_interval,
            max_interval=max_poll_interval,
        )

    def _download_dir(
        self,
        job_id: str,
        run_id: str | None,
        target_dir: str | Path,
        *,
        status: str | None,
        name_fn: Callable[[TaskResult], str] | None,
        use_external_id: bool,
        concurrency: int,
        progress: bool,
        max_files: int | None,
        max_bytes_per_file: int | None,
    ) -> int:
        return download_to_dir(
            self,
            job_id,
            Path(target_dir),
            run_id=run_id,
            status=status,
            name_fn=name_fn,
            use_external_id=use_external_id,
            concurrency=concurrency,
            progress=progress,
            max_files=max_files if max_files is not None else DEFAULT_MAX_FILES,
            max_bytes_per_file=(
                max_bytes_per_file if max_bytes_per_file is not None else DEFAULT_MAX_BYTES_PER_FILE
            ),
        )

    def _download_memory(
        self,
        job_id: str,
        run_id: str | None,
        *,
        status: str | None,
        concurrency: int,
        progress: bool,
        max_count: int | None,
        max_total_bytes: int | None,
        max_bytes_per_file: int | None,
    ) -> list[DownloadedResult]:
        return download_to_memory(
            self,
            job_id,
            run_id=run_id,
            status=status,
            concurrency=concurrency,
            progress=progress,
            max_count=max_count if max_count is not None else DEFAULT_MAX_COUNT_IN_MEMORY,
            max_total_bytes=(
                max_total_bytes
                if max_total_bytes is not None
                else DEFAULT_MAX_TOTAL_BYTES_IN_MEMORY
            ),
            max_bytes_per_file=(
                max_bytes_per_file if max_bytes_per_file is not None else DEFAULT_MAX_BYTES_PER_FILE
            ),
        )


# ----- helpers -----


def _scan(
    fetch_page: Any,
    *,
    items_attr: str,
    cursor_attr: str = "next_cursor",
) -> Iterator[Any]:
    """Generic cursor-pagination scanner."""
    cursor: str | None = None
    while True:
        page = fetch_page(cursor)
        yield from getattr(page, items_attr)
        cursor = getattr(page, cursor_attr, None)
        if not cursor:
            return


class _PendingRun:
    """Sentinel returned by the waiter when a scheduled job has not
    fired its first run yet."""

    status = None


def _normalize_schedule(s: JobScheduleDict) -> JobScheduleDict:
    """Convert a `datetime` in `s["at"]` to a tz-naive ISO string.
    Reject tz-aware datetimes with a clear ValueError — keeping the
    tz field as the single source of truth is what makes DST
    transitions deterministic. Returns a shallow copy with the
    normalised `at`; other fields pass through unchanged."""
    if "at" not in s:
        return s
    raw_at = s["at"]
    if isinstance(raw_at, datetime):
        if raw_at.tzinfo is not None and raw_at.utcoffset() is not None:
            raise ValueError(
                "schedule.at must be a naive datetime (no tzinfo); "
                "supply schedule.timezone separately so DST transitions "
                "stay deterministic."
            )
        out: JobScheduleDict = {**s}
        out["at"] = raw_at.strftime("%Y-%m-%dT%H:%M:%S")  # type: ignore[typeddict-item]
        return out
    return s


def _build_submit_body(
    *,
    job_type: Literal["regular", "scheduled"],
    urls: list[str | TaskInputDict] | None,
    file_input_id: str | None,
    status: Literal["open", "closed"],
    zenrows_params: ParamMap | None,
    schedule: JobScheduleDict | None = None,
    external_id: str | None = None,
    name: str | None = None,
    metadata: dict[str, str] | None = None,
    webhook: WebhookDict | None = None,
) -> SubmitJobDict:
    """Shared bodies for the type-specific submit methods.

    Validates the urls/file_input_id mutual exclusion locally so
    the API call doesn't pay for an obvious client-side mistake.
    Returns a dict (not a SubmitJobRequest) so the values that
    weren't supplied stay out of the wire payload — leaning on
    the transport's `exclude_unset=True` semantics."""
    if urls is not None and file_input_id is not None:
        raise ValueError("submit: pass `urls` OR `file_input_id`, not both.")
    if urls is None and file_input_id is None and status == "closed":
        raise ValueError(
            "submit: closed jobs require `urls` or `file_input_id` "
            "(use submit_open() for the open/extend pattern)."
        )

    body: SubmitJobDict = {"type": job_type, "status": status}
    if zenrows_params:
        body["zenrows_params"] = zenrows_params
    if schedule:
        body["schedule"] = schedule
    if file_input_id:
        body["file_input_id"] = file_input_id
    if urls:
        body["tasks"] = [_coerce_url(u) for u in urls]
    if external_id:
        body["external_id"] = external_id
    if name:
        body["name"] = name
    if metadata:
        body["metadata"] = metadata
    if webhook:
        # `signature` is optional for the caller but required on the wire;
        # default it to false (unsigned), matching the server default.
        body["webhook"] = {"signature": False, **webhook}
    return body


def _coerce_url(item: str | TaskInputDict) -> TaskInputDict:
    """Accept a bare URL string OR a TaskInputDict. Strings become
    `{"url": "..."}`; dicts pass through untouched."""
    if isinstance(item, str):
        return {"url": item}
    return item


def _as_model(value: BaseModel | Mapping[str, Any], cls: type[M]) -> M:
    if isinstance(value, cls):
        return value
    return cls.model_validate(value)


def _parse(payload: Any, cls: type[M]) -> M:
    return cls.model_validate(payload)


def _enum_value(v: JobType | JobStatus | str | None) -> str | None:
    """Flatten Enum-or-str-or-None to the wire string."""
    if not v:
        return None
    if isinstance(v, Enum):
        return v.value
    return v


def _read_csv_body(source: str | Path | IO[bytes]) -> bytes:
    if isinstance(source, (str, Path)):
        return Path(source).read_bytes()
    data = source.read()
    if isinstance(data, str):
        return data.encode("utf-8")
    return data


# ----- waiter progress shim -----


from contextlib import contextmanager  # noqa: E402


@contextmanager
def _maybe_wait_progress(enabled: bool, *, job_id: str):
    """Yields an `on_poll(run)` callback that updates a tqdm bar each
    iteration with `(successful+failed)/total`. No-op when
    `enabled=False` or `tqdm` is missing.

    The bar's `total` starts at None (indeterminate) and is set on
    the first poll that returns a non-pending run — for scheduled
    jobs we don't know the totals until the first fire materialises."""
    if not enabled:
        yield lambda _run: None
        return
    try:
        from tqdm.auto import tqdm
    except ImportError:
        yield lambda _run: None
        return

    bar = tqdm(total=None, desc=f"{job_id}: pending", unit="task")
    try:
        last_done = [0]

        def on_poll(run: Run | _PendingRun) -> None:
            if isinstance(run, _PendingRun):
                # Scheduled job's first run hasn't materialised yet.
                bar.set_description_str(f"{job_id}: pending")
                return
            if bar.total is None:
                bar.total = run.stats.total
                bar.refresh()
            done = run.stats.successful + run.stats.failed
            if done > last_done[0]:
                bar.update(done - last_done[0])
                last_done[0] = done
            bar.set_description_str(f"{job_id}: {run.status.value}")

        yield on_poll
    finally:
        bar.close()


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_USER_AGENT",
    "TERMINAL_EXPORT_STATUSES",
    "TERMINAL_RUN_STATUSES",
    "ZenRowsBatchClient",
]
