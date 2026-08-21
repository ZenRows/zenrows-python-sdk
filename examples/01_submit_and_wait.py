"""01: Submit → wait → iterate results.

The canonical end-to-end flow. Demonstrates:
  - `submit_regular(urls, ...)` — the type-specific shortcut that
    skips the `type=`/`status=` boilerplate. Accepts either bare URL
    strings or inline dicts with per-task `external_id` / `metadata`
    / `params`.
  - The returned `JobRef` knows its client, so current-run ops chain
    off the `.run` facet (`job.run.wait()` / `job.run.results()`)
    without re-passing the job id.
  - `job.run.wait()` returns a `RunHandle` for the run you waited on
    (guaranteed `.data`), so `run.results()` and
    `run.download_to_dir(...)` are one call away.

Run with:
    export ZENROWS_API_KEY=zr_...
    python examples/01_submit_and_wait.py
"""

import os

from zenrows import ZenRowsBatchClient


def main() -> None:
    client = ZenRowsBatchClient(api_key=os.environ["ZENROWS_API_KEY"])

    job = client.submit_regular(
        [
            {"url": "https://example.com/a", "external_id": "order-1"},
            {"url": "https://example.com/b", "external_id": "order-2"},
            {"url": "https://example.com/c", "external_id": "order-3"},
        ],
        zenrows_params={"js_render": "true", "premium_proxy": "true"},
    )
    print(f"submitted {job}")

    # Block until the current run is terminal (default target:
    # `completed | stopped | deleted`). Internally jittered
    # exponential backoff — friendly to the API for both 5-second
    # and 5-minute jobs. Returns the RunHandle.
    run = job.run.wait(timeout=600.0)
    t = run.data.stats
    print(f"run {run.run_id} {run.data.status.value}: {t.successful}/{t.total} successful")

    for row in run.results(status="successful"):
        # external_id is whatever the caller supplied at submit; it
        # rides through to results unmodified so caller systems can
        # correlate without knowing our task_id.
        print(f"  {row.external_id or row.task_id} -> {row.result_url}")


if __name__ == "__main__":
    main()
