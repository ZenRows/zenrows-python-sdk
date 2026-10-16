"""Python SDK (`zenrows`): the README walk-through, checks 1-21 and the Python extra check.

Runs in a clean python image. run-in-container.sh installs the wheel first; this script then
imports it the way a user would.
"""

import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import uuid
from datetime import UTC, datetime
from importlib import metadata

SURFACE = "python"
KEY = os.environ.get("ZENROWS_API_KEY", "")
HAS_KEY = bool(KEY)
CRAWL_URL = os.environ["CRAWL_URL"]
INCLUDE = os.environ["CRAWL_INCLUDE"]
API_BASE = os.environ.get("CRAWL_API_BASE", "https://api.zenrows.com/v1")
NO_MATCH = (
    "/crawl-acceptance-matches-nothing/"  # no URL contains it: the crawl keeps nothing (check 19)
)
DOCUMENTED_DEFAULT = 100  # the default the README documents for max_pages and max_items (check 21)
RESULTS = "/results"
PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
TERMINAL = {"completed", "stopped", "failed"}
os.makedirs(f"{RESULTS}/checks", exist_ok=True)
_seq = 0


def redact(v):
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    return s.replace(KEY, "<REDACTED>") if KEY else s


def trim(v, n):
    s = redact(v)
    return s if len(s) <= n else f"{s[:n]}… [{len(s) - n} more chars]"


def record(check, title, status, expected, actual, evidence=""):
    global _seq
    _seq += 1
    r = {
        "surface": SURFACE,
        "check": str(check),
        "title": title,
        "status": status,
        "expected": expected,
        "actual": trim(actual, 800),
        "evidence": trim(evidence, 2500),
    }
    name = f"{_seq:03d}-{re.sub(r'[^A-Za-z0-9_.-]+', '_', str(check))}.json"
    with open(f"{RESULTS}/checks/{name}", "w") as f:
        json.dump(r, f, indent=2)
    first = r["actual"].splitlines()[0][:200] if r["actual"] else ""
    print(f"[{status}] python {check} {title}: {first}", flush=True)


def pass_if(ok):
    return PASS if ok else FAIL


def run_check(check, title, expected, fn, live=False):
    if live and not HAS_KEY:
        return record(
            check, title, SKIP, expected, "ZENROWS_API_KEY not set (run without --no-key)"
        )
    try:
        status, actual, evidence = fn()
        record(check, title, status, expected, actual, evidence)
    except Exception as e:  # an unexpected exception is a FAIL with its traceback
        record(
            check, title, FAIL, expected, f"raised {type(e).__name__}: {e}", traceback.format_exc()
        )


def ledger(crawl_id, purpose):
    if crawl_id:
        with open(f"{RESULTS}/crawls.jsonl", "a") as f:
            f.write(json.dumps({"crawl_id": crawl_id, "purpose": purpose}) + "\n")


def sh(args, **kw):
    p = subprocess.run(args, capture_output=True, text=True, **kw)
    return p.returncode, p.stdout + p.stderr


def crawls_since(t):
    if not HAS_KEY:
        return []
    req = urllib.request.Request(f"{API_BASE}/crawls?limit=100", headers={"X-API-Key": KEY})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.load(resp)
    out = []
    for c in body["crawls"]:
        created = datetime.strptime(c["created_at"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
        if created.timestamp() >= t:
            out.append(c["crawl_id"])
    return out


# ---------- README: install (run-in-container.sh) and the Crawl quickstart ----------
with open(f"{RESULTS}/pip-install.log") as f:
    install_log = f.read()
record(
    "readme-1",
    "Install (README: `pip install zenrows`; here the release wheel)",
    PASS,
    "the package installs",
    f"zenrows {metadata.version('zenrows')} installed",
    f"$ pip install <wheel>\n{install_log[-1200:]}",
)

readme = metadata.metadata("zenrows").get_payload() or ""
HEADING = "## Quickstart — Crawl API (new)"
section = readme.split(HEADING, 1)[1].split("\n## ", 1)[0] if HEADING in readme else ""
m = re.search(r"```python\n(.*?)```", section, re.S)
block = m.group(1) if m else None
record(
    "readme-2",
    "README has a Crawl quickstart",
    pass_if(bool(block)),
    "a ```python block under the Crawl quickstart",
    f"{len(block.splitlines())} lines" if block else "not found",
    block or section[:600],
)

subs = [
    ('"https://www.scrapingcourse.com/ecommerce/"', json.dumps(CRAWL_URL), "start URL → CRAWL_URL"),
    ("max_items=50,", "max_items=5,", "max_items 50 → 5"),
    ("max_pages=100,", "max_pages=10,", "max_pages 100 → 10 (at most 10)"),
    (
        'include_patterns=["/product/"]',
        f"include_patterns=[{json.dumps(INCLUDE)}]",
        "include → CRAWL_INCLUDE",
    ),
]
if API_BASE != "https://api.zenrows.com/v1":
    # The quickstart leaves the base URL out, so it would reach production
    # whatever CRAWL_API_BASE says. Point it where the rest of the run goes.
    subs.append(
        (
            'ZenRowsCrawlClient(api_key=os.environ["ZENROWS_API_KEY"])',
            'ZenRowsCrawlClient(api_key=os.environ["ZENROWS_API_KEY"], '
            f"base_url={json.dumps(API_BASE)})",
            "base URL → CRAWL_API_BASE",
        )
    )
quick, applied = block or "", []
for a, b, why in subs:
    if a in quick:
        quick = quick.replace(a, b)
        applied.append(why)
    else:
        applied.append(f"not found, left as is: {why}")
applied.append("the key comes from ZENROWS_API_KEY, as the quickstart reads it")
with open("/work/quickstart.py", "w") as f:
    f.write(quick)


def readme_run():
    since = time.time() - 5
    code, out = sh([sys.executable, "/work/quickstart.py"], timeout=12 * 60)
    mine = crawls_since(since)
    for i in mine:
        ledger(i, "README quickstart")
    return (
        pass_if(code == 0),
        f"exit {code}; crawls: {', '.join(mine) or 'none'}",
        f"substitutions: {'; '.join(applied)}\n$ python quickstart.py\n{out}",
    )


run_check(
    "readme-3",
    "Run the README Crawl quickstart",
    "it runs to the end without an error",
    readme_run,
    live=True,
)


# ---------- extra: a typed package (py.typed) under mypy --strict ----------
def mypy_check():
    code, out = sh(
        [sys.executable, "-m", "mypy", "--strict", "--no-incremental", "/harness/typecheck.py"]
    )
    untyped = (
        "missing library stubs or py.typed" in out or "module is installed, but missing" in out
    )
    revealed = "def (url: builtins.str" in out
    errors = [line for line in out.splitlines() if ": error:" in line]
    return (
        pass_if(not untyped and revealed and not errors),
        f"mypy exit {code}; py.typed seen {not untyped}; "
        f"real signature revealed {revealed}; {len(errors)} errors",
        f"$ mypy --strict typecheck.py (mypy {metadata.version('mypy')})\n{out}",
    )


run_check(
    "x-python-typed",
    "mypy --strict sees the package as typed (py.typed)",
    "no missing-stubs error; the real signatures",
    mypy_check,
)

# ---------- check 1, offline half: only the fields you set are sent ----------
captured = []


class StandIn(http.server.BaseHTTPRequestHandler):
    """A local stand-in for the Crawl API that records what the client sends."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"null")
        captured.append(body)
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        crawl = {
            "crawl_id": "c_capture",
            "status": "running",
            "url": body.get("url"),
            "max_items": DOCUMENTED_DEFAULT,
            "max_pages": DOCUMENTED_DEFAULT,
            "created_at": datetime.now(UTC).isoformat(),
            "coverage": {"pages_fetched": 0, "pages_failed": 0, "items_found": 0},
        }
        if "depth" in body:  # as the API: a crawl created without depth omits it
            crawl["depth"] = body["depth"]
        self.wfile.write(json.dumps(crawl).encode())

    def do_GET(self):
        if "c_nobody" in self.path:
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(404)
        self.send_header("Content-Type", "application/problem+json")
        self.end_headers()
        self.wfile.write(b'{"status":404,"title":"Not Found","code":"crawl_not_found"}')


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StandIn)
threading.Thread(target=srv.serve_forever, daemon=True).start()

from zenrows import ZenRowsCrawlClient  # noqa: E402  (imported once installed, as a user would)
from zenrows.batch._open_enum import is_unknown  # noqa: E402
from zenrows.crawl import CrawlAPIError, CrawlErrorCode  # noqa: E402

cap = ZenRowsCrawlClient(
    "capture-key", base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1", retries=0
)
minimal = cap.create("https://example.com/")  # depth is optional: no depth limit
cap.create(
    "https://example.com/",
    depth=1,
    max_items=5,
    max_pages=10,
    include_patterns=["/p/"],
    idempotency_key="idem-1",
)
sent_ok = (
    sorted(captured[0]) == ["url"]
    and minimal.depth is None
    and sorted(captured[1])
    == sorted(["url", "depth", "max_items", "max_pages", "include_patterns"])
)
sent_line = (
    f"sent (stand-in server): minimal={json.dumps(captured[0])} full={json.dumps(captured[1])}"
)

# ---------- live ----------
client = ZenRowsCrawlClient(KEY or "none", base_url=API_BASE)
BASE = {"depth": 1, "max_items": 5, "max_pages": 10, "include_patterns": [INCLUDE]}
state = {}


def create(**kw):
    for _ in range(10):
        try:
            c = client.create(CRAWL_URL, **kw)
            ledger(c.crawl_id, "check")
            return c
        except CrawlAPIError as e:
            if e.status_code != 429:
                raise
            time.sleep(e.retry_after or 30)  # the account's active jobs are shared: wait as told
    raise RuntimeError("still 429 after 10 tries")


def err_of(fn):
    try:
        return fn(), None
    except CrawlAPIError as e:
        return None, e


def esum(e):
    return None if e is None else {"status_code": e.status_code, "code": e.code, "detail": e.detail}


def c1():
    if not HAS_KEY:
        return (
            (SKIP if sent_ok else FAIL),
            f"live part skipped (no key); only set fields sent: {sent_ok}",
            sent_line,
        )
    t = time.time()
    a = state["A"] = create(**BASE)
    echo = {
        "url": a.url,
        "depth": a.depth,
        "max_items": a.max_items,
        "max_pages": a.max_pages,
        "include_patterns": a.include_patterns,
    }
    want = {"url": CRAWL_URL, **BASE}
    ok = sent_ok and a.status.value == "running" and a.crawl_id.startswith("c_") and echo == want
    return (
        pass_if(ok),
        f"{a.status.value}, {a.crawl_id}, {int((time.time() - t) * 1000)} ms, "
        f"echo {json.dumps(echo)}; only set fields sent: {sent_ok}",
        f"{a.model_dump_json()}\n{sent_line}",
    )


def c5():
    a = state.get("A")
    if not a:
        return SKIP, "no crawl from check 1", ""
    t = time.time()
    w = client.wait(a.crawl_id, timeout=1)
    ms = int((time.time() - t) * 1000)
    if w.status.value != "running":
        return SKIP, f"the crawl had already ended ({w.status.value}) after {ms} ms", ""
    return pass_if(ms < 5000), f"{w.status.value} after {ms} ms, no exception", w.model_dump_json()


def partial_read():
    a = state.get("A")
    if not a:
        return
    running = client.get(a.crawl_id, limit=1).status.value == "running"
    t = time.time()
    items, e = err_of(lambda: [r.url for r in client.results(a.crawl_id)])
    state["partial"] = (running, len(items or []), int((time.time() - t) * 1000), e)


def c3():
    key = f"acc-{uuid.uuid4()}"
    body = dict(BASE, max_items=3, max_pages=3)
    c = state["C"] = create(idempotency_key=key, **body)
    again = create(idempotency_key=key, **body)
    v, e = err_of(lambda: client.create(CRAWL_URL, idempotency_key=key, **dict(body, max_items=4)))
    if v:
        ledger(v.crawl_id, "idempotency, different body (unexpected create)")
    ok = (
        c.crawl_id == again.crawl_id
        and e is not None
        and e.status_code == 422
        and e.code == "idempotency_key_reused"
    )
    third = f"{e.status_code} {e.code}" if e else f"created {v.crawl_id}"
    return (
        pass_if(ok),
        f"first {c.crawl_id}, second {again.crawl_id}; different body → {third}",
        json.dumps(esum(e)),
    )


def c11():
    c = state.get("C")
    if not c:
        return SKIP, "no crawl from check 3", ""
    first = client.stop(c.crawl_id)
    reason = first.stop_reason.value if first.stop_reason else None
    if first.status.value == "completed" and reason != "user":
        return SKIP, f"the crawl had already ended on its own ({first.status.value}/{reason})", ""
    second, e = err_of(lambda: client.stop(c.crawl_id))
    ok = (
        first.status.value == "stopped"
        and reason == "user"
        and e is None
        and second.status == first.status
    )
    two = f"error {e}" if e else second.status.value
    return (
        pass_if(ok),
        f"first: {first.status.value}/{reason}; second: {two}",
        first.model_dump_json(),
    )


def c4():
    a = state.get("A")
    if not a:
        return SKIP, "no crawl from check 1", ""
    e = state["ended"] = client.wait(a.crawl_id)
    reason = f"/{e.stop_reason.value}" if e.stop_reason else ""
    return (
        pass_if(e.status.value in TERMINAL),
        f"{e.status.value}{reason}; coverage {e.coverage.model_dump_json()}",
        e.model_dump_json(),
    )


def c6():
    e = state.get("ended")
    if not e:
        return SKIP, "no ended crawl", ""
    pages, cursor = [], None
    for _ in range(20):
        p = client.get(e.crawl_id, limit=2, cursor=cursor)
        pages.append((len(p.results), p.next_cursor, [r.url for r in p.results]))
        if p.next_cursor is None:
            break
        cursor = p.next_cursor
    urls = [u for _, _, us in pages for u in us]
    curs = [c for _, c, _ in pages if c is not None]
    ok = (
        all(n <= 2 for n, _, _ in pages)
        and pages[-1][1] is None
        and len(set(curs)) == len(curs)
        and len(set(urls)) == len(urls)
        and (len(pages) >= 2 or e.coverage.items_found <= 2)
    )
    sizes = ",".join(str(n) for n, _, _ in pages)
    return (
        pass_if(ok),
        f"{len(pages)} pages of {sizes}; last next_cursor {pages[-1][1]}; {len(urls)} URLs",
        "",
    )


def c7():
    p1 = client.list(limit=2)
    p2 = client.list(limit=2, cursor=p1.next_cursor) if p1.next_cursor else None
    allc = p1.crawls + (p2.crawls if p2 else [])
    times = [c.created_at for c in allc]
    newest = all(times[i - 1] >= times[i] for i in range(1, len(times)))
    overlap = len({c.crawl_id for c in allc}) != len(allc)
    cursor, pages = (p2.next_cursor if p2 else None), 2
    while cursor and pages < 60:
        cursor = client.list(limit=100, cursor=cursor).next_cursor
        pages += 1
    ok = len(p1.crawls) == 2 and newest and not overlap and not cursor
    return (
        pass_if(ok),
        f"newest first {newest}, no overlap {not overlap}; last page after {pages} pages: "
        f"next_cursor {json.dumps(cursor)}",
        "",
    )


def c8():
    e = state.get("ended")
    if not e:
        return SKIP, "no ended crawl", ""
    urls = [r.url for r in client.results(e.crawl_id, limit=2)]
    distinct, match = len(set(urls)) == len(urls), all(INCLUDE in u for u in urls)
    p = state.get("partial")
    run = (
        (
            f"while running ({'running' if p[0] else 'already ended'}): {p[1]} URLs in {p[2]} ms, "
            f"{'error ' + str(p[3]) if p[3] else 'returned'}"
        )
        if p
        else "not read while running"
    )
    ok = (
        bool(urls)
        and distinct
        and match
        and len(urls) == e.coverage.items_found
        and not (p and p[3])
    )
    return (
        pass_if(ok),
        f"{len(urls)} URLs (items_found {e.coverage.items_found}), distinct {distinct}, "
        f"all match {match}; {run}",
        "\n".join(urls),
    )


def settle(crawl_id):
    """Poll until no result is still pending (the pages in flight finished), at most 90 s."""
    for _ in range(45):
        page = client.get(crawl_id, limit=100)
        if not any(r.content_status and r.content_status.value == "pending" for r in page.results):
            return "settled"
        time.sleep(2)
    return "still pending after 90 s"


def stop_mid_flight():
    """Create the html crawl and stop it as soon as it has kept URLs.

    The stop lands while their pages are fetched.
    """
    try:
        b = state["B"] = create(output_format="html", **BASE)
        g = None
        for _ in range(100):
            g = client.get(b.crawl_id, limit=1)
            if g.status.value != "running" or g.coverage.items_found > 0:
                break
            time.sleep(0.15)
        s = client.stop(b.crawl_id)
        state["stop"] = {"items": g.coverage.items_found, "status": s.status.value}
    except Exception as e:  # recorded by check 20
        state["stop"] = {"error": e}


def c20():
    b, st = state.get("B"), state.get("stop") or {}
    if not b or "error" in st:
        return FAIL, f"setup failed: {st.get('error')}", ""
    if st["status"] != "stopped":
        return SKIP, f"the crawl had already ended ({st['status']}) before the stop", ""
    t = time.time()
    box = {}
    # A daemon thread, so a results() that pages forever fails the check instead of hanging the run.
    reader = threading.Thread(
        target=lambda: box.update(now=list(client.results(b.crawl_id))), daemon=True
    )
    reader.start()
    reader.join(60)
    if "now" not in box:
        return (
            FAIL,
            "results() did not finish within 60 s on a crawl stopped with pages in flight",
            "",
        )
    now, ms = box["now"], int((time.time() - t) * 1000)
    settled = settle(b.crawl_id)
    if settled != "settled":
        settled += " (an API issue: the stop left these pages pending on an ended crawl)"
    later = [r.url for r in client.results(b.crawl_id)]
    same = sorted(r.url for r in now) == sorted(later)
    pending = sum(1 for r in now if r.content_status and r.content_status.value == "pending")
    return (
        pass_if(same and bool(now)),
        f"stopped with {st['items']} kept; read {len(now)} URLs ({pending} pending) in {ms} ms, "
        f"then {settled}: {len(later)} URLs, same set {same}",
        "",
    )


def c2():
    b = state.get("B")
    if not b:
        return SKIP, "no html crawl", ""
    if (state.get("stop") or {}).get("status") == "stopped":
        settle(b.crawl_id)
    rs = list(client.results(b.crawl_id))
    ok = (
        b.output_format is not None
        and b.output_format.value == "html"
        and bool(rs)
        and all(
            r.content_status and (r.content_status.value != "fetched" or r.content_url) for r in rs
        )
    )
    with_url = sum(1 for r in rs if r.content_url)
    return (
        pass_if(ok),
        f"{b.crawl_id} ({(state.get('stop') or {}).get('status', '-')}); {len(rs)} results; "
        f"with content_url {with_url}",
        json.dumps([r.model_dump(mode="json") for r in rs[:2]]),
    )


def c9():
    b = state.get("B")
    if not b:
        return SKIP, "no html crawl", ""

    def first_fetched(crawl_id):
        return next((x for x in client.results(crawl_id) if x.content_url), None)

    # The stop in check 20 can land before any page is fetched. Then read a
    # one-item html crawl that runs to the end instead.
    src = b.crawl_id
    r = first_fetched(src)
    if not r:
        own = create(output_format="html", **{**BASE, "max_items": 1})
        client.wait(own.crawl_id)
        src = own.crawl_id
        r = first_fetched(src)
    if not r:
        return FAIL, f"no result with content_url on {src}", ""
    cid = r.content_url.rstrip("/").rsplit("/", 1)[-1]
    by_id, by_url = client.content(src, cid), client.content(src, r.content_url)
    ok = by_id == by_url and re.search(r"<html|<!doctype", by_id, re.I) is not None
    return (
        pass_if(ok),
        f"by id {len(by_id)} chars, by content_url {len(by_url)} chars, equal {by_id == by_url}",
        "",
    )


def c10():
    b = state.get("B")
    if not b:
        return SKIP, "no html crawl", ""
    with client.download(b.crawl_id) as d:
        status = d.status.value if d.status else None
        lines = list(d.lines)
    fetched = [ln for ln in lines if ln.content_status and ln.content_status.value == "fetched"]
    ok = (
        status is not None
        and bool(lines)
        and all(ln.url and ln.content_status for ln in lines)
        and all(isinstance(ln.content, str) and ln.content for ln in fetched)
    )
    return pass_if(ok), f"status {status}; {len(lines)} lines; {len(fetched)} with content", ""


errs = {}


def c12():
    _, e = err_of(lambda: client.get("c_does_not_exist"))
    errs["not_found"] = e
    return (
        pass_if(e is not None and e.status_code == 404 and e.code == "crawl_not_found"),
        json.dumps(esum(e)),
        "",
    )


def c13():
    v, e = err_of(lambda: client.create(CRAWL_URL, **dict(BASE, depth=0)))
    if v:
        ledger(v.crawl_id, "depth 0 (unexpected create)")
        client.stop(v.crawl_id)
    errs["invalid"] = e
    return (
        pass_if(e is not None and e.status_code == 422 and e.code == "invalid_parameter"),
        json.dumps(esum(e) or "created"),
        "",
    )


def c14():
    v, e = err_of(lambda: client.create(CRAWL_URL, output_format="json", **BASE))  # type: ignore[arg-type]
    if v:
        ledger(v.crawl_id, "output_format json (accepted)")
        client.stop(v.crawl_id)
        return (
            PASS,
            f"accepted ({v.crawl_id}): the key is in a beta segment, not a bug; stopped it",
            "",
        )
    return (
        pass_if(e is not None and e.status_code == 422 and e.code == "invalid_parameter"),
        json.dumps(esum(e)),
        'output_format="json" (the hint allows only "html"; the value is sent as is)',
    )


def c15():
    bad = ZenRowsCrawlClient("invalid", base_url=API_BASE, retries=0)
    _, e = err_of(lambda: bad.get("c_does_not_exist"))
    errs["bad_key"] = e
    return pass_if(e is not None and e.status_code == 401), json.dumps(esum(e)), ""


def c16():
    _, none = err_of(lambda: cap.get("c_nobody"))
    rows = {k: (v.code if v else None) for k, v in errs.items()}
    rows["no_body_502"] = f"{none.status_code} code={none.code!r}" if none else "no error"
    live_ok = not HAS_KEY or (
        rows.get("not_found") == "crawl_not_found" and rows.get("invalid") == "invalid_parameter"
    )
    ok = (
        live_ok
        and bool(rows.get("bad_key"))
        and none is not None
        and none.status_code == 502
        and none.code is None
    )
    status = (PASS if HAS_KEY else SKIP) if ok else FAIL
    return status, json.dumps(rows), "12, 13, 15, and a 502 with no body from the stand-in"


def c19():
    f = state["F"] = create(depth=1, include_patterns=[NO_MATCH])
    w = client.wait(f.crawl_id)
    err = w.error.model_dump(mode="json") if w.error else None
    ok = (
        w.status.value == "failed"
        and err is not None
        and err.get("code") == "no_items_found"
        and bool(err.get("detail"))
    )
    return (
        pass_if(ok),
        f"{f.crawl_id}: {w.status.value}; error {json.dumps(err)}; returned, no exception",
        w.model_dump_json(),
    )


def c21():
    f = state.get("F")
    if not f:
        return SKIP, "no crawl from check 19", ""
    ok = f.max_pages == DOCUMENTED_DEFAULT and f.max_items == DOCUMENTED_DEFAULT
    doc = [
        ln
        for ln in readme.splitlines()
        if "default" in ln.lower() and re.search(r"max_(pages|items)", ln)
    ]
    return (
        pass_if(ok),
        f"the API applied max_pages {f.max_pages}, max_items {f.max_items}; "
        f"documented {DOCUMENTED_DEFAULT}",
        "README lines on the defaults:\n" + ("\n".join(doc) or "(none)"),
    )


run_check(
    "1",
    "Create",
    "returns at once: running, a crawl_id, the echoed parameters; only the fields set are sent",
    c1,
)
run_check(
    "5",
    "Wait runs out",
    "wait with timeout 1 s on a running crawl returns it running, no error",
    c5,
    live=True,
)
if HAS_KEY:
    partial_read()
run_check(
    "3",
    "Idempotency",
    "same key + body → same crawl_id; different body → 422 idempotency_key_reused",
    c3,
    live=True,
)
run_check(
    "11",
    "Stop",
    "running → stopped, stop_reason user; ended → answers as it ended, no error",
    c11,
    live=True,
)
run_check(
    "4",
    "Wait",
    "wait (default timeout) returns the ended crawl; failed is returned, not raised",
    c4,
    live=True,
)
run_check(
    "6",
    "Get",
    "limit 2 then the cursor: one page each; next_cursor moves on and is None on the last page",
    c6,
    live=True,
)
run_check(
    "7",
    "List",
    "limit 2 then the next page: newest first; next_cursor None on the last page",
    c7,
    live=True,
)
run_check(
    "8",
    "Results",
    "every kept URL once, each matching CRAWL_INCLUDE; on a running crawl, what is kept so far",
    c8,
    live=True,
)
if HAS_KEY:
    stop_mid_flight()
run_check(
    "20",
    "Results after stop",
    "results on a crawl stopped with pages in flight end without looping, and lose no URL",
    c20,
    live=True,
)
run_check(
    "2",
    "Create with html",
    "results get content_status and, once fetched, content_url",
    c2,
    live=True,
)
run_check(
    "9", "Content", "content by content id, then by content_url: the same HTML", c9, live=True
)
run_check(
    "10",
    "Download",
    "a status and one line per result, each with url, content_status, content",
    c10,
    live=True,
)
run_check("12", "Not found", "get c_does_not_exist → 404 crawl_not_found", c12, live=True)
run_check("13", "Invalid input", "create with depth 0 → 422 invalid_parameter", c13, live=True)
run_check(
    "14",
    "Gated option",
    "output_format json → 422 invalid_parameter for a customer key",
    c14,
    live=True,
)
run_check("15", "Bad key", "any call with the key `invalid` → 401", c15)
run_check(
    "16",
    "Errors carry the server code",
    "code holds the server code; None when the body has none",
    c16,
)
srv.shutdown()
run_check(
    "19",
    "Failed crawl",
    "a crawl that keeps nothing ends failed (no_items_found); wait returns it, not raises",
    c19,
    live=True,
)
run_check(
    "21",
    "Documented defaults",
    f"a create without max_pages and max_items echoes the documented default "
    f"({DOCUMENTED_DEFAULT})",
    c21,
    live=True,
)


def c22():
    """discovery: sent as given, echoed back, and ["links"] when left out."""
    given = create(discovery=["links"], **BASE)
    left_out = create(**{**BASE, "max_items": 1})
    for c in (given, left_out):
        client.stop(c.crawl_id)
    values = [[d.value for d in c.discovery] for c in (given, left_out)]
    return (
        pass_if(values == [["links"], ["links"]]),
        json.dumps({"discovery=['links']": values[0], "left out": values[1]}),
        "",
    )


def c23():
    """content(download=True): the same page, asked for as a file to save."""

    def first_fetched(crawl_id):
        return next(
            (r for r in client.get(crawl_id, limit=100).results if r.content_url),
            None,
        )

    # The stop in check 20 can land before any of B's pages is fetched. Then read
    # a one-item html crawl that runs to the end instead, as check 9 does.
    b = state.get("B")
    src = b.crawl_id if b else None
    fetched = first_fetched(src) if src else None
    if not fetched:
        own = create(**{**BASE, "max_items": 1, "output_format": "html"})
        client.wait(own.crawl_id)
        src = own.crawl_id
        fetched = first_fetched(src)
    if not fetched:
        return FAIL, f"no result with a content_url on {src}", ""
    plain = client.content(src, fetched)
    as_file = client.content(src, fetched, download=True)
    return (
        pass_if(plain == as_file and bool(plain)),
        f"same {len(plain)} chars with and without download=True",
        trim(plain, 400),
    )


def c24():
    """api_key_cap_reached parses as the named member, not as UNKNOWN."""
    code = CrawlErrorCode("api_key_cap_reached")
    return (
        pass_if(code is CrawlErrorCode.API_KEY_CAP_REACHED and not is_unknown(code)),
        f"{code!r} (name {code.name})",
        "",
    )


run_check(
    "22",
    "Discovery",
    'create(discovery=["links"]) is echoed back, and a create without it reads ["links"]',
    c22,
    live=True,
)
run_check(
    "23",
    "Content as a file",
    "content(download=True) returns the same page as content() does",
    c23,
    live=True,
)
run_check(
    "24",
    "Run error codes",
    "api_key_cap_reached is a named CrawlErrorCode member, not an unknown value",
    c24,
)

with open(f"{RESULTS}/naming.json", "w") as f:
    json.dump(
        {
            "operations": {
                k: k
                for k in ["create", "get", "list", "results", "content", "download", "stop", "wait"]
            },
            "params": {
                k: k
                for k in [
                    "url",
                    "depth",
                    "max_items",
                    "max_pages",
                    "include_patterns",
                    "exclude_patterns",
                    "output_format",
                    "discovery",
                    "idempotency_key",
                    "cursor",
                    "limit",
                    "timeout",
                    "content_id",
                    "download",
                ]
            },
        },
        f,
        indent=2,
    )
record("17", "Naming", SKIP, "the API's names in this surface's casing", "computed by the report")


def c18():
    problems = []
    lines = section.splitlines()
    beta = [ln for ln in lines if re.search(r"\bbeta\b", ln, re.I)]
    beta += [
        ln
        for ln in readme.splitlines()
        if "ZenRowsCrawlClient" in ln and re.search(r"\bbeta\b", ln, re.I)
    ]
    evolving = [ln for ln in readme.splitlines() if "still evolving" in ln.lower()]
    limit = [ln for ln in lines if "active jobs" in ln.lower()]
    if beta:
        problems.append(f'"beta" on a Crawl line: {beta[0].strip()[:160]}')
    if len(evolving) != 1:
        problems.append(f'{len(evolving)} "still evolving" lines in the README (want 1)')
    if not any("batch" in ln.lower() for ln in limit):
        problems.append(
            "no line reads the 429 limit as the account's active jobs, shared with Batch"
        )
    if "Crawl API (new)" not in readme:
        problems.append('the Crawl section does not say "new"')
    import zenrows.crawl as zc

    if any(
        re.search(r"\bbeta\b", d or "", re.I) for d in (zc.__doc__, zc.ZenRowsCrawlClient.__doc__)
    ):
        problems.append('"beta" in the crawl module docstrings')
    return (
        pass_if(not problems),
        "; ".join(problems) or 'README: "new", 1 "still evolving" line, 429 wording ok',
        "still evolving: " + " | ".join(evolving) + "\n429: " + " | ".join(limit),
    )


run_check(
    "18",
    "Copy",
    'Crawl is "new", never "beta"; one "still evolving" line; the 429 limit is active jobs shared '
    "with Batch",
    c18,
)
print("python: done")
