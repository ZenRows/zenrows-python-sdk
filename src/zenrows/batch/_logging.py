"""Structured logging for the Batch SDK.

One logger per module under the `zenrows.batch` namespace. Logging
follows the stdlib `logging` conventions — callers opt in by
configuring a handler on `zenrows.batch` (or any ancestor); we never
install one ourselves. That keeps quiet libraries quiet by default
and lets apps route Batch SDK events into whatever observability
stack they already have.

Log levels we use:
  - DEBUG   one record per HTTP request (method, path, status, ms).
  - INFO    notable client-side events (waiter started, download
            completed, key rotation).
  - WARNING soft errors a caller probably wants to know about
            (progress dep missing while progress=True, etc.).
  - ERROR   non-2xx responses that turned into BatchAPIError.

Fields are passed as `logging.LogRecord` `extra=` so structured-log
backends (json, OpenTelemetry, Datadog) can index them. Plain-text
formatters see the message string and ignore the extras.
"""

import logging
from collections.abc import Mapping
from typing import Any

# Root logger for the Batch SDK. Submodules get child loggers via
# `_logger("zenrows.batch.client")` etc. — children inherit handlers
# from this one when callers configure it.
ROOT = "zenrows.batch"


def logger(name: str) -> logging.Logger:
    """`logging.getLogger` namespaced under `zenrows.batch`.

    Pass the bare module name (e.g. `"client"`); the prefix is
    added here so callers never have to remember the full string.
    """
    return logging.getLogger(f"{ROOT}.{name}")


def log_request(
    log: logging.Logger,
    *,
    method: str,
    path: str,
    status: int,
    elapsed_ms: float,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Emit a per-request DEBUG record. Cheap when the level is off.

    The structured fields (`extra=`) match what most backends index
    on out of the box: `http.method`, `http.path`, `http.status`,
    `elapsed_ms`. Plain-text formatters get a single-line message.
    """
    if not log.isEnabledFor(logging.DEBUG):
        return
    payload: dict[str, Any] = {
        "http.method": method,
        "http.path": path,
        "http.status": status,
        "elapsed_ms": round(elapsed_ms, 2),
    }
    if extra:
        payload.update(extra)
    log.debug("%s %s -> %d (%.1f ms)", method, path, status, elapsed_ms, extra=payload)


def log_error(
    log: logging.Logger,
    *,
    method: str,
    path: str,
    status: int,
    code: str,
    detail: str | None,
) -> None:
    """Emit an ERROR record for a non-2xx that becomes BatchAPIError."""
    log.error(
        "%s %s -> %d %s%s",
        method,
        path,
        status,
        code,
        f": {detail}" if detail else "",
        extra={
            "http.method": method,
            "http.path": path,
            "http.status": status,
            "error.code": code,
        },
    )


__all__ = ["ROOT", "log_error", "log_request", "logger"]
