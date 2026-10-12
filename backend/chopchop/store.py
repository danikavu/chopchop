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
from dataclasses import dataclass, field
from pathlib import Path


def env(name: str) -> str | None:
    """CHOPCHOP_<name> from the environment (the old CHOPPER_<name> still works)."""
    return os.environ.get(f"CHOPCHOP_{name}") or os.environ.get(f"CHOPPER_{name}")


def data_dir() -> Path:
    root = Path(env("DATA") or Path.cwd() / "data").expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def safe_name(name: str) -> str:
    """Filesystem-safe identifier for tracks and export files."""
    cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "_", name.strip().lower()).strip("_")
    return cleaned or "track"


PAD_SLOTS = 64  # 4 banks x 16 pads
MAX_UNDO = 100
# Unreferenced audio is only deleted once it's this old, so a file a background job
# has just written (and is about to register) is never swept up.
GC_MIN_AGE_S = 300


@dataclass
class History:
    """Undo/redo stacks of (label, project JSON before/after the step). In memory:
    history lasts for the server session."""

    undo: list[tuple[str, str]] = field(default_factory=list)
    redo: list[tuple[str, str]] = field(default_factory=list)


class ProjectNotFound(KeyError):
    pass


class Store:
    def __init__(self, root: Path | None = None):
        self.root = (root or data_dir()) / "projects"
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()
        self._history: dict[str, History] = {}
        self._group = threading.local()  # active undo group on this thread

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
    def edit(self, pid: str, label: str | None = None):
        """Load, mutate and persist a project under its lock.

        label: records the change as one undo step (e.g. "Delete sample"); None
        for changes that shouldn't be undoable. Edits that change nothing record nothing.
        """
        with self._lock(pid):
            project = self.get(pid)
            before = json.dumps(project, sort_keys=True)
            yield project
            after = json.dumps(project, sort_keys=True)
            if after == before:
                return
            self._write(pid, project)
            group = getattr(self._group, "active", None)
            if group is not None and group["pid"] == pid:
                group.setdefault("before", before)  # the group records one step at the end
            elif label:
                self._record(pid, label, before)

    @contextmanager
    def undo_group(self, pid: str, label: str):
        """Make every edit inside this block (on this thread) a single undo step."""
        group = {"pid": pid}
        self._group.active = group
        try:
            yield
        finally:
            self._group.active = None
            if "before" in group:
                with self._lock(pid):
                    self._record(pid, label, group["before"])

    def _hist(self, pid: str) -> History:
        if pid not in self._history:
            self._history[pid] = History()
            self.collect_garbage(pid)  # files orphaned in an earlier session
        return self._history[pid]

    def _record(self, pid: str, label: str, before: str) -> None:
        h = self._hist(pid)
        h.undo.append((label, before))
        dropped = bool(h.redo) or len(h.undo) > MAX_UNDO
        h.redo.clear()
        del h.undo[:-MAX_UNDO]
        if dropped:
            self.collect_garbage(pid)

    def history(self, pid: str) -> dict:
        h = self._history.get(pid)
        return {
            "undo": h.undo[-1][0] if h and h.undo else None,
            "redo": h.redo[-1][0] if h and h.redo else None,
        }

    def _step(self, pid: str, back: bool) -> tuple[str, dict] | None:
        with self._lock(pid):
            h = self._hist(pid)
            src, dst = (h.undo, h.redo) if back else (h.redo, h.undo)
            if not src:
                return None
            label, state = src.pop()
            dst.append((label, json.dumps(self.get(pid), sort_keys=True)))
            project = json.loads(state)
            self._write(pid, project)
            return label, project

    def undo(self, pid: str) -> tuple[str, dict] | None:
        """Restore the state before the last step; returns (label, project)."""
        return self._step(pid, back=True)

    def redo(self, pid: str) -> tuple[str, dict] | None:
        return self._step(pid, back=False)

    def collect_garbage(self, pid: str) -> int:
        """Delete track/sample audio no current or undo/redo state refers to."""
        folder = self.path(pid)
        states = [self.get(pid)]
        h = self._history.get(pid)
        if h:
            states += [json.loads(s) for _, s in h.undo + h.redo]
        keep = {folder / x["file"] for st in states for key in ("tracks", "samples") for x in st.get(key, [])}
        now, removed = time.time(), 0
        for sub in ("tracks", "samples"):
            for f in (folder / sub).glob("*.wav"):
                if f in keep or now - f.stat().st_mtime < GC_MIN_AGE_S:
                    continue
                f.unlink(missing_ok=True)
                for cache in f.parent.glob(f"{f.stem}.peaks*.json"):
                    cache.unlink(missing_ok=True)
                removed += 1
        return removed

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
