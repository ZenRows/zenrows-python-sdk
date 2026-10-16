"""Turn out/results/checks/*.json into out/crawl-first-install-report.md and out/results.json.

Also computes check 17: the names this surface uses against the API's names.
"""

import glob
import json
import os
import re
import sys

OUT = sys.argv[1]
SURFACE = os.environ["SURFACE"]
REPO = os.environ["REPO"]
NAMES = {
    "node": "Node SDK (`zenrows` on npm)",
    "python": "Python SDK (`zenrows` on PyPI)",
    "go": "Go SDK (`github.com/zenrows/zenrows-go-sdk/service/crawl`)",
    "mcp": "MCP server (`@zenrows/mcp`)",
    "cli": "CLI (`@zenrows/cli`)",
}
CHECK_TITLES = {
    "1": "Create",
    "2": "Create with html",
    "3": "Idempotency",
    "4": "Wait",
    "5": "Wait runs out",
    "6": "Get",
    "7": "List",
    "8": "Results",
    "9": "Content",
    "10": "Download",
    "11": "Stop",
    "12": "Not found",
    "13": "Invalid input",
    "14": "Gated option",
    "15": "Bad key",
    "16": "Errors carry the server code",
    "17": "Naming",
    "18": "Copy",
    "19": "Patterns match nothing",
    "20": "Results after stop",
    "21": "Documented defaults",
}
# The API's operation and parameter names. Each surface uses them in its own casing.
CANON_OPS = ["create", "get", "list", "results", "content", "download", "stop", "wait"]
CANON_PARAMS = [
    "url",
    "depth",
    "max_items",
    "max_pages",
    "include_patterns",
    "exclude_patterns",
    "output_format",
    "idempotency_key",
    "cursor",
    "limit",
    "timeout",
    "content_id",
]


def read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def snake(name):
    n = re.sub(r"^crawl[._ ]", "", name.strip())
    n = n.replace("()", "").replace("<", "").replace(">", "").lstrip("-").replace("-", "_")
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", n).lower()


def naming(rows):
    p = os.path.join(OUT, "results", "naming.json")
    if not os.path.exists(p):
        return
    with open(p) as f:
        d = json.load(f)
    diffs = []
    for op in CANON_OPS:
        got = d["operations"].get(op)
        if got is None:
            diffs.append(f"operation `{op}` missing")
        elif snake(got) != op:
            diffs.append(f"operation `{op}` is `{got}`")
    for prm in CANON_PARAMS:
        got = d["params"].get(prm)
        if got is None:
            diffs.append(f"parameter `{prm}` missing")
        elif snake(got) != prm:
            diffs.append(f"parameter `{prm}` is `{got}`")
    rows["17"] = {
        "surface": SURFACE,
        "check": "17",
        "title": "Naming",
        "status": "FAIL" if diffs else "PASS",
        "expected": "the API's operation and parameter names, in this surface's casing",
        "actual": "; ".join(diffs) or "matches",
        "evidence": json.dumps({k: d[k] for k in ("operations", "params")}),
    }


def severity(check):
    if check in ("17", "18", "21") or check.startswith("x-"):
        return "minor"
    if check.startswith("readme") or check == "build":
        return "blocker"
    return "major"


def first_line(s, n=110):
    line = re.sub(r"\s+", " ", (s or "").split("\n")[0])
    return line if len(line) <= n else line[: n - 1] + "…"


def main():
    rows = {}
    for p in sorted(glob.glob(os.path.join(OUT, "results", "checks", "*.json"))):
        with open(p) as f:
            r = json.load(f)
        rows[r["check"]] = r
    naming(rows)
    env = json.loads(read(os.path.join(OUT, "results", "env.json"), "{}") or "{}")
    cleanup = json.loads(read(os.path.join(OUT, "cleanup.json"), "null") or "null")
    sha = read(os.path.join(OUT, "build", "sha"), "-")
    origin = read(os.path.join(OUT, "build", "origin"), "-")
    artifacts = [
        os.path.basename(p) for p in glob.glob(os.path.join(OUT, "build", "artifact", "*"))
    ]
    gomod = read(os.path.join(OUT, "results", "go-module.txt"))

    findings = [
        {"check": c, "severity": severity(c), "r": r}
        for c, r in rows.items()
        if r["status"] == "FAIL"
    ]
    order = {"blocker": 0, "major": 1, "minor": 2}
    findings.sort(key=lambda f: order[f["severity"]])
    blocking = [f for f in findings if f["severity"] != "minor"]
    counts = {}
    for r in rows.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    lines = []
    w = lines.append
    w(f"# Crawl first-install test: {NAMES[SURFACE]}\n")
    w(
        f"Run `{read(os.path.join(OUT, 'run_id'), '-')}`. The client was built as a release "
        "artifact, installed in a clean container, set up from its README, then tested against "
        "the live Crawl API.\n"
    )

    w("## 1. Verdict\n")
    if blocking:
        w(
            "**Not ready.** "
            + "; ".join(
                f"{f['check']} {f['r']['title']}: {first_line(f['r']['actual'], 90)}"
                for f in blocking
            )
        )
    else:
        w("**Ready.**" + (f" {len(findings)} minor finding(s), see Findings." if findings else ""))
    w("\n" + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) + ".\n")

    w("## 2. Environment\n")
    w(f"- Command: `{os.environ.get('CMDLINE') or 'bash run.sh'}`")
    w(f"- Repo: ZenRows/{REPO}; source: {origin}; SHA tested: `{sha}`")
    w(f"- Artifact: {', '.join(artifacts) or ('go get ' + gomod.split()[0] if gomod else '-')}")
    w(
        f"- `CRAWL_URL`: {os.environ.get('CRAWL_URL')}; "
        f"`CRAWL_INCLUDE`: `{os.environ.get('CRAWL_INCLUDE')}`; "
        f"API: {os.environ.get('CRAWL_API_BASE')}"
    )
    image = {
        "node": "NODE_IMAGE",
        "mcp": "NODE_IMAGE",
        "cli": "NODE_IMAGE",
        "python": "PYTHON_IMAGE",
        "go": "GO_IMAGE",
    }[SURFACE]
    w(
        f"- Image: `{os.environ.get(image)}`; container: "
        + ", ".join(f"{k} {v}" for k, v in env.items())
    )
    tools = {
        "node": f"typescript {os.environ.get('TYPESCRIPT_VERSION')}, "
        f"pnpm {os.environ.get('PNPM_VERSION')}",
        "mcp": f"@modelcontextprotocol/sdk {os.environ.get('MCP_SDK_VERSION')}",
        "cli": "none",
        "python": f"mypy {os.environ.get('MYPY_VERSION')}, "
        f"build {os.environ.get('PY_BUILD_VERSION')}",
        "go": "none",
    }[SURFACE]
    w(f"- Pinned tools: {tools}\n")

    w("## 3. First-time install\n")
    for check, r in rows.items():
        if check.startswith("readme") or check == "build":
            w(f"- **{r['status']}** {r['title']}: {first_line(r['actual'], 200)}")
            ev = r.get("evidence") or ""
            if ev and (r["status"] != "PASS" or "substitutions" in ev):
                w("\n  ```\n  " + "\n  ".join(ev.splitlines()[:16]) + "\n  ```")
    w("")

    w("## 4. Feature matrix\n")
    w("| # | Check | Result |")
    w("| --- | --- | --- |")
    for c in [str(i) for i in range(1, 22)]:
        r = rows.get(c)
        cell = f"**{r['status']}** {first_line(r['actual'])}".replace("|", "\\|") if r else "—"
        w(f"| {c} | {CHECK_TITLES[c]} | {cell} |")
    for c, r in rows.items():
        if c.startswith("x-"):
            w(
                f"| extra | {r['title']} | "
                + f"**{r['status']}** {first_line(r['actual'])}".replace("|", "\\|")
                + " |"
            )
    w("")

    w("## 5. Findings\n")
    if not findings:
        w("None.\n")
    for i, f in enumerate(findings, 1):
        r = f["r"]
        w(f"### {i}. {r['title']} ({f['severity']})\n")
        w(f"- What happened: {r['actual']}")
        w(f"- Expected: {r['expected']}")
        w(
            "- Evidence:\n\n  ```\n  "
            + "\n  ".join((r.get("evidence") or "").splitlines()[:20])
            + "\n  ```\n"
        )

    w("## 6. Cost\n")
    if cleanup and cleanup["crawls"]:
        w("| Crawl | Purpose | Final status | pages_fetched |")
        w("| --- | --- | --- | --- |")
        total = 0
        for c in cleanup["crawls"]:
            total += c.get("pages_fetched") or 0
            html = " (html)" if c.get("output_format") == "html" else ""
            reason = "/" + c["stop_reason"] if c.get("stop_reason") else ""
            w(
                f"| `{c['crawl_id']}` | {', '.join(c['purposes'])}{html} | "
                f"{c.get('status')}{reason} | {c.get('pages_fetched')} |"
            )
        w(f"\nTotal pages fetched: **{total}**.\n")
    else:
        w("No crawls.\n")

    w("## 7. Clean-up\n")
    if cleanup and cleanup["crawls"]:
        stopped = [c["crawl_id"] for c in cleanup["crawls"] if c.get("stopped_by_cleanup")]
        w(
            f"Checked at {cleanup['checked_at']}: "
            f"{len(cleanup['crawls'])} crawls started by this run, "
            f"{', '.join(cleanup['still_running']) or 'none'} still running. "
            + (
                f"Clean-up stopped {', '.join(stopped)}."
                if stopped
                else "Clean-up had nothing to stop."
            )
            + "\n"
        )
    else:
        w("No crawls to clean up.\n")

    with open(os.path.join(OUT, "crawl-first-install-report.md"), "w") as f:
        f.write("\n".join(lines))
    with open(os.path.join(OUT, "results.json"), "w") as f:
        json.dump(list(rows.values()), f, indent=2)
    print(
        f"{SURFACE}: "
        + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        + f"; findings: {len(findings)}"
    )


main()
