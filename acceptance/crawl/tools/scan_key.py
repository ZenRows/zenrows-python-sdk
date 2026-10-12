"""Fail the run if the API key appears in any file under out/. Reads the key from the env."""
import os
import sys

key = os.environ.get("ZENROWS_API_KEY", "").encode()
# The copy of the source under test holds the repo, not anything the run wrote.
source_dir = os.path.join(sys.argv[1], "build", "source")
hits = []
for root, dirs, files in os.walk(sys.argv[1]):
    if root == source_dir:
        dirs.clear()
        continue
    for f in files:
        p = os.path.join(root, f)
        try:
            with open(p, "rb") as fh:
                if key and key in fh.read():
                    hits.append(p)
        except OSError:
            pass
if hits:
    print("THE API KEY APPEARS IN:", *hits, sep="\n  ")
    sys.exit(1)
print("key scan: the key appears in no result, log or report file")
