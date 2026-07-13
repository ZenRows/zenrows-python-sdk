"""TypedDict mirrors of the request models — for dict-literal callers.

Most `client.submit_job(...)` / `add_tasks(...)` call sites pass a
plain dict (it's nicer than constructing a pydantic model with the
right kwargs). Without TypedDicts, those dicts get no autocomplete
and no type-check; users discover misspellings at runtime.

These TypedDicts mirror the pydantic models field-for-field. The
client methods accept `Model | DictForm`; type-checkers route the
dict literal against the TypedDict and surface IDE hints.

Keep in sync with `models.py`. When a field is added there, mirror
it here.
"""

import sys
from datetime import datetime
from typing import Any, Literal, TypedDict

if sys.version_info >= (3, 11):
    from typing import NotRequired
else:
    # `typing.NotRequired` only exists on 3.11+; the SDK floors at 3.10.
    from typing_extensions import NotRequired

# ----- task input -----


class TaskInputDict(TypedDict, total=False):
    """One task in a submit/AddTasks payload."""

    url: str
    external_id: NotRequired[str]
    metadata: NotRequired[dict[str, str]]
    zenrows_params: NotRequired[dict[str, Any]]


# ----- submit -----


class JobScheduleDict(TypedDict, total=False):
    """Structured schedule block for `type: scheduled` submits.
    Exactly one of `at`, `rate`, `calendar` must be set.

    `at` accepts either a tz-naive ISO string (`"2026-09-01T09:00:00"`)
    or a naive `datetime.datetime` (no `tzinfo`). The SDK normalises
    to the string form before sending; aware datetimes are rejected
    client-side with a ValueError."""

    at: NotRequired[str | datetime]
    rate: NotRequired[dict[str, Any]]  # {"every": int, "unit": "minute"|"hour"|"day"}
    calendar: NotRequired[dict[str, Any]]  # times_of_day + cadence
    timezone: NotRequired[str]  # IANA name; mandatory for `at` and `calendar`


class WebhookDict(TypedDict):
    """Completion-callback config for a job. Set it at `POST /jobs`, or
    manage it later via `PUT`/`DELETE /jobs/{id}/webhook`. `url` must be
    HTTPS. Set `signature: true` to have each delivery signed with your
    org's active HMAC key (delivered as an `X-Signature` header so your
    receiver can verify it); it defaults to `false` (unsigned). Manage
    signing keys via the `/hmac/keys` endpoints."""

    url: str
    signature: NotRequired[bool]


class SubmitJobDict(TypedDict, total=False):
    """Body of `POST /jobs`. Mirrors `SubmitJobRequest`."""

    type: NotRequired[Literal["regular", "scheduled"]]
    status: NotRequired[Literal["open", "closed"]]
    zenrows_params: NotRequired[dict[str, Any]]
    schedule: NotRequired[JobScheduleDict]
    tasks: NotRequired[list[TaskInputDict]]
    file_input_id: NotRequired[str]
    external_id: NotRequired[str]
    name: NotRequired[str]
    metadata: NotRequired[dict[str, str]]
    webhook: NotRequired[WebhookDict]


# ----- add tasks -----


class AddTasksDict(TypedDict):
    """Body of `POST /jobs/{id}/tasks`."""

    tasks: list[TaskInputDict]
    last_batch: NotRequired[bool]


# ----- file inputs -----


class CSVFieldsDict(TypedDict, total=False):
    """The `csv.fields` map in a CreateJobInput body."""

    # Each value is `int` (0-based index) or `str` (column name,
    # requires `header=True`). TypedDicts can't express ColumnRef
    # exactly, but `int | str` covers both shapes.
    url: int | str
    external_id: NotRequired[int | str]


class CSVSpecDict(TypedDict, total=False):
    """Body of `csv` block on a CreateJobInput."""

    fields: CSVFieldsDict
    header: NotRequired[bool]
    delimiter: NotRequired[str]
    quote: NotRequired[str]


class CreateJobInputDict(TypedDict, total=False):
    """Body of `POST /job_inputs`."""

    type: Literal["csv"]
    csv: CSVSpecDict


__all__ = [
    "AddTasksDict",
    "CSVFieldsDict",
    "CSVSpecDict",
    "CreateJobInputDict",
    "JobScheduleDict",
    "SubmitJobDict",
    "TaskInputDict",
    "WebhookDict",
]
