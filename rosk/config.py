"""Filesystem locations. ROSK_DATA_DIR overrides the data directory (used by tests)."""

import os
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("ROSK_DATA_DIR")
    if override:
        path = Path(override)
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        path = Path(base) / "rosk"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "rosk.db"


def media_path(blob_id: str) -> Path:
    """A downloaded Keep image (KeepSync puts them in media/ next to keep-state.json)."""
    return data_dir() / "media" / blob_id.replace("/", "_")


def package_dir() -> Path:
    return Path(__file__).resolve().parent


def locale_dir() -> Path:
    # Installed layout: <prefix>/lib/rosk/rosk (package) and <prefix>/lib/rosk/locale.
    # Development layout: <repo>/rosk and <repo>/build/locale.
    root = package_dir().parent
    for candidate in (root / "locale", root / "build" / "locale"):
        if candidate.is_dir():
            return candidate
    return root / "locale"
