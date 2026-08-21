"""Generate docs/batch-client-reference.md — a PUBLIC-facing markdown API reference.

Renders each public module with pydoc-markdown, then presents it as the
public import surface (`from zenrows.batch import ...`) rather than where
things physically live:

  - the internal module headers (`# zenrows.batch._resources`) become
    friendly section titles, and
  - the `zenrows.batch._x.` qualifiers are stripped from anchors and inline
    references,

so the reference never exposes the private `_module` layout. The modules
stay private on purpose (curated `__all__` re-export from the package) —
this only fixes how the docs are presented.

Run via `make docs` (which supplies pydoc-markdown through `uv --with`).
"""

import re
import subprocess
import sys

# (module, public section title) — order defines the reference layout.
SECTIONS = [
    ("zenrows.batch.client", "Client"),
    ("zenrows.batch._resources", "Job, run & export handles"),
    ("zenrows.batch._waiters", "Waiters"),
    ("zenrows.batch._download", "Downloads"),
    ("zenrows.batch._estimate", "Cost estimation"),
    ("zenrows.batch._schedule", "Schedule builders"),
    ("zenrows.batch.errors", "Errors"),
    ("zenrows.batch.models", "Models"),
]

HEADER = (
    "# Zenrows Batch — Python SDK Reference\n\n"
    "_Auto-generated from the SDK docstrings via `make docs`. Do not edit by "
    "hand. Everything below is imported from the top-level `zenrows.batch` "
    "package (`from zenrows.batch import ...`)._\n"
)

# Member-qualified references like `zenrows.batch._resources.JobHandle`
# (anchors + inline) → bare `JobHandle` (the public import name).
_QUALIFIER = re.compile(r"zenrows\.batch\.[A-Za-z_][A-Za-z0-9_]*\.")


def render(module: str, title: str) -> str:
    out = subprocess.run(
        ["pydoc-markdown", "-m", module, "-I", "src"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # Drop the leading module anchor + `# zenrows.batch.<mod>` header and put
    # the friendly section title in its place.
    out = re.sub(
        r'\A<a id="' + re.escape(module) + r'"></a>\n\n# [^\n]*\n',
        f"# {title}\n",
        out,
        count=1,
    )
    # Strip the private/module qualifiers everywhere else.
    out = _QUALIFIER.sub("", out)
    return out.rstrip() + "\n"


def main() -> None:
    parts = [HEADER, *(render(mod, title) for mod, title in SECTIONS)]
    sys.stdout.write("\n".join(parts))


if __name__ == "__main__":
    main()
