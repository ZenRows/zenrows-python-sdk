"""Resource handles for the Batch SDK — a two-tier (typestate) design
with namespaced sub-facets.

For each resource there's a *reference* and a *loaded handle*:

  - ``JobRef`` wraps ``(client, job_id)`` and exposes every
    job-template operation that needs **only the id** — ``close``,
    ``delete``, ``rerun`` / ``retry_failed``, ``add_tasks``, ``runs()``.
    It holds no snapshot; ``ref.load()`` fetches and returns a…
  - ``JobHandle`` — a ``JobRef`` plus a guaranteed, synchronous
    ``data: Job``. Returned by ``get_job`` / ``iter_jobs`` and the ops
    that echo fresh state.

Two sub-facets hang off every job (ref or handle), because the API has
two distinct scopes that both use the word "pause":

  - ``job.run.*``      — operations on the **current run**: ``pause`` /
    ``resume`` (run-level suspend), ``stop`` / ``cancel``, ``wait``,
    ``results``, downloads, ``start_export``.
  - ``job.schedule.*`` — operations on the **schedule**: ``pause`` /
    ``resume`` (skip future fires) and ``update``.

Address a *specific historical* run with ``client.run(job_id, run_id)``
(a ``RunRef``); it deliberately has no ``pause`` / ``stop``, since the
API only pauses or stops the current run.

Handles are immutable snapshots: mutating ops (``pause``, ``stop``, …)
return a *new* loaded handle carrying the server's fresh state rather
than updating in place. Every method here delegates to the client's
transport, so behaviour is identical to the flat, pydantic-typed
surface.
"""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO, TYPE_CHECKING

import httpx

from zenrows.batch._download import download_task_to_file as _download_task_to_file
from zenrows.batch._download import download_task_to_memory as _download_task_to_memory
from zenrows.batch._typed_dicts import AddTasksDict
from zenrows.batch.errors import BatchAPIError
from zenrows.batch.models import (
    AddTasksRequest,
    AddTasksResponse,
    Export,
    ExportStatus,
    Job,
    JobStatus,
    RerunJobResponse,
    Run,
    RunStats,
    RunStatus,
    StartExportResponse,
    SubmitJobResponse,
    TaskHistoryResponse,
    TaskResult,
    WebhookConfig,
)

if TYPE_CHECKING:
    from zenrows.batch._download import DownloadedResult
    from zenrows.batch._schedule import Schedule
    from zenrows.batch._typed_dicts import JobScheduleDict
    from zenrows.batch.client import ZenRowsBatchClient


# ======================= specific runs =======================


class RunRef:
    """A reference to a **specific** run by ``(job_id, run_id)``.

    Read/download operations on one (usually historical) run. No
    ``pause`` / ``stop``: the API only suspends or stops the *current*
    run (see ``job.run`` — the :class:`CurrentRun` facet). Call
    ``load()`` for a :class:`RunHandle` with data.
    """

    def __init__(
        self,
        client: "ZenRowsBatchClient",
        job_id: str,
        run_id: str,
    ):
        self._client = client
        self.job_id = job_id
        self.run_id = run_id

    def __repr__(self) -> str:
        return f"<{type(self).__name__} job_id={self.job_id!r} run_id={self.run_id!r}>"

    def load(self) -> "RunHandle":
        """``GET /jobs/{id}/runs/{run_id}`` — fetch the run and return a
        loaded :class:`RunHandle`."""
        return RunHandle(
            self._client,
            self.job_id,
            self.run_id,
            self._client._get_run_data(self.job_id, self.run_id),
        )

    # ----- lifecycle -----

    def delete(self) -> None:
        """``DELETE /jobs/{id}/runs/{run_id}`` — scrub one run only."""
        self._client._delete_run(self.job_id, self.run_id)

    # ----- results / content -----

    def results(self, *, status: str | None = None) -> Iterator[TaskResult]:
        return self._client._iter_results_raw(self.job_id, run_id=self.run_id, status=status)

    def task_history(self, task_id: str) -> TaskHistoryResponse:
        return self._client._get_task_history_raw(self.job_id, task_id, run_id=self.run_id)

    # ----- bulk download -----

    def download_to_dir(
        self,
        target_dir: str | Path,
        *,
        status: str | None = "successful",
        name_fn: Callable[[TaskResult], str] | None = None,
        use_external_id: bool = False,
        concurrency: int = 1,
        progress: bool = False,
        max_files: int | None = None,
        max_bytes_per_file: int | None = None,
    ) -> int:
        return self._client._download_dir(
            self.job_id,
            self.run_id,
            target_dir,
            status=status,
            name_fn=name_fn,
            use_external_id=use_external_id,
            concurrency=concurrency,
            progress=progress,
            max_files=max_files,
            max_bytes_per_file=max_bytes_per_file,
        )

    def download_to_memory(
        self,
        *,
        status: str | None = "successful",
        concurrency: int = 1,
        progress: bool = False,
        max_count: int | None = None,
        max_total_bytes: int | None = None,
        max_bytes_per_file: int | None = None,
    ) -> "list[DownloadedResult]":
        return self._client._download_memory(
            self.job_id,
            self.run_id,
            status=status,
            concurrency=concurrency,
            progress=progress,
            max_count=max_count,
            max_total_bytes=max_total_bytes,
            max_bytes_per_file=max_bytes_per_file,
        )

    # ----- single-task download -----

    def download_task_to_file(self, task: TaskResult, target: "str | Path | IO[bytes]") -> None:
        """Download one ``task``'s body straight from its presigned
        ``result_url`` to ``target`` — a path (``str`` / ``Path``) or an
        open binary file object."""
        _download_task_to_file(task, target)

    def download_task_to_memory(self, task: TaskResult) -> bytes:
        """Download one ``task``'s body straight from its presigned
        ``result_url`` and return the raw bytes."""
        return _download_task_to_memory(task)

    # ----- waiter -----

    def wait(
        self,
        *,
        target_statuses: set[str] | frozenset[str] | None = None,
        failure_statuses: set[str] | frozenset[str] | None = None,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
        progress: bool = False,
    ) -> "RunHandle":
        """Block until this run reaches a target state. Returns a fresh
        loaded :class:`RunHandle` so chains like
        ``run.wait().download_to_dir(...)`` work without re-wrapping."""
        run = self._client._wait_for_run_raw(
            self.job_id,
            run_id=self.run_id,
            target_statuses=target_statuses,
            failure_statuses=failure_statuses,
            timeout=timeout,
            poll_interval=poll_interval,
            progress=progress,
        )
        return RunHandle(self._client, self.job_id, self.run_id, run)

    # ----- async results-zip export -----

    def start_export(self) -> "ExportRef":
        """``POST .../exports`` — kick off an async zip of this run's
        bodies. Returns an :class:`ExportRef` carrying the just-issued
        id; chain ``.wait()`` to block for completion."""
        return self._client.start_results_export(self.job_id, self.run_id)

    def export(self, export_id: str) -> "ExportRef":
        """Address a specific export of this run by id (no network call).
        Lazy — the first method call surfaces 404 if the id is wrong or
        TTL-swept."""
        return ExportRef(self._client, self.job_id, self.run_id, export_id)

    def download_all_results(
        self,
        target_path: str | Path,
        *,
        wait_timeout: float = 600.0,
        poll_interval: float = 2.0,
    ) -> Path:
        """Start an export of this run's results, wait for it, and save
        the zip to ``target_path``. See
        ``ZenRowsBatchClient.download_all_results`` for the contract.

        Capped at 1 GiB per run server-side; for larger runs use
        ``download_to_dir`` (no size limit, but slower — one body at a
        time, tunable via ``concurrency=``)."""
        return self._client.download_all_results(
            self.job_id,
            self.run_id,
            target_path,
            wait_timeout=wait_timeout,
            poll_interval=poll_interval,
        )


class RunHandle(RunRef):
    """A :class:`RunRef` plus a guaranteed, synchronous ``data`` snapshot."""

    def __init__(
        self,
        client: "ZenRowsBatchClient",
        job_id: str,
        run_id: str,
        data: Run,
    ):
        super().__init__(client, job_id, run_id)
        self.data = data

    def __repr__(self) -> str:
        t = self.data.stats
        return (
            f"<RunHandle job_id={self.job_id!r} run_id={self.run_id!r} "
            f"status={self.data.status.value!r} tasks={t.successful + t.failed}/{t.total}>"
        )

    @property
    def status(self) -> RunStatus:
        """This run's status. Shortcut for ``self.data.status``."""
        return self.data.status

    @property
    def stats(self) -> RunStats:
        """This run's task rollup (``total``, ``successful``, ``failed``,
        ``spend``, …). Shortcut for ``self.data.stats``."""
        return self.data.stats


# ============================ exports ============================


class ExportRef:
    """A reference to a results-export by id.

    A results export is async: ``start_export()`` returns immediately
    with a ``pending`` ref; the server zips the run in the background
    and it reaches ``completed`` (download URL ready) or ``failed``
    (with an ``error`` message — e.g. the 1 GiB size cap). Call
    ``load()`` / ``wait()`` for an :class:`ExportHandle` with data.
    """

    def __init__(
        self,
        client: "ZenRowsBatchClient",
        job_id: str,
        run_id: str,
        export_id: str,
        *,
        start_response: StartExportResponse | None = None,
    ):
        self._client = client
        self.job_id = job_id
        self.run_id = run_id
        self.export_id = export_id
        # Only set on the ref returned by start_export; carries the
        # initial status/timestamps without forcing a GET.
        self.start_response = start_response

    def __repr__(self) -> str:
        bits = [f"export_id={self.export_id!r}"]
        if self.start_response:
            bits.append(f"status={self.start_response.status.value!r}")
        return f"<{type(self).__name__} {' '.join(bits)}>"

    def load(self) -> "ExportHandle":
        """``GET .../exports/{id}`` — fetch the export and return a
        loaded :class:`ExportHandle`."""
        data = self._client._get_export(self.job_id, self.run_id, self.export_id)
        return ExportHandle(
            self._client,
            self.job_id,
            self.run_id,
            self.export_id,
            data,
            start_response=self.start_response,
        )

    # ----- waiter -----

    def wait(
        self,
        *,
        target_statuses: set[str] | frozenset[str] | None = None,
        timeout: float = 600.0,
        poll_interval: float = 2.0,
    ) -> "ExportHandle":
        """Block until the export reaches a terminal state (defaults to
        ``{completed, failed}``). Returns the loaded handle."""
        from zenrows.batch.client import TERMINAL_EXPORT_STATUSES

        data = self._client._wait_for_export_raw(
            self.job_id,
            self.run_id,
            self.export_id,
            target_statuses=target_statuses or TERMINAL_EXPORT_STATUSES,
            timeout=timeout,
            poll_interval=poll_interval,
        )
        return ExportHandle(
            self._client,
            self.job_id,
            self.run_id,
            self.export_id,
            data,
            start_response=self.start_response,
        )

    # ----- download -----

    def download_to_path(
        self,
        target_path: str | Path,
        *,
        chunk_size: int = 1 << 20,
    ) -> Path:
        """Stream the export zip to ``target_path``. The export must
        already be ``completed`` — call ``.wait()`` first or pair with
        ``client.download_all_results(...)`` for the one-shot flow.

        Fetches once for a fresh presigned URL (the server signs a new
        URL per request)."""
        data = self.load().data
        if data.status != ExportStatus.COMPLETED:
            raise BatchAPIError(
                status_code=0,
                problem=None,
                raw=(data.error if data.error else "export not completed").encode(),
            )
        if not data.download_url:
            raise BatchAPIError(
                status_code=0,
                problem=None,
                raw=b"export completed but server returned no download_url",
            )
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with (
            httpx.Client(timeout=httpx.Timeout(60.0)) as bare,
            bare.stream("GET", str(data.download_url)) as r,
        ):
            if r.status_code >= 400:
                raise BatchAPIError(r.status_code, problem=None, raw=r.read())
            with target.open("wb") as f:
                for chunk in r.iter_bytes(chunk_size):
                    f.write(chunk)
        return target


class ExportHandle(ExportRef):
    """An :class:`ExportRef` plus a guaranteed, synchronous ``data`` snapshot."""

    def __init__(
        self,
        client: "ZenRowsBatchClient",
        job_id: str,
        run_id: str,
        export_id: str,
        data: Export,
        *,
        start_response: StartExportResponse | None = None,
    ):
        super().__init__(client, job_id, run_id, export_id, start_response=start_response)
        self.data = data

    def __repr__(self) -> str:
        return f"<ExportHandle export_id={self.export_id!r} status={self.data.status.value!r}>"

    @property
    def status(self) -> ExportStatus:
        """This export's status. Shortcut for ``self.data.status``."""
        return self.data.status


# ===================== current-run facet =====================


class CurrentRun:
    """Operations on a job's **current run**, reached via ``job.run``.

    The pause / stop family only ever targets the latest run (the API's
    run-less endpoints resolve it server-side), which is why they live
    here and not on :class:`RunRef`. Minted lazily by the ``job.run``
    property; holds no snapshot of its own.
    """

    def __init__(self, client: "ZenRowsBatchClient", job_id: str):
        self._client = client
        self.job_id = job_id

    def __repr__(self) -> str:
        return f"<CurrentRun job_id={self.job_id!r}>"

    def _current_run_id(self) -> str:
        job = self._client._get_job_data(self.job_id)
        if not job.latest_run:
            raise ValueError(f"job {self.job_id!r} has no run yet")
        return job.latest_run.run_id

    def load(self) -> "RunHandle":
        """``GET /jobs/{id}`` → the latest run as a loaded
        :class:`RunHandle`."""
        job = self._client._get_job_data(self.job_id)
        if not job.latest_run:
            raise ValueError(f"job {self.job_id!r} has no run yet")
        return RunHandle(self._client, self.job_id, job.latest_run.run_id, job.latest_run)

    # ----- lifecycle -----

    def pause(self) -> "RunHandle":
        """``POST /jobs/{id}/pause`` — reversibly suspend the current
        run: the dispatcher stops pulling its queue (in-flight tasks may
        still settle), setting ``latest_run.pause_state = paused``.
        Orthogonal to ``status``; undo with ``resume()``. Returns the
        fresh loaded :class:`RunHandle`."""
        run = self._client._post_pause(self.job_id)
        return RunHandle(self._client, self.job_id, run.run_id, run)

    def resume(self) -> "RunHandle":
        """``POST /jobs/{id}/resume`` — un-pause the current run (the
        dispatcher resumes polling). Returns the fresh loaded handle."""
        run = self._client._post_resume(self.job_id)
        return RunHandle(self._client, self.job_id, run.run_id, run)

    def stop(self) -> "RunHandle":
        """``POST /jobs/{id}/stop`` — terminally stop the current run.
        Returns the fresh loaded :class:`RunHandle`."""
        run = self._client._post_stop(self.job_id)
        return RunHandle(self._client, self.job_id, run.run_id, run)

    def cancel(self) -> "RunHandle":
        """Alias for :meth:`stop`."""
        return self.stop()

    # ----- waiter -----

    def wait(
        self,
        *,
        target_statuses: set[str] | frozenset[str] | None = None,
        failure_statuses: set[str] | frozenset[str] | None = None,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
        progress: bool = False,
    ) -> "RunHandle":
        """Block until the current run reaches a target state. Returns a
        loaded :class:`RunHandle`, so chains like
        ``job.run.wait().download_to_dir(...)`` work cleanly.

        See ``ZenRowsBatchClient.wait_for_run`` for the full contract."""
        run = self._client._wait_for_run_raw(
            self.job_id,
            run_id=None,
            target_statuses=target_statuses,
            failure_statuses=failure_statuses,
            timeout=timeout,
            poll_interval=poll_interval,
            progress=progress,
        )
        return RunHandle(self._client, self.job_id, run.run_id, run)

    # ----- results / content -----

    def results(self, *, status: str | None = None) -> Iterator[TaskResult]:
        """Auto-paginate task results from the current run."""
        return self._client._iter_results_raw(self.job_id, status=status)

    def task_history(self, task_id: str) -> TaskHistoryResponse:
        """Current-run per-attempt event log for one task."""
        return self._client._get_task_history_raw(self.job_id, task_id)

    # ----- bulk download -----

    def download_to_dir(
        self,
        target_dir: str | Path,
        *,
        status: str | None = "successful",
        name_fn: Callable[[TaskResult], str] | None = None,
        use_external_id: bool = False,
        concurrency: int = 1,
        progress: bool = False,
        max_files: int | None = None,
        max_bytes_per_file: int | None = None,
    ) -> int:
        return self._client._download_dir(
            self.job_id,
            None,
            target_dir,
            status=status,
            name_fn=name_fn,
            use_external_id=use_external_id,
            concurrency=concurrency,
            progress=progress,
            max_files=max_files,
            max_bytes_per_file=max_bytes_per_file,
        )

    def download_to_memory(
        self,
        *,
        status: str | None = "successful",
        concurrency: int = 1,
        progress: bool = False,
        max_count: int | None = None,
        max_total_bytes: int | None = None,
        max_bytes_per_file: int | None = None,
    ) -> "list[DownloadedResult]":
        return self._client._download_memory(
            self.job_id,
            None,
            status=status,
            concurrency=concurrency,
            progress=progress,
            max_count=max_count,
            max_total_bytes=max_total_bytes,
            max_bytes_per_file=max_bytes_per_file,
        )

    # ----- single-task download -----

    def download_task_to_file(self, task: TaskResult, target: "str | Path | IO[bytes]") -> None:
        """Download one ``task``'s body straight from its presigned
        ``result_url`` to ``target`` — a path (``str`` / ``Path``) or an
        open binary file object. The per-task counterpart of
        ``download_to_dir`` — handy inside a ``results()`` loop when a
        Python-side filter picks which bodies to keep."""
        _download_task_to_file(task, target)

    def download_task_to_memory(self, task: TaskResult) -> bytes:
        """Download one ``task``'s body straight from its presigned
        ``result_url`` and return the raw bytes."""
        return _download_task_to_memory(task)

    # ----- async results-zip export -----

    def start_export(self) -> "ExportRef":
        """``POST .../exports`` — kick off an async zip of the current
        run's bodies. Resolves the current run id first (one GET), then
        returns an :class:`ExportRef`."""
        return self._client.start_results_export(self.job_id, self._current_run_id())

    def download_all_results(
        self,
        target_path: str | Path,
        *,
        wait_timeout: float = 600.0,
        poll_interval: float = 2.0,
    ) -> Path:
        """Start an export of the current run's results, wait for it,
        and save the zip to ``target_path``. See
        ``ZenRowsBatchClient.download_all_results`` for the contract.

        Capped at 1 GiB per run server-side; for larger runs use
        ``download_to_dir`` (no size limit, but slower — one body at a
        time, tunable via ``concurrency=``)."""
        return self._client.download_all_results(
            self.job_id,
            self._current_run_id(),
            target_path,
            wait_timeout=wait_timeout,
            poll_interval=poll_interval,
        )


# ===================== schedule facet =====================


class ScheduleControls:
    """Operations on a scheduled job's schedule, reached via
    ``job.schedule``. Scheduled jobs only — regular jobs 409."""

    def __init__(self, client: "ZenRowsBatchClient", job_id: str):
        self._client = client
        self.job_id = job_id

    def __repr__(self) -> str:
        return f"<ScheduleControls job_id={self.job_id!r}>"

    def pause(self) -> "JobHandle":
        """``POST /jobs/{id}/schedule/state`` → ``paused`` — skip future
        scheduled fires (an in-flight run keeps running). The schedule
        keeps ticking server-side but fires are dropped until
        ``resume()``. Idempotent; returns the fresh loaded handle."""
        return JobHandle(
            self._client, self.job_id, self._client._post_schedule_state(self.job_id, "paused")
        )

    def resume(self) -> "JobHandle":
        """``POST /jobs/{id}/schedule/state`` → ``active`` — re-enable
        scheduled fires on a paused job. Idempotent; returns the fresh
        loaded handle."""
        return JobHandle(
            self._client, self.job_id, self._client._post_schedule_state(self.job_id, "active")
        )

    def update(self, schedule: "Schedule | JobScheduleDict") -> "JobHandle":
        """``PUT /jobs/{id}/schedule`` — replace the schedule. Accepts a
        typed builder (``At`` / ``Rate`` / ``Calendar``) or a raw
        ``JobScheduleDict``, same as ``submit_scheduled``.

        An in-flight run keeps running; the new schedule governs only
        future fires. Returns the fresh loaded handle."""
        resolved = self._client._resolve_schedule(schedule)
        return JobHandle(
            self._client, self.job_id, self._client._put_schedule(self.job_id, resolved)
        )


# ============================ jobs ============================


class JobRef:
    """A reference to a job by id — job-template operations, plus the
    ``run`` and ``schedule`` sub-facets.

    Minted with **no network call** by ``client.job(id)`` and returned
    by the ``submit_*`` methods (with ``submit_response`` attached).
    Call ``load()`` for a :class:`JobHandle` with data. Construction is
    handled by the SDK — callers never build these directly.
    """

    def __init__(
        self,
        client: "ZenRowsBatchClient",
        job_id: str,
        *,
        submit_response: SubmitJobResponse | None = None,
    ):
        self._client = client
        self.job_id = job_id
        # Populated only when the ref was returned by a submit_* call;
        # exposes accepted_tasks + the initial status/latest_run without
        # forcing a follow-up GET.
        self.submit_response = submit_response
        self._run_facet: CurrentRun | None = None
        self._schedule_facet: ScheduleControls | None = None

    def __repr__(self) -> str:
        bits = [f"job_id={self.job_id!r}"]
        if self.submit_response:
            bits.append(f"status={self.submit_response.status.value!r}")
            bits.append(f"accepted={self.submit_response.accepted_tasks}")
        return f"<{type(self).__name__} {' '.join(bits)}>"

    # ----- sub-facets -----

    @property
    def run(self) -> CurrentRun:
        """Operations on the **current run** — ``pause`` / ``resume`` /
        ``stop`` / ``wait`` / ``results`` / downloads / ``start_export``."""
        if self._run_facet is None:
            self._run_facet = CurrentRun(self._client, self.job_id)
        return self._run_facet

    @property
    def schedule(self) -> ScheduleControls:
        """Operations on the **schedule** — ``pause`` / ``resume`` /
        ``update`` (scheduled jobs only)."""
        if self._schedule_facet is None:
            self._schedule_facet = ScheduleControls(self._client, self.job_id)
        return self._schedule_facet

    # ----- snapshot accessors (submit-response backed) -----

    @property
    def status(self) -> JobStatus | None:
        """The job status from the submit response. Only known on refs
        returned by a ``submit_*`` call (no network); ``None`` otherwise
        — ``load()`` for a :class:`JobHandle` whose ``.status`` reads the
        fetched ``.data``."""
        return self.submit_response.status if self.submit_response else None

    @property
    def accepted_tasks(self) -> int | None:
        """How many tasks landed at submit. Only known on refs returned
        by a ``submit_*`` call; returns ``None`` otherwise."""
        return self.submit_response.accepted_tasks if self.submit_response else None

    def load(self) -> "JobHandle":
        """``GET /jobs/{id}`` — fetch the full job and return a loaded
        :class:`JobHandle` whose ``.data`` is ready."""
        return JobHandle(self._client, self.job_id, self._client._get_job_data(self.job_id))

    # ----- lifecycle -----

    def close(self) -> "JobHandle":
        """``POST /jobs/{id}/close`` — lock the job (no more ``add_tasks``).
        Returns the fresh loaded handle."""
        return JobHandle(self._client, self.job_id, self._client._post_close(self.job_id))

    def delete(self) -> None:
        """``DELETE /jobs/{id}`` — async hard delete."""
        self._client._delete(self.job_id)

    def rerun(
        self,
        *,
        status: str | list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> "RunHandle":
        """``POST /jobs/{id}/rerun[?status=...]`` — start a new run.

        Without ``status``: full rerun of the previous run's tasks
        (or, for a scheduled job with no prior run, a manual fire
        from the template). No source-child link.

        With ``status`` (single value like ``"failed"`` or list like
        ``["failed", "pending"]``): partial retry — matching statuses
        are reset to ``pending`` and re-enqueued; everything else is
        inherited verbatim with ``source_run_id`` stamped.

        Returns a :class:`RunHandle` for the newly-created run."""
        resp: RerunJobResponse = self._client._post_rerun(
            self.job_id, status=status, idempotency_key=idempotency_key
        )
        return RunHandle(self._client, self.job_id, resp.latest_run.run_id, resp.latest_run)

    def retry_failed(
        self,
        *,
        include_pending: bool = False,
        idempotency_key: str | None = None,
    ) -> "RunHandle":
        """Start a new run that re-executes only the previous run's
        **failed** tasks (partial retry). Successful tasks
        are inherited verbatim, so the new run's totals already carry
        the prior successes — you only pay to re-scrape what failed.

        Shortcut for ``rerun(status="failed")``. Set
        ``include_pending=True`` to also re-enqueue tasks that never
        started (``status="failed,pending"``) — the usual move after a
        ``stop()`` left orphan ``pending`` rows.

        Returns a :class:`RunHandle` for the new run. Requires the
        previous run to be terminal (``completed`` / ``stopped``); raises
        ``BatchAPIError`` (409 ``run_not_terminal``) otherwise, and
        (409 ``no_matching_tasks``) when nothing matched the filter.
        """
        statuses = ["failed", "pending"] if include_pending else "failed"
        return self.rerun(status=statuses, idempotency_key=idempotency_key)

    # ----- tasks (write path) -----

    def add_tasks(self, body: AddTasksRequest | AddTasksDict) -> AddTasksResponse:
        """``POST /jobs/{id}/tasks`` — append to the open initial run."""
        return self._client._post_tasks(self.job_id, body)

    # ----- runs (list) -----

    def runs(self, *, page_size: int | None = None) -> Iterator["RunHandle"]:
        """Auto-paginate runs of this job, yielding :class:`RunHandle`s.
        To address a *specific* run by id, use
        ``client.run(job_id, run_id)``; for the current run, ``job.run``."""
        for run in self._client._iter_runs_raw(self.job_id, page_size=page_size):
            yield RunHandle(self._client, self.job_id, run.run_id, run)

    # ----- file inputs -----

    def add_file_input(
        self,
        source: str | Path | IO[bytes],
        *,
        fields: dict[str, int | str],
        header: bool = False,
        delimiter: str = ",",
        quote: str = '"',
    ) -> str:
        """Upload a CSV that *this job* would consume on a future
        submission. Returns the ``file_input_id``. (File inputs are
        not tied to a job at create-time, but living off the handle
        keeps the call site discoverable.)"""
        return self._client.upload_csv(
            source,
            fields=fields,
            header=header,
            delimiter=delimiter,
            quote=quote,
        )

    # ----- webhooks -----

    def get_webhook(self) -> WebhookConfig:
        """`GET /jobs/{id}/webhook` — this job's current webhook config.
        Raises `BatchAPIError` (404) when none is set."""
        return self._client.get_job_webhook(self.job_id)

    def set_webhook(self, url: str, *, signature: bool) -> WebhookConfig:
        """`PUT /jobs/{id}/webhook` — replace this job's webhook config.
        Both fields are required (no defaulting, so you can't silently
        toggle signing); pass `signature=False` explicitly for an
        unsigned receiver. Returns the persisted config."""
        return self._client.put_job_webhook(self.job_id, {"url": url, "signature": signature})

    def delete_webhook(self) -> None:
        """`DELETE /jobs/{id}/webhook` — clear this job's webhook config.
        Idempotent."""
        self._client.delete_job_webhook(self.job_id)

    # ----- ingest waiter -----

    def wait_for_ingest(
        self,
        *,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
        max_poll_interval: float = 15.0,
    ) -> "JobHandle":
        """Block until the current run's async-carrier ingestion has
        finished writing task rows.

        Large submissions return ``202 Accepted`` and stream task rows
        into storage off the request path; until that finishes,
        results pages may be partial and ``add_tasks`` on an open job
        is rejected with 409. This polls ``GET /jobs/{id}`` until
        ``latest_run.ingest_status`` leaves ``pending`` (also satisfied
        by a terminal run — a mid-ingest stop flips the field to
        ``done``). Runs that never ingested asynchronously (any 201
        submission) are done on the first poll.

        Raises ``WaiterTimeout`` after ``timeout`` seconds. Returns the
        fresh loaded handle, so chains like
        ``job.wait_for_ingest().add_tasks(...)`` work. Or opt in at
        submit time via ``submit_job(..., wait_for_ingest=True)``."""
        return JobHandle(
            self._client,
            self.job_id,
            self._client._wait_for_ingest_raw(
                self.job_id,
                timeout=timeout,
                poll_interval=poll_interval,
                max_poll_interval=max_poll_interval,
            ),
        )


class JobHandle(JobRef):
    """A :class:`JobRef` plus a guaranteed, synchronous ``data`` snapshot.

    Returned by ``get_job`` / ``iter_jobs`` and the ops that echo fresh
    state (``close``, ``job.schedule.pause``, …). Inherits the ``run``
    and ``schedule`` facets from :class:`JobRef`.
    """

    def __init__(
        self,
        client: "ZenRowsBatchClient",
        job_id: str,
        data: Job,
        *,
        submit_response: SubmitJobResponse | None = None,
    ):
        super().__init__(client, job_id, submit_response=submit_response)
        self.data = data

    def __repr__(self) -> str:
        bits = [f"job_id={self.job_id!r}", f"status={self.data.status.value!r}"]
        if self.data.latest_run:
            t = self.data.latest_run.stats
            bits.append(f"tasks={t.successful + t.failed}/{t.total}")
        return f"<JobHandle {' '.join(bits)}>"

    @property
    def status(self) -> JobStatus:
        """This job's status. Shortcut for ``self.data.status``."""
        return self.data.status


__all__ = [
    "CurrentRun",
    "ExportHandle",
    "ExportRef",
    "JobHandle",
    "JobRef",
    "RunHandle",
    "RunRef",
    "ScheduleControls",
]
