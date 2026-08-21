"""Public surface for the Zenrows Batch (async-job) API.

What's where:
  - `client.ZenRowsBatchClient` — the friendly typed facade. One
    method per OpenAPI operation, returning real pydantic models.
  - `models` — pydantic v2 models, regenerated from the backend's
    canonical `../docs/openapi.yaml` via `make generate`. Do NOT hand-edit.
  - `errors.BatchAPIError` / `errors.ProblemDetail` — RFC 7807 mapping.
  - `_transport._Transport` — httpx wrapper; internal.

Re-exports below are the curated, stable surface. Anything in
`models` that callers need but isn't here can be imported directly.

Completion is surfaced via waiters (`job.wait()` / `run.wait()`) and
webhooks — poll or get notified; there's no callback to wire up.
"""

from zenrows.batch._download import DownloadedResult, DownloadLimitExceeded
from zenrows.batch._estimate import (
    CostEstimate,
    CostLine,
    TaskCost,
    Tier,
)
from zenrows.batch._resources import (
    CurrentRun,
    ExportHandle,
    ExportRef,
    JobHandle,
    JobRef,
    RunHandle,
    RunRef,
    ScheduleControls,
)
from zenrows.batch._schedule import (
    At,
    Cadence,
    Calendar,
    Daily,
    Monthly,
    Rate,
    Schedule,
    Weekly,
)
from zenrows.batch._typed_dicts import (
    AddTasksDict,
    CreateJobInputDict,
    CSVFieldsDict,
    CSVSpecDict,
    JobScheduleDict,
    SubmitJobDict,
    TaskInputDict,
    WebhookDict,
)
from zenrows.batch._waiters import WaiterError, WaiterTimeout
from zenrows.batch.client import TERMINAL_RUN_STATUSES, ZenRowsBatchClient
from zenrows.batch.errors import BatchAPIError, ProblemDetail
from zenrows.batch.models import (
    AddTasksRequest,
    CreateJobInputRequest,
    CreateJobInputResponse,
    IngestStatus,
    Job,
    JobStatus,
    JobType,
    Run,
    RunStatus,
    SubmitJobRequest,
    SubmitJobResponse,
    TaskInput,
    TaskResult,
    TaskStatus,
    TestWebhookRequest,
    TestWebhookResponse,
    WebhookConfig,
)

__all__ = [
    "TERMINAL_RUN_STATUSES",
    "AddTasksDict",
    "AddTasksRequest",
    "At",
    "BatchAPIError",
    "CSVFieldsDict",
    "CSVSpecDict",
    "Cadence",
    "Calendar",
    "CostEstimate",
    "CostLine",
    "CreateJobInputDict",
    "CreateJobInputRequest",
    "CreateJobInputResponse",
    "CurrentRun",
    "Daily",
    "DownloadLimitExceeded",
    "DownloadedResult",
    "ExportHandle",
    "ExportRef",
    "IngestStatus",
    "Job",
    "JobHandle",
    "JobRef",
    "JobScheduleDict",
    "JobStatus",
    "JobType",
    "Monthly",
    "ProblemDetail",
    "Rate",
    "Run",
    "RunHandle",
    "RunRef",
    "RunStatus",
    "Schedule",
    "ScheduleControls",
    "SubmitJobDict",
    "SubmitJobRequest",
    "SubmitJobResponse",
    "TaskCost",
    "TaskInput",
    "TaskInputDict",
    "TaskResult",
    "TaskStatus",
    "TestWebhookRequest",
    "TestWebhookResponse",
    "Tier",
    "WaiterError",
    "WaiterTimeout",
    "WebhookConfig",
    "WebhookDict",
    "Weekly",
    "ZenRowsBatchClient",
]
