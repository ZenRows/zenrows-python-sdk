"""03: Submit thousands of URLs via a CSV upload.

For jobs with too many URLs to fit comfortably in a JSON payload,
the Batch API exposes a two-step upload flow:

    1. POST /job_inputs  → presigned PUT URL + file_input_id
    2. PUT <presigned>   → upload the CSV body
    3. POST /jobs        → reference the file_input_id

`upload_csv` collapses (1)+(2) into one call; `submit_regular`
takes the resulting `file_input_id` as a kwarg instead of inline
`urls`. Eligible for `regular` (closed) and `scheduled` jobs.

The `fields` map says how to interpret the CSV. Each value is either:
  - an integer  → 0-based column index (works regardless of header)
  - a string    → the column's header name (requires `header=True`)

`url` is required; `external_id` is optional.

Run with:
    export ZENROWS_API_KEY=zr_...
    python examples/03_csv_input.py
"""

import os
import tempfile
from pathlib import Path

from zenrows import ZenRowsBatchClient


def main() -> None:
    client = ZenRowsBatchClient(api_key=os.environ["ZENROWS_API_KEY"])

    # Toy CSV for the sake of the example. In real use this is a
    # large file on disk you'd pass a Path to.
    csv = "URL,Customer Ref\nhttps://example.com/a,cust-1\nhttps://example.com/b,cust-2\n"
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tmp:
        tmp.write(csv)
        csv_path = Path(tmp.name)

    file_input_id = client.upload_csv(
        csv_path,
        fields={"url": "URL", "external_id": "Customer Ref"},
        header=True,
    )
    print(f"uploaded slot {file_input_id}")

    # `submit_regular(file_input_id=...)` — no inline urls, no
    # `type=`/`status=` boilerplate. Returns a JobRef ready to
    # `.run.wait()` and `.run.results()` against.
    job = client.submit_regular(
        file_input_id=file_input_id,
        zenrows_params={"js_render": "true"},
    )
    print(f"submitted {job}")


if __name__ == "__main__":
    main()
