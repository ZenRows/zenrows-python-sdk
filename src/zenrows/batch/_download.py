"""Download helpers for the Batch SDK.

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
"""

import os
import threading
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Protocol

import httpx

from zenrows.batch.errors import BatchAPIError
from zenrows.batch.models import TaskResult, TaskStatus


class _BodyFetcher(Protocol):
    """The slice of `ZenRowsBatchClient` the helpers need — just result
    pagination. Bodies are pulled from each row's presigned `result_url`,
    so no content endpoint is involved.

    Declared so unit tests can substitute a fake without spinning up
    the whole client.
    """

    def _iter_results_raw(
        self,
        job_id: str,
        *,
        run_id: str | None = ...,
        status: str | None = ...,
    ) -> Iterator[TaskResult]: ...


@dataclass(slots=True)
class DownloadedResult:
    """One row's worth of downloaded content held in memory."""

    task_id: str
    external_id: str | None
    content_type: str
    body: bytes


# Safety caps. Deliberate, conservative defaults — bulk downloads of
# scraping jobs can easily reach gigabytes if not bounded.
DEFAULT_MAX_FILES = 100_000
DEFAULT_MAX_BYTES_PER_FILE = 50 * 1024 * 1024  # 50 MiB
DEFAULT_MAX_COUNT_IN_MEMORY = 10_000
DEFAULT_MAX_TOTAL_BYTES_IN_MEMORY = 500 * 1024 * 1024  # 500 MiB


class DownloadLimitExceeded(RuntimeError):
    """Raised when a download exceeds a configured cap."""

    def __init__(self, limit_name: str, limit: int, observed: int):
        self.limit_name = limit_name
        self.limit = limit
        self.observed = observed
        super().__init__(f"download: {limit_name} cap exceeded ({observed} > {limit})")


# ----- to-disk -----


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
    max_bytes_per_file: int = DEFAULT_MAX_BYTES_PER_FILE,
) -> int:
    """Stream every task's body into `target_dir`. Returns the count.

    `concurrency` parallelises the body-fetch + write. With the
    default 1, behaviour is exactly serial. With N>1, up to N bodies
    are fetched in flight; iteration of the result list stays
    single-threaded so we never out-pace the server's pagination.

    `progress=True` shows a live count via `tqdm` if it's
    importable.

    See module docstring for the naming + capping rules.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    if not name_fn:
        name_fn = _external_id_filename if use_external_id else _default_filename

    # `external_id` is NOT unique on the server side (callers can
    # reuse it), so multiple rows can ask for the same filename.
    # The allocator hands the first claimer the bare name and tacks
    # `_01`, `_02`, … on subsequent claimers. Thread-safe under
    # concurrency > 1.
    allocator = _NameAllocator()

    def handle(row: TaskResult) -> int:
        body, _ct = _fetch_result_url(row)
        if len(body) > max_bytes_per_file:
            raise DownloadLimitExceeded("max_bytes_per_file", max_bytes_per_file, len(body))
        chosen = allocator.claim(name_fn(row))
        path = target_dir / chosen
        # parent dirs may have been requested by a custom name_fn
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return 1

    return _run_bulk(
        client,
        job_id,
        run_id=run_id,
        status=status,
        per_row=handle,
        concurrency=concurrency,
        progress=progress,
        max_rows=max_files,
        max_rows_limit_name="max_files",
        progress_description="downloading to disk",
    )


def _default_filename(row: TaskResult) -> str:
    """`<task_id>.<ext>`. Server-assigned ULID — collision-free."""
    return f"{row.task_id}.{_ext_for(row)}"


def _external_id_filename(row: TaskResult) -> str:
    """`<external_id>.<ext>`, coerced to a filesystem-safe name.

    `external_id` is caller-controlled *data* and can legitimately hold
    characters that aren't valid in a filename (spaces, slashes, …), so
    when writing files we **coerce** rather than reject: every char
    outside `[A-Za-z0-9._-]` becomes `_`, and a missing/empty
    external_id falls back to `task_id`. (Validating here would fail on
    data the API already accepted; enforce clean ids at submit time if
    you need to.) Pass a custom `name_fn` for different behaviour.

    `external_id` is not server-enforced unique; the name allocator
    (see `_NameAllocator`) handles cross-row collisions by appending
    `_01`, `_02`, … to later claimers.
    """
    raw = row.external_id or ""
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in raw)
    base = safe or row.task_id
    return f"{base}.{_ext_for(row)}"


class _NameAllocator:
    """Hands out unique filenames against a running set.

    `claim("order-1.html")` returns `"order-1.html"` the first time
    and `"order-1_01.html"`, `"order-1_02.html"`, … on subsequent
    calls with the same input. Thread-safe; `download_to_dir` with
    `concurrency=N` shares one allocator across workers.

    Doesn't peek at the filesystem — only the in-memory counter
    matters. Pre-existing files in `target_dir` from a previous run
    are NOT considered for collision; if you re-download into a
    populated directory, you'll overwrite the first occurrence of
    each name. Use a fresh dir if that matters.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def claim(self, name: str) -> str:
        with self._lock:
            n = self._counts.get(name, 0)
            self._counts[name] = n + 1
        if n == 0:
            return name
        stem, ext = os.path.splitext(name)
        return f"{stem}_{n:02d}{ext}"


def _ext_for(row: TaskResult) -> str:
    """File extension for one result. `row.type` is the pydantic
    `ResultType` enum or None; failed-but-downloaded rows have no
    type and fall back to `.bin`."""
    if not row.type:
        return "bin"
    return row.type.value


# ----- single task (straight from the presigned result_url) -----
#
# NOTE (please read before "improving" this): the API also exposes a
# `GET .../tasks/{id}/content` endpoint, but it exists for the **web UI**
# — it proxies the body back through the API with display-friendly
# headers so a browser can render it inline. The SDK deliberately does
# NOT use it: `result_url` is a presigned storage URL, so fetching it is
# one hop straight to the bucket (no API round-trip, no auth header, and
# it keeps body bandwidth off the API). Do not reintroduce a
# `/content`-based download path here — pull from `result_url`.


def _fetch_result_url(task: TaskResult) -> tuple[bytes, str]:
    """GET a task's presigned `result_url` directly — no API round-trip
    and no auth header (it's a signed storage URL). Returns
    `(body, content_type)`. Raises `ValueError` when the task carries no
    result (e.g. a failed task)."""
    if not task.result_url:
        raise ValueError(
            f"task {task.task_id!r} has no result_url — "
            "only successful tasks have a downloadable body"
        )
    with httpx.Client(timeout=httpx.Timeout(60.0)) as bare:
        r = bare.get(str(task.result_url))
    if r.status_code >= 400:
        raise BatchAPIError(r.status_code, problem=None, raw=r.content)
    return r.content, r.headers.get("Content-Type", "")


def download_task_to_file(task: TaskResult, target: "str | Path | IO[bytes]") -> None:
    """Download one task's body straight from its `result_url` and write
    it to `target` — a filesystem path (`str` / `Path`) or an already-open
    binary file object."""
    body, _ct = _fetch_result_url(task)
    if isinstance(target, (str, Path)):
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    else:
        target.write(body)


def download_task_to_memory(task: TaskResult) -> bytes:
    """Download one task's body straight from its `result_url` and
    return the raw bytes."""
    return _fetch_result_url(task)[0]


# ----- to-memory -----


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
    max_bytes_per_file: int = DEFAULT_MAX_BYTES_PER_FILE,
) -> list[DownloadedResult]:
    """Load every (matching) task body into a list and return it.

    Three independent caps that all raise `DownloadLimitExceeded`:
      - `max_count` — how many rows total
      - `max_total_bytes` — running sum of body sizes
      - `max_bytes_per_file` — any single oversize body aborts

    `concurrency` + `progress` work the same as on `download_to_dir`.
    Returned list ordering is NOT guaranteed when concurrency > 1.
    """
    out: list[DownloadedResult] = []
    total = [0]  # boxed so the closure can mutate

    def handle(row: TaskResult) -> int:
        body, content_type = _fetch_result_url(row)
        if len(body) > max_bytes_per_file:
            raise DownloadLimitExceeded("max_bytes_per_file", max_bytes_per_file, len(body))
        total[0] += len(body)
        if total[0] > max_total_bytes:
            raise DownloadLimitExceeded("max_total_bytes", max_total_bytes, total[0])
        out.append(
            DownloadedResult(
                task_id=row.task_id,
                external_id=row.external_id,
                content_type=content_type,
                body=body,
            )
        )
        return 1

    _run_bulk(
        client,
        job_id,
        run_id=run_id,
        status=status,
        per_row=handle,
        concurrency=concurrency,
        progress=progress,
        max_rows=max_count,
        max_rows_limit_name="max_count",
        progress_description="downloading to memory",
    )
    return out


# ----- shared driver -----


def _run_bulk(
    client: _BodyFetcher,
    job_id: str,
    *,
    run_id: str | None,
    status: str | None,
    per_row: Callable[[TaskResult], int],
    concurrency: int,
    progress: bool,
    max_rows: int,
    max_rows_limit_name: str,
    progress_description: str,
) -> int:
    """Iterate results + apply `per_row` to each, with optional
    parallelism + progress bar.

    Returns the number of rows processed.

    Cap checking is done *here* rather than in `per_row` so the
    count is authoritative even when the closures run out of order.
    """
    rows = client._iter_results_raw(job_id, run_id=run_id, status=status)

    with _maybe_progress(progress, progress_description) as advance:
        if concurrency <= 1:
            written = 0
            for row in rows:
                if written >= max_rows:
                    raise DownloadLimitExceeded(max_rows_limit_name, max_rows, written + 1)
                per_row(row)
                written += 1
                advance(1)
            return written

        return _run_parallel(
            rows,
            per_row=per_row,
            concurrency=concurrency,
            max_rows=max_rows,
            max_rows_limit_name=max_rows_limit_name,
            advance=advance,
        )


def _run_parallel(
    rows: Iterable[TaskResult],
    *,
    per_row: Callable[[TaskResult], int],
    concurrency: int,
    max_rows: int,
    max_rows_limit_name: str,
    advance: Callable[[int], None],
) -> int:
    """Fan `per_row` across a thread pool.

    We submit at most `concurrency * 2` futures at a time so the
    upstream paginator doesn't race ahead and buffer the entire
    result set in memory. The pool is sized to `concurrency`; the
    extra slack absorbs latency without unbounded queuing.
    """
    written = 0
    in_flight: set[Any] = set()
    pool = ThreadPoolExecutor(max_workers=concurrency)
    iterator = iter(rows)
    try:
        # Prime the pool.
        for _ in range(concurrency * 2):
            try:
                row = next(iterator)
            except StopIteration:
                break
            if written + len(in_flight) >= max_rows:
                raise DownloadLimitExceeded(max_rows_limit_name, max_rows, max_rows + 1)
            in_flight.add(pool.submit(per_row, row))

        while in_flight:
            # Drain completed futures one at a time so we can keep
            # filling the pool incrementally.
            done = next(as_completed(in_flight))
            in_flight.discard(done)
            done.result()  # re-raises any worker exception
            written += 1
            advance(1)

            # Refill — but stop submitting once we've reached the cap.
            if written + len(in_flight) >= max_rows:
                # Drain the rest of the in-flight futures; do NOT
                # submit more.
                continue
            try:
                row = next(iterator)
            except StopIteration:
                continue
            in_flight.add(pool.submit(per_row, row))
    finally:
        pool.shutdown(wait=True)
    return written


@contextmanager
def _maybe_progress(enabled: bool, description: str):
    """Yields an `advance(n)` callable. With `tqdm` installed and
    `enabled=True`, drives a `tqdm` bar; otherwise a no-op closure.

    `tqdm` is a soft dependency — leaving it out is fine, you just
    don't get a progress bar."""
    if not enabled:
        with nullcontext():
            yield lambda _n: None
        return
    try:
        from tqdm.auto import tqdm
    except ImportError:
        # tqdm not installed — quietly degrade.
        with nullcontext():
            yield lambda _n: None
        return

    # `total=None` shows an indeterminate spinner that fills in once
    # the count is known; we don't know it upfront because the result
    # set is streamed via cursor pagination.
    with tqdm(total=None, desc=description, unit="task") as bar:
        yield bar.update


__all__ = [
    "DEFAULT_MAX_BYTES_PER_FILE",
    "DEFAULT_MAX_COUNT_IN_MEMORY",
    "DEFAULT_MAX_FILES",
    "DEFAULT_MAX_TOTAL_BYTES_IN_MEMORY",
    "DownloadLimitExceeded",
    "DownloadedResult",
    "download_to_dir",
    "download_to_memory",
]
