"""FastAPI app: REST API + static frontend."""

from __future__ import annotations

import mimetypes
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analysis, audio, kits, separation, slicing
from .jobs import Job, JobRunner
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


class ExportSlicesReq(BaseModel):
    tracks: list[str]
    slice_ids: list[str] | None = None
    fade_in_ms: float = Field(2, ge=0, le=500)
    fade_out_ms: float = Field(8, ge=0, le=2000)
    normalize: bool = False


class SeparateReq(BaseModel):
    model: str = "htdemucs_6s.yaml"


class KitReq(BaseModel):
    track: str
    mode: str = "drums"  # drums | chops | phrases
    sensitivity: float = Field(0.5, ge=0, le=1)
    similarity: float = Field(0.93, ge=0.3, le=1)
    max_per_category: int = Field(8, ge=1, le=200)
    kit_size: int | None = Field(None, ge=1, le=64)  # auto kit: best N unique sounds
    load_pads: bool = False  # lay the result out on the drum pads
    replace: bool = True


class PadsReq(BaseModel):
    pads: list[str | None]


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


class ExportKitReq(BaseModel):
    sample_ids: list[str] | None = None
    kit_name: str = "kit"
    normalize: bool = False
    from_pads: bool = False  # export what's on the pads, named A01_…, B02_…


def create_app(store: Store | None = None) -> FastAPI:
    store = store or Store()
    jobs = JobRunner()
    app = FastAPI(title="Chopper")

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

    def add_track(pid: str, name: str, kind: str, src: Path) -> dict:
        folder = store.path(pid)
        fname = safe_name(name)
        rel = f"tracks/{fname}.wav"
        meta = audio.import_audio(src, folder / rel)
        track = {"name": fname, "kind": kind, "file": rel, **meta}
        with store.edit(pid) as p:
            p["tracks"] = [t for t in p["tracks"] if t["name"] != fname] + [track]
            if kind == "mix":
                p["duration"] = meta["duration"]
                p["sample_rate"] = meta["sample_rate"]
        return track

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
        }

    # ---------- projects ----------

    @app.get("/api/projects")
    def list_projects():
        return store.list()

    @app.post("/api/projects")
    async def create_project(file: UploadFile = File(...), name: str = Form("")):
        tmp = await save_upload(file)
        project = store.create(name or Path(file.filename or "Untitled").stem)
        try:
            add_track(project["id"], "mix", "mix", tmp)
        except Exception as e:
            store.delete(project["id"])
            raise HTTPException(400, f"could not read audio: {e}")
        finally:
            tmp.unlink(missing_ok=True)
        return store.get(project["id"])

    @app.get("/api/projects/{pid}")
    def project(pid: str):
        return get_project(pid)

    @app.patch("/api/projects/{pid}")
    def rename_project(pid: str, body: dict):
        with store.edit(pid) as p:
            if "name" in body and str(body["name"]).strip():
                p["name"] = str(body["name"]).strip()
        return store.get(pid)

    @app.delete("/api/projects/{pid}")
    def delete_project(pid: str):
        store.delete(pid)
        return {"ok": True}

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
            return add_track(pid, stem, "stem", tmp)
        except Exception as e:
            raise HTTPException(400, f"could not read audio: {e}")
        finally:
            tmp.unlink(missing_ok=True)

    @app.delete("/api/projects/{pid}/tracks/{name}")
    def delete_track(pid: str, name: str):
        with store.edit(pid) as p:
            t = track_or_404(p, name)
            if t["kind"] == "mix":
                raise HTTPException(400, "cannot delete the original mix")
            p["tracks"].remove(t)
        (store.path(pid) / t["file"]).unlink(missing_ok=True)
        return {"ok": True}

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
        p = get_project(pid)
        st = separation.status()
        if not st["available"]:
            raise HTTPException(400, st["reason"])
        mix = next(t for t in p["tracks"] if t["kind"] == "mix")
        folder = store.path(pid)

        def work(job: Job):
            out = folder / "separation" / new_id()
            try:
                stems = separation.separate(folder / mix["file"], out, req.model, job.update)
                added = []
                for i, (name, path) in enumerate(stems.items()):
                    job.update(0.9 + 0.1 * i / len(stems), f"Importing stem {i + 1}/{len(stems)}: {name}")
                    added.append(add_track(pid, name, "stem", path)["name"])
            finally:
                shutil.rmtree(out, ignore_errors=True)
            return {"tracks": added}

        return asdict(jobs.submit("separate", pid, work))

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
        with store.edit(pid) as p:
            p["slices"] = slices
        return {"slices": slices}

    @app.put("/api/projects/{pid}/slices")
    def put_slices(pid: str, req: SlicesReq):
        slices = sorted(
            ({"id": s.id or new_id(), "start": round(s.start, 4), "end": round(s.end, 4)}
             for s in req.slices if s.end > s.start),
            key=lambda s: s["start"],
        )
        with store.edit(pid) as p:
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
        try:
            path = slicing.export_slices(store.path(pid), tracks, slices, req.fade_in_ms, req.fade_out_ms, req.normalize)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {
            "file": path.name,
            "path": str(path),
            "url": f"/api/projects/{pid}/exports/{path.name}",
            "count": len(slices) * len(tracks),
        }

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
        t = track_or_404(p, req.track)
        if req.mode not in ("drums", "chops", "phrases"):
            raise HTTPException(400, "mode must be drums, chops or phrases")
        folder = store.path(pid)

        def work(job: Job):
            recs = kits.extract(folder, t, req.mode, req.sensitivity, req.similarity,
                                req.max_per_category, kit_size=req.kit_size, progress=job.update)
            job.update(0.96, f"Saving {len(recs)} samples…")
            with store.edit(pid) as proj:
                if req.replace:
                    old = [s for s in proj["samples"] if s.get("track") == t["name"] and s.get("origin") == req.mode]
                    for s in old:
                        (folder / s["file"]).unlink(missing_ok=True)
                    gone = {s["id"] for s in old}
                    proj["samples"] = [s for s in proj["samples"] if s["id"] not in gone]
                    proj["pads"] = [None if p in gone else p for p in proj["pads"]]
                saved = [kits.save_sample(folder, proj, t, r) for r in recs]
                if req.load_pads:
                    proj["pads"] = kits.layout_pads(saved)
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
        with store.edit(pid) as proj:
            return kits.save_sample(folder, proj, t, rec)

    @app.patch("/api/projects/{pid}/samples/{sid}")
    def update_sample(pid: str, sid: str, req: SampleUpdateReq):
        folder = store.path(pid)
        with store.edit(pid) as p:
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
        with store.edit(pid) as p:
            s = sample_or_404(p, sid)
            s.update(rec)
            return s

    @app.put("/api/projects/{pid}/pads")
    def put_pads(pid: str, req: PadsReq):
        if len(req.pads) != PAD_SLOTS:
            raise HTTPException(400, f"expected {PAD_SLOTS} pads")
        with store.edit(pid) as p:
            ids = {s["id"] for s in p["samples"]}
            unknown = [x for x in req.pads if x is not None and x not in ids]
            if unknown:
                raise HTTPException(400, f"unknown samples: {unknown}")
            p["pads"] = list(req.pads)
            return {"pads": p["pads"]}

    @app.delete("/api/projects/{pid}/samples/{sid}")
    def delete_sample(pid: str, sid: str):
        with store.edit(pid) as p:
            s = sample_or_404(p, sid)
            p["samples"].remove(s)
            p["pads"] = [None if x == sid else x for x in p["pads"]]
        (store.path(pid) / s["file"]).unlink(missing_ok=True)
        return {"ok": True}

    @app.post("/api/projects/{pid}/samples/delete")
    def delete_samples(pid: str, req: DeleteSamplesReq):
        """Bulk delete; also empties any pads the samples were on."""
        ids = set(req.ids)
        with store.edit(pid) as p:
            gone = [s for s in p["samples"] if s["id"] in ids]
            p["samples"] = [s for s in p["samples"] if s["id"] not in ids]
            p["pads"] = [None if x in ids else x for x in p["pads"]]
        for s in gone:
            (store.path(pid) / s["file"]).unlink(missing_ok=True)
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
        try:
            path = kits.export_kit(store.path(pid), p, samples, req.kit_name or p["name"], req.normalize, labels)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {
            "file": path.name,
            "path": str(path),
            "url": f"/api/projects/{pid}/exports/{path.name}",
            "count": len(samples),
        }

    # ---------- frontend ----------

    if FRONTEND_DIST.exists():
        # Python's mimetypes reads the Windows registry, where .js is often registered
        # as text/plain; browsers refuse module scripts with that type (blank page).
        for ext, mime in ((".js", "text/javascript"), (".mjs", "text/javascript"),
                          (".css", "text/css"), (".svg", "image/svg+xml")):
            mimetypes.add_type(mime, ext)
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app
