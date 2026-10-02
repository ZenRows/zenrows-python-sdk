"""Bump the SDK version in pyproject.toml and src/zenrows/__version__.py.

Usage:
    uv run python scripts/bump_version.py patch|minor|major|<explicit-version>

Keeps the two hardcoded version strings in sync (there's no dynamic
versioning set up — hatchling reads pyproject.toml's `version` directly).
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
VERSION_FILE = ROOT / "src" / "zenrows" / "__version__.py"

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def current_version() -> str:
    text = PYPROJECT.read_text()
    match = re.search(r'^version = "(.*)"$', text, flags=re.MULTILINE)
    if not match:
        raise SystemExit("could not find version = \"...\" in pyproject.toml")
    return match.group(1)


def next_version(current: str, bump: str) -> str:
    match = VERSION_RE.match(current)
    if match:
        major, minor, patch = (int(part) for part in match.groups())
        if bump == "major":
            return f"{major + 1}.0.0"
        if bump == "minor":
            return f"{major}.{minor + 1}.0"
        if bump == "patch":
            return f"{major}.{minor}.{patch + 1}"
    # Not patch/minor/major, or current version isn't plain semver: treat
    # the argument as an explicit version string.
    if not VERSION_RE.match(bump):
        raise SystemExit(f"'{bump}' is not 'patch', 'minor', 'major', or a X.Y.Z version")
    return bump


def write_version(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text()
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"could not find version string to replace in {path}")
    path.write_text(new_text)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)

    old = current_version()
    new = next_version(old, sys.argv[1])

    write_version(PYPROJECT, r'^version = ".*"$', f'version = "{new}"')
    write_version(VERSION_FILE, r'^__version__ = ".*"$', f'__version__ = "{new}"')

    print(f"{old} -> {new}")


if __name__ == "__main__":
    main()
