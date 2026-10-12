"""App settings that persist across sessions: <data>/settings.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .store import data_dir


def _path() -> Path:
    return data_dir() / "settings.json"


def load() -> dict:
    try:
        return json.loads(_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


def put(key: str, value: Any) -> None:
    """Save one setting; None removes it."""
    current = load()
    if value is None:
        current.pop(key, None)
    else:
        current[key] = value
    _path().write_text(json.dumps(current, indent=1))


def export_dir() -> Path | None:
    """Where exports go (batches, slices, kits, pads); None = the defaults
    (batches in the data folder, project exports as a zip download)."""
    value = get("export_dir")
    return Path(value) if value else None


def set_export_dir(path: str | None) -> Path | None:
    """Validate (absolute, creatable) and save the export folder; None/'' clears it."""
    if not path or not path.strip():
        put("export_dir", None)
        return None
    p = Path(path.strip()).expanduser()
    if not p.is_absolute():
        raise ValueError(f"use a full path, e.g. {Path.home() / 'Music' / 'ChopChop'}")
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError(f"can't use {p}: {e.strerror or e}") from e
    put("export_dir", str(p))
    return p
