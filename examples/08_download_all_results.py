"""08: Download a whole run as one zip (server-side export).

`download_all_results(path)` drives the async export flow:
`POST .../exports` kicks off a server-side zip of every task body,
the SDK polls until it's `completed`, then streams the presigned zip
to disk. Use this when you want a single artifact; for one file per
task instead, see `download_to_dir` (example 02).

The zip has a 12h TTL and is capped at 1 GiB server-side — an
oversize run fails the export and raises `BatchAPIError`. For larger
runs (or one file per task), use the iterative client-side download
`download_to_dir` (example 02): it has no size limit but is slower,
fetching bodies one at a time (tunable via `concurrency=`).

`job.run.download_all_results` targets the job's current run; address
a specific run with `client.get_run(job_id, run_id=...).download_all_results(...)`.

Run with:
    export ZENROWS_API_KEY=zr_...
    python examples/08_download_all_results.py --job-id 01J… --out results.zip
"""

import argparse
import os

from zenrows import ZenRowsBatchClient
from zenrows.batch import BatchAPIError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job-id", required=True, help="Job whose latest run to export.")
    parser.add_argument("--out", default="results.zip", help="Where to write the zip.")
    args = parser.parse_args()

    client = ZenRowsBatchClient(api_key=os.environ["ZENROWS_API_KEY"])
    job = client.get_job(args.job_id)

    try:
        path = job.run.download_all_results(args.out)
        print(f"wrote {path}")
    except BatchAPIError as e:
        # e.g. the run exceeded the 1 GiB export cap.
        print(f"export failed: {e}")


if __name__ == "__main__":
    main()
