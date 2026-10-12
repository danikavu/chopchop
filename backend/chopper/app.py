"""FastAPI app: REST API + static frontend."""

from __future__ import annotations

import mimetypes
import platform
import re
import subprocess
import shutil
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analysis, audio, kits, separation, settings, slicing, tempo, transpose, workflows
from .jobs import Job, JobRunner
from .service import Chopper
from .store import PAD_SLOTS, ProjectNotFound, Store, find_sample, find_track, new_id, safe_name

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


# ---------- request models ----------

class DetectReq(BaseModel):
    track: str
    sensitivity: float = Field(0.5, ge=0, le=1)
    min_gap: float = Field(0.08, ge=0.01, le=5)
    max_len: float | None = Field(None, gt=0)
    gate_db: float = Field(-40, ge=-90, le=0)


class Slice(BaseModel):
    id: str | None = None
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class SlicesReq(BaseModel):
    slices: list[Slice]
    label: str | None = Field(None, max_length=60)  # undo step name, e.g. "Split slice"


class ExportSlicesReq(BaseModel):
    tracks: list[str]
    slice_ids: list[str] | None = None
    fade_in_ms: float = Field(2, ge=0, le=500)
    fade_out_ms: float = Field(8, ge=0, le=2000)
    normalize: bool = False
    key_names: bool = True  # detect each slice's key/note and put it in the file name
    bpm_names: bool = True  # tag slices ≥ ~1 beat with the project tempo
    skip_silent: bool = True  # leave out slices that are (nearly) silent on a track


class SeparateReq(BaseModel):
    model: str = "htdemucs_6s.yaml"
    stems: list[str] | None = None  # which stems to keep; None = all the model makes
    rest: bool = True  # mix the stems not kept into one extra track (e.g. "instrumental")
    debleed: bool = True  # move drum bleed out of other / guitar / piano into drums
    replace: bool = True  # remove the project's current stems first


class ToolReq(BaseModel):
    tool: str  # a key of separation.TOOLS


class KitReq(BaseModel):
    track: str
    tracks: list[str] | None = None  # build one kit from several tracks (e.g. all drum parts)
    mode: str = "drums"  # drums | chops | phrases
    sensitivity: float = Field(0.5, ge=0, le=1)
    similarity: float = Field(0.93, ge=0.3, le=1)
    max_per_category: int = Field(8, ge=1, le=200)
    kit_size: int | None = Field(None, ge=1, le=64)  # auto kit: best N unique sounds
    load_pads: bool = False  # lay the result out on the drum pads
    replace: bool = True


class PadsReq(BaseModel):
    pads: list[str | None]
    label: str | None = Field(None, max_length=60)


class SampleCreateReq(BaseModel):
    track: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    category: str | None = None
    name: str | None = None


class SampleUpdateReq(BaseModel):
    name: str | None = None
    category: str | None = None
    key: str | None = None
    start: float | None = Field(None, ge=0)
    end: float | None = Field(None, gt=0)
    clear_key: bool = False


class DeleteSamplesReq(BaseModel):
    ids: list[str]


class TempoDetectReq(BaseModel):
    track: str | None = None  # default: the drum stem, else the mix


class TempoSetReq(BaseModel):
    bpm: float | None = Field(None, gt=20, lt=400)
    downbeat: int | None = Field(None, ge=0, le=3)  # which beat starts the bar
    shift: float | None = Field(None, ge=-30, le=30)  # move the whole grid (seconds)
    anchor: float | None = Field(None, ge=0)  # make the hit at this time a bar start


class ExportKitReq(BaseModel):
    sample_ids: list[str] | None = None
    kit_name: str = "kit"
    normalize: bool = False
    from_pads: bool = False  # export what's on the pads, named A01_…, B02_…
    # Also write keyed samples transposed to C, each way in its own folder.
    to_c: Literal["none", "repitched", "warped", "both"] = "none"


class BatchReq(BaseModel):
    input_dir: str
    files: list[str] | None = None  # subset of the folder's audio files (names); None = all
    recursive: bool = False
    output_dir: str | None = None  # default: <data>/batches
    workflow: workflows.Workflow


class SettingsReq(BaseModel):
    export_dir: str | None = None  # where exports go; empty / null = defaults


class RevealReq(BaseModel):
    path: str


def create_app(store: Store | None = None) -> FastAPI:
    store = store or Store()
    svc = Chopper(store)
    jobs = JobRunner()
    app = FastAPI(title="ChopChop")

    @app.middleware("http")
    async def undo_headers(request, call_next):
        """Every project response says what Undo / Redo would do next, so the UI's
        buttons stay current without extra requests."""
        response = await call_next(request)
        m = re.match(r"/api/projects/([a-f0-9]{10})(/|$)", request.url.path)
        if m and response.status_code < 500:
            h = store.history(m.group(1))
            response.headers["X-Chopper-Undo"] = quote(h["undo"] or "")
            response.headers["X-Chopper-Redo"] = quote(h["redo"] or "")
        return response

    @app.exception_handler(ProjectNotFound)
    async def _nf(_req, exc):
        return JSONResponse({"detail": f"project not found: {exc}"}, status_code=404)

    def get_project(pid: str) -> dict:
        return store.get(pid)

    def track_or_404(project: dict, name: str) -> dict:
        try:
            return find_track(project, name)
        except KeyError as e:
            raise HTTPException(404, str(e))

    def sample_or_404(project: dict, sid: str) -> dict:
        try:
            return find_sample(project, sid)
        except KeyError as e:
            raise HTTPException(404, str(e))

    def export_folder(project: dict, kind: str) -> Path | None:
        """<export folder>/<project>/<kind> – <date>/ when an export folder is set, else
        None (export as a zip download, as before)."""
        base = settings.export_dir()
        if not base:
            return None
        stamp = time.strftime("%Y-%m-%d %H.%M")
        return workflows.unique_dir(base / workflows.folder_name(project["name"]) / f"{kind} – {stamp}")

    def export_result(pid: str, path: Path, folder: Path | None, count: int) -> dict:
        return {
            "file": path.name,
            "path": str(path),
            "url": None if folder else f"/api/projects/{pid}/exports/{path.name}",
            "folder": folder is not None,
            "count": count,
        }

    add_track = svc.add_track
    detect_tempo = svc.detect_tempo

    async def save_upload(file: UploadFile) -> Path:
        suffix = Path(file.filename or "audio").suffix or ".wav"
        fd, name = tempfile.mkstemp(suffix=suffix)
        # Write through mkstemp's own handle so it gets closed; a leaked handle makes
        # the later unlink fail on Windows (files can't be deleted while open).
        with open(fd, "wb") as fh:
            shutil.copyfileobj(file.file, fh)
        return Path(name)

    # ---------- meta ----------

    @app.get("/api/meta")
    def meta():
        return {
            "categories": analysis.CATEGORIES,
            "drum_categories": analysis.DRUM_CATEGORIES,
            "keys": analysis.KEYS,
            "notes": analysis.NOTE_NAMES,
            "separation": separation.status(),
            "device": separation.device_info() if separation.status()["available"] else None,
            "models": separation.MODELS,
            "tools": {k: {**{x: v[x] for x in ("label", "description", "for")}, "outputs": list(v["outputs"].values())}
                      for k, v in separation.TOOLS.items()},
        }

    # ---------- projects ----------

    @app.get("/api/projects")
    def list_projects():
        return store.list()

    @app.post("/api/projects")
    async def create_project(file: UploadFile = File(...), name: str = Form("")):
        tmp = await save_upload(file)
        try:
            return svc.create_project(tmp, name or Path(file.filename or "Untitled").stem)
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            tmp.unlink(missing_ok=True)

    @app.get("/api/projects/{pid}")
    def project(pid: str):
        return get_project(pid)

    @app.patch("/api/projects/{pid}")
    def rename_project(pid: str, body: dict):
        with store.edit(pid, "Rename project") as p:
            if "name" in body and str(body["name"]).strip():
                p["name"] = str(body["name"]).strip()
        return store.get(pid)

    @app.delete("/api/projects/{pid}")
    def delete_project(pid: str):
        store.delete(pid)
        return {"ok": True}

    # ---------- undo / redo ----------

    @app.get("/api/projects/{pid}/history")
    def history(pid: str):
        get_project(pid)
        return store.history(pid)

    @app.post("/api/projects/{pid}/undo")
    def undo(pid: str):
        get_project(pid)
        step = store.undo(pid)
        if step is None:
            raise HTTPException(409, "nothing to undo")
        return {"undone": step[0], "project": step[1]}

    @app.post("/api/projects/{pid}/redo")
    def redo(pid: str):
        get_project(pid)
        step = store.redo(pid)
        if step is None:
            raise HTTPException(409, "nothing to redo")
        return {"redone": step[0], "project": step[1]}

    @app.get("/api/projects/{pid}/jobs")
    def project_jobs(pid: str):
        return jobs.for_project(pid)

    # ---------- tracks ----------

    @app.post("/api/projects/{pid}/tracks")
    async def import_stem(pid: str, file: UploadFile = File(...), name: str = Form("")):
        get_project(pid)
        tmp = await save_upload(file)
        stem = name or Path(file.filename or "stem").stem
        if safe_name(stem) == "mix":
            stem = "mix_2"
        try:
            return add_track(pid, stem, "stem", tmp, f"Import stem {safe_name(stem)}")
        except Exception as e:
            raise HTTPException(400, f"could not read audio: {e}")
        finally:
            tmp.unlink(missing_ok=True)

    @app.delete("/api/projects/{pid}/tracks/{name}")
    def delete_track(pid: str, name: str):
        with store.edit(pid, f"Remove stem {name}") as p:
            t = track_or_404(p, name)
            if t["kind"] == "mix":
                raise HTTPException(400, "cannot delete the original mix")
            p["tracks"].remove(t)
        return {"ok": True}  # the file stays until no undo step needs it

    @app.get("/api/projects/{pid}/tracks/{name}/audio")
    def track_audio(pid: str, name: str):
        t = track_or_404(get_project(pid), name)
        return FileResponse(store.path(pid) / t["file"], media_type="audio/wav")

    @app.get("/api/projects/{pid}/tracks/{name}/peaks")
    def track_peaks(pid: str, name: str, n: int = 4000):
        t = track_or_404(get_project(pid), name)
        n = max(100, min(n, 50000))
        return {"duration": t["duration"], "peaks": audio.peaks(store.path(pid) / t["file"], n)}

    @app.get("/api/projects/{pid}/tracks/{name}/window")
    def track_window(pid: str, name: str, start: float, end: float):
        t = track_or_404(get_project(pid), name)
        if end <= start:
            raise HTTPException(400, "end must be after start")
        y, sr = audio.read(store.path(pid) / t["file"], max(0.0, start), min(end, start + 120))
        return Response(audio.wav_bytes(y, sr), media_type="audio/wav")

    # ---------- separation ----------

    @app.post("/api/projects/{pid}/separate")
    def separate(pid: str, req: SeparateReq):
        get_project(pid)
        try:
            svc.check_separate(req.model, req.stems)
        except ValueError as e:
            raise HTTPException(400, str(e))

        def work(job: Job):
            return {"tracks": svc.separate(pid, req.model, req.stems, req.rest, req.replace, job.update,
                                           debleed=req.debleed)}

        return asdict(jobs.submit("separate", pid, work))

    # ---------- stem tools (drum split, de-reverb, lead/backing) ----------

    @app.post("/api/projects/{pid}/tracks/{name}/tool")
    def run_tool(pid: str, name: str, req: ToolReq):
        track_or_404(get_project(pid), name)
        try:
            svc.check_tool(req.tool)
        except ValueError as e:
            raise HTTPException(400, str(e))

        def work(job: Job):
            return {"tracks": svc.run_tool(pid, name, req.tool, job.update)}

        return asdict(jobs.submit(f"tool:{req.tool}", pid, work))

    # ---------- tempo ----------

    @app.post("/api/projects/{pid}/tempo")
    def post_tempo(pid: str, req: TempoDetectReq):
        try:
            return detect_tempo(pid, req.track)
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.put("/api/projects/{pid}/tempo")
    def put_tempo(pid: str, req: TempoSetReq):
        """Adjust the tempo / grid by hand: bpm (typed, tapped, x2, ÷2), which beat is
        beat 1, shift the grid, or anchor a bar start to a hit."""
        label = ("Change tempo" if req.bpm is not None else "Set bar start" if req.anchor is not None
                 else "Move beat grid" if req.shift is not None else "Change downbeat")
        # Anchor on the actual attack near the chosen time, so a rough click lands on the hit.
        at = None
        if req.anchor is not None:
            p0 = get_project(pid)
            src = next((t for t in p0["tracks"] if t["name"] == (p0.get("tempo") or {}).get("track")), None)
            at = req.anchor
            if src:
                y, sr = audio.read_mono(store.path(pid) / src["file"], sr=None,
                                        start=max(0.0, req.anchor - 0.1), end=req.anchor + 0.1)
                lo = max(0.0, req.anchor - 0.1)
                i = int((req.anchor - lo) * sr)
                at = lo + slicing.snap_to_attack(y, sr, i - int(0.06 * sr), i + int(0.06 * sr)) / sr
        with store.edit(pid, label) as p:
            base = p.get("tempo") or {"bpm": req.bpm, "beats": [], "alternatives": [], "range": None}
            if req.bpm is not None:
                base = tempo.rescale(base, req.bpm)
            if req.downbeat is not None:
                base = {**base, "downbeat": req.downbeat}
            if req.shift is not None:
                base = tempo.shift(base, req.shift)
            if at is not None:
                try:
                    base = tempo.anchor(base, at)
                except ValueError as e:
                    raise HTTPException(400, str(e))
            if base.get("bpm") is None:
                raise HTTPException(400, "set a bpm first")
            p["tempo"] = base
            return p["tempo"]

    @app.post("/api/jobs/{jid}/cancel")
    def cancel_job(jid: str):
        if not jobs.cancel(jid):
            raise HTTPException(409, "job is not running")
        return {"ok": True}

    # ---------- batch workflows ----------

    @app.get("/api/settings")
    def get_settings():
        out = settings.export_dir()
        return {"export_dir": str(out) if out else None, "default_batch_output": str(workflows.default_output())}

    @app.put("/api/settings")
    def put_settings(body: SettingsReq):
        try:
            settings.set_export_dir(body.export_dir)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return get_settings()

    @app.get("/api/fs")
    def list_folder(path: str | None = None):
        """Folders and audio files at a path on this computer (for the folder picker)."""
        try:
            return workflows.list_dir(path)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/workflows")
    def list_workflows():
        return {
            "presets": [w.model_dump() for w in workflows.PRESETS],
            "saved": workflows.saved(),
            "default_output": str(settings.export_dir() or workflows.default_output()),
        }

    @app.put("/api/workflows")
    def save_workflow(wf: workflows.Workflow):
        try:
            workflows.check(wf)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"saved": workflows.save(wf)}

    @app.delete("/api/workflows/{name}")
    def delete_workflow(name: str):
        return {"saved": workflows.delete(name)}

    @app.post("/api/batches")
    def start_batch(req: BatchReq):
        try:
            workflows.check(req.workflow)
            if req.workflow.separate.enabled or req.workflow.tools:
                st = separation.status()
                if not st["available"]:
                    raise ValueError(st["reason"])
        except ValueError as e:
            raise HTTPException(400, str(e))
        try:
            files, out_root = workflows.prepare(req.input_dir, req.files, req.recursive, req.output_dir)
        except ValueError as e:
            raise HTTPException(400, str(e))

        def work(job: Job):
            return workflows.run_batch(svc, files, req.workflow, out_root, job)

        job = jobs.submit("batch", "batch", work)
        job.detail = {"output": str(out_root), "workflow": req.workflow.name,
                      "songs": [{"file": str(f), "song": f.stem, "status": "queued", "message": ""} for f in files]}
        return asdict(job)

    def hidden_runs() -> set[str]:
        return set(settings.get("hidden_runs", []))

    def hide_runs(paths: list[str]) -> None:
        # Forget folders that are gone so the list doesn't grow forever.
        keep = {p for p in hidden_runs() | set(paths) if Path(p).exists()}
        settings.put("hidden_runs", sorted(keep))

    def project_exists(pid: str) -> bool:
        try:
            return (store.path(pid) / "project.json").is_file()
        except ProjectNotFound:
            return False

    def disk_runs(scan: list[str]) -> list[dict]:
        """Runs found on disk (chopper-batch.json) in the export / batch folders and the
        folders the batch page is looking at, minus hidden ones and this session's own."""
        folders = [Path(p) for p in scan if p] + [workflows.default_output()]
        if settings.export_dir():
            folders.append(settings.export_dir())
        live = {str(Path(j["detail"]["output"]).resolve()) for j in jobs.of_kind("batch") if j.get("detail")}
        hidden = hidden_runs()
        runs = []
        for r in workflows.find_runs(folders):
            out = r["detail"]["output"]
            if out in live or out in hidden:
                continue
            for song in r["detail"]["songs"]:  # projects deleted since then: no "open" link
                if song.get("project") and not project_exists(song["project"]):
                    song.pop("project", None)
            runs.append(r)
        return runs

    @app.get("/api/batches")
    def list_batches(scan: list[str] = Query(default=[])):
        runs = jobs.of_kind("batch") + disk_runs(scan)
        return sorted(runs, key=lambda r: -r["created_at"])

    @app.delete("/api/batches/{jid}")
    def remove_batch(jid: str, scan: list[str] = Query(default=[])):
        """Remove a finished run from the list. Its output files are left alone."""
        if jid.startswith("disk:"):
            run = next((r for r in disk_runs(scan) if r["id"] == jid), None)
            if not run:
                raise HTTPException(404, "batch not found")
            hide_runs([run["detail"]["output"]])
            return {"ok": True}
        job = jobs.get(jid)
        if not job or not job.kind.startswith("batch"):
            raise HTTPException(404, "batch not found")
        if not jobs.remove(jid):
            raise HTTPException(409, "the batch is still running; cancel it first")
        if job.detail and job.detail.get("output"):
            hide_runs([str(Path(job.detail["output"]).resolve())])  # so it doesn't come back from disk
        return {"ok": True}

    @app.delete("/api/batches")
    def clear_batches(scan: list[str] = Query(default=[])):
        """Remove every finished run from the list (running ones stay; files stay)."""
        finished = [j for j in jobs.of_kind("batch") if j["status"] not in ("pending", "running")]
        on_disk = disk_runs(scan)
        hide_runs([str(Path(j["detail"]["output"]).resolve()) for j in finished if j.get("detail")]
                  + [r["detail"]["output"] for r in on_disk])
        return {"removed": jobs.clear("batch") + len(on_disk)}

    @app.post("/api/reveal")
    def reveal(req: RevealReq):
        """Open a folder in Finder / Explorer / the file manager (ChopChop runs locally)."""
        target = Path(req.path).expanduser()
        if not target.exists():
            raise HTTPException(404, "folder not found")
        cmd = {"Darwin": ["open"], "Windows": ["explorer"]}.get(platform.system(), ["xdg-open"])
        subprocess.Popen([*cmd, str(target)])
        return {"ok": True}

    @app.get("/api/jobs/{jid}")
    def job(jid: str):
        j = jobs.get(jid)
        if not j:
            raise HTTPException(404, "job not found")
        return asdict(j)

    # ---------- slices ----------

    @app.post("/api/projects/{pid}/slices/detect")
    def detect(pid: str, req: DetectReq):
        p = get_project(pid)
        t = track_or_404(p, req.track)
        y, sr = audio.read_mono(store.path(pid) / t["file"], sr=None)
        onsets = slicing.detect_onsets(y, sr, req.sensitivity, req.min_gap, req.gate_db)
        slices = slicing.slices_from_onsets(onsets, t["duration"], req.max_len)
        with store.edit(pid, "Detect slices") as p:
            p["slices"] = slices
        return {"slices": slices}

    @app.put("/api/projects/{pid}/slices")
    def put_slices(pid: str, req: SlicesReq):
        slices = sorted(
            ({"id": s.id or new_id(), "start": round(s.start, 4), "end": round(s.end, 4)}
             for s in req.slices if s.end > s.start),
            key=lambda s: s["start"],
        )
        with store.edit(pid, req.label or "Edit slices") as p:
            p["slices"] = slices
        return {"slices": slices}

    @app.post("/api/projects/{pid}/slices/export")
    def export_slices(pid: str, req: ExportSlicesReq):
        p = get_project(pid)
        tracks = [track_or_404(p, n) for n in req.tracks]
        slices = p["slices"]
        if req.slice_ids is not None:
            wanted = set(req.slice_ids)
            slices = [s for s in slices if s["id"] in wanted]
        if not slices:
            raise HTTPException(400, "no slices to export")
        if not tracks:
            raise HTTPException(400, "no tracks selected")
        bpm = (p.get("tempo") or {}).get("bpm") if req.bpm_names else None

        def work(job: Job):
            out = export_folder(p, "Slices")
            path, written, silent = slicing.export_slices(
                store.path(pid), tracks, slices, req.fade_in_ms, req.fade_out_ms, req.normalize, bpm=bpm,
                keys=req.key_names, progress=job.update, out_dir=out, skip_silent=req.skip_silent)
            return export_result(pid, path, out, written) | {"silent": silent}

        return asdict(jobs.submit("export:slices", pid, work))

    @app.get("/api/projects/{pid}/exports/{fname}")
    def download_export(pid: str, fname: str):
        path = store.path(pid) / "exports" / Path(fname).name
        if not path.exists():
            raise HTTPException(404, "export not found")
        return FileResponse(path, filename=path.name, media_type="application/zip")

    # ---------- kits & sound bits ----------

    @app.post("/api/projects/{pid}/kits")
    def build_kit(pid: str, req: KitReq):
        p = get_project(pid)
        names = req.tracks or [req.track]
        for n in names:
            track_or_404(p, n)
        if req.mode not in ("drums", "chops", "phrases"):
            raise HTTPException(400, "mode must be drums, chops or phrases")

        def work(job: Job):
            saved = svc.build_kit(pid, names, req.mode, req.sensitivity, req.similarity, req.max_per_category,
                                  req.kit_size, req.load_pads, req.replace, job.update)
            return {"samples": [s["id"] for s in saved], "pads_loaded": req.load_pads}

        return asdict(jobs.submit(f"kit:{req.mode}", pid, work))

    # ---------- samples ----------

    @app.post("/api/projects/{pid}/samples")
    def create_sample(pid: str, req: SampleCreateReq):
        p = get_project(pid)
        t = track_or_404(p, req.track)
        end = min(req.end, t["duration"])
        if end - req.start < 0.005:
            raise HTTPException(400, "region too short")
        folder = store.path(pid)
        rec = kits.analyse_region(folder, t, req.start, end, req.category)
        rec.update({"track": t["name"], "start": round(req.start, 4), "end": round(end, 4),
                    "origin": "manual", "score": None, "name": req.name})
        with store.edit(pid, "Save sample") as proj:
            return kits.save_sample(folder, proj, t, rec)

    @app.patch("/api/projects/{pid}/samples/{sid}")
    def update_sample(pid: str, sid: str, req: SampleUpdateReq):
        folder = store.path(pid)
        what = "Trim" if req.start is not None or req.end is not None else \
            "Rename" if req.name else "Recategorize" if req.category else "Set key of"
        with store.edit(pid, f"{what} sample") as p:
            s = sample_or_404(p, sid)
            if req.name is not None and req.name.strip():
                s["name"] = req.name.strip()
            if req.category is not None and req.category.strip():
                s["category"] = safe_name(req.category)
            if req.key is not None:
                s["key"] = req.key.strip() or None
                s["key_confidence"] = 1.0
            if req.clear_key:
                s["key"] = None
            if req.start is not None or req.end is not None:
                t = track_or_404(p, s["track"])
                start = req.start if req.start is not None else s["start"]
                end = min(req.end if req.end is not None else s["end"], t["duration"])
                if end - start < 0.005:
                    raise HTTPException(400, "region too short")
                y, sr = audio.cut(folder / t["file"], start, end)
                # New file, so undo can bring back the untrimmed audio.
                s["file"] = f"samples/{s['id']}-{new_id()[:6]}.wav"
                audio.write_wav(folder / s["file"], y, sr)
                s.update(start=round(start, 4), end=round(end, 4), duration=round(y.shape[1] / sr, 4))
            return s

    @app.post("/api/projects/{pid}/samples/{sid}/analyze")
    def reanalyze_sample(pid: str, sid: str, keep_category: bool = False):
        folder = store.path(pid)
        p = get_project(pid)
        s = sample_or_404(p, sid)
        t = track_or_404(p, s["track"])
        rec = kits.analyse_region(folder, t, s["start"], s["end"], s["category"] if keep_category else None)
        with store.edit(pid, "Re-detect key") as p:
            s = sample_or_404(p, sid)
            s.update(rec)
            return s

    @app.put("/api/projects/{pid}/pads")
    def put_pads(pid: str, req: PadsReq):
        if len(req.pads) != PAD_SLOTS:
            raise HTTPException(400, f"expected {PAD_SLOTS} pads")
        with store.edit(pid, req.label or "Edit pads") as p:
            ids = {s["id"] for s in p["samples"]}
            unknown = [x for x in req.pads if x is not None and x not in ids]
            if unknown:
                raise HTTPException(400, f"unknown samples: {unknown}")
            p["pads"] = list(req.pads)
            return {"pads": p["pads"]}

    @app.delete("/api/projects/{pid}/samples/{sid}")
    def delete_sample(pid: str, sid: str):
        with store.edit(pid, "Delete sample") as p:
            s = sample_or_404(p, sid)
            p["samples"].remove(s)
            p["pads"] = [None if x == sid else x for x in p["pads"]]
        return {"ok": True}

    @app.post("/api/projects/{pid}/samples/delete")
    def delete_samples(pid: str, req: DeleteSamplesReq):
        """Bulk delete; also empties any pads the samples were on."""
        ids = set(req.ids)
        with store.edit(pid, f"Delete {len(ids)} samples") as p:
            gone = [s for s in p["samples"] if s["id"] in ids]
            p["samples"] = [s for s in p["samples"] if s["id"] not in ids]
            p["pads"] = [None if x in ids else x for x in p["pads"]]
        return {"deleted": len(gone)}

    @app.get("/api/projects/{pid}/samples/{sid}/audio")
    def sample_audio(pid: str, sid: str):
        s = sample_or_404(get_project(pid), sid)
        return FileResponse(store.path(pid) / s["file"], media_type="audio/wav")

    @app.get("/api/projects/{pid}/samples/{sid}/peaks")
    def sample_peaks(pid: str, sid: str, n: int = 200):
        s = sample_or_404(get_project(pid), sid)
        return {"duration": s["duration"], "peaks": audio.peaks(store.path(pid) / s["file"], max(20, min(n, 4000)))}

    @app.post("/api/projects/{pid}/samples/export")
    def export_kit(pid: str, req: ExportKitReq):
        p = get_project(pid)
        samples = p["samples"]
        labels = None
        if req.from_pads:
            by_id = {s["id"]: s for s in samples}
            on_pads = [(i, by_id[x]) for i, x in enumerate(p["pads"]) if x in by_id]
            samples = [s for _, s in on_pads]
            # A sample on several pads is exported once, under its first pad.
            labels = {}
            for i, s in on_pads:
                labels.setdefault(s["id"], kits.pad_label(i))
            samples = list({s["id"]: s for s in samples}.values())
        elif req.sample_ids is not None:
            wanted = set(req.sample_ids)
            samples = [s for s in samples if s["id"] in wanted]
        if not samples:
            raise HTTPException(400, "no samples to export")
        bpm = (p.get("tempo") or {}).get("bpm")
        to_c = transpose.modes(req.to_c)
        out = export_folder(p, "Pads" if req.from_pads else "Kit")
        if out:
            kits.write_kit(store.path(pid), samples, out, req.normalize, labels, bpm, to_c)
            path = out
        else:
            path = kits.export_kit(store.path(pid), p, samples, req.kit_name or p["name"], req.normalize, labels,
                                   bpm, to_c)
        result = export_result(pid, path, out, len(samples))
        # Keyed samples not already in C, once per way of transposing.
        result["transposed"] = sum(transpose.steps_to_c(s.get("key")) is not None for s in samples) * len(to_c)
        return result

    # ---------- frontend ----------

    if FRONTEND_DIST.exists():
        # Python's mimetypes reads the Windows registry, where .js is often registered
        # as text/plain; browsers refuse module scripts with that type (blank page).
        for ext, mime in ((".js", "text/javascript"), (".mjs", "text/javascript"),
                          (".css", "text/css"), (".svg", "image/svg+xml")):
            mimetypes.add_type(mime, ext)
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app
