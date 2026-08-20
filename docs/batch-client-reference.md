# ZenRows Batch — Python SDK Reference

_Auto-generated from the SDK docstrings via `make docs`. Do not edit by hand. Everything below is imported from the top-level `zenrows.batch` package (`from zenrows.batch import ...`)._

# Client

ZenRowsBatchClient — the friendly, typed facade.

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

<a id="ZenRowsBatchClient"></a>

## ZenRowsBatchClient Objects

```python
class ZenRowsBatchClient()
```

Synchronous, typed client for the ZenRows Batch API.

`api_key` is required. Most users read it from a secrets store or
environment variable at the call site:

    client = ZenRowsBatchClient(api_key=os.environ["ZENROWS_API_KEY"])

Every non-2xx raises `BatchAPIError`; `BatchAPIError.code` carries
the stable `code` from the Problem body.

<a id="ZenRowsBatchClient.__init__"></a>

#### \_\_init\_\_

```python
def __init__(api_key: str,
             *,
             base_url: str | None = None,
             timeout: float | httpx.Timeout = 30.0,
             retries: int = 3,
             verify_ssl: bool | str = True,
             user_agent: str = DEFAULT_USER_AGENT,
             httpx_args: dict[str, Any] | None = None)
```

`retries` bounds automatic retries of transient failures
(HTTP 429/502/503/504 and network errors) on idempotent
requests — GET/PUT/DELETE and POSTs carrying an
`Idempotency-Key`. Retries use jittered exponential backoff and
honor `Retry-After`; set `retries=0` to disable.

<a id="ZenRowsBatchClient.submit_job"></a>

#### submit\_job

```python
def submit_job(body: SubmitJobRequest | SubmitJobDict,
               *,
               idempotency_key: str | None = None,
               wait_for_ingest: bool = False) -> JobRef
```

`POST /jobs` — submit a new scraping job. The general,
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
for anyway.

<a id="ZenRowsBatchClient.submit_regular"></a>

#### submit\_regular

```python
def submit_regular(urls: list[str | TaskInputDict] | None = None,
                   *,
                   file_input_id: str | None = None,
                   zenrows_params: ParamMap | None = None,
                   external_id: str | None = None,
                   name: str | None = None,
                   metadata: dict[str, str] | None = None,
                   webhook: WebhookDict | None = None,
                   idempotency_key: str | None = None,
                   wait_for_ingest: bool = False) -> JobRef
```

Submit a one-shot scraping job (closed, all tasks upfront).

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

<a id="ZenRowsBatchClient.submit_open"></a>

#### submit\_open

```python
def submit_open(urls: list[str | TaskInputDict] | None = None,
                *,
                zenrows_params: ParamMap | None = None,
                external_id: str | None = None,
                name: str | None = None,
                metadata: dict[str, str] | None = None,
                webhook: WebhookDict | None = None,
                idempotency_key: str | None = None,
                wait_for_ingest: bool = False) -> JobRef
```

Submit a streaming-style job that stays open for more tasks.

Created with `status=open` — `urls` can be empty (or omitted)
and tasks are added later via `JobRef.add_tasks`. Close
the job with `job.close()` once you're done; until then the
run keeps running and accepting new work.

File-input upload is not supported here (the server only
accepts CSV inputs for `closed` regular jobs and scheduled
jobs).

<a id="ZenRowsBatchClient.submit_scheduled"></a>

#### submit\_scheduled

```python
def submit_scheduled(schedule: Schedule | JobScheduleDict,
                     urls: list[str | TaskInputDict] | None = None,
                     *,
                     file_input_id: str | None = None,
                     zenrows_params: ParamMap | None = None,
                     external_id: str | None = None,
                     name: str | None = None,
                     metadata: dict[str, str] | None = None,
                     webhook: WebhookDict | None = None,
                     idempotency_key: str | None = None) -> JobRef
```

Submit a scheduled job.

`schedule` is one of the typed builders (`At`, `Rate`,
`Calendar`) or a raw `JobScheduleDict` for power users. The
typed builders validate their inputs in `__post_init__`;
the dict form is a passthrough — the server validates.

**Examples**:

  
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

<a id="ZenRowsBatchClient.get_job"></a>

#### get\_job

```python
def get_job(job_id: str) -> JobHandle
```

`GET /jobs/{job_id}` — returns a loaded `JobHandle` with
`.data` already populated.

**Want the full `Job` in one line?** This is it —
`client.get_job(job_id).data` gives you the complete pydantic
`Job`. It's the eager shortcut for `client.job(job_id).load()`
(same single GET, same handle); reach for the bare
`client.job(job_id)` ref only when you want to *act* on the id
without fetching it.

<a id="ZenRowsBatchClient.job"></a>

#### job

```python
def job(job_id: str) -> JobRef
```

A `JobRef` for an existing job with **no network call**.
Lifecycle operations act on the id directly (`delete`, `close`,
`rerun`, `add_tasks`); current-run and schedule ops live on the
`.run` / `.schedule` facets; call `.load()` for a `JobHandle`
with `.data`. Prefer this over `get_job` when you just want to
act on a known id without fetching it first::

    client.job(job_id).delete()            # no round-trip
    client.job(job_id).run.stop()          # POST /jobs/{id}/stop
    client.job(job_id).schedule.pause()    # skip future fires
    client.job(job_id).load().data.status  # explicit GET

<a id="ZenRowsBatchClient.estimate_cost"></a>

#### estimate\_cost

```python
def estimate_cost(body: SubmitJobRequest | SubmitJobDict) -> CostEstimate
```

Estimate the credit cost of a job before submitting it,
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

<a id="ZenRowsBatchClient.list_jobs"></a>

#### list\_jobs

```python
def list_jobs(*,
              limit: int | None = None,
              cursor: str | None = None,
              job_type: JobType | str | None = None,
              status: JobStatus | str | None = None) -> ListJobsResponse
```

`GET /jobs` — raw page (with `next_cursor`). For most uses
prefer the auto-paginating `iter_jobs`.

<a id="ZenRowsBatchClient.iter_jobs"></a>

#### iter\_jobs

```python
def iter_jobs(*,
              job_type: JobType | str | None = None,
              status: JobStatus | str | None = None,
              page_size: int | None = None) -> Iterator[JobHandle]
```

Auto-paginate `list_jobs`, yielding loaded `JobHandle`s with
their `.data` pre-populated from the page.

<a id="ZenRowsBatchClient.get_run"></a>

#### get\_run

```python
def get_run(job_id: str, *, run_id: str) -> RunHandle
```

`GET /jobs/{job_id}/runs/{run_id}` — returns a loaded
`RunHandle` with `.data` populated.

<a id="ZenRowsBatchClient.run"></a>

#### run

```python
def run(job_id: str, run_id: str) -> RunRef
```

A `RunRef` for an existing run with **no network call** — the
run counterpart of `job`. Acts on `(job_id, run_id)` directly
(`delete`, `results`, `wait`); call `.load()` for a `RunHandle`
with `.data`::

    client.run(job_id, run_id).delete()   # scrub one run, no GET

<a id="ZenRowsBatchClient.list_runs"></a>

#### list\_runs

```python
def list_runs(job_id: str,
              *,
              limit: int | None = None,
              cursor: str | None = None) -> ListJobRunsResponse
```

`GET /jobs/{job_id}/runs` — raw page. For most uses prefer
the auto-paginating `JobRef.runs()`.

<a id="ZenRowsBatchClient.iter_runs"></a>

#### iter\_runs

```python
def iter_runs(job_id: str,
              *,
              page_size: int | None = None) -> Iterator[RunHandle]
```

Auto-paginate runs of a job, yielding loaded `RunHandle`s.

<a id="ZenRowsBatchClient.list_results"></a>

#### list\_results

```python
def list_results(job_id: str,
                 *,
                 run_id: str | None = None,
                 status: str | None = None,
                 cursor: str | None = None) -> ListResultsResponse
```

Raw results page. Prefer `iter_results` or
`JobRef.results()` for auto-pagination.

<a id="ZenRowsBatchClient.iter_results"></a>

#### iter\_results

```python
def iter_results(job_id: str,
                 *,
                 run_id: str | None = None,
                 status: str | None = None) -> Iterator[TaskResult]
```

Auto-paginate results, yielding `TaskResult` per row.

<a id="ZenRowsBatchClient.wait_for_run"></a>

#### wait\_for\_run

```python
def wait_for_run(job_id: str,
                 *,
                 run_id: str | None = None,
                 target_statuses: set[str]
                 | frozenset[str] = TERMINAL_RUN_STATUSES,
                 failure_statuses: set[str] | frozenset[str] | None = None,
                 timeout: float = 300.0,
                 poll_interval: float = 2.0,
                 max_poll_interval: float = 15.0,
                 progress: bool = False) -> Run
```

Block until a run reaches one of `target_statuses`, polling
with jittered exponential backoff.

`progress=True` shows a tqdm bar with totals as they advance.
`None` inherits the client-level `progress` setting (which
itself defaults to off unless `ZENROWS_BATCH_PROGRESS=true`).

<a id="ZenRowsBatchClient.create_job_input"></a>

#### create\_job\_input

```python
def create_job_input(
    body: CreateJobInputRequest | CreateJobInputDict
) -> CreateJobInputResponse
```

`POST /job_inputs` — allocate a CSV upload slot. Most
callers want the higher-level `upload_csv` instead.

<a id="ZenRowsBatchClient.upload_csv"></a>

#### upload\_csv

```python
def upload_csv(source: str | Path | IO[bytes],
               *,
               fields: dict[str, int | str],
               header: bool = False,
               delimiter: str = ",",
               quote: str = '"') -> str
```

Allocate a CSV slot + PUT the body. Returns the
`file_input_id` you then pass to `submit_job(...)`.

<a id="ZenRowsBatchClient.get_job_webhook"></a>

#### get\_job\_webhook

```python
def get_job_webhook(job_id: str) -> WebhookConfig
```

`GET /jobs/{job_id}/webhook` — the job's current webhook
config. Raises `BatchAPIError` (404) when none is set.

<a id="ZenRowsBatchClient.put_job_webhook"></a>

#### put\_job\_webhook

```python
def put_job_webhook(job_id: str,
                    config: WebhookConfig | WebhookDict) -> WebhookConfig
```

`PUT /jobs/{job_id}/webhook` — replace the webhook config
wholesale. Both `url` and `signature` are required (no
defaulting at the mutate boundary, so a partial update can't
silently toggle signing). Returns the persisted config.

<a id="ZenRowsBatchClient.delete_job_webhook"></a>

#### delete\_job\_webhook

```python
def delete_job_webhook(job_id: str) -> None
```

`DELETE /jobs/{job_id}/webhook` — clear the webhook config.
Idempotent: 204 whether or not one was set.

<a id="ZenRowsBatchClient.test_webhook"></a>

#### test\_webhook

```python
def test_webhook(
        config: TestWebhookRequest | WebhookDict) -> TestWebhookResponse
```

`POST /webhook/test` — dispatch a synthetic `webhook.test`
event to a receiver URL and report the outcome, without touching
any job. Handy to verify a receiver before you wire it to a job.

`signature` defaults to `false`; set it `true` to exercise the
HMAC path (requires an active signing key, else 400
`webhook_signing_requires_active_key`).

<a id="ZenRowsBatchClient.rotate_hmac_key"></a>

#### rotate\_hmac\_key

```python
def rotate_hmac_key() -> HMACKeyCreated
```

Capture the returned `secret` HERE — it is not revealed again.

<a id="ZenRowsBatchClient.start_results_export"></a>

#### start\_results\_export

```python
def start_results_export(job_id: str, run_id: str) -> ExportRef
```

`POST /jobs/{job_id}/runs/{run_id}/exports` — start an async
zip of every task body in the run. Returns an `ExportRef`
carrying the just-issued `export_id`; call `.load()` or block on
`.wait()` for the download URL.

Failure modes: 404 if the job/run is missing or not yours.
The worker fails the export (status=failed,
error="results are larger then 1 gb") if the pre-zip total
exceeds the server's 1 GiB cap.

<a id="ZenRowsBatchClient.get_results_export"></a>

#### get\_results\_export

```python
def get_results_export(job_id: str, run_id: str,
                       export_id: str) -> ExportHandle
```

`GET /jobs/{job_id}/runs/{run_id}/exports/{export_id}` —
snapshot one export. Returns a loaded `ExportHandle` with `.data`
pre-populated. 404 covers both "no such id" and "TTL-swept".

<a id="ZenRowsBatchClient.wait_for_export"></a>

#### wait\_for\_export

```python
def wait_for_export(job_id: str,
                    run_id: str,
                    export_id: str,
                    *,
                    target_statuses: set[str]
                    | frozenset[str] = TERMINAL_EXPORT_STATUSES,
                    timeout: float = 600.0,
                    poll_interval: float = 2.0,
                    max_poll_interval: float = 15.0) -> Export
```

Block until an export reaches a terminal state. Defaults to
`{completed, failed}`. Returns the `Export` snapshot — callers
check `.status` and (on `completed`) `.download_url`.

`failed` is NOT raised; the caller decides whether
`error="results are larger then 1 gb"` is fatal or expected.

<a id="ZenRowsBatchClient.download_all_results"></a>

#### download\_all\_results

```python
def download_all_results(job_id: str,
                         run_id: str,
                         target_path: str | Path,
                         *,
                         wait_timeout: float = 600.0,
                         poll_interval: float = 2.0,
                         chunk_size: int = 1 << 20) -> Path
```

End-to-end helper: start an export, wait for it, and save
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

# Job, run & export handles

Resource handles for the Batch SDK — a two-tier (typestate) design
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

<a id="RunRef"></a>

## RunRef Objects

```python
class RunRef()
```

A reference to a **specific** run by ``(job_id, run_id)``.

Read/download operations on one (usually historical) run. No
``pause`` / ``stop``: the API only suspends or stops the *current*
run (see ``job.run`` — the :class:`CurrentRun` facet). Call
``load()`` for a :class:`RunHandle` with data.

<a id="RunRef.load"></a>

#### load

```python
def load() -> "RunHandle"
```

``GET /jobs/{id}/runs/{run_id}`` — fetch the run and return a
loaded :class:`RunHandle`.

<a id="RunRef.delete"></a>

#### delete

```python
def delete() -> None
```

``DELETE /jobs/{id}/runs/{run_id}`` — scrub one run only.

<a id="RunRef.download_task_to_file"></a>

#### download\_task\_to\_file

```python
def download_task_to_file(task: TaskResult,
                          target: "str | Path | IO[bytes]") -> None
```

Download one ``task``'s body straight from its presigned
``result_url`` to ``target`` — a path (``str`` / ``Path``) or an
open binary file object.

<a id="RunRef.download_task_to_memory"></a>

#### download\_task\_to\_memory

```python
def download_task_to_memory(task: TaskResult) -> bytes
```

Download one ``task``'s body straight from its presigned
``result_url`` and return the raw bytes.

<a id="RunRef.wait"></a>

#### wait

```python
def wait(*,
         target_statuses: set[str] | frozenset[str] | None = None,
         failure_statuses: set[str] | frozenset[str] | None = None,
         timeout: float = 300.0,
         poll_interval: float = 2.0,
         progress: bool = False) -> "RunHandle"
```

Block until this run reaches a target state. Returns a fresh
loaded :class:`RunHandle` so chains like
``run.wait().download_to_dir(...)`` work without re-wrapping.

<a id="RunRef.start_export"></a>

#### start\_export

```python
def start_export() -> "ExportRef"
```

``POST .../exports`` — kick off an async zip of this run's
bodies. Returns an :class:`ExportRef` carrying the just-issued
id; chain ``.wait()`` to block for completion.

<a id="RunRef.export"></a>

#### export

```python
def export(export_id: str) -> "ExportRef"
```

Address a specific export of this run by id (no network call).
Lazy — the first method call surfaces 404 if the id is wrong or
TTL-swept.

<a id="RunRef.download_all_results"></a>

#### download\_all\_results

```python
def download_all_results(target_path: str | Path,
                         *,
                         wait_timeout: float = 600.0,
                         poll_interval: float = 2.0) -> Path
```

Start an export of this run's results, wait for it, and save
the zip to ``target_path``. See
``ZenRowsBatchClient.download_all_results`` for the contract.

Capped at 1 GiB per run server-side; for larger runs use
``download_to_dir`` (no size limit, but slower — one body at a
time, tunable via ``concurrency=``).

<a id="RunHandle"></a>

## RunHandle Objects

```python
class RunHandle(RunRef)
```

A :class:`RunRef` plus a guaranteed, synchronous ``data`` snapshot.

<a id="RunHandle.status"></a>

#### status

```python
@property
def status() -> RunStatus
```

This run's status. Shortcut for ``self.data.status``.

<a id="RunHandle.stats"></a>

#### stats

```python
@property
def stats() -> RunStats
```

This run's task rollup (``total``, ``successful``, ``failed``,
``spend``, …). Shortcut for ``self.data.stats``.

<a id="ExportRef"></a>

## ExportRef Objects

```python
class ExportRef()
```

A reference to a results-export by id.

A results export is async: ``start_export()`` returns immediately
with a ``pending`` ref; the server zips the run in the background
and it reaches ``completed`` (download URL ready) or ``failed``
(with an ``error`` message — e.g. the 1 GiB size cap). Call
``load()`` / ``wait()`` for an :class:`ExportHandle` with data.

<a id="ExportRef.load"></a>

#### load

```python
def load() -> "ExportHandle"
```

``GET .../exports/{id}`` — fetch the export and return a
loaded :class:`ExportHandle`.

<a id="ExportRef.wait"></a>

#### wait

```python
def wait(*,
         target_statuses: set[str] | frozenset[str] | None = None,
         timeout: float = 600.0,
         poll_interval: float = 2.0) -> "ExportHandle"
```

Block until the export reaches a terminal state (defaults to
``{completed, failed}``). Returns the loaded handle.

<a id="ExportRef.download_to_path"></a>

#### download\_to\_path

```python
def download_to_path(target_path: str | Path,
                     *,
                     chunk_size: int = 1 << 20) -> Path
```

Stream the export zip to ``target_path``. The export must
already be ``completed`` — call ``.wait()`` first or pair with
``client.download_all_results(...)`` for the one-shot flow.

Fetches once for a fresh presigned URL (the server signs a new
URL per request).

<a id="ExportHandle"></a>

## ExportHandle Objects

```python
class ExportHandle(ExportRef)
```

An :class:`ExportRef` plus a guaranteed, synchronous ``data`` snapshot.

<a id="ExportHandle.status"></a>

#### status

```python
@property
def status() -> ExportStatus
```

This export's status. Shortcut for ``self.data.status``.

<a id="CurrentRun"></a>

## CurrentRun Objects

```python
class CurrentRun()
```

Operations on a job's **current run**, reached via ``job.run``.

The pause / stop family only ever targets the latest run (the API's
run-less endpoints resolve it server-side), which is why they live
here and not on :class:`RunRef`. Minted lazily by the ``job.run``
property; holds no snapshot of its own.

<a id="CurrentRun.load"></a>

#### load

```python
def load() -> "RunHandle"
```

``GET /jobs/{id}`` → the latest run as a loaded
:class:`RunHandle`.

<a id="CurrentRun.pause"></a>

#### pause

```python
def pause() -> "RunHandle"
```

``POST /jobs/{id}/pause`` — reversibly suspend the current
run: the dispatcher stops pulling its queue (in-flight tasks may
still settle), setting ``latest_run.pause_state = paused``.
Orthogonal to ``status``; undo with ``resume()``. Returns the
fresh loaded :class:`RunHandle`.

<a id="CurrentRun.resume"></a>

#### resume

```python
def resume() -> "RunHandle"
```

``POST /jobs/{id}/resume`` — un-pause the current run (the
dispatcher resumes polling). Returns the fresh loaded handle.

<a id="CurrentRun.stop"></a>

#### stop

```python
def stop() -> "RunHandle"
```

``POST /jobs/{id}/stop`` — terminally stop the current run.
Returns the fresh loaded :class:`RunHandle`.

<a id="CurrentRun.cancel"></a>

#### cancel

```python
def cancel() -> "RunHandle"
```

Alias for :meth:`stop`.

<a id="CurrentRun.wait"></a>

#### wait

```python
def wait(*,
         target_statuses: set[str] | frozenset[str] | None = None,
         failure_statuses: set[str] | frozenset[str] | None = None,
         timeout: float = 300.0,
         poll_interval: float = 2.0,
         progress: bool = False) -> "RunHandle"
```

Block until the current run reaches a target state. Returns a
loaded :class:`RunHandle`, so chains like
``job.run.wait().download_to_dir(...)`` work cleanly.

See ``ZenRowsBatchClient.wait_for_run`` for the full contract.

<a id="CurrentRun.results"></a>

#### results

```python
def results(*, status: str | None = None) -> Iterator[TaskResult]
```

Auto-paginate task results from the current run.

<a id="CurrentRun.task_history"></a>

#### task\_history

```python
def task_history(task_id: str) -> TaskHistoryResponse
```

Current-run per-attempt event log for one task.

<a id="CurrentRun.download_task_to_file"></a>

#### download\_task\_to\_file

```python
def download_task_to_file(task: TaskResult,
                          target: "str | Path | IO[bytes]") -> None
```

Download one ``task``'s body straight from its presigned
``result_url`` to ``target`` — a path (``str`` / ``Path``) or an
open binary file object. The per-task counterpart of
``download_to_dir`` — handy inside a ``results()`` loop when a
Python-side filter picks which bodies to keep.

<a id="CurrentRun.download_task_to_memory"></a>

#### download\_task\_to\_memory

```python
def download_task_to_memory(task: TaskResult) -> bytes
```

Download one ``task``'s body straight from its presigned
``result_url`` and return the raw bytes.

<a id="CurrentRun.start_export"></a>

#### start\_export

```python
def start_export() -> "ExportRef"
```

``POST .../exports`` — kick off an async zip of the current
run's bodies. Resolves the current run id first (one GET), then
returns an :class:`ExportRef`.

<a id="CurrentRun.download_all_results"></a>

#### download\_all\_results

```python
def download_all_results(target_path: str | Path,
                         *,
                         wait_timeout: float = 600.0,
                         poll_interval: float = 2.0) -> Path
```

Start an export of the current run's results, wait for it,
and save the zip to ``target_path``. See
``ZenRowsBatchClient.download_all_results`` for the contract.

Capped at 1 GiB per run server-side; for larger runs use
``download_to_dir`` (no size limit, but slower — one body at a
time, tunable via ``concurrency=``).

<a id="ScheduleControls"></a>

## ScheduleControls Objects

```python
class ScheduleControls()
```

Operations on a scheduled job's schedule, reached via
``job.schedule``. Scheduled jobs only — regular jobs 409.

<a id="ScheduleControls.pause"></a>

#### pause

```python
def pause() -> "JobHandle"
```

``POST /jobs/{id}/schedule/state`` → ``paused`` — skip future
scheduled fires (an in-flight run keeps running). The schedule
keeps ticking server-side but fires are dropped until
``resume()``. Idempotent; returns the fresh loaded handle.

<a id="ScheduleControls.resume"></a>

#### resume

```python
def resume() -> "JobHandle"
```

``POST /jobs/{id}/schedule/state`` → ``active`` — re-enable
scheduled fires on a paused job. Idempotent; returns the fresh
loaded handle.

<a id="ScheduleControls.update"></a>

#### update

```python
def update(schedule: "Schedule | JobScheduleDict") -> "JobHandle"
```

``PUT /jobs/{id}/schedule`` — replace the schedule. Accepts a
typed builder (``At`` / ``Rate`` / ``Calendar``) or a raw
``JobScheduleDict``, same as ``submit_scheduled``.

An in-flight run keeps running; the new schedule governs only
future fires. Returns the fresh loaded handle.

<a id="JobRef"></a>

## JobRef Objects

```python
class JobRef()
```

A reference to a job by id — job-template operations, plus the
``run`` and ``schedule`` sub-facets.

Minted with **no network call** by ``client.job(id)`` and returned
by the ``submit_*`` methods (with ``submit_response`` attached).
Call ``load()`` for a :class:`JobHandle` with data. Construction is
handled by the SDK — callers never build these directly.

<a id="JobRef.run"></a>

#### run

```python
@property
def run() -> CurrentRun
```

Operations on the **current run** — ``pause`` / ``resume`` /
``stop`` / ``wait`` / ``results`` / downloads / ``start_export``.

<a id="JobRef.schedule"></a>

#### schedule

```python
@property
def schedule() -> ScheduleControls
```

Operations on the **schedule** — ``pause`` / ``resume`` /
``update`` (scheduled jobs only).

<a id="JobRef.status"></a>

#### status

```python
@property
def status() -> JobStatus | None
```

The job status from the submit response. Only known on refs
returned by a ``submit_*`` call (no network); ``None`` otherwise
— ``load()`` for a :class:`JobHandle` whose ``.status`` reads the
fetched ``.data``.

<a id="JobRef.accepted_tasks"></a>

#### accepted\_tasks

```python
@property
def accepted_tasks() -> int | None
```

How many tasks landed at submit. Only known on refs returned
by a ``submit_*`` call; returns ``None`` otherwise.

<a id="JobRef.load"></a>

#### load

```python
def load() -> "JobHandle"
```

``GET /jobs/{id}`` — fetch the full job and return a loaded
:class:`JobHandle` whose ``.data`` is ready.

<a id="JobRef.close"></a>

#### close

```python
def close() -> "JobHandle"
```

``POST /jobs/{id}/close`` — lock the job (no more ``add_tasks``).
Returns the fresh loaded handle.

<a id="JobRef.delete"></a>

#### delete

```python
def delete() -> None
```

``DELETE /jobs/{id}`` — async hard delete.

<a id="JobRef.rerun"></a>

#### rerun

```python
def rerun(*,
          status: str | list[str] | None = None,
          idempotency_key: str | None = None) -> "RunHandle"
```

``POST /jobs/{id}/rerun[?status=...]`` — start a new run.

Without ``status``: full rerun of the previous run's tasks
(or, for a scheduled job with no prior run, a manual fire
from the template). No source-child link.

With ``status`` (single value like ``"failed"`` or list like
``["failed", "pending"]``): partial retry — matching statuses
are reset to ``pending`` and re-enqueued; everything else is
inherited verbatim with ``source_run_id`` stamped.

Returns a :class:`RunHandle` for the newly-created run.

<a id="JobRef.retry_failed"></a>

#### retry\_failed

```python
def retry_failed(*,
                 include_pending: bool = False,
                 idempotency_key: str | None = None) -> "RunHandle"
```

Start a new run that re-executes only the previous run's
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

<a id="JobRef.add_tasks"></a>

#### add\_tasks

```python
def add_tasks(body: AddTasksRequest | AddTasksDict) -> AddTasksResponse
```

``POST /jobs/{id}/tasks`` — append to the open initial run.

<a id="JobRef.runs"></a>

#### runs

```python
def runs(*, page_size: int | None = None) -> Iterator["RunHandle"]
```

Auto-paginate runs of this job, yielding :class:`RunHandle`s.
To address a *specific* run by id, use
``client.run(job_id, run_id)``; for the current run, ``job.run``.

<a id="JobRef.add_file_input"></a>

#### add\_file\_input

```python
def add_file_input(source: str | Path | IO[bytes],
                   *,
                   fields: dict[str, int | str],
                   header: bool = False,
                   delimiter: str = ",",
                   quote: str = '"') -> str
```

Upload a CSV that *this job* would consume on a future
submission. Returns the ``file_input_id``. (File inputs are
not tied to a job at create-time, but living off the handle
keeps the call site discoverable.)

<a id="JobRef.get_webhook"></a>

#### get\_webhook

```python
def get_webhook() -> WebhookConfig
```

`GET /jobs/{id}/webhook` — this job's current webhook config.
Raises `BatchAPIError` (404) when none is set.

<a id="JobRef.set_webhook"></a>

#### set\_webhook

```python
def set_webhook(url: str, *, signature: bool) -> WebhookConfig
```

`PUT /jobs/{id}/webhook` — replace this job's webhook config.
Both fields are required (no defaulting, so you can't silently
toggle signing); pass `signature=False` explicitly for an
unsigned receiver. Returns the persisted config.

<a id="JobRef.delete_webhook"></a>

#### delete\_webhook

```python
def delete_webhook() -> None
```

`DELETE /jobs/{id}/webhook` — clear this job's webhook config.
Idempotent.

<a id="JobRef.wait_for_ingest"></a>

#### wait\_for\_ingest

```python
def wait_for_ingest(*,
                    timeout: float = 300.0,
                    poll_interval: float = 2.0,
                    max_poll_interval: float = 15.0) -> "JobHandle"
```

Block until the current run's async-carrier ingestion has
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
submit time via ``submit_job(..., wait_for_ingest=True)``.

<a id="JobHandle"></a>

## JobHandle Objects

```python
class JobHandle(JobRef)
```

A :class:`JobRef` plus a guaranteed, synchronous ``data`` snapshot.

Returned by ``get_job`` / ``iter_jobs`` and the ops that echo fresh
state (``close``, ``job.schedule.pause``, …). Inherits the ``run``
and ``schedule`` facets from :class:`JobRef`.

<a id="JobHandle.status"></a>

#### status

```python
@property
def status() -> JobStatus
```

This job's status. Shortcut for ``self.data.status``.

# Waiters

Polling helpers used by `ZenRowsBatchClient.wait_for_*` methods.

Convention: a *waiter* polls a resource until it reaches a target
state or a timeout/error fires. We default to exponential-ish
backoff (capped) so short jobs don't pay a multi-second poll
cadence and long ones don't hammer the API every two seconds.

Errors are `WaiterTimeout` (timed out) and `WaiterError` (predicate
raised, or the resource transitioned into an unexpected state the
caller flagged as failure). Both subclass the stdlib `TimeoutError`
/ `RuntimeError` so callers can catch with the broad stdlib types
too.

<a id="WaiterTimeout"></a>

## WaiterTimeout Objects

```python
class WaiterTimeout(TimeoutError)
```

Raised when a waiter's `timeout` elapsed before the target state.

<a id="WaiterError"></a>

## WaiterError Objects

```python
class WaiterError(RuntimeError)
```

Raised when the resource entered a `failure_states` value.

<a id="poll_until"></a>

#### poll\_until

```python
def poll_until(fetch: Callable[[], T],
               *,
               is_done: Callable[[T], bool],
               is_failure: Callable[[T], bool] | None = None,
               timeout: float = 300.0,
               initial_interval: float = 1.0,
               max_interval: float = 15.0,
               backoff: float = 1.5,
               jitter: float = 0.2) -> T
```

Generic poll loop.

Calls `fetch()` repeatedly until `is_done(value)` is true (returns
the value) or `is_failure(value)` is true (raises `WaiterError`),
or `timeout` seconds elapse (raises `WaiterTimeout`).

The wait between calls starts at `initial_interval`, multiplies by
`backoff` each iteration, caps at `max_interval`, and is jittered
by ±`jitter` fraction so concurrent waiters don't synchronise into
thundering-herd patterns against the API.

# Downloads

Download helpers for the Batch SDK.

Every helper fetches task bodies straight from each result's presigned
`result_url` (a signed storage URL) — no auth header, no API content
endpoint in the loop.

Bulk, both with optional concurrency + a tqdm progress bar:

  - `download_to_dir(...)` writes every task body to a directory on
    disk. Streams one task at a time so memory stays bounded; safety
    caps are `max_files` (count) and `max_bytes_per_file` (per body).
  - `download_to_memory(...)` loads bodies into a list of
    `DownloadedResult`. Has BOTH `max_count` and `max_total_bytes`
    safety caps; refuses to start (or raises mid-stream) before
    exhausting RAM.

Single task (for a `TaskResult` you already hold from `results()`):

  - `download_task_to_file(task, target)` writes one body to a path or
    an open binary file object.
  - `download_task_to_memory(task)` returns one body as raw `bytes`.

`concurrency > 1` fans the body GETs out across a `ThreadPoolExecutor`.
Results are NOT guaranteed to be in iteration order in either bulk
flavour. The iteration of `iter_results` itself stays single-threaded
so we never page faster than we drain.

`progress=True` installs a `rich.progress` bar if `rich` is
importable; falls back to no-op if it isn't (so the SDK works in
minimal envs without a hard dep).

<a id="DownloadedResult"></a>

## DownloadedResult Objects

```python
@dataclass(slots=True)
class DownloadedResult()
```

One row's worth of downloaded content held in memory.

<a id="DEFAULT_MAX_BYTES_PER_FILE"></a>

#### DEFAULT\_MAX\_BYTES\_PER\_FILE

50 MiB

<a id="DEFAULT_MAX_TOTAL_BYTES_IN_MEMORY"></a>

#### DEFAULT\_MAX\_TOTAL\_BYTES\_IN\_MEMORY

500 MiB

<a id="DownloadLimitExceeded"></a>

## DownloadLimitExceeded Objects

```python
class DownloadLimitExceeded(RuntimeError)
```

Raised when a download exceeds a configured cap.

<a id="download_to_dir"></a>

#### download\_to\_dir

```python
def download_to_dir(
        client: _BodyFetcher,
        job_id: str,
        target_dir: Path,
        *,
        run_id: str | None = None,
        status: str | None = TaskStatus.SUCCESSFUL.value,
        name_fn: Callable[[TaskResult], str] | None = None,
        use_external_id: bool = False,
        concurrency: int = 1,
        progress: bool = False,
        max_files: int = DEFAULT_MAX_FILES,
        max_bytes_per_file: int = DEFAULT_MAX_BYTES_PER_FILE) -> int
```

Stream every task's body into `target_dir`. Returns the count.

`concurrency` parallelises the body-fetch + write. With the
default 1, behaviour is exactly serial. With N>1, up to N bodies
are fetched in flight; iteration of the result list stays
single-threaded so we never out-pace the server's pagination.

`progress=True` shows a live count via `tqdm` if it's
importable.

See module docstring for the naming + capping rules.

<a id="download_task_to_file"></a>

#### download\_task\_to\_file

```python
def download_task_to_file(task: TaskResult,
                          target: "str | Path | IO[bytes]") -> None
```

Download one task's body straight from its `result_url` and write
it to `target` — a filesystem path (`str` / `Path`) or an already-open
binary file object.

<a id="download_task_to_memory"></a>

#### download\_task\_to\_memory

```python
def download_task_to_memory(task: TaskResult) -> bytes
```

Download one task's body straight from its `result_url` and
return the raw bytes.

<a id="download_to_memory"></a>

#### download\_to\_memory

```python
def download_to_memory(
    client: _BodyFetcher,
    job_id: str,
    *,
    run_id: str | None = None,
    status: str | None = TaskStatus.SUCCESSFUL.value,
    concurrency: int = 1,
    progress: bool = False,
    max_count: int = DEFAULT_MAX_COUNT_IN_MEMORY,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES_IN_MEMORY,
    max_bytes_per_file: int = DEFAULT_MAX_BYTES_PER_FILE
) -> list[DownloadedResult]
```

Load every (matching) task body into a list and return it.

Three independent caps that all raise `DownloadLimitExceeded`:
  - `max_count` — how many rows total
  - `max_total_bytes` — running sum of body sizes
  - `max_bytes_per_file` — any single oversize body aborts

`concurrency` + `progress` work the same as on `download_to_dir`.
Returned list ordering is NOT guaranteed when concurrency > 1.

# Cost estimation

Client-side cost estimation for Batch jobs.

The Batch API prices a scrape per **successful request**, driven by two
scraper knobs (`js_render`, `premium_proxy`) plus the dynamic
`mode=auto`. The rate card is small, stable, and identical for every
caller, so estimation lives client-side rather than behind a network
round-trip — there is intentionally no `POST /v1/jobs/estimate`
endpoint.

What an estimate answers: *if every URL succeeds exactly once, what
is the charge?* It is **not** a consumption forecast — it ignores
failures, `retries`, and `reruns`, which affect realized usage but
not the per-success price. Realized cost is whatever teller bills
post-factum; this is advisory.

The math is a sum of per-task intervals:

    task in `mode=auto`        → [1, 25]   (dynamic, billed post-factum)
    otherwise, by merged flags → exact 1 | 5 | 10 | 25

    job.min = Σ taskᵢ.min      job.max = Σ taskᵢ.max

An estimate is *exact* (`min == max`) iff it contains no auto tasks;
the interval width is exactly `24 x (number of auto tasks)`.

`mode=auto` and the explicit `js_render` / `premium_proxy` flags are
mutually exclusive at submit, so every task sits in
exactly one tier. If a malformed param map carries both, `auto`
wins here (it's what the engine would honor) — but the server would
reject that body at submit anyway.

The per-task `method` / `body` fields (POST tasks) do NOT affect the
rate card — pricing is driven by the flags above regardless of HTTP
method, and the render-tier combinations the platform can't execute
for POST (`js_render`, `js_instructions`, `json_response`) are
rejected at submit, so a priced job is a billable job.

<a id="ParamValue"></a>

#### ParamValue

dict form: `custom_headers` map

<a id="Tier"></a>

## Tier Objects

```python
class Tier(str, Enum)
```

The pricing tier a task falls into. Exactly one per task.

<a id="TaskCost"></a>

## TaskCost Objects

```python
@dataclass(slots=True, frozen=True)
class TaskCost()
```

The credit interval for a single task. `min == max` for every
tier except `auto`.

<a id="CostLine"></a>

## CostLine Objects

```python
@dataclass(slots=True, frozen=True)
class CostLine()
```

One row of a breakdown: all tasks sharing a tier, aggregated.

<a id="CostEstimate"></a>

## CostEstimate Objects

```python
@dataclass(slots=True, frozen=True)
class CostEstimate()
```

Result of `client.estimate_cost`. Credits assuming every task succeeds
once. `min == max` (`exact`) when no task uses `mode=auto`.

Designed-for-later (not built today): money pricing. Credits are
the only unit now. Money would layer in as `money = credits x
price_per_credit`, where the per-credit price is plan-dependent and
not part of the static rate card. The natural, non-breaking
extension is additive fields — e.g. an optional `money_min` /
`money_max` (or a nested `money` object) on this class and a
matching subtotal on `CostLine` — populated only once a per-credit
price is supplied. Nothing here is renamed to a credit-specific
name precisely so that addition reads naturally.

<a id="CostEstimate.exact"></a>

#### exact

```python
@property
def exact() -> bool
```

True when the charge is a single number (no auto tasks).

<a id="CostEstimate.auto_tasks"></a>

#### auto\_tasks

```python
@property
def auto_tasks() -> int
```

How many tasks use `mode=auto` — the only source of range.

<a id="CostEstimate.format"></a>

#### format

```python
def format() -> str
```

Multi-line breakdown table, e.g.::

1000 tasks → 4800-6000 credits
   950 x base (1)     =    950
    50 x auto (1-25)  = 50-1250

# Schedule builders

Pythonic schedule builders for `submit_scheduled`.

The wire format is a structured dict (`JobScheduleDict`) — three
mutually exclusive shapes (`at` / `rate` / `calendar`) plus an
optional `timezone`. The dict form is fine for power users; everyone
else gets these typed classes:

    from zenrows.batch import At, Rate, Calendar, Weekly
    from datetime import datetime

    client.submit_scheduled(
        At(datetime(2026, 9, 1, 9, 0), timezone="Europe/Berlin"),
        urls=["https://example.com/once"],
    )
    client.submit_scheduled(
        Rate(every=15, unit="minute"),
        urls=["https://example.com/poll"],
    )
    client.submit_scheduled(
        Calendar(
            times_of_day=["09:00", "18:00"],
            cadence=Weekly(days=["mon", "wed", "fri"]),
            timezone="Europe/Berlin",
        ),
        urls=["https://example.com/recurring"],
    )

Each class validates its inputs in `__post_init__`. The same rules
the server enforces — naive `at`, full-hour `times_of_day`, day
name spelling, valid IANA timezone — fail fast in Python rather
than round-tripping through a 400.

The low-level `client.submit_job({...})` path stays a pure
passthrough: callers who hand-build a dict bypass these checks and
rely on the server's response for error reporting.

<a id="At"></a>

## At Objects

```python
@dataclass
class At()
```

One-shot fire at a specific wall-clock time.

`at` accepts either a tz-naive `datetime` (no tzinfo) or a
tz-naive ISO string (`"2026-09-01T09:00:00"`). Aware datetimes
and offset-bearing strings are rejected — `timezone` is the
single authoritative interpreter, which keeps DST transitions
deterministic.

<a id="Rate"></a>

## Rate Objects

```python
@dataclass
class Rate()
```

Interval-based fire policy — every N units, no alignment to
wall clock. Timezone is irrelevant and not accepted here.

<a id="Daily"></a>

## Daily Objects

```python
@dataclass
class Daily()
```

Fire every day. No knobs.

<a id="Weekly"></a>

## Weekly Objects

```python
@dataclass
class Weekly()
```

Fire on specific days of the week.

`days` is a list of lower-case 3-letter day names — at least one,
deduplicated server-side.

<a id="Monthly"></a>

## Monthly Objects

```python
@dataclass
class Monthly()
```

Fire on specific days of the month.

`days` is a list of integers 1-31. Days that don't exist in a
given month (e.g. 31 in April) are silently skipped by the
scheduler.

<a id="Calendar"></a>

## Calendar Objects

```python
@dataclass
class Calendar()
```

Calendar-style fire policy: a list of times-of-day on a
daily / weekly / monthly cadence.

`times_of_day` are full-hour 24h strings (`"09:00"`, not
`"09:30"`). `cadence` is exactly one of `Daily()`, `Weekly(...)`,
or `Monthly(...)`. `timezone` is mandatory (IANA name).

# Errors

RFC 7807 Problem JSON → friendly Python exceptions.

The Batch API returns errors as `application/problem+json`.
We surface them as `BatchAPIError` with a structured `problem` payload
plus a flat `code` shortcut so callers can branch without indexing
into a dict.

<a id="ProblemDetail"></a>

## ProblemDetail Objects

```python
@dataclass(slots=True)
class ProblemDetail()
```

Decoded RFC 7807 Problem body.

`extras` keeps any non-standard members (e.g. `invalid_tasks`)
so handlers can dig in without us tracking every shape.

<a id="ProblemDetail.from_response"></a>

#### from\_response

```python
@classmethod
def from_response(cls, response: httpx.Response) -> "ProblemDetail | None"
```

Parse a Problem body. Returns None if the body isn't JSON.

Tolerant — production servers occasionally return non-JSON
errors (e.g. ALB blocked at the edge); we degrade gracefully.

<a id="BatchAPIError"></a>

## BatchAPIError Objects

```python
class BatchAPIError(Exception)
```

A non-2xx response from the Batch API.

`code` is the RFC 7807 `code` member (e.g. `file_input_not_found`,
`idempotency_key_conflict`). Stable; safe to branch on.

# Models

<a id="JobType"></a>

## JobType Objects

```python
class JobType(Enum)
```

- `regular` — a run is created at submit time.
- `scheduled` — fires on a recurring or one-shot schedule.
  Submit stores the task list as a template on the job row;
  each scheduled fire produces a fresh Run. `schedule`
  field required. Read-only template: `addTasks` and
  `close` return 409. `rerun` (full or filtered) and `stop`
  are allowed.

<a id="JobStatus"></a>

## JobStatus Objects

```python
class JobStatus(Enum)
```

- `open` — initial run still accepting `addTasks`. Only
  meaningful while `latest_run.run_sequence == 1`.
- `closed` — no more tasks accepted (created closed, or
  closed via `/close` / `addTasks{last_batch}` / `/rerun`).
- `deleted` — async deletion in progress; the job disappears
  once it finishes.

<a id="ScheduleState"></a>

## ScheduleState Objects

```python
class ScheduleState(Enum)
```

Run/pause flag on a scheduled job. While `paused`, scheduled
fires are skipped. Default at submit: `active`. Flip via
`POST /v1/jobs/{id}/schedule/state`.

<a id="RunTrigger"></a>

## RunTrigger Objects

```python
class RunTrigger(Enum)
```

What set this run in motion. Always set.
- `manual` — caller-initiated: `POST /jobs`, `POST /jobs/{id}/rerun`
  (any job type, full or `?status=`-filtered, manual fire of
  a scheduled job).
- `scheduled` — automatic, fired by the configured
  schedule (recurring or one-shot).

<a id="RunStatus"></a>

## RunStatus Objects

```python
class RunStatus(Enum)
```

In-flight:
- `running` — work queued / in flight.
- `pending` — initial run of an open job, idle between batches.

Terminal:
- `completed` — natural finish.
- `stopped` — caller called `POST /jobs/{id}/stop`. No new
  tasks are picked up; in-flight tasks may still finish.
  Result bodies are kept. `stats.completed < stats.total`
  signals "stopped early".
- `failed` — the run was auto-failed on an account-level error
  (insufficient credits / inactive subscription). No new tasks
  are picked up; `failure_reason` carries the cause. Re-runnable
  once the account is resolved. Result bodies already produced
  are kept.
- `deleted` — caller called
  `DELETE /v1/jobs/{id}/runs/{run_id}`. The run's result
  bodies and data are being deleted; once complete the run
  disappears.

<a id="ResultType"></a>

## ResultType Objects

```python
class ResultType(Enum)
```

Body format of a successful task result; matches the job's `format` 1:1.

<a id="Format"></a>

## Format Objects

```python
class Format(Enum)
```

Derived server-side from `zenrows_params` at submit time.
Precedence:
  - `response_type: markdown|plaintext|pdf` → matching format.
  - `autoparse: true`, `json_response: true`, or non-empty
    `css_extractor` → `json`.
  - otherwise → `html`.
Stamped on every successful task result and used to set the
right `Content-Type` when you fetch the content.

<a id="Method"></a>

## Method Objects

```python
class Method(Enum)
```

HTTP method used against `url`. Case-insensitive. POST is
for **safe/idempotent** requests only (GraphQL queries,
search endpoints): tasks are retried on transient failures
and reruns, so the target may see the same POST more than
once. Callers that cannot tolerate a duplicate should
disable reruns. POST rides the standard (non-headless)
scraping path — combining it with `js_render`,
`js_instructions`, or `json_response` is rejected with
400 `method_param_conflict`.

<a id="TaskInput"></a>

## TaskInput Objects

```python
class TaskInput(BaseModel)
```

<a id="TaskInput.external_id"></a>

#### external\_id

Optional caller-supplied correlation id — typically an
identifier from the caller's own system. Surfaced verbatim
in result/content responses so callers can match results
back to their records. **Not required to be unique** —
callers may reuse the same value across tasks (e.g. when
multiple scrapes correlate to the same upstream record).
Independent of the server-
assigned `task_id`.

<a id="TaskInput.url"></a>

#### url

Must be http(s). Other schemes rejected at submit.

<a id="TaskInput.method"></a>

#### method

HTTP method used against `url`. Case-insensitive. POST is
for **safe/idempotent** requests only (GraphQL queries,
search endpoints): tasks are retried on transient failures
and reruns, so the target may see the same POST more than
once. Callers that cannot tolerate a duplicate should
disable reruns. POST rides the standard (non-headless)
scraping path — combining it with `js_render`,
`js_instructions`, or `json_response` is rejected with
400 `method_param_conflict`.

<a id="TaskInput.body"></a>

#### body

Request body, only with `method: POST`. Any JSON value,
16 KiB max. An object/array/number/boolean is sent as its
JSON encoding with `Content-Type: application/json`; a
string is sent verbatim with
`Content-Type: application/x-www-form-urlencoded`. Set a
different target Content-Type via the `custom_headers`
zenrows param. Never echoed in results listings.

<a id="TaskInput.zenrows_params"></a>

#### zenrows\_params

Per-task scraper params. Override the job-level
`zenrows_params` on key collision (task wins).

<a id="Status"></a>

## Status Objects

```python
class Status(Enum)
```

Initial state. `open` is only allowed for `regular` jobs;
after the initial run, `open` has no meaning so the job
is auto-closed.

<a id="AddTasksRequest"></a>

## AddTasksRequest Objects

```python
class AddTasksRequest(BaseModel)
```

<a id="AddTasksRequest.last_batch"></a>

#### last\_batch

Set true on the final batch. Closes the job (status →
`closed`) and marks the run's `last_batch_received`.

<a id="Spend"></a>

## Spend Objects

```python
class Spend(BaseModel)
```

Indicative `{credits, cost}` pair — what was charged for
the scoped work. **Not billing-grade**; your account
statement is authoritative. Use as a "how much did this
cost?" indicator, not for reconciliation.

<a id="TaskSpend"></a>

## TaskSpend Objects

```python
class TaskSpend(BaseModel)
```

Per-task indicative spend with two roll-ups: `total`
accumulates across every attempt (including retries),
`last_attempt` carries just the most recent gateway call.
On a task that succeeded on its first try the two are
equal; on a retried task they diverge.

<a id="RunStats"></a>

## RunStats Objects

```python
class RunStats(BaseModel)
```

<a id="RunStats.total"></a>

#### total

Number of tasks in this run.

<a id="RunStats.completed"></a>

#### completed

successful + failed.

<a id="RunStats.failure_reasons"></a>

#### failure\_reasons

Coarse rollup of terminal failures keyed by a small public
taxonomy. Lets you answer "what kinds of failures did I
get?" without paging every error blob. Best-effort,
indicative — omitted on runs with no failures yet and on
runs that predate the feature.

Vocabulary (the only keys that appear):
- `auth_failed` — credentials or billing
- `blocked` — anti-bot / policy denials
- `bad_target` — target URL is the problem (bad host, 404, 410, too large)
- `rate_limited` — target throttled the request
- `timeout` — the scrape didn't complete in time
- `gateway_error` — ZenRows-side transport / 5xx
- `other` — anything else

<a id="RunStats.spend"></a>

#### spend

Indicative spend summed across every task attempt in
this run. Absent on runs whose tasks predate this
field (treat as zero).

<a id="PauseState"></a>

## PauseState Objects

```python
class PauseState(Enum)
```

Reversible-suspend flag, orthogonal to `status`.
Omitted from responses when `active` / absent (legacy
rows). Flip via `POST /jobs/{id}/pause` and
`/resume`.

<a id="IngestStatus"></a>

## IngestStatus Objects

```python
class IngestStatus(Enum)
```

Present only on runs created by a large (202) submission
or a large (202) rerun. `pending` — task rows are still
streaming into storage; reads may return partial pages
and `addTasks` returns `409`. `done` — every accepted
task row is visible. Omitted on runs whose tasks were
written on the request path (201 submissions and
reruns, `addTasks` batches).

<a id="FailureReason"></a>

## FailureReason Objects

```python
class FailureReason(Enum)
```

Present only when `status == failed`: the account-level
cause of the auto-fail. `insufficient_credits` (out of
credits) or `subscription_inactive` (subscription not
active). Omitted otherwise. Distinct from
`stats.failure_reasons` (the per-task rollup).

<a id="Run"></a>

## Run Objects

```python
class Run(BaseModel)
```

<a id="Run.last_batch_received"></a>

#### last\_batch\_received

Meaningful only for the initial run of an open job.
Once true, `addTasks` is rejected and the run drains
into `completed`.

<a id="Run.pause_state"></a>

#### pause\_state

Reversible-suspend flag, orthogonal to `status`.
Omitted from responses when `active` / absent (legacy
rows). Flip via `POST /jobs/{id}/pause` and
`/resume`.

<a id="Run.ingest_status"></a>

#### ingest\_status

Present only on runs created by a large (202) submission
or a large (202) rerun. `pending` — task rows are still
streaming into storage; reads may return partial pages
and `addTasks` returns `409`. `done` — every accepted
task row is visible. Omitted on runs whose tasks were
written on the request path (201 submissions and
reruns, `addTasks` batches).

<a id="Run.failure_reason"></a>

#### failure\_reason

Present only when `status == failed`: the account-level
cause of the auto-fail. `insufficient_credits` (out of
credits) or `subscription_inactive` (subscription not
active). Omitted otherwise. Distinct from
`stats.failure_reasons` (the per-task rollup).

<a id="ScheduleRate"></a>

## ScheduleRate Objects

```python
class ScheduleRate(BaseModel)
```

Interval-based fire policy — every N units.

<a id="ScheduleCadence"></a>

## ScheduleCadence Objects

```python
class ScheduleCadence(BaseModel)
```

Picks which days the schedule fires on. Exactly one of
`daily`, `weekly`, `monthly` must be set.

<a id="ScheduleCadence.daily"></a>

#### daily

Fire every day. No knobs.

<a id="Method1"></a>

## Method1 Objects

```python
class Method1(Enum)
```

The task's HTTP method. Omitted for GET (the default).
The request `body` is intentionally not part of listing
responses.

<a id="WebhookConfig"></a>

## WebhookConfig Objects

```python
class WebhookConfig(BaseModel)
```

Webhook delivery config for `run.completed` / `run.failed`. Returned on
`GET /v1/jobs/{id}` (inline under `webhook`) and on
`GET /v1/jobs/{id}/webhook`. `PUT` requires both fields —
no defaulting at the mutate boundary (otherwise toggling
`url` would silently disable signing). Submit body accepts
the same shape with `signature` optional (defaults `false`).

<a id="WebhookConfig.url"></a>

#### url

HTTPS only. Host must resolve via DNS within 1 second
(1+ A/AAAA record).

<a id="WebhookConfig.signature"></a>

#### signature

Opt-in HMAC signing. When `true`, each delivery is signed
with the org's active HMAC key
(`POST /v1/hmac/keys/rotate`) and carries
`X-Signature: t=<unix>,v1=<hex>,kid=<active>`. The signed
input is `t + "." + raw_body`, HMAC-SHA256. When `false`,
deliveries carry **no** `X-Signature` header — header
absence is the signal.

<a id="TestWebhookRequest"></a>

## TestWebhookRequest Objects

```python
class TestWebhookRequest(BaseModel)
```

Body for `POST /v1/webhook/test`. Same shape as the submit-time
`webhook` field; `signature` is optional and defaults `false`.

<a id="TestWebhookRequest.url"></a>

#### url

HTTPS only. Host must resolve via DNS within 1 second.
Identical validation to the submit/PUT webhook URL.

<a id="TestWebhookRequest.signature"></a>

#### signature

When `true`, the test event is signed with the org's
active HMAC key — same `X-Signature: t,v1,kid` header
real deliveries use. Returns `400
webhook_signing_requires_active_key` when no active key
exists.

<a id="TestWebhookResponse"></a>

## TestWebhookResponse Objects

```python
class TestWebhookResponse(BaseModel)
```

Outcome of the synthetic test dispatch. HTTP status is always
`200` when the request was validated — the receiver outcome
is in the body. The synthetic envelope uses `event_type:
"webhook.test"` and stable sentinel IDs (`job_id`/`run_id` =
`"test"`) so receivers can recognise and discard test traffic.

<a id="TestWebhookResponse.delivered"></a>

#### delivered

`true` iff the receiver responded with a 2xx status. `false`
on non-2xx, timeout, or transport error.

<a id="TestWebhookResponse.event_id"></a>

#### event\_id

ULID of the synthetic event. Different on every call so
receivers' dedup tables don't suppress repeated tests.

<a id="TestWebhookResponse.status_code"></a>

#### status\_code

HTTP status returned by the receiver. Absent on timeout or
transport error (no response was received).

<a id="TestWebhookResponse.error"></a>

#### error

Human-readable reason when `delivered: false`. Same
vocabulary as real webhook deliveries
(`http_4xx:<code>`, `http_5xx:<code>`, `timeout`,
`transport_error:<detail>`). Absent on success.

<a id="TestWebhookResponse.elapsed_ms"></a>

#### elapsed\_ms

Wall-clock duration of the receiver POST in milliseconds.

<a id="FileInputColumnRef1"></a>

## FileInputColumnRef1 Objects

```python
class FileInputColumnRef1(RootModel[str])
```

<a id="FileInputColumnRef1.root"></a>

#### root

Either a column name (string) — requires `csv.header: true` —
or a 0-based column index (integer). Other shapes are
rejected at create-time.

<a id="FileInputColumnRef2"></a>

## FileInputColumnRef2 Objects

```python
class FileInputColumnRef2(RootModel[int])
```

<a id="FileInputColumnRef2.root"></a>

#### root

Either a column name (string) — requires `csv.header: true` —
or a 0-based column index (integer). Other shapes are
rejected at create-time.

<a id="Fields"></a>

## Fields Objects

```python
class Fields(BaseModel)
```

Map from canonical task field → CSV column. Only
`url` (required) and `external_id` (optional) are
accepted. Each value is a column index (int) or a
column name (string, requires `header: true`).

<a id="Fields.url"></a>

#### url

Either a column name (string) — requires `csv.header: true` —
or a 0-based column index (integer). Other shapes are
rejected at create-time.

<a id="Fields.external_id"></a>

#### external\_id

Either a column name (string) — requires `csv.header: true` —
or a 0-based column index (integer). Other shapes are
rejected at create-time.

<a id="Csv"></a>

## Csv Objects

```python
class Csv(BaseModel)
```

<a id="Csv.delimiter"></a>

#### delimiter

Single-character field delimiter.

<a id="Csv.quote"></a>

#### quote

Single-character quoting character.

<a id="Csv.header"></a>

#### header

When true, the first CSV row is consumed as a header
row and `csv.fields.*` values may be column names.

<a id="Csv.fields"></a>

#### fields

Map from canonical task field → CSV column. Only
`url` (required) and `external_id` (optional) are
accepted. Each value is a column index (int) or a
column name (string, requires `header: true`).

<a id="CreateJobInputRequest"></a>

## CreateJobInputRequest Objects

```python
class CreateJobInputRequest(BaseModel)
```

<a id="CreateJobInputRequest.type"></a>

#### type

Only `csv` is supported in v1.

<a id="FileInputUploadTarget"></a>

## FileInputUploadTarget Objects

```python
class FileInputUploadTarget(BaseModel)
```

<a id="FileInputUploadTarget.url"></a>

#### url

Presigned PUT URL. Caller MUST send the body with the
exact `Content-Type` shown in `headers` — the signature
binds the content-type.

<a id="FileInputUploadTarget.expires_at"></a>

#### expires\_at

PUT URL TTL (~30 min).

<a id="CreateJobInputResponse"></a>

## CreateJobInputResponse Objects

```python
class CreateJobInputResponse(BaseModel)
```

<a id="CreateJobInputResponse.expires_at"></a>

#### expires\_at

24 h slot lifetime — beyond this the slot and its uploaded
body are removed and the `file_input_id` returns 404.

<a id="HMACKeyMeta"></a>

## HMACKeyMeta Objects

```python
class HMACKeyMeta(BaseModel)
```

Public view of one HMAC key — id + creation time. Never
includes secret material; that's only returned at /rotate.

<a id="HMACKeyMeta.kid"></a>

#### kid

ULID identifying this key. Stable for the life of the slot; a new candidate gets a new kid.

<a id="HMACKeyList"></a>

## HMACKeyList Objects

```python
class HMACKeyList(BaseModel)
```

Slots populated at the time of the call.

<a id="HMACKeyCreated"></a>

## HMACKeyCreated Objects

```python
class HMACKeyCreated(BaseModel)
```

Response to `/rotate`. `secret` is base64-encoded raw key
material. **This is the ONLY response that ever contains
the secret value** — capture it now or generate a new one
via another /rotate call.

<a id="HMACKeyCreated.secret"></a>

#### secret

Base64-encoded 32-byte HMAC key.

<a id="HMACKeyFinalized"></a>

## HMACKeyFinalized Objects

```python
class HMACKeyFinalized(BaseModel)
```

Response to `/rotate/finalize`. No secret.

<a id="InvalidTask"></a>

## InvalidTask Objects

```python
class InvalidTask(BaseModel)
```

<a id="InvalidTask.value"></a>

#### value

Offending input. For URL / metadata reasons this is
a redacted/truncated form of the bad value. For
`unknown_param` / `invalid_param_value` this is the
param key.

<a id="Problem"></a>

## Problem Objects

```python
class Problem(BaseModel)
```

RFC 7807 Problem Details.

<a id="Problem.invalid_tasks"></a>

#### invalid\_tasks

Present on validation errors.

<a id="ExportStatus"></a>

## ExportStatus Objects

```python
class ExportStatus(Enum)
```

Lifecycle state of a results export.
* `pending` — export accepted, not started yet.
* `running` — the zip is being produced.
* `completed` — `download_url` will be present.
* `failed`   — `error` carries the reason.

<a id="StartExportResponse"></a>

## StartExportResponse Objects

```python
class StartExportResponse(BaseModel)
```

Returned by `startResultsExport`.

<a id="StartExportResponse.export_id"></a>

#### export\_id

ULID identifying this export. Use it for `getResultsExport`.

<a id="StartExportResponse.expires_at"></a>

#### expires\_at

12 h after `created_at`. Past this point the export and
its download are removed and the export id 404s.

<a id="Export"></a>

## Export Objects

```python
class Export(BaseModel)
```

Polled view of a results export. `download_url` is presigned
fresh on every successful poll — stash the metadata, but
re-fetch the URL right before you download.

<a id="Export.error"></a>

#### error

Non-empty only when `status = failed`. Stable strings —
e.g. `"results are larger then 1 gb"` when the combined
results exceed the 1 GiB cap.

<a id="Export.download_url"></a>

#### download\_url

Presigned download URL for the zipped run results.
Present only when `status = completed`. Short-lived — the
server mints a new one on every poll.

<a id="Export.expires_at"></a>

#### expires\_at

12 h after `created_at`. The download is unavailable
after this point.

<a id="SubmitJobResponse"></a>

## SubmitJobResponse Objects

```python
class SubmitJobResponse(BaseModel)
```

<a id="SubmitJobResponse.latest_run"></a>

#### latest\_run

Absent for `scheduled` jobs that haven't fired yet.

<a id="SubmitJobResponse.webhook"></a>

#### webhook

Echo of the webhook config persisted on the job (when
one was supplied). Omitted when no webhook was set.

<a id="RerunJobResponse"></a>

## RerunJobResponse Objects

```python
class RerunJobResponse(BaseModel)
```

<a id="RerunJobResponse.rerun_of"></a>

#### rerun\_of

`run_id` of the previous run that was replayed. Empty on
the first manual fire of a scheduled job (no prior run).

<a id="RerunJobResponse.retried_tasks"></a>

#### retried\_tasks

Number of rows reset to `pending` and re-enqueued. Equals
`latest_run.stats.total` for a full rerun; equals the
filter-matched count for a `?status=` partial retry.

<a id="RerunJobResponse.inherited_tasks"></a>

#### inherited\_tasks

Number of rows copied verbatim from the previous run with
`source_run_id` stamped. Zero for a full rerun; non-zero
only when `?status=` is set.

<a id="ScheduleCalendar"></a>

## ScheduleCalendar Objects

```python
class ScheduleCalendar(BaseModel)
```

Calendar-style fire policy. Fires at every `times_of_day`
entry on every day matching the cadence.

<a id="ScheduleCalendar.times_of_day"></a>

#### times\_of\_day

Wall-clock times on a 24-hour clock, full hours only
(`"09:00"`, `"18:00"`). Minute granularity is rejected
with 400.

<a id="TaskResult"></a>

## TaskResult Objects

```python
class TaskResult(BaseModel)
```

<a id="TaskResult.external_id"></a>

#### external\_id

Caller-supplied correlation id from submit/AddTasks.
Omitted when the caller did not supply one.

<a id="TaskResult.method"></a>

#### method

The task's HTTP method. Omitted for GET (the default).
The request `body` is intentionally not part of listing
responses.

<a id="TaskResult.result_url"></a>

#### result\_url

24-hour presigned download URL for the result body, or a
`/v1/jobs/<id>/runs/<run>/tasks/<tid>/content` URL you can
fetch directly. Empty for non-successful tasks.

<a id="TaskResult.error"></a>

#### error

Present on failed tasks. The scraping engine's error
response as Problem JSON, or a synthesised envelope with
`code: "gateway_unreachable"` when it couldn't be reached.

<a id="TaskResult.source_run_id"></a>

#### source\_run\_id

Set when this row was copied from another run by
`/rerun?status=`. The row is terminal at creation, is
never re-executed, and its `result_url` resolves to the
source run's stored result. On chained retries,
`source_run_id` chases back to the run that actually
owns the result. Empty for normally-executed rows.

<a id="TaskHistoryEvent"></a>

## TaskHistoryEvent Objects

```python
class TaskHistoryEvent(BaseModel)
```

<a id="TaskHistoryEvent.attempt"></a>

#### attempt

1-indexed attempt within the run.

<a id="TaskHistoryEvent.spend"></a>

#### spend

Indicative spend charged for this single attempt. Zero
on attempts that didn't reach the scraping engine or
were not charged.

<a id="JobSchedule"></a>

## JobSchedule Objects

```python
class JobSchedule(BaseModel)
```

Structured scheduling block attached to `type: scheduled`
jobs. Exactly one of `at`, `rate`, or `calendar` must be
set.

<a id="JobSchedule.at"></a>

#### at

One-shot fire at a specific wall-clock timestamp.
Mutually exclusive with `rate` and `schedule`.

**Must be tz-naive** — no trailing `Z`, no offset. The
sibling `timezone` field (mandatory) is the single
authoritative interpreter. This keeps DST transitions
deterministic.

<a id="JobSchedule.timezone"></a>

#### timezone

IANA timezone name (e.g. `Europe/Berlin`, `UTC`).
**Required** when `at` or `calendar` is set;
ignored by `rate` (interval-based, no wall-clock
meaning). Anchoring wall-clock times to a named zone
(rather than a UTC offset baked into the string) keeps
DST transitions deterministic.

<a id="Job"></a>

## Job Objects

```python
class Job(BaseModel)
```

<a id="Job.zenrows_params"></a>

#### zenrows\_params

Stored canonical form — values are always strings even
though submit accepts any JSON scalar (see ScraperParams).

<a id="Job.external_id"></a>

#### external\_id

Caller-supplied correlation id passed at submit (omitted
when the caller did not supply one). Not server-enforced
unique.

<a id="Job.name"></a>

#### name

Optional human label passed at submit (omitted when the
caller did not supply one). Free-form, up to 100 chars.

<a id="Job.schedule"></a>

#### schedule

Schedule block — present only for `type: scheduled`
jobs.

<a id="Job.next_scheduled_run"></a>

#### next\_scheduled\_run

Server-computed timestamp of the next expected fire,
stamped at submit and re-stamped on every fire. `null`
for non-scheduled jobs and for one-shot `at(...)`
schedules that have already fired. Stays computed when
`schedule_state == paused` — "what would fire next if
you resumed."

<a id="Job.schedule_state"></a>

#### schedule\_state

Set only for `type: scheduled`. Default `active` at
submit; flip via `POST /v1/jobs/{id}/schedule/state`.

<a id="Job.webhook"></a>

#### webhook

Webhook delivery config. Present iff a
webhook is configured. Mutable via `PUT/DELETE
/v1/jobs/{id}/webhook`; `signature` never appears
alone — the whole `webhook` key is omitted when no
URL is set.

<a id="Job.latest_run"></a>

#### latest\_run

Snapshot projection of the latest run. Absent for
`scheduled` jobs that haven't fired yet.

<a id="SubmitJobRequest"></a>

## SubmitJobRequest Objects

```python
class SubmitJobRequest(BaseModel)
```

<a id="SubmitJobRequest.status"></a>

#### status

Initial state. `open` is only allowed for `regular` jobs;
after the initial run, `open` has no meaning so the job
is auto-closed.

<a id="SubmitJobRequest.zenrows_params"></a>

#### zenrows\_params

Job-level scraper params, applied to every task of every
run of the job. Each task can override individual keys
via its own `zenrows_params` (task wins on collision).

<a id="SubmitJobRequest.schedule"></a>

#### schedule

Schedule block for `type: scheduled`. Required there,
ignored otherwise.

<a id="SubmitJobRequest.tasks"></a>

#### tasks

Required for closed jobs (1–1000) unless `file_input_id`
is provided. Optional for open jobs — may be empty if the
caller will follow up with `addTasks`. Mutually exclusive
with `file_input_id`.

<a id="SubmitJobRequest.file_input_id"></a>

#### file\_input\_id

Reference to a previously-uploaded CSV input (see
`POST /v1/job_inputs`). Mutually exclusive with `tasks`.
Eligible only for regular-closed and scheduled job types.
The uploaded CSV is parsed under the saved spec; its rows
become tasks (regular-closed) or template_tasks
(scheduled).

<a id="SubmitJobRequest.external_id"></a>

#### external\_id

Optional caller-supplied correlation id for the job —
same semantics as `task.external_id`. Shape-checked,
**not** required to be unique. Surfaced verbatim in
`getJob` / `listJobs` responses.

<a id="SubmitJobRequest.name"></a>

#### name

Optional human-readable label for the job. Free-form —
no shape rules. Up to 100 characters. Surfaced verbatim
in `getJob` / `listJobs` responses. No uniqueness, no
indexing; cannot be changed after submit.

<a id="SubmitJobRequest.webhook"></a>

#### webhook

Optional `run.completed` / `run.failed` delivery config.
A terminal run fires `run.completed` on a natural finish, or
`run.failed` (with `failure_reason` + partial stats) when the
run auto-fails on an account-level error. `signature`
defaults to `false` here so first-time integrations
don't need an HMAC key. Config is mutable post-submit
via `PUT /v1/jobs/{id}/webhook` and `DELETE
/v1/jobs/{id}/webhook`; the current config is surfaced
on `GET /v1/jobs/{id}`.
