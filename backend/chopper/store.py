"""On-disk project storage: one folder + project.json per project."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def data_dir() -> Path:
    root = Path(os.environ.get("CHOPPER_DATA", Path.cwd() / "data")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def safe_name(name: str) -> str:
    """Filesystem-safe identifier for tracks and export files."""
    cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "_", name.strip().lower()).strip("_")
    return cleaned or "track"


PAD_SLOTS = 64  # 4 banks x 16 pads


class ProjectNotFound(KeyError):
    pass


class Store:
    def __init__(self, root: Path | None = None):
        self.root = (root or data_dir()) / "projects"
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()

    def _lock(self, pid: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(pid, threading.RLock())

    def path(self, pid: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{10}", pid):
            raise ProjectNotFound(pid)
        return self.root / pid

    def _meta(self, pid: str) -> Path:
        return self.path(pid) / "project.json"

    def create(self, name: str) -> dict:
        pid = new_id()
        folder = self.path(pid)
        for sub in ("tracks", "samples", "exports"):
            (folder / sub).mkdir(parents=True, exist_ok=True)
        project = {
            "id": pid,
            "name": name,
            "created_at": time.time(),
            "tracks": [],
            "slices": [],
            "samples": [],
            "pads": [None] * PAD_SLOTS,
            "tempo": None,
        }
        self._write(pid, project)
        return project

    def list(self) -> list[dict]:
        out = []
        for meta in self.root.glob("*/project.json"):
            try:
                p = json.loads(meta.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            out.append({
                "id": p["id"],
                "name": p["name"],
                "created_at": p["created_at"],
                "tracks": len(p["tracks"]),
                "samples": len(p["samples"]),
            })
        return sorted(out, key=lambda p: p["created_at"], reverse=True)

    def get(self, pid: str) -> dict:
        meta = self._meta(pid)
        if not meta.exists():
            raise ProjectNotFound(pid)
        with self._lock(pid):
            project = json.loads(meta.read_text())
        # Projects created before drum pads existed.
        pads = project.setdefault("pads", [])
        pads.extend([None] * (PAD_SLOTS - len(pads)))
        project.setdefault("tempo", None)
        return project

    def _write(self, pid: str, project: dict) -> None:
        meta = self._meta(pid)
        tmp = meta.with_suffix(".tmp")
        tmp.write_text(json.dumps(project, indent=1))
        tmp.replace(meta)

    @contextmanager
    def edit(self, pid: str):
        """Load, mutate and persist a project under its lock."""
        with self._lock(pid):
            project = self.get(pid)
            yield project
            self._write(pid, project)

    def delete(self, pid: str) -> None:
        folder = self.path(pid)
        if not folder.exists():
            raise ProjectNotFound(pid)
        with self._lock(pid):
            shutil.rmtree(folder)


def find_track(project: dict, name: str) -> dict:
    for t in project["tracks"]:
        if t["name"] == name:
            return t
    raise KeyError(f"track not found: {name}")


def find_sample(project: dict, sid: str) -> dict:
    for s in project["samples"]:
        if s["id"] == sid:
            return s
    raise KeyError(f"sample not found: {sid}")
