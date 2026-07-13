"""02: Bulk download every successful body to disk.

The Batch API doesn't ship bodies inline — results give you metadata
+ a presigned `result_url` per task. For real bulk ingestion you
usually want every body on disk; `download_to_dir` bundles
"list → presigned GET → write" into one call with built-in safety
caps + optional parallelism + progress bar.

Highlights:
  - Resource-style: `client.get_job(...)` returns a `JobHandle` so
    the current-run wait + download are one chain off `.run`.
  - `concurrency=N` fans body-fetches across a ThreadPool.
  - `progress=True` shows a tqdm bar (soft dep — degrades to no-op
    if tqdm isn't installed).
  - `use_external_id=True` writes `<external_id>.<ext>` filenames
    instead of the default `<task_id>.<ext>`. Useful when the
    downstream pipeline addresses files by your own ids; ids are
    coerced to safe filenames (chars outside `[A-Za-z0-9._-]` become
    `_`), missing ids fall back to task_id, and clashes get `_01`,
    `_02`, … appended.
  - `max_files` + `max_bytes_per_file` are tunable safety caps that
    raise `DownloadLimitExceeded` — a runaway job can't silently
    fill the disk.
  - `status="successful"` (the default) skips failed rows; pass
    `status=None` to grab everything.

Run with:
    export ZENROWS_API_KEY=zr_...
    python examples/02_download_to_dir.py --job-id 01J…
"""

import argparse
import os

from zenrows import ZenRowsBatchClient
from zenrows.batch import DownloadLimitExceeded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--job-id",
        required=True,
        help="Job id whose results you want to download.",
    )
    parser.add_argument(
        "--out",
        default="./out",
        help="Target directory (default: ./out).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Parallel body-fetch workers (default: 8).",
    )
    args = parser.parse_args()

    client = ZenRowsBatchClient(api_key=os.environ["ZENROWS_API_KEY"])
    job = client.get_job(args.job_id)
    run = job.run.wait(timeout=600.0, progress=True)

    try:
        written = run.download_to_dir(
            args.out,
            use_external_id=True,  # name files by caller's id
            concurrency=args.concurrency,  # parallel body fetches
            progress=True,  # live tqdm bar
            max_files=20_000,  # cap row count
            max_bytes_per_file=10 * 1024 * 1024,  # cap single body @ 10 MiB
        )
        print(f"wrote {written} files to {args.out}/")
    except DownloadLimitExceeded as exc:
        # `limit_name` is one of: max_files, max_bytes_per_file.
        # `limit` + `observed` for diagnosis.
        print(f"aborted: {exc.limit_name} cap hit ({exc.observed} > {exc.limit})")
        raise


if __name__ == "__main__":
    main()
