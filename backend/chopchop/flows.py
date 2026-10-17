"""Node workflows: a graph of steps, run on one track (a project) or a folder (a batch).

    Input track ─▶ Separate (MDX) ─┬─ vocals ──────────────▶ Save stems
                                   └─ instrumental ─▶ Separate (Demucs) ─┬─ drums ─▶ Sound bits ─▶ Save samples (in C)
                                                                         └─ bass ───┘

Every connection carries a list of things of one kind:

    audio    tracks of the project (the mix, stems, stem-tool parts)
    slices   chops cut on one track
    samples  drum-kit hits / sound bits

A node runs once over everything connected to its input: Separate splits each
incoming track, Sound bits cuts bits from every stem fed to it, Save samples writes
all incoming samples into one folder. Outputs fan out to any number of nodes.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field

from . import analysis, audio, kits, separation, slicing, transpose
from .naming import export_name
from .store import data_dir, new_id, safe_name

Progress = Callable[[float, str], None]

# ---------- the graph ----------


class FlowNode(BaseModel):
    id: str
    type: Literal["input", "separate", "tool", "slices", "kit", "bits", "save_stems", "save_slices", "save_samples"]
    params: dict = {}
    x: float = 0.0  # position on the canvas (the editor's, not used to run)
    y: float = 0.0


class FlowEdge(BaseModel):
    source: str
    port: str  # output port of the source node, e.g. "vocals"
    target: str


class Flow(BaseModel):
    kind: Literal["flow"] = "flow"
    name: str = Field("My workflow", min_length=1, max_length=80)
    description: str = ""
    nodes: list[FlowNode]
    edges: list[FlowEdge] = []
    keep_projects: bool = True  # batches: keep each song as a project to open later


# Options of each node, with their defaults (the editor shows every one).

class SeparateParams(BaseModel):
    model: str = "UVR-MDX-NET-Inst_HQ_4.onnx"
    stems: list[str] | None = None  # None = every stem the model makes
    rest: bool = False  # mix the stems not kept into one more track
    debleed: bool = True  # move drum bleed out of other / guitar / piano into drums


class ToolParams(BaseModel):
    tool: str = "drumsep"


class SlicesParams(BaseModel):
    mode: Literal["detect", "grid"] = "detect"
    sensitivity: float = Field(0.5, ge=0, le=1)
    min_gap: float = Field(0.08, ge=0.01, le=5)
    max_len: float | None = Field(None, gt=0, le=60)
    gate_db: float = Field(-40, ge=-90, le=0)
    grid_beats: float = Field(4, ge=0.25, le=16)  # grid mode: cell size in beats (4 = 1 bar)


class KitParams(BaseModel):
    sensitivity: float = Field(0.5, ge=0, le=1)
    uniqueness: float = Field(0.93, ge=0.3, le=1)
    max_per_type: int = Field(8, ge=1, le=200)
    kit_size: int | None = Field(16, ge=1, le=64)  # None = every distinct hit
    load_pads: bool = True  # also lay it out on the project's drum pads


class BitsParams(BaseModel):
    mode: Literal["chops", "phrases"] = "chops"
    sensitivity: float = Field(0.5, ge=0, le=1)
    uniqueness: float = Field(0.9, ge=0.3, le=1)
    max: int = Field(16, ge=1, le=200)


class SaveStemsParams(BaseModel):
    folder: str = "stems"
    normalize: bool = False
    bpm_names: bool = True
    key_names: bool = True  # the song's key, on pitched stems


class SaveSlicesParams(BaseModel):
    folder: str = "slices"
    fade_in_ms: float = Field(2, ge=0, le=500)
    fade_out_ms: float = Field(8, ge=0, le=2000)
    normalize: bool = False
    bpm_names: bool = True
    key_names: bool = False  # detect each chop's key (slower)
    skip_silent: bool = True


class SaveSamplesParams(BaseModel):
    folder: str = "samples"
    normalize: bool = False
    bpm_names: bool = True
    to_c: Literal["none", "repitched", "warped", "both"] = "none"
    pad_names: bool = False  # drum kits as A01_kick… in pad order


PARAMS: dict[str, type[BaseModel] | None] = {
    "input": None, "separate": SeparateParams, "tool": ToolParams, "slices": SlicesParams, "kit": KitParams,
    "bits": BitsParams, "save_stems": SaveStemsParams, "save_slices": SaveSlicesParams,
    "save_samples": SaveSamplesParams,
}
INPUT: dict[str, str | None] = {  # what each node takes in
    "input": None, "separate": "audio", "tool": "audio", "slices": "audio", "kit": "audio", "bits": "audio",
    "save_stems": "audio", "save_slices": "slices", "save_samples": "samples",
}
LABELS = {"input": "Input track", "separate": "Separate", "tool": "Stem tool", "slices": "Slices", "kit": "Drum kit",
          "bits": "Sound bits", "save_stems": "Save stems", "save_slices": "Save slices",
          "save_samples": "Save samples"}


def params(node: FlowNode) -> BaseModel | None:
    model = PARAMS[node.type]
    return model(**node.params) if model else None


def outputs(node: FlowNode) -> dict[str, str]:
    """Output ports of a node: {port: kind}."""
    p = params(node)
    if node.type == "input":
        return {"mix": "audio"}
    if node.type == "separate":
        info = next((m for m in separation.models() if m["id"] == p.model), None)
        if info is None:
            raise ValueError(f"unknown model: {p.model}")
        keep = [s for s in info["stems"] if p.stems is None or s in p.stems]
        ports = keep + ([separation.rest_name(keep)] if p.rest and len(keep) < len(info["stems"]) else [])
        return {s: "audio" for s in ports}
    if node.type == "tool":
        if p.tool not in separation.TOOLS:
            raise ValueError(f"unknown stem tool: {p.tool}")
        return {s: "audio" for s in separation.TOOLS[p.tool]["outputs"].values()}
    if node.type == "slices":
        return {"slices": "slices"}
    if node.type in ("kit", "bits"):
        return {"samples": "samples"}
    return {}  # save nodes end a branch


def check(flow: Flow) -> list[FlowNode]:
    """Validate; returns the nodes in run order (each after everything it depends on)."""
    by_id = {n.id: n for n in flow.nodes}
    if len(by_id) != len(flow.nodes):
        raise ValueError("two nodes share an id")
    inputs = [n for n in flow.nodes if n.type == "input"]
    if len(inputs) != 1:
        raise ValueError("a workflow needs exactly one Input track node")
    ports = {}
    for n in flow.nodes:
        try:
            ports[n.id] = outputs(n)
        except ValueError as e:
            raise ValueError(f"{LABELS[n.type]}: {e}") from e
        except Exception as e:  # pydantic: a bad option value
            raise ValueError(f"{LABELS[n.type]}: {e}") from e
    for e in flow.edges:
        if e.source not in by_id or e.target not in by_id:
            raise ValueError("a connection points at a node that doesn't exist")
        kind = ports[e.source].get(e.port)
        if kind is None:
            raise ValueError(f"{LABELS[by_id[e.source].type]} has no output '{e.port}'")
        want = INPUT[by_id[e.target].type]
        if want != kind:
            raise ValueError(f"can't connect {kind} into {LABELS[by_id[e.target].type]} (it takes {want or 'nothing'})")
    # Topological order (Kahn); anything left over is a loop.
    deps = {n.id: {e.source for e in flow.edges if e.target == n.id} for n in flow.nodes}
    order, ready = [], [n.id for n in flow.nodes if not deps[n.id]]
    while ready:
        nid = ready.pop(0)
        order.append(by_id[nid])
        for other, d in deps.items():
            if nid in d:
                d.discard(nid)
                if not d and by_id[other] not in order and other not in ready:
                    ready.append(other)
    if len(order) != len(flow.nodes):
        raise ValueError("the workflow has a loop")
    return order


# ---------- saved workflows ----------

def _saved_path() -> Path:
    return data_dir() / "flows.json"


def saved() -> list[dict]:
    try:
        return json.loads(_saved_path().read_text())
    except (OSError, ValueError):
        return []


def save(flow: Flow) -> list[dict]:
    check(flow)
    items = [f for f in saved() if f["name"] != flow.name] + [flow.model_dump()]
    _saved_path().write_text(json.dumps(items, indent=1))
    return items


def delete(name: str) -> list[dict]:
    items = [f for f in saved() if f["name"] != name]
    _saved_path().write_text(json.dumps(items, indent=1))
    return items


def _n(id, type, x, y, **params) -> FlowNode:
    return FlowNode(id=id, type=type, x=x, y=y, params=params)


def _e(source, port, target) -> FlowEdge:
    return FlowEdge(source=source, port=port, target=target)


PRESETS = [
    Flow(
        name="Vocals, then bits from the instrumental in C",
        description="MDX splits vocals / instrumental and saves both; Demucs splits the instrumental, "
                    "every one of its stems gives sound bits, saved transposed to C.",
        nodes=[
            _n("in", "input", 0, 160),
            _n("mdx", "separate", 240, 160, model="UVR-MDX-NET-Inst_HQ_4.onnx"),
            _n("stems1", "save_stems", 560, 20, folder="1 stems"),
            _n("demucs", "separate", 560, 230, model="htdemucs_ft.yaml", stems=["drums", "bass", "other"]),
            _n("bits", "bits", 880, 230),
            _n("inC", "save_samples", 1160, 230, folder="2 bits in C", to_c="both"),
        ],
        edges=[
            _e("in", "mix", "mdx"), _e("mdx", "vocals", "stems1"), _e("mdx", "instrumental", "stems1"),
            _e("mdx", "instrumental", "demucs"),
            *[_e("demucs", s, "bits") for s in ("drums", "bass", "other")],
            _e("bits", "samples", "inC"),
        ],
    ),
    Flow(
        name="Drum kit from split drums",
        description="Demucs drums → split into kick / snare / hats … → one 16-pad kit across the parts.",
        nodes=[
            _n("in", "input", 0, 120),
            _n("sep", "separate", 240, 120, model="htdemucs_ft.yaml", stems=["drums"]),
            _n("split", "tool", 540, 120, tool="drumsep"),
            _n("kit", "kit", 840, 120),
            _n("save", "save_samples", 1120, 120, folder="kit", pad_names=True, bpm_names=False),
        ],
        edges=[
            _e("in", "mix", "sep"), _e("sep", "drums", "split"),
            *[_e("split", s, "kit") for s in ("kick", "snare", "toms", "hihat", "ride", "crash")],
            _e("kit", "samples", "save"),
        ],
    ),
    Flow(
        name="1-bar loops of every stem",
        description="6 stems, each chopped on the beat grid into 1-bar loops, saved with BPM in the names.",
        nodes=[
            _n("in", "input", 0, 120),
            _n("sep", "separate", 240, 120, model="htdemucs_6s.yaml"),
            _n("loops", "slices", 560, 120, mode="grid", grid_beats=4),
            _n("save", "save_slices", 840, 120, folder="loops"),
        ],
        edges=[
            _e("in", "mix", "sep"),
            *[_e("sep", s, "loops") for s in ("vocals", "drums", "bass", "guitar", "piano", "other")],
            _e("loops", "slices", "save"),
        ],
    ),
]


# Presets kept as exported files (Workflows → Export): every *.chopchop.json in
# flow_presets/ ships as an example, exactly as it was built in the editor.
PRESETS += [Flow(**json.loads(p.read_text())) for p in sorted((Path(__file__).parent / "flow_presets").glob("*.chopchop.json"))]


# ---------- running ----------

WEIGHTS = {"input": 0.1, "separate": 6, "tool": 4, "slices": 0.5, "kit": 1.5, "bits": 1.5,
           "save_stems": 0.3, "save_slices": 1, "save_samples": 0.5}


def grid_points(beats: list[float], step: float, downbeat: int = 0) -> list[float]:
    """Beat-grid lines every `step` beats from the downbeat (same as the editor's grid)."""
    if len(beats) < 2:
        return []
    ibi = (beats[-1] - beats[0]) / (len(beats) - 1)
    ext = [*beats, beats[-1] + ibi]
    if step < 1:
        n = round(1 / step)
        out = [ext[i] + (ext[i + 1] - ext[i]) * k / n for i in range(len(ext) - 1) for k in range(n)]
        return out + [ext[-1]]
    k = round(step)
    out = [t for i, t in enumerate(ext) if i >= downbeat and (i - downbeat) % k == 0]
    if out and ext[-1] - out[-1] >= k * ibi * 0.5:
        out.append(out[-1] + k * ibi)
    return out


def run(svc, pid: str, flow: Flow, out_dir: Path, progress: Progress = lambda f, m: None,
        check_cancel: Callable[[], None] = lambda: None) -> dict:
    """Run the workflow on a project (its mix is the Input track). Files go under out_dir."""
    order = check(flow)
    store = svc.store
    folder = store.path(pid)
    warnings: list[str] = []
    result: dict = {"warnings": warnings, "files": 0, "samples": 0}
    values: dict[tuple[str, str], list] = {}
    ins = lambda node: _dedupe(x for e in flow.edges if e.target == node.id for x in values.get((e.source, e.port), []))  # noqa: E731
    total = sum(WEIGHTS[n.type] for n in order)
    done = 0.0
    cache: dict = {}

    def tempo() -> float | None:
        p = store.get(pid)
        if not p.get("tempo"):
            p = {**p, "tempo": svc.detect_tempo(pid)}
        result["bpm"] = p["tempo"]["bpm"]
        return p["tempo"]["bpm"]

    def song_key() -> str | None:
        if "key" not in cache:
            mix = _mix(store.get(pid))
            y, sr = audio.read_mono(folder / mix["file"])
            cache["key"] = analysis.detect_key(y, sr)[0]
            result["key"] = cache["key"]
        return cache["key"]

    def target(p) -> Path:
        out = out_dir / (_folder(p.folder) or "output")
        out.mkdir(parents=True, exist_ok=True)
        return out

    for i, node in enumerate(order):
        check_cancel()
        label = LABELS[node.type]
        w = WEIGHTS[node.type]

        def sub(f: float, m: str, done=done, w=w, i=i, label=label) -> None:
            progress((done + w * max(0.0, min(1.0, f))) / total, f"Node {i + 1}/{len(order)} · {label}: {m}")

        sub(0, "…")
        p = params(node)
        got = ins(node) if node.type != "input" else []
        if node.type != "input" and not got:
            if any(e.target == node.id for e in flow.edges):
                warnings.append(f"{label}: nothing came in (skipped)")
            done += w
            continue

        if node.type == "input":
            values[(node.id, "mix")] = [_mix(store.get(pid))["name"]]

        elif node.type == "separate":
            mix = _mix(store.get(pid))["name"]
            for j, t in enumerate(got):
                each = _part(sub, j, len(got), f"{t} → ")
                made = svc.separate_track(pid, None if t == mix else t, p.model, p.stems, p.rest, False, each,
                                          debleed=p.debleed, overwrite=False)
                for port in outputs(node):
                    if port in made:
                        values.setdefault((node.id, port), []).append(made[port])

        elif node.type == "tool":
            for j, t in enumerate(got):
                added = svc.run_tool(pid, t, p.tool, _part(sub, j, len(got), f"{t} → "))
                for name in added:
                    values.setdefault((node.id, name[len(safe_name(t)) + 1:]), []).append(name)

        elif node.type == "slices":
            items = []
            for t in got:
                track = _track(store.get(pid), t)
                if p.mode == "grid":
                    pr = store.get(pid)
                    if not pr.get("tempo"):
                        tempo()
                        pr = store.get(pid)
                    pts = grid_points(pr["tempo"].get("beats") or [], p.grid_beats, pr["tempo"].get("downbeat") or 0)
                    if len(pts) < 2:
                        warnings.append(f"{label}: no beat grid on {t} (tempo not found)")
                        continue
                    cuts = [{"id": new_id(), "start": round(a, 4), "end": round(min(b, track["duration"]), 4)}
                            for a, b in zip(pts, pts[1:]) if min(b, track["duration"]) - a > 0.01]
                else:
                    y, sr = audio.read_mono(folder / track["file"], sr=None)
                    onsets = slicing.detect_onsets(y, sr, p.sensitivity, p.min_gap, p.gate_db)
                    cuts = slicing.slices_from_onsets(onsets, track["duration"], p.max_len)
                # Kept in the project as this track's own chops, to look at later.
                with store.edit(pid, f"Workflow: slices on {t}") as proj:
                    proj["slices"] = proj["slices"] + [{**c, "track": track["name"]} for c in cuts]
                items.append({"track": track["name"], "slices": cuts})
            values[(node.id, "slices")] = items

        elif node.type == "kit":
            saved = svc.build_kit(pid, got, "drums", p.sensitivity, p.uniqueness, p.max_per_type, p.kit_size,
                                  p.load_pads, replace=False, progress=sub)
            pads = kits.layout_pads(saved)
            labels = {}
            for k, sid in enumerate(pads):
                if sid:
                    labels.setdefault(sid, kits.pad_label(k))
            values[(node.id, "samples")] = [{"ids": [s["id"] for s in saved], "labels": labels}]
            result["kit"] = result.get("kit", 0) + len(saved)
            if not saved:
                warnings.append(f"{label}: no drum hits found in {', '.join(got)}")

        elif node.type == "bits":
            saved = svc.build_kit(pid, got, p.mode, p.sensitivity, p.uniqueness, p.max, None, False,
                                  replace=False, progress=sub)
            values[(node.id, "samples")] = [{"ids": [s["id"] for s in saved], "labels": {}}]
            result["samples"] += len(saved)
            if not saved:
                warnings.append(f"{label}: nothing audible to cut in {', '.join(got)}")

        elif node.type == "save_stems":
            out = target(p)
            bpm = tempo() if p.bpm_names else None
            for t in got:
                track = _track(store.get(pid), t)
                key = song_key() if p.key_names and not analysis.is_drum_track(t) else None
                dest = _unique(out / f"{export_name(track['name'], bpm, track['duration'], key)}.wav")
                if p.normalize:
                    y, sr = audio.read(folder / track["file"])
                    audio.write_wav(dest, audio.normalize(y), sr)
                else:
                    shutil.copyfile(folder / track["file"], dest)
                result["files"] += 1

        elif node.type == "save_slices":
            out = target(p)
            bpm = tempo() if p.bpm_names else None
            for j, item in enumerate(got):
                track = _track(store.get(pid), item["track"])
                try:
                    _, n, _ = slicing.export_slices(folder, [track], item["slices"], p.fade_in_ms, p.fade_out_ms,
                                                    p.normalize, bpm=bpm, keys=p.key_names,
                                                    progress=_part(sub, j, len(got), ""), out_dir=out,
                                                    skip_silent=p.skip_silent)
                    result["files"] += n
                except ValueError as e:
                    warnings.append(f"{label}: {item['track']}: {e}")

        elif node.type == "save_samples":
            ids, labels = [], {}
            for item in got:
                ids += item["ids"]
                labels.update(item["labels"])
            ids = _dedupe(ids)
            by_id = {s["id"]: s for s in store.get(pid)["samples"]}
            samples = [by_id[x] for x in ids if x in by_id]
            if samples:
                bpm = tempo() if p.bpm_names else None
                use = {s["id"]: labels[s["id"]] for s in samples if s["id"] in labels} if p.pad_names else None
                if use is not None and len(use) < len(samples):
                    use = None  # pad names only when every sample has a pad
                result["files"] += len(kits.write_kit(folder, samples, target(p), p.normalize, use, bpm,
                                                      transpose.modes(p.to_c)))
        done += w
        sub(1, "done")
    return result


def run_song(svc, src: Path, flow: Flow, out_dir: Path, progress: Progress,
             check_cancel: Callable[[], None]) -> dict:
    """Batches: one file → a new project → the workflow (same shape as workflows.run_song)."""
    started = time.time()
    project = svc.create_project(src, src.stem)
    pid = project["id"]
    try:
        res = run(svc, pid, flow, out_dir, progress, check_cancel)
    finally:
        if not flow.keep_projects:
            svc.store.delete(pid)  # the files are saved; the project isn't wanted
    out = {"song": src.stem, "seconds": round(time.time() - started, 1), **res}
    if flow.keep_projects:
        out["project"] = pid
    return out


# ---------- helpers ----------

def _mix(p: dict) -> dict:
    return next(t for t in p["tracks"] if t["kind"] == "mix")


def _track(p: dict, name: str) -> dict:
    t = next((t for t in p["tracks"] if t["name"] == name), None)
    if t is None:
        raise ValueError(f"no track '{name}'")
    return t


def _dedupe(items) -> list:
    out, seen = [], set()
    for x in items:
        key = json.dumps(x, sort_keys=True) if isinstance(x, dict) else x
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def _part(progress: Progress, j: int, n: int, prefix: str) -> Progress:
    return lambda f, m: progress((j + f) / n, prefix + m)


def _folder(name: str) -> str:
    from .workflows import folder_name
    return folder_name(name)


def _unique(path: Path) -> Path:
    n = 2
    out = path
    while out.exists():
        out = path.with_name(f"{path.stem}_{n}{path.suffix}")
        n += 1
    return out
