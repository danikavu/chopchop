"""Batch workflows: run a recipe of steps over every track in a folder.

A workflow is a chain of optional steps, all driven by the same operations as the app:

    separate -> stem tools -> tempo / key -> drum kit / sound bits -> export

Each song becomes a ChopChop project (kept, or deleted after export) and gets its own
folder in the output: <output>/<batch>/<song>/{stems,kit,samples}/ plus a summary
(chopchop-batch.json) for the whole batch.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field

from . import analysis, audio, kits, separation, transpose
from .naming import export_name
from .service import ChopChop
from .store import data_dir

AUDIO_EXTS = {".wav", ".flac", ".aif", ".aiff", ".mp3", ".ogg", ".oga", ".opus", ".m4a", ".aac"}


# ---------- the recipe ----------

class SeparateStep(BaseModel):
    enabled: bool = True
    model: str = "htdemucs_6s.yaml"
    stems: list[str] | None = None  # None = every stem the model makes
    rest: bool = False  # mix the stems not kept into one extra track
    debleed: bool = True  # move drum bleed out of other / guitar / piano into drums


class ToolStep(BaseModel):
    tool: str  # separation.TOOLS key
    on: str  # the stem to run it on, e.g. "drums"


class KitStep(BaseModel):
    enabled: bool = False
    source: str = "parts"  # "parts" (split drums) | "drums" | any track name
    size: int = Field(16, ge=1, le=64)
    uniqueness: float = Field(0.9, ge=0.7, le=0.995)  # similarity above which hits are duplicates


class BitsStep(BaseModel):
    enabled: bool = False
    stems: list[str] = ["vocals"]
    mode: Literal["chops", "phrases"] = "chops"
    max: int = Field(16, ge=1, le=200)
    uniqueness: float = Field(0.9, ge=0.7, le=0.995)


class ExportStep(BaseModel):
    stems: Literal["all", "kept", "none"] | list[str] = "all"  # kept = only the separated stems, not tool outputs
    mix: bool = False  # also copy the original mix
    kit: bool = True  # the drum kit, pad-named (A01_kick_01.wav …)
    samples: bool = True  # sound bits by category
    bpm_names: bool = True
    key_names: bool = True
    normalize: bool = False
    # Kit / sound bits also transposed to C (keyed samples not already in C), each way in
    # its own "In C (…)" folder: see transpose.py.
    to_c: Literal["none", "repitched", "warped", "both"] = "none"


class Workflow(BaseModel):
    name: str = "Custom"
    description: str = ""
    separate: SeparateStep = SeparateStep()
    tools: list[ToolStep] = []
    tempo: bool = True  # BPM (and beat grid), used for names
    key: bool = True  # the song's key, used for names of melodic stems
    kit: KitStep = KitStep()
    bits: BitsStep = BitsStep()
    export: ExportStep = ExportStep()
    keep_projects: bool = True  # keep each song as a ChopChop project to tweak later


PRESETS: list[Workflow] = [
    Workflow(
        name="Drum kit per song",
        description="Best drum separation → split into kick / snare / hats … → 16-pad kit of the best unique hits.",
        separate=SeparateStep(model="htdemucs_ft.yaml", stems=["drums"]),
        tools=[ToolStep(tool="drumsep", on="drums")],
        kit=KitStep(enabled=True, source="parts", size=16),
        export=ExportStep(stems="none", kit=True, samples=False),
    ),
    Workflow(
        name="Vocals + bass stems",
        description="Two-stage separation (cleanest vocals), keep only vocals and bass.",
        separate=SeparateStep(model="two_stage_ft", stems=["vocals", "bass"]),
        export=ExportStep(stems="all", kit=False, samples=False),
    ),
    Workflow(
        name="All stems",
        description="Two-stage separation into vocals, drums, bass, guitar, piano, other.",
        separate=SeparateStep(model="two_stage_6s"),
        export=ExportStep(stems="all", kit=False, samples=False),
    ),
    Workflow(
        name="Stems + samples",
        description="All stems, a 16-pad drum kit from the split drums, and vocal + bass one-shots.",
        separate=SeparateStep(model="two_stage_6s"),
        tools=[ToolStep(tool="drumsep", on="drums")],
        kit=KitStep(enabled=True, source="parts", size=16),
        bits=BitsStep(enabled=True, stems=["vocals", "bass"], mode="chops", max=16),
        export=ExportStep(stems="kept", kit=True, samples=True),
    ),
    Workflow(
        name="Acapellas",
        description="Best vocal model → dry (de-reverbed) acapella plus the instrumental.",
        separate=SeparateStep(model="vocals_mel_band_roformer.ckpt", stems=["vocals"], rest=True),
        tools=[ToolStep(tool="dereverb", on="vocals")],
        export=ExportStep(stems="all", kit=False, samples=False),
    ),
]


def check(wf: Workflow) -> None:
    """Reject recipes that can't run, before a batch starts."""
    if wf.separate.enabled:
        info = next((m for m in separation.MODELS if m["id"] == wf.separate.model), None)
        if info is None:
            raise ValueError(f"unknown model: {wf.separate.model}")
        bad = set(wf.separate.stems or []) - set(info["stems"])
        if bad:
            raise ValueError(f"{info['label']} doesn't make: {', '.join(sorted(bad))}")
        if wf.separate.stems == []:
            raise ValueError("pick at least one stem to keep")
    for t in wf.tools:
        if t.tool not in separation.TOOLS:
            raise ValueError(f"unknown tool: {t.tool}")


# ---------- saved workflows ----------

def _saved_path() -> Path:
    return data_dir() / "workflows.json"


def saved() -> list[dict]:
    try:
        return json.loads(_saved_path().read_text())
    except (OSError, json.JSONDecodeError):
        return []


def save(wf: Workflow) -> list[dict]:
    items = [w for w in saved() if w["name"] != wf.name] + [wf.model_dump()]
    _saved_path().write_text(json.dumps(items, indent=1))
    return items


def delete(name: str) -> list[dict]:
    items = [w for w in saved() if w["name"] != name]
    _saved_path().write_text(json.dumps(items, indent=1))
    return items


# ---------- input folders ----------

def list_dir(path: str | None) -> dict:
    """A folder's subfolders and audio files, for the folder picker."""
    p = Path(path).expanduser() if path else Path.home()
    p = p.resolve()
    if not p.is_dir():
        raise ValueError(f"not a folder: {p}")
    dirs, files = [], []
    try:
        for e in sorted(p.iterdir(), key=lambda e: e.name.lower()):
            if e.name.startswith("."):
                continue
            if e.is_dir():
                dirs.append(e.name)
            elif e.suffix.lower() in AUDIO_EXTS:
                files.append(e.name)
    except PermissionError as e:
        raise ValueError(f"can't read {p}: permission denied") from e
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "dirs": dirs, "files": files}


def find_audio(folder: Path, recursive: bool = False) -> list[Path]:
    it = folder.rglob("*") if recursive else folder.iterdir()
    return sorted((f for f in it if f.is_file() and f.suffix.lower() in AUDIO_EXTS and not f.name.startswith(".")),
                  key=lambda f: str(f).lower())


def default_output() -> Path:
    return data_dir() / "batches"


def folder_name(name: str) -> str:
    """Readable but filesystem-safe (keeps spaces, drops / \\ : * ? \" < > |)."""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name).strip(" .")
    return cleaned[:120] or "untitled"


def unique_dir(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while (path.parent / f"{path.name} ({n})").exists():
        n += 1
    return path.parent / f"{path.name} ({n})"


# ---------- running ----------

Progress = Callable[[float, str], None]


def run_song(svc: ChopChop, src: Path, wf: Workflow, out_dir: Path, progress: Progress,
             check_cancel: Callable[[], None]) -> dict:
    """Run the recipe on one file; returns what was made."""
    steps = ["import"]
    if wf.separate.enabled:
        steps.append("separate")
    steps += [f"tool:{t.tool}:{t.on}" for t in wf.tools]
    if wf.tempo or wf.key or wf.export.bpm_names or wf.export.key_names:
        steps.append("analyse")
    if wf.kit.enabled:
        steps.append("kit")
    if wf.bits.enabled:
        steps.append("bits")
    steps.append("export")
    weights = {"import": 0.5, "separate": 6, "analyse": 0.5, "kit": 1.5, "bits": 1.5, "export": 0.5}
    w = [weights.get(s.split(":")[0], 3) for s in steps]  # stem tools are heavy too
    total = sum(w)
    warnings: list[str] = []
    result: dict = {"song": src.stem, "warnings": warnings}

    timings: dict[str, float] = {}
    result["timings"] = timings
    clock = {"step": None, "t": time.time()}

    def step(i: int, label: str) -> Progress:
        if clock["step"]:
            timings[clock["step"]] = round(time.time() - clock["t"], 1)
        clock.update(step=label, t=time.time())
        done = sum(w[:i])

        def sub(f: float, m: str) -> None:
            progress((done + w[i] * max(0.0, min(1.0, f))) / total, f"Step {i + 1}/{len(steps)} · {label}: {m}")
        sub(0.0, "…")
        return sub

    i = 0
    project = svc.create_project(src, src.stem)
    pid = project["id"]
    result["project"] = pid
    try:
        tracks = lambda: {t["name"] for t in svc.store.get(pid)["tracks"]}  # noqa: E731
        kept: list[str] = []
        for s in steps[1:]:
            i += 1
            check_cancel()
            if s == "separate":
                sp = wf.separate
                kept = svc.separate(pid, sp.model, sp.stems, sp.rest, True, step(i, "separate"), tempo_after=False,
                                    debleed=sp.debleed)
            elif s.startswith("tool:"):
                _, tool, on = s.split(":", 2)
                label = separation.TOOLS[tool]["label"]
                if on not in tracks():
                    warnings.append(f"{label}: no '{on}' stem to run on (skipped)")
                    continue
                svc.run_tool(pid, on, tool, step(i, f"{label} ({on})"))
            elif s == "analyse":
                sub = step(i, "tempo & key")
                if wf.tempo or wf.export.bpm_names:
                    t = svc.detect_tempo(pid)
                    result["bpm"] = t["bpm"]
                    result["tempo_confidence"] = t["confidence"]
                sub(0.6, "key…")
                if wf.key or wf.export.key_names:
                    p = svc.store.get(pid)
                    mix = next(t for t in p["tracks"] if t["kind"] == "mix")
                    y, sr = audio.read_mono(svc.store.path(pid) / mix["file"])
                    key, conf = analysis.detect_key(y, sr)
                    result["key"] = key
                    with svc.store.edit(pid) as proj:
                        proj["key"] = {"key": key, "confidence": conf}
            elif s == "kit":
                result["kit"] = _kit(svc, pid, wf.kit, step(i, "drum kit"), warnings)
            elif s == "bits":
                result["samples"] = _bits(svc, pid, wf.bits, step(i, "sound bits"), warnings)
            elif s == "export":
                result["files"] = _export(svc, pid, wf, kept, out_dir, step(i, "export"))
        timings[clock["step"]] = round(time.time() - clock["t"], 1)
        result["seconds"] = round(sum(timings.values()), 1)
        return result
    finally:
        if not wf.keep_projects:
            svc.store.delete(pid)
            result.pop("project", None)


def _kit(svc: ChopChop, pid: str, k: KitStep, progress: Progress, warnings: list[str]) -> int:
    names = [t["name"] for t in svc.store.get(pid)["tracks"]]
    parts = [n for n in names if analysis.drum_part(n) and analysis.is_drum_track(n)]
    drums = [n for n in names if analysis.is_drum_track(n) and not analysis.drum_part(n)]
    if k.source == "parts" and parts:
        source = parts
    elif k.source in ("parts", "drums"):
        source = drums[:1] or ["mix"]
        if k.source == "parts":
            warnings.append("drum kit: no split drum parts; used " + source[0])
        if source == ["mix"]:
            warnings.append("drum kit: no drum stem; built from the full mix")
    elif k.source in names:
        source = [k.source]
    else:
        warnings.append(f"drum kit: no '{k.source}' track (skipped)")
        return 0
    saved = svc.build_kit(pid, source, "drums", similarity=k.uniqueness, max_per_category=16,
                          kit_size=k.size, load_pads=True, progress=progress)
    return len(saved)


def _bits(svc: ChopChop, pid: str, b: BitsStep, progress: Progress, warnings: list[str]) -> int:
    names = {t["name"] for t in svc.store.get(pid)["tracks"]}
    todo = [s for s in b.stems if s in names]
    for s in b.stems:
        if s not in names:
            warnings.append(f"sound bits: no '{s}' stem (skipped)")
    count = 0
    for j, s in enumerate(todo):
        def sub(f, m, j=j):
            progress((j + f) / len(todo), m)
        made = len(svc.build_kit(pid, [s], b.mode, similarity=b.uniqueness, max_per_category=b.max, progress=sub))
        if not made:
            warnings.append(f"sound bits: '{s}' has no audible sounds of its own in this song (nothing cut)")
        count += made
    return count


def _export(svc: ChopChop, pid: str, wf: Workflow, kept: list[str], out_dir: Path, progress: Progress) -> int:
    e = wf.export
    p = svc.store.get(pid)
    folder = svc.store.path(pid)
    bpm = (p.get("tempo") or {}).get("bpm") if e.bpm_names else None
    song_key = (p.get("key") or {}).get("key") if e.key_names else None
    written = 0

    stems = [t for t in p["tracks"] if t["kind"] == "stem"]
    if e.stems == "none":
        stems = []
    elif e.stems == "kept":
        stems = [t for t in stems if t["name"] in kept]
    elif isinstance(e.stems, list):
        stems = [t for t in stems if t["name"] in e.stems]
    if e.mix:
        stems = [next(t for t in p["tracks"] if t["kind"] == "mix"), *stems]
    for t in stems:
        # The song's key only means something for pitched stems, not drums.
        key = None if analysis.is_drum_track(t["name"]) else song_key
        name = export_name(t["name"], bpm, t["duration"], key)
        target = out_dir / "stems" / f"{name}.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        if e.normalize:
            y, sr = audio.read(folder / t["file"])
            audio.write_wav(target, audio.normalize(y), sr)
        else:
            shutil.copyfile(folder / t["file"], target)
        written += 1
    progress(0.4, "stems written")

    samples = p["samples"]
    to_c = transpose.modes(e.to_c)
    if e.kit:
        by_id = {s["id"]: s for s in samples}
        on_pads = [(i, by_id[x]) for i, x in enumerate(p["pads"]) if x in by_id]
        labels: dict[str, str] = {}
        for i, s in on_pads:
            labels.setdefault(s["id"], kits.pad_label(i))
        kit = list({s["id"]: s for _, s in on_pads}.values())
        written += len(kits.write_kit(folder, kit, out_dir / "kit", e.normalize, labels, bpm, to_c))
    progress(0.7, "kit written")
    if e.samples:
        bits = [s for s in samples if s.get("origin") in ("chops", "phrases", "manual")]
        written += len(kits.write_kit(folder, bits, out_dir / "samples", e.normalize, None, bpm, to_c))
    return written


def prepare(input_dir: str | Path, files: list[str] | None = None, recursive: bool = False,
            output_dir: str | Path | None = None) -> tuple[list[Path], Path]:
    """Resolve a batch request into (audio files, output folder for this run).

    files: subset to process, by file name or path relative to input_dir (None = all).
    The run folder is <output>/<input folder name> – <date>, numbered if it exists.
    """
    folder = Path(input_dir).expanduser()
    if not folder.is_dir():
        raise ValueError(f"not a folder: {folder}")
    found = find_audio(folder, recursive)
    if files is not None:
        wanted = set(files)
        found = [f for f in found if f.name in wanted or str(f.relative_to(folder)) in wanted]
    if not found:
        raise ValueError(f"no audio files to process in {folder}")
    from . import settings

    base = Path(output_dir).expanduser() if output_dir else (settings.export_dir() or default_output())
    return found, unique_dir(base / batch_name(folder))


def run_batch(svc: ChopChop, files: list[Path], wf: Workflow, out_root: Path, job,
              on_song: Callable[[dict], None] | None = None) -> dict:
    """Run the workflow over files, one song after another. A failing song is recorded
    and the batch carries on; Cancel stops between steps. on_song(entry) is called as
    each song finishes (done or error)."""
    out_root.mkdir(parents=True, exist_ok=True)
    started = time.time()
    songs = [{"file": str(f), "song": f.stem, "status": "queued", "message": ""} for f in files]
    job.detail = {"output": str(out_root), "songs": songs, "workflow": wf.name}

    for n, (f, entry) in enumerate(zip(files, songs)):
        job.check_cancel()
        entry["status"] = "running"
        out_dir = unique_dir(out_root / folder_name(f.stem))

        def progress(frac: float, msg: str, n=n, entry=entry) -> None:
            entry["message"] = msg
            job.update((n + frac) / len(files), f"Song {n + 1}/{len(files)} · {f.stem} · {msg}")

        try:
            res = run_song(svc, f, wf, out_dir, progress, job.check_cancel)
            entry.update(status="done", message="Done", output=str(out_dir), **res)
        except Exception as exc:
            from .jobs import Cancelled
            if isinstance(exc, Cancelled):
                entry.update(status="cancelled", message="Cancelled")
                for later in songs[n + 1:]:
                    later["status"] = "cancelled"
                _write_summary(out_root, wf, songs, started, "cancelled")
                raise
            entry.update(status="error", message=str(exc) or exc.__class__.__name__)
        if on_song:
            on_song(entry)

    _write_summary(out_root, wf, songs, started)
    ok = sum(s["status"] == "done" for s in songs)
    job.update(1.0, f"Finished: {ok}/{len(songs)} songs")
    return {"output": str(out_root), "done": ok, "failed": len(songs) - ok, "songs": songs}


SUMMARY = "chopchop-batch.json"
OLD_SUMMARY = "chopper-batch.json"  # runs made before the rename are still listed


def summary_file(run_dir: Path) -> Path | None:
    return next((run_dir / n for n in (SUMMARY, OLD_SUMMARY) if (run_dir / n).is_file()), None)


def _write_summary(out_root: Path, wf: Workflow, songs: list[dict], started: float, status: str = "done") -> None:
    summary = {
        "workflow": wf.model_dump(),
        "status": status,
        "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
        "seconds": round(time.time() - started, 1),
        "songs": songs,
    }
    (out_root / SUMMARY).write_text(json.dumps(summary, indent=1))


def find_runs(folders: list[Path]) -> list[dict]:
    """Earlier batch runs on disk: each folder, and the folders directly inside it, that
    hold a chopchop-batch.json. Returned in the same shape as a live batch job (newest
    first), so the app lists them next to the runs of this session."""
    seen: set[Path] = set()
    runs = []
    for folder in folders:
        try:
            folder = folder.expanduser().resolve()
            candidates = [folder, *(d for d in folder.iterdir() if d.is_dir())] if folder.is_dir() else []
        except OSError:
            continue
        for d in candidates:
            if d in seen or not summary_file(d):
                continue
            seen.add(d)
            run = read_run(d)
            if run:
                runs.append(run)
    return sorted(runs, key=lambda r: -r["created_at"])


def read_run(run_dir: Path) -> dict | None:
    """A run folder's chopchop-batch.json as a finished batch job; None if unreadable."""
    path = summary_file(run_dir)
    if path is None:
        return None
    try:
        summary = json.loads(path.read_text())
        songs = summary["songs"]
        name = summary["workflow"]["name"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        started = time.mktime(time.strptime(summary["started"], "%Y-%m-%d %H:%M:%S"))
    except (KeyError, TypeError, ValueError, OverflowError):
        started = path.stat().st_mtime
    ok = sum(s.get("status") == "done" for s in songs)
    status = summary.get("status") or ("cancelled" if any(s.get("status") == "cancelled" for s in songs) else "done")
    return {
        "id": "disk:" + hashlib.sha1(str(run_dir).encode()).hexdigest()[:12],
        "kind": "batch",
        "project_id": "batch",
        "status": status,
        "progress": 1.0,
        "message": f"Finished: {ok}/{len(songs)} songs",
        "result": None,
        "error": None,
        "created_at": started,
        "started_at": started,
        "finished_at": started + float(summary.get("seconds") or 0),
        "detail": {"output": str(run_dir), "workflow": name, "songs": songs, "from_disk": True},
        "cancel_requested": False,
    }


def batch_name(input_dir: Path) -> str:
    return f"{folder_name(input_dir.name)} – {time.strftime('%Y-%m-%d %H.%M')}"

