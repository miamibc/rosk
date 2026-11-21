#!/usr/bin/env python3
"""Prints the next release version (minor + 1: 0.1.0 -> 0.2.0, 0.9.0 -> 0.10.0) and, with
--write, puts it into every file that carries the version.

    python3 tools/bump_version.py             # prints the next version
    python3 tools/bump_version.py --write     # also writes it
    python3 tools/bump_version.py --current   # prints the current one

The version is read from the text of rosk/__init__.py, never by importing it: an import leaves
a .pyc behind, and a rewrite in the same second with the same length ("0.1.0" -> "0.2.0") is
not noticed by Python, so a later import would still see the old version.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "rosk" / "__init__.py"
METADATA = ROOT / "extension" / "metadata.json"
VERSION_RE = re.compile(r'^__version__ = "(\d+)\.(\d+)\.(\d+)"$', re.M)
META_RE = re.compile(r'"version-name": "[^"]*"')


def current():
    m = VERSION_RE.search(INIT.read_text())
    if m is None:
        sys.exit(f"no __version__ = \"X.Y.Z\" in {INIT}")
    return tuple(int(x) for x in m.groups())


def next_version(version):
    major, minor, _patch = version
    return f"{major}.{minor + 1}.0"


def write(version):
    INIT.write_text(VERSION_RE.sub(f'__version__ = "{version}"', INIT.read_text(), count=1))
    # a regex, not json.dump: the file keeps its own formatting
    meta = METADATA.read_text()
    if not META_RE.search(meta):
        sys.exit(f'no "version-name" in {METADATA}')
    METADATA.write_text(META_RE.sub(f'"version-name": "{version}"', meta, count=1))


def main():
    if "--current" in sys.argv[1:]:
        print(".".join(map(str, current())))
        return
    version = next_version(current())
    if "--write" in sys.argv[1:]:
        write(version)
    print(version)


if __name__ == "__main__":
    main()
