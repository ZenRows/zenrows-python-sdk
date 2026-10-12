"""Record a source or build failure as a FAIL result, so the report shows it."""
import json
import os
import sys

out, surface = sys.argv[1], os.environ["SURFACE"]
log = ""
for name in ("fetch.log", "build.log"):
    p = os.path.join(out, "build", name)
    if os.path.exists(p):
        with open(p, errors="replace") as f:
            log += f.read()[-2500:]
d = os.path.join(out, "results", "checks")
os.makedirs(d, exist_ok=True)
with open(os.path.join(d, "000-build.json"), "w") as f:
    json.dump({"surface": surface, "check": "build", "title": "Build the release artifact", "status": "FAIL",
               "expected": "the artifact builds", "actual": "the source or builder step failed", "evidence": log}, f, indent=2)
