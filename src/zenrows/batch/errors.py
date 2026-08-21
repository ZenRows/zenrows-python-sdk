"""RFC 7807 Problem JSON → friendly Python exceptions.

The Batch API returns errors as `application/problem+json`.
We surface them as `BatchAPIError` with a structured `problem` payload
plus a flat `code` shortcut so callers can branch without indexing
into a dict.
"""

import json
from dataclasses import dataclass
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


class BatchAPIError(Exception):
    """A non-2xx response from the Batch API.

    `code` is the RFC 7807 `code` member (e.g. `file_input_not_found`,
    `idempotency_key_conflict`). Stable; safe to branch on.
    """

    def __init__(self, status_code: int, problem: ProblemDetail | None, raw: bytes):
        self.status_code = status_code
        self.problem = problem
        self.raw = raw
        self.code: str = problem.code if problem else "internal"
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
