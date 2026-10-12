"""Clean-up: stop every crawl this run started, then record each one's final state.

Reads out/results/crawls.jsonl, the ledger the checks write. A crawl still running is
stopped; then each crawl is read for its final status and coverage.pages_fetched, which the
report uses for the cost table. Writes out/cleanup.json.
"""
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request

OUT = sys.argv[1]
BASE = os.environ.get("CRAWL_API_BASE", "https://api.zenrows.com/v1").rstrip("/")
KEY = os.environ["ZENROWS_API_KEY"]


def call(method, path):
    req = urllib.request.Request(BASE + path, method=method, headers={"X-API-Key": KEY})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null"), r.headers
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null"), e.headers
        except ValueError:
            return e.code, None, e.headers


def stop(crawl_id):
    """POST stop, retrying 503 crawl_busy up to 3 times. Returns the final HTTP status."""
    for attempt in range(4):
        status, body, headers = call("POST", f"/crawls/{crawl_id}/stop")
        busy = status == 503 and isinstance(body, dict) and body.get("code") == "crawl_busy"
        if not busy or attempt == 3:
            return status
        try:
            wait = float(headers.get("Retry-After", 1))
        except ValueError:
            wait = 1
        time.sleep(min(wait if wait >= 0 else 1, 30))


crawls = {}
ledger = os.path.join(OUT, "results", "crawls.jsonl")
if os.path.exists(ledger):
    with open(ledger) as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                entry = crawls.setdefault(row["crawl_id"], {"purposes": []})
                if row["purpose"] not in entry["purposes"]:
                    entry["purposes"].append(row["purpose"])

rows = []
for crawl_id, entry in crawls.items():
    stopped = False
    try:
        status, body, _ = call("GET", f"/crawls/{crawl_id}?limit=1")
        if status == 200 and body and body.get("status") == "running":
            stopped = stop(crawl_id) < 300
            time.sleep(2)
            status, body, _ = call("GET", f"/crawls/{crawl_id}?limit=1")
    except (OSError, http.client.HTTPException, ValueError) as e:
        # One unreachable crawl must not keep the others running.
        rows.append({"crawl_id": crawl_id, **entry, "stopped_by_cleanup": stopped, "error": repr(e)})
        print(f"{crawl_id} not checked: {e!r}")
        continue
    row = {"crawl_id": crawl_id, **entry, "stopped_by_cleanup": stopped, "http": status}
    if status == 200 and body:
        row.update(status=body.get("status"), stop_reason=body.get("stop_reason"),
                   pages_fetched=body.get("coverage", {}).get("pages_fetched"),
                   output_format=body.get("output_format"))
    rows.append(row)
    print(f"{crawl_id} {row.get('status')} pages_fetched={row.get('pages_fetched')}"
          f"{' (stopped by clean-up)' if stopped else ''}")

still = [r["crawl_id"] for r in rows if r.get("status") == "running" or "error" in r]
with open(os.path.join(OUT, "cleanup.json"), "w") as f:
    json.dump({"crawls": rows, "still_running": still,
               "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, f, indent=2)
print(f"clean-up: {len(rows)} crawls, {len(still)} still running")
