"""Model registry: the built-in models, models the user added, and everything known
about each one (files on disk, quality scores, speed on this machine, settings).

A model is added either

  from the catalog   any of the UVR models audio-separator can download (VR, MDX,
                     MDX23C / Roformer, Demucs); it only needs to know what the model
                     makes and how to label it, or
  from your files    a model audio-separator doesn't list: a Roformer / MDX23C
                     checkpoint + its YAML config, or an MDX-Net .onnx with its
                     parameters. The files are copied into the models folder and
                     audio-separator is pointed at them when the model runs.

Saved in settings.json under "models": {"added": [...], "settings": {id: {...}},
"hidden": [id, ...]}.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from . import settings
from .store import data_dir

log = logging.getLogger(__name__)

ARCHS = {"mdx": "MDX-Net", "mdxc": "MDX23C / Roformer", "vr": "VR", "demucs": "Demucs"}
ARCH_TYPE = {"mdx": "MDX", "mdxc": "MDXC", "vr": "VR", "demucs": "Demucs"}  # audio-separator's names

# Every per-architecture option audio-separator takes, with its defaults. Batch size is
# not here: GPU tuning (`chopchop benchmark`) owns it.
OPTIONS: dict[str, dict] = {
    "mdx": {"segment_size": 256, "overlap": 0.25, "hop_length": 1024, "enable_denoise": False},
    "mdxc": {"segment_size": 256, "override_model_segment_size": False, "overlap": None, "pitch_shift": 0},
    "vr": {"window_size": 512, "aggression": 5, "enable_tta": False, "enable_post_process": False,
           "post_process_threshold": 0.2, "high_end_process": False},
    "demucs": {"segment_size": "Default", "shifts": 2, "overlap": 0.25, "segments_enabled": True},
}
HELP = {
    "segment_size": "audio per model pass; bigger = more context, more memory",
    "overlap": "overlap between passes; more = smoother joins, slower (MDXC: empty = the model's own)",
    "hop_length": "STFT hop the model was trained with (leave unless you know)",
    "enable_denoise": "run the model on the inverted audio too and average (cleaner, 2× slower)",
    "override_model_segment_size": "use the segment size above instead of the model config's",
    "pitch_shift": "semitones to shift before separating (and back after)",
    "window_size": "320 = best quality, 512 = balanced, 1024 = fastest",
    "aggression": "how hard to pull the main stem out (-100…100; 5 is UVR's default)",
    "enable_tta": "test-time augmentation: better, slower",
    "enable_post_process": "remove leftover instrument bits from the vocal stem",
    "post_process_threshold": "how strict that clean-up is (0.1–0.3)",
    "high_end_process": "mirror the missing high frequencies",
    "shifts": "random shifts averaged: more = cleaner, linearly slower",
    "segments_enabled": "split the song into segments (less memory)",
}
# MDX-Net parameters a model of your own needs (UVR knows them by file hash for its own).
MDX_MODEL_DATA = {"primary_stem": "Vocals", "compensate": 1.035, "mdx_dim_f_set": 3072, "mdx_dim_t_set": 8,
                  "mdx_n_fft_scale_set": 6144}


def model_dir() -> Path:
    d = data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------- saved state ----------

def _state() -> dict:
    s = settings.get("models", {})
    return {"added": s.get("added", []), "settings": s.get("settings", {}), "hidden": s.get("hidden", [])}


def _save(state: dict) -> None:
    settings.put("models", state)


def added() -> list[dict]:
    return _state()["added"]


def hidden() -> set[str]:
    return set(_state()["hidden"])


def user_settings(model: str) -> dict:
    return _state()["settings"].get(model, {})


def options_for(model: str, arch: str) -> dict:
    """The options a run uses: audio-separator's defaults with the user's changes."""
    return {**OPTIONS.get(arch, {}), **user_settings(model)}


def set_settings(model: str, arch: str, values: dict) -> dict:
    """Validate and save; values equal to the default are dropped (so defaults can move)."""
    allowed = OPTIONS.get(arch)
    if allowed is None:
        raise ValueError(f"{model} has no settings")
    clean = {}
    for k, v in values.items():
        if k not in allowed:
            raise ValueError(f"unknown setting for {ARCHS[arch]}: {k}")
        default = allowed[k]
        if isinstance(default, bool):
            if not isinstance(v, bool):
                raise ValueError(f"{k} must be on or off")
        elif k == "overlap" and arch == "mdxc":
            if v is not None and not (isinstance(v, (int, float)) and 2 <= v <= 50):
                raise ValueError("overlap must be 2–50, or empty for the model's own")
        elif k == "segment_size" and arch == "demucs":
            if v != "Default" and not (isinstance(v, int) and 1 <= v <= 100):
                raise ValueError("segment size must be Default or 1–100")
        elif isinstance(default, (int, float)):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"{k} must be a number")
            if k in ("overlap", "post_process_threshold") and not 0 <= v < 1:
                raise ValueError(f"{k} must be between 0 and 1")
            if k == "aggression" and not -100 <= v <= 100:
                raise ValueError("aggression must be -100…100")
            if k in ("segment_size", "hop_length", "window_size", "shifts") and v < 1:
                raise ValueError(f"{k} must be at least 1")
            v = int(v) if isinstance(default, int) and float(v).is_integer() else v
        if v != default:
            clean[k] = v
    state = _state()
    if clean:
        state["settings"][model] = clean
    else:
        state["settings"].pop(model, None)
    _save(state)
    return clean


def set_hidden(model: str, hide: bool) -> None:
    state = _state()
    h = set(state["hidden"])
    (h.add if hide else h.discard)(model)
    state["hidden"] = sorted(h)
    _save(state)


# ---------- catalog (what audio-separator can download) ----------

_catalog: list[dict] | None = None


def catalog(refresh: bool = False) -> list[dict]:
    """Every model audio-separator knows: {arch, name, filename, stems, scores, files}.
    Fetched once (it reads a small index from GitHub), then cached on disk so it also
    works offline."""
    global _catalog
    if _catalog is not None and not refresh:
        return _catalog
    cache = model_dir() / "chopchop-catalog.json"
    try:
        from audio_separator.separator import Separator

        sep = Separator(model_file_dir=str(model_dir()), info_only=True, log_level=logging.ERROR)
        grouped = sep.list_supported_model_files()
        arch_of = {"MDX": "mdx", "MDXC": "mdxc", "VR": "vr", "Demucs": "demucs"}
        items = []
        for kind, models in grouped.items():
            for name, info in models.items():
                files = [f.split("/")[-1] for f in info.get("download_files", [])] or [info["filename"]]
                items.append({
                    "arch": arch_of.get(kind, kind.lower()), "name": name, "filename": info["filename"],
                    "stems": [s.lower() for s in info.get("stems", [])], "target_stem": info.get("target_stem"),
                    "scores": info.get("scores", {}), "files": files,
                })
        cache.write_text(json.dumps(items))
        _catalog = items
    except Exception as e:  # noqa: BLE001 - offline, or audio-separator missing
        log.info("model catalog unavailable (%s); using the cached copy", e)
        try:
            _catalog = json.loads(cache.read_text())
        except (OSError, ValueError):
            _catalog = []
    return _catalog


def in_catalog(filename: str) -> dict | None:
    return next((c for c in catalog() if c["filename"] == filename), None)


# ---------- adding / removing ----------

def _taken(model_id: str) -> bool:
    from . import separation

    return any(m["id"] == model_id for m in separation.MODELS) or any(m["id"] == model_id for m in added()) \
        or any(t["model"] == model_id for t in separation.TOOLS.values())


def _entry_fields(req: dict, stems: list[str]) -> dict:
    label = (req.get("label") or "").strip()
    if not label:
        raise ValueError("give the model a name")
    stems = [s.strip().lower().replace(" ", "_") for s in (req.get("stems") or stems) if s.strip()]
    if not stems:
        raise ValueError("list the stems the model makes")
    aliases = {k.strip().lower(): v.strip().lower() for k, v in (req.get("aliases") or {}).items() if k.strip() and v.strip()}
    return {
        "label": label, "group": (req.get("group") or "Added").strip() or "Added", "stems": stems,
        "note": (req.get("note") or "").strip(), "best_for": [s for s in req.get("best_for") or [] if s in stems],
        "aliases": aliases,
    }


def add_from_catalog(req: dict) -> dict:
    """Register a model audio-separator can download (it downloads on first use)."""
    filename = req.get("filename") or ""
    cat = in_catalog(filename)
    if cat is None:
        raise ValueError(f"{filename!r} isn't in audio-separator's catalog")
    if _taken(filename):
        raise ValueError(f"{filename} is already in your models")
    entry = {"id": filename, **_entry_fields(req, cat["stems"]), "source": "catalog", "arch": cat["arch"],
             "catalog_name": cat["name"], "added_at": time.strftime("%Y-%m-%d %H:%M")}
    state = _state()
    state["added"].append(entry)
    _save(state)
    return entry


def inspect_file(path: str) -> dict:
    """What a local model / config file looks like, to pre-fill the add form."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"no file at {p}")
    out: dict = {"name": p.name, "size": p.stat().st_size}
    if p.suffix.lower() in (".yaml", ".yml"):
        import yaml

        try:
            cfg = yaml.safe_load(p.read_text()) or {}
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"can't read that YAML: {e}") from e
        training = cfg.get("training") or {}
        out["stems"] = [str(s).lower() for s in training.get("instruments") or []]
        out["target"] = (training.get("target_instrument") or None) and str(training["target_instrument"]).lower()
        out["kind"] = "config"
    else:
        out["kind"] = {".onnx": "mdx", ".ckpt": "mdxc", ".pth": "vr", ".th": "demucs"}.get(p.suffix.lower(), "unknown")
    return out


def add_from_files(req: dict) -> dict:
    """Register a model from files on this computer (copied into the models folder)."""
    arch = req.get("arch")
    if arch not in ("mdxc", "mdx"):
        raise ValueError("own model files: MDX23C / Roformer (.ckpt + .yaml) or MDX-Net (.onnx)")
    src = Path(req.get("model_path") or "").expanduser()
    if not src.is_file():
        raise ValueError(f"no model file at {src}")
    want = {"mdxc": (".ckpt", ".pth", ".pt", ".safetensors"), "mdx": (".onnx",)}[arch]
    if src.suffix.lower() not in want:
        raise ValueError(f"a {ARCHS[arch]} model file ends in {' / '.join(want)}")
    if _taken(src.name) or (model_dir() / src.name).exists() and in_catalog(src.name):
        raise ValueError(f"a model called {src.name} is already there")
    files = [src.name]
    extra: dict = {}
    stems: list[str] = []
    if arch == "mdxc":
        cfg = Path(req.get("config_path") or "").expanduser()
        if not cfg.is_file() or cfg.suffix.lower() not in (".yaml", ".yml"):
            raise ValueError("an MDX23C / Roformer model needs its .yaml config")
        info = inspect_file(str(cfg))
        stems = info.get("stems") or []
        extra["config"] = cfg.name
        files.append(cfg.name)
    else:
        data = {**MDX_MODEL_DATA, **(req.get("mdx_params") or {})}
        for k in ("compensate", "mdx_dim_f_set", "mdx_dim_t_set", "mdx_n_fft_scale_set"):
            if not isinstance(data[k], (int, float)) or isinstance(data[k], bool) or data[k] <= 0:
                raise ValueError(f"{k} must be a positive number")
        data["primary_stem"] = str(data["primary_stem"]).strip().title() or "Vocals"
        extra["mdx_params"] = data
        other = "Instrumental" if data["primary_stem"] == "Vocals" else f"No {data['primary_stem']}"
        stems = [data["primary_stem"].lower(), other.lower().replace(" ", "_")]
    entry = {"id": src.name, **_entry_fields(req, stems), "source": "files", "arch": arch, "files": files,
             **extra, "added_at": time.strftime("%Y-%m-%d %H:%M")}
    dest = model_dir()
    shutil.copyfile(src, dest / src.name)
    if arch == "mdxc":
        shutil.copyfile(Path(req["config_path"]).expanduser(), dest / extra["config"])
    state = _state()
    state["added"].append(entry)
    _save(state)
    return entry


def update(model: str, req: dict) -> dict:
    """Change what an added model makes / how it's labelled."""
    state = _state()
    entry = next((m for m in state["added"] if m["id"] == model), None)
    if entry is None:
        raise ValueError("only models you added can be edited (built-in ones keep their setup)")
    entry.update(_entry_fields({**entry, **req}, entry["stems"]))
    _save(state)
    return entry


def remove(model: str, delete_files: bool = False) -> None:
    state = _state()
    if not any(m["id"] == model for m in state["added"]):
        raise ValueError("only models you added can be removed")
    if delete_files:
        delete_downloads(model)
    state = _state()
    state["added"] = [m for m in state["added"] if m["id"] != model]
    state["settings"].pop(model, None)
    state["hidden"] = [h for h in state["hidden"] if h != model]
    _save(state)


# ---------- files on disk ----------

def files_of(entry: dict) -> list[str]:
    """Files a model needs in the models folder."""
    from . import separation

    if entry.get("stages"):
        return [f for s in entry["stages"] for f in files_of(separation._info(s) or {"id": s})]
    if entry.get("files"):
        return list(entry["files"])
    cat = in_catalog(entry.get("id") or entry.get("model", ""))
    return cat["files"] if cat else [entry.get("id") or entry.get("model", "")]


def disk(entry: dict) -> dict:
    out = []
    for name in files_of(entry):
        p = model_dir() / name
        out.append({"name": name, "present": p.is_file(), "size": p.stat().st_size if p.is_file() else None})
    return {"files": out, "downloaded": bool(out) and all(f["present"] for f in out),
            "size": sum(f["size"] or 0 for f in out)}


def delete_downloads(model: str) -> list[str]:
    """Remove a model's downloaded files, except ones another model also uses."""
    from . import separation

    entry = separation._info(model)
    if not entry:
        raise ValueError(f"unknown model: {model}")
    mine = set(files_of(entry))
    others = set()
    for m in all_entries():
        if (m.get("id") or m.get("model")) != model:
            others |= set(files_of(m))
    removed = []
    for name in sorted(mine - others):
        p = model_dir() / name
        if p.is_file():
            p.unlink()
            removed.append(name)
    return removed


def download(model: str, progress=lambda f, m: None) -> None:
    """Fetch a catalog model's files now (otherwise they download on first use)."""
    from . import separation

    entry = separation._info(model)
    if not entry:
        raise ValueError(f"unknown model: {model}")
    ids = entry.get("stages") or [entry.get("id") or entry.get("model")]
    from audio_separator.separator import Separator

    sep = Separator(model_file_dir=str(model_dir()), info_only=True, log_level=logging.ERROR)
    for i, mid in enumerate(ids):
        e = separation._info(mid) or {}
        if e.get("source") == "files":
            continue  # already here
        progress(i / len(ids), f"Downloading {e.get('label', mid)}…")
        with separation._ProgressCapture(lambda f, m, i=i: progress((i + f) / len(ids), m), "download"):
            sep.download_model_files(mid)
    progress(1, "Downloaded")


# ---------- what the app shows ----------

def all_entries() -> list[dict]:
    """Separation models (built-in + added) and the stem tools' models."""
    from . import separation

    tools = [{"id": t["model"], "label": t["label"], "group": "Stem tools", "tool": tid, "stems": list(t["outputs"].values()),
              "note": t["description"], "builtin": True} for tid, t in separation.TOOLS.items()]
    return [*({**m, "builtin": True} for m in separation.MODELS), *added(), *tools]


def perf(model: str, limit: int = 200) -> dict | None:
    """Speed on this machine from perf.log: runs, realtime factor, last device."""
    try:
        lines = (data_dir() / "perf.log").read_text().splitlines()[-2000:]
    except OSError:
        return None
    runs = []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("model") == model and r.get("seconds"):
            runs.append(r)
    if not runs:
        return None
    runs = runs[-limit:]
    speeds = [r["audio_seconds"] / r["seconds"] for r in runs if r.get("audio_seconds")]
    last = runs[-1]
    return {"runs": len(runs), "realtime": round(sum(speeds) / len(speeds), 1) if speeds else None,
            "last_at": last.get("at"), "last_device": last.get("device"), "last_seconds": last.get("seconds"),
            "fallbacks": sum(1 for r in runs if r.get("fallback"))}


def describe(entry: dict) -> dict:
    """Everything the Models page shows for one model."""
    from . import separation

    mid = entry.get("id")
    arch = None if entry.get("stages") else entry.get("arch") or separation.arch_of(mid)
    cat = in_catalog(mid) if not entry.get("stages") else None
    return {
        **entry,
        "arch": arch, "arch_label": ARCHS.get(arch, "two models in a row") if arch else "two models in a row",
        "source": entry.get("source") or ("built-in" if entry.get("builtin") else "catalog"),
        "catalog_name": entry.get("catalog_name") or (cat or {}).get("name"),
        "scores": (cat or {}).get("scores") or {},
        "hidden": mid in hidden(),
        "settings": options_for(mid, arch) if arch else None,
        "changed": user_settings(mid),
        "defaults": OPTIONS.get(arch) if arch else None,
        "gpu": separation.gpu_tuning(mid) if arch and arch != "demucs" else None,
        "perf": perf(mid),
        **disk(entry),
    }
