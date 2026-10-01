"""Post-generation step for `make generate`: open the extensible enums.

datamodel-codegen does not pass schema extensions to enum templates, so
this step reads the spec itself. Every schema with an `enum` list and
`x-extensible-enum: true` (the response side) is matched to its generated
Enum class by its set of values, and that class gets

    _missing_ = classmethod(open_enum_missing)

(see src/zenrows/batch/_open_enum.py). Enums without the flag (request
side) stay strict, so a typo still fails locally. Deterministic; exits
non-zero if a value set is both extensible and strict, or an extensible
schema has no generated class.

Usage: python scripts/open_extensible_enums.py SPEC MODELS_PY
"""

import ast
import sys
from pathlib import Path

import yaml

HOOK = (
    "    # Open enum (x-extensible-enum): unknown values parse as UNKNOWN.\n"
    "    _missing_ = classmethod(open_enum_missing)\n"
)
IMPORT = "from zenrows.batch._open_enum import open_enum_missing\n"


def collect(node, extensible: set, strict: set) -> None:
    if isinstance(node, dict):
        if isinstance(node.get("enum"), list):
            values = frozenset(node["enum"])
            (extensible if node.get("x-extensible-enum") is True else strict).add(values)
        for v in node.values():
            collect(v, extensible, strict)
    elif isinstance(node, list):
        for v in node:
            collect(v, extensible, strict)


def main(spec_path: str, models_path: str) -> int:
    extensible: set = set()
    strict: set = set()
    collect(yaml.safe_load(Path(spec_path).read_text()), extensible, strict)
    if clash := extensible & strict:
        print(f"ambiguous enum value sets (extensible and strict): {clash}", file=sys.stderr)
        return 1

    src = Path(models_path).read_text()
    if "open_enum_missing" in src:
        print("models already processed; regenerate first", file=sys.stderr)
        return 1
    lines = src.splitlines(keepends=True)
    inserts: list[int] = []
    matched: set = set()
    for node in ast.parse(src).body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(isinstance(b, ast.Name) and b.id == "Enum" for b in node.bases):
            continue
        values = frozenset(
            stmt.value.value
            for stmt in node.body
            if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant)
        )
        if values in extensible:
            matched.add(values)
            inserts.append(node.end_lineno or 0)
    if missing := extensible - matched:
        print(f"extensible enums with no generated class: {missing}", file=sys.stderr)
        return 1

    for idx in sorted(inserts, reverse=True):
        lines.insert(idx, "\n" + HOOK)
    future = next((i for i, ln in enumerate(lines) if ln.startswith("from __future__")), None)
    if future is None:
        print(f"{models_path}: no `from __future__` import to anchor on", file=sys.stderr)
        return 1
    lines.insert(future + 1, IMPORT)
    Path(models_path).write_text("".join(lines))
    print(f"opened {len(inserts)} extensible enums")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
