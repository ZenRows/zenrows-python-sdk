"""RFC 7807 Problem JSON → friendly Python exceptions.

The Batch API returns errors as `application/problem+json`.
We surface them as `BatchAPIError` with a structured `problem` payload
plus a flat `code` shortcut so callers can branch without indexing
into a dict.
"""

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx


@dataclass(slots=True)
class ProblemDetail:
    """Decoded RFC 7807 Problem body.

    `extras` keeps any non-standard members (e.g. `invalid_tasks`)
    so handlers can dig in without us tracking every shape.
    """

    type: str
    title: str
    status: int
    code: str
    detail: str | None = None
    instance: str | None = None
    extras: dict[str, Any] | None = None

    @classmethod
    def from_response(cls, response: httpx.Response) -> "ProblemDetail | None":
        """Parse a Problem body. Returns None if the body isn't JSON.

        Tolerant — production servers occasionally return non-JSON
        errors (e.g. ALB blocked at the edge); we degrade gracefully.
        """
        try:
            body = json.loads(response.content or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(body, dict):
            return None
        standard = {"type", "title", "status", "code", "detail", "instance"}
        extras = {k: v for k, v in body.items() if k not in standard}
        return cls(
            type=body.get("type", "about:blank"),
            title=body.get("title", "Error"),
            status=int(body.get("status", response.status_code)),
            code=body.get("code", "internal"),
            detail=body.get("detail"),
            instance=body.get("instance"),
            extras=extras or None,
        )


class BatchErrorCode(str, Enum):
    """The stable `code` values the Batch API sends in its problem+json
    errors, and in the `error` of a failed task.

    Members are strings, so they compare equal to `BatchAPIError.code`:

        except BatchAPIError as exc:
            if exc.code == BatchErrorCode.QUOTA_EXCEEDED:
                ...

    The set is open: the API may add codes, and `BatchAPIError.code`
    stays a plain `str`, so keep a fallback branch.
    """

    # Generic
    INVALID_ARGUMENT = "invalid_argument"
    """400: the request failed validation. `extras["invalid_tasks"]`
    lists per-task reasons when the problem is in `tasks`."""
    NOT_FOUND = "not_found"
    """404: no such resource, or it is not yours."""
    CONFLICT = "conflict"
    """409: the request conflicts with the resource's current state."""
    UNAUTHENTICATED = "unauthenticated"
    """401: missing or invalid API key."""
    SUBSCRIPTION_INACTIVE = "subscription_inactive"
    """401: the key is valid, but the subscription is not active
    (past due, unpaid, paused). Fix it in Billing."""
    PAYMENT_REQUIRED = "payment_required"
    """402: the account is out of credits."""
    API_KEY_CAP_REACHED = "api_key_cap_reached"
    """402: this API key reached one of its credit caps. `detail` says
    which cap, and when it resets."""
    PERMISSION_DENIED = "permission_denied"
    """403: the account may not use this feature."""
    QUOTA_EXCEEDED = "quota_exceeded"
    """429: an account limit was reached: active jobs (3, shared with
    Crawl), scheduled jobs (5) or an open job's task cap (100,000). Not
    transient; the client does not retry it."""
    INTERNAL = "internal"
    """500: an unexpected server error. Also the `code` of a
    `BatchAPIError` whose response had no problem body."""
    NOT_IMPLEMENTED = "not_implemented"
    """501: the feature is not available."""

    # Job, run and task lifecycle (409 unless noted)
    JOB_NOT_OPEN = "job_not_open"
    RUN_NOT_TERMINAL = "run_not_terminal"
    """The previous run is still pending or running (for example on a
    rerun), or a large submit is still ingesting (on add-tasks)."""
    RUN_NOT_STOPPABLE = "run_not_stoppable"
    NO_PRIOR_RUN = "no_prior_run"
    NO_MATCHING_TASKS = "no_matching_tasks"
    """A partial rerun's `status` filter matched no task."""
    RUN_HAS_DESCENDANTS = "run_has_descendants"
    LAST_BATCH_RECEIVED = "last_batch_received"
    IDEMPOTENCY_KEY_CONFLICT = "idempotency_key_conflict"
    """The `Idempotency-Key` was used with a different body, or the
    first call with it is still running."""
    TASK_NOT_READY = "task_not_ready"
    PARTIAL_RERUN_WINDOW_EXPIRED = "partial_rerun_window_expired"
    """A partial rerun (`status=`) more than 10 days after the run it
    retries started. A full rerun still works."""
    RESULT_EXPIRED = "result_expired"
    """410: the result is past the job's retention window (14 days by
    default)."""

    # HMAC key rotation (409)
    CANDIDATE_PENDING = "candidate_pending"
    NO_CANDIDATE = "no_candidate"

    # Task input validation (400, often per task in `invalid_tasks`)
    UNSUPPORTED_SCHEME = "unsupported_scheme"
    MALFORMED_URL = "malformed_url"
    MISSING_HOST = "missing_host"
    URL_TOO_LONG = "url_too_long"
    METADATA_TOO_LARGE = "metadata_too_large"
    METADATA_KEY_INVALID = "metadata_key_invalid"
    INVALID_EXTERNAL_ID = "invalid_external_id"
    UNSUPPORTED_METHOD = "unsupported_method"
    BODY_NOT_ALLOWED = "body_not_allowed"
    BODY_TOO_LARGE = "body_too_large"
    BODY_MALFORMED = "body_malformed"
    METHOD_PARAM_CONFLICT = "method_param_conflict"

    # CSV file inputs
    FILE_INPUT_NOT_FOUND = "file_input_not_found"
    """404: no such file input, or its 24 h slot expired."""
    FILE_INPUT_NOT_UPLOADED = "file_input_not_uploaded"
    """409: the CSV was never uploaded to the slot."""
    FILE_INPUT_UNSUPPORTED_FOR_JOB_TYPE = "file_input_unsupported_for_job_type"
    """409: CSV input works only for closed regular jobs and scheduled
    jobs."""

    # Webhook registration (400)
    CALLBACK_URL_MUST_BE_HTTPS = "callback_url_must_be_https"
    CALLBACK_URL_UNRESOLVABLE = "callback_url_unresolvable"
    WEBHOOK_SIGNING_REQUIRES_ACTIVE_KEY = "webhook_signing_requires_active_key"

    # Task level (in a failed task's `error`)
    GATEWAY_UNREACHABLE = "gateway_unreachable"
    """The scraping API could not be reached for this task."""


class BatchAPIError(Exception):
    """A non-2xx response from the Batch API.

    `code` is the RFC 7807 `code` member (e.g. `file_input_not_found`,
    `idempotency_key_conflict`, `api_key_cap_reached`). Stable; safe to
    branch on; `BatchErrorCode` names every code the API sends. `detail`
    is the human-readable explanation (e.g. which credit cap was reached
    and when it resets): display it, don't parse it.
    """

    def __init__(self, status_code: int, problem: ProblemDetail | None, raw: bytes):
        self.status_code = status_code
        self.problem = problem
        self.raw = raw
        self.code: str = problem.code if problem else "internal"
        self.detail: str | None = problem.detail if problem else None
        msg = (
            f"{status_code} {problem.title}: {problem.detail or problem.code}"
            if problem
            else f"{status_code} (no problem body)"
        )
        super().__init__(msg)

    @classmethod
    def from_response(cls, response: httpx.Response) -> "BatchAPIError":
        return cls(
            status_code=response.status_code,
            problem=ProblemDetail.from_response(response),
            raw=response.content,
        )
