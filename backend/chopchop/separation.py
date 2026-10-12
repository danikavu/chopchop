"""Stem separation using UVR models through the audio-separator package."""

from __future__ import annotations

import contextlib
import functools
import importlib.util
import io
import json
import logging
import os
import time
import platform
import re
import shutil
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from .store import data_dir, env

# SDR figures (dB, higher = cleaner) are audio-separator's published scores.
MODELS = [
    {
        "id": "htdemucs_6s.yaml",
        "label": "Demucs 6-stem",
        "group": "Multi-stem",
        "stems": ["vocals", "drums", "bass", "guitar", "piano", "other"],
        "note": "the only model with guitar and piano",
        "default": True,
    },
    {
        "id": "htdemucs_ft.yaml",
        "label": "Demucs 4-stem fine-tuned",
        "group": "Multi-stem",
        "stems": ["vocals", "drums", "bass", "other"],
        "note": "best drums (10.0 dB) · slow",
        "best_for": ["drums"],
        "bag": 4,  # averages 4 models, each run separately
    },
    {
        "id": "hdemucs_mmi.yaml",
        "label": "Demucs MMI",
        "group": "Multi-stem",
        "stems": ["vocals", "drums", "bass", "other"],
        "note": "best bass (12.2 dB)",
        "best_for": ["bass"],
    },
    {
        "id": "htdemucs.yaml",
        "label": "Demucs 4-stem",
        "group": "Multi-stem",
        "stems": ["vocals", "drums", "bass", "other"],
        "note": "fastest",
    },
    {
        "id": "two_stage_6s",
        "label": "Two-stage: Mel-Band vocals → Demucs 6-stem",
        "group": "Two-stage (cleanest)",
        "stems": ["vocals", "drums", "bass", "guitar", "piano", "other"],
        "stages": ["vocals_mel_band_roformer.ckpt", "htdemucs_6s.yaml"],
        "note": "best vocals, then 6 stems from the instrumental · slow",
    },
    {
        "id": "two_stage_ft",
        "label": "Two-stage: Mel-Band vocals → Demucs fine-tuned",
        "group": "Two-stage (cleanest)",
        "stems": ["vocals", "drums", "bass", "other"],
        "stages": ["vocals_mel_band_roformer.ckpt", "htdemucs_ft.yaml"],
        "note": "best vocals + best drums/bass · slowest",
    },
    {
        "id": "vocals_mel_band_roformer.ckpt",
        "label": "Mel-Band Roformer",
        "group": "Vocals / instrumental",
        "stems": ["vocals", "instrumental"],
        "aliases": {"other": "instrumental"},  # the model calls the backing track "other"
        "note": "best vocals (12.6 dB) · slow",
        "best_for": ["vocals"],
    },
    {
        "id": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        "label": "BS-Roformer",
        "group": "Vocals / instrumental",
        "stems": ["vocals", "instrumental"],
        "note": "vocals 11.8 dB, instrumental 16.5 dB",
    },
    {
        "id": "Kim_Vocal_2.onnx",
        "label": "MDX-Net Kim Vocal 2",
        "group": "Vocals / instrumental",
        "stems": ["vocals", "instrumental"],
        "note": "classic UVR vocal model (10.2 dB) · fast",
        "passes": 2,
    },
    {
        "id": "UVR-MDX-NET-Inst_HQ_4.onnx",
        "label": "MDX-Net Inst HQ 4",
        "group": "Vocals / instrumental",
        "stems": ["vocals", "instrumental"],
        "note": "cleanest MDX instrumental (15.5 dB) · fast",
        "passes": 2,
    },
]

# Tools that run on one existing stem. "outputs" maps the model's stem names to the
# suffix of the new tracks, e.g. drums -> drums_kick, drums_snare, …
TOOLS = {
    "drumsep": {
        "model": "MDX23C-DrumSep-aufr33-jarredou.ckpt",
        "label": "Split drums",
        "description": "kick, snare, toms, hi-hat, ride, crash",
        "for": "drums",
        "outputs": {"kick": "kick", "snare": "snare", "toms": "toms", "hh": "hihat", "ride": "ride", "crash": "crash"},
    },
    "dereverb": {
        "model": "dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt",
        "label": "Remove reverb",
        "description": "dry version + the reverb on its own",
        "for": "vocals",
        "outputs": {"noreverb": "dry", "reverb": "reverb"},
    },
    "karaoke": {
        "model": "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt",
        "label": "Lead / backing vocals",
        "description": "main vocal and the harmonies / ad-libs",
        "for": "vocals",
        "outputs": {"vocals": "lead", "instrumental": "backing"},
    },
}

Progress = Callable[[float, str], None]
log = logging.getLogger(__name__)

# Terminal use (the CLI): keep audio-separator's INFO logs and its console progress bars
# out of the output; ChopChop reports progress itself.
QUIET = False
LAST_RUN: dict = {}  # what the most recent model run used and how long it took (benchmark)


class _NullWriter:
    def write(self, _s: str) -> int:
        return 0

    def flush(self) -> None:
        pass


# One separation at a time: they saturate the GPU/CPU anyway, and progress capture
# temporarily swaps audio-separator's tqdm bars (process-wide).
_lock = threading.Lock()


class SeparationUnavailable(RuntimeError):
    pass


def status() -> dict:
    """Report whether separation can run on this machine."""
    try:
        from audio_separator.separator import Separator  # noqa: F401
    except ImportError:
        return {"available": False, "reason": "audio-separator is not installed (run: python scripts/run.py install)"}
    if shutil.which("ffmpeg") is None:
        how = {"Windows": "winget install Gyan.FFmpeg", "Darwin": "brew install ffmpeg"}.get(
            platform.system(), "install it with your package manager")
        return {"available": False, "reason": f"ffmpeg is not installed ({how})"}
    return {"available": True, "reason": None}


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


@functools.cache
def device_info() -> dict:
    """Which device separation will use, plus a hint when a GPU is present but unused."""
    try:
        import torch
    except ImportError:
        return {"device": "cpu", "name": "CPU", "hint": None}
    if torch.cuda.is_available():
        vram = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
        return {"device": "cuda", "name": f"{torch.cuda.get_device_name(0)} · {vram:.0f} GB", "hint": None}
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return {"device": "mps", "name": "Apple GPU (Metal)", "hint": None}
    if _has("torch_directml"):
        import torch_directml
        if torch_directml.is_available():
            return {"device": "directml", "name": f"{torch_directml.device_name(0)} (DirectML)", "hint": None}
    hint = None
    if shutil.which("nvidia-smi") and torch.version.cuda is None:
        hint = ("An NVIDIA GPU was found, but the installed PyTorch is CPU-only. "
                "Re-run `python scripts/run.py install` to switch to the CUDA build.")
    return {"device": "cpu", "name": "CPU", "hint": hint}


def model_dir() -> Path:
    d = data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def stem_name(filename: str) -> str:
    """'song_(Vocals)_htdemucs_6s.wav' -> 'vocals'. The last "(...)" wins: in a second
    stage the input is already named 'song_(Instrumental)_…', so outputs look like
    'song_(Instrumental)_vocals_mel_band_roformer_(Drums)_htdemucs_6s.wav'."""
    groups = re.findall(r"_\(([^)]+)\)", filename)
    return (groups[-1] if groups else Path(filename).stem).strip().lower().replace(" ", "_")


# Overall progress ranges for each phase (the caller maps 0.9-1.0 to importing stems).
DOWNLOAD = (0.02, 0.15)
SEPARATE = (0.18, 0.88)


class _ProgressCapture:
    """Swap audio-separator's tqdm bars for ones that report to ``emit``.

    Model downloads show bytes; separation shows pass k/N and a percentage. Demucs
    runs one bar per shift per model in its bag, so ``passes`` is set once the model
    is loaded; progress never moves backwards even if the estimate is off.
    """

    def __init__(self, emit: Progress, device: str):
        self._emit = emit
        self.last = 0.0
        self.device = device
        self.passes = 1
        self.pass_no = 0
        self.best = 0.0
        self._saved: list[tuple[object, str, object]] = []

    def emit(self, progress: float, message: str) -> None:
        # Several files download one after another (weights, config), each with its
        # own bar; never let the overall bar move backwards.
        self.last = max(self.last, progress)
        self._emit(self.last, message)

    def _report(self, bar) -> None:
        total = bar.total or 0
        if getattr(bar, "unit", "") == "iB":  # model download
            if total and total < 1_000_000:
                return  # a small config file; not worth a message
            frac = bar.n / total if total else 0.0
            mb = f"{bar.n / 1e6:.0f}/{total / 1e6:.0f} MB" if total else f"{bar.n / 1e6:.0f} MB"
            self.emit(DOWNLOAD[0] + (DOWNLOAD[1] - DOWNLOAD[0]) * frac, f"Downloading model · {mb}")
            return
        frac = min(1.0, bar.n / total) if total else 0.0
        passes = max(self.passes, self.pass_no)
        overall = ((self.pass_no - 1) + frac) / passes
        self.best = max(self.best, overall)
        p = SEPARATE[0] + (SEPARATE[1] - SEPARATE[0]) * self.best
        step = f" · pass {self.pass_no}/{passes}" if passes > 1 else ""
        self.emit(p, f"Separating on {self.device}{step} · {frac * 100:.0f}%")

    def __enter__(self):
        import tqdm as tqdm_module

        from audio_separator.separator import separator as sep_module
        from audio_separator.separator.architectures import mdx_separator, mdxc_separator, vr_separator

        cap = self
        base = tqdm_module.tqdm

        class Bar(base):  # type: ignore[misc, valid-type]
            def __init__(self, *args, **kwargs):
                if QUIET:
                    kwargs["file"] = _NullWriter()
                super().__init__(*args, **kwargs)
                if getattr(self, "unit", "") != "iB":
                    cap.pass_no += 1
                cap._report(self)

            def update(self, n=1):
                out = super().update(n)
                cap._report(self)
                return out

            def close(self):
                if not getattr(self, "_chopchop_closed", False):
                    self._chopchop_closed = True
                    cap._report(self)
                super().close()

        # Modules that did `from tqdm import tqdm`, plus `tqdm.tqdm` for Demucs'
        # `import tqdm; tqdm.tqdm(...)`.
        for mod in (sep_module, mdx_separator, mdxc_separator, vr_separator):
            if hasattr(mod, "tqdm"):
                self._saved.append((mod, "tqdm", mod.tqdm))
                mod.tqdm = Bar
        self._saved.append((tqdm_module, "tqdm", base))
        tqdm_module.tqdm = Bar
        return self

    def __exit__(self, *exc):
        for mod, name, value in reversed(self._saved):
            setattr(mod, name, value)
        self._saved.clear()
        return False


# audio-separator's defaults for these architectures; only batch_size is changed.
MDX_DEFAULTS = {"hop_length": 1024, "segment_size": 256, "overlap": 0.25, "batch_size": 1, "enable_denoise": False}
MDXC_DEFAULTS = {"segment_size": 256, "override_model_segment_size": False, "batch_size": 1, "overlap": None,
                 "pitch_shift": 0}


def arch_of(model: str) -> str:
    """'demucs' (.yaml), 'mdx' (.onnx: MDX-Net) or 'mdxc' (.ckpt: Roformer, MDX23C)."""
    return "demucs" if model.endswith(".yaml") else "mdx" if model.endswith(".onnx") else "mdxc"


def gpu_settings() -> dict:
    """Saved per-architecture GPU settings ({"mdx": {"batch": 1, "fp16": false}, …}),
    written by `chopchop benchmark` from measurements on this machine."""
    from . import settings

    return settings.get("gpu", {})


def save_gpu_settings(gpu: dict) -> None:
    from . import settings

    settings.put("gpu", gpu)


def auto_batch(arch: str) -> int:
    """A batch size the free VRAM should hold (a *candidate* for `chopchop benchmark`,
    never applied untested: on Windows an oversized batch doesn't fail, the driver
    spills into system RAM and everything crawls)."""
    import torch

    free_gb = torch.cuda.mem_get_info()[0] / 1024 ** 3
    if arch == "mdx":
        return max(1, min(16, int((free_gb - 1.0) / 0.5)))
    return max(1, min(8, int((free_gb - 2.0) / 1.0)))


def gpu_tuning(model: str = "", force_cpu: bool = False, batch: int | None = None,
               fp16: bool | None = None) -> dict:
    """Batch size and fp16 for one run.

    Defaults are audio-separator's own (batch 1, fp32): measured faster than bigger
    guesses on at least one 6 GB Windows card. They only change through `chopchop
    benchmark` (measured on this machine, saved in settings.json) or the overrides
    CHOPCHOP_GPU_BATCH=N / CHOPCHOP_FP16=0|1. Demucs gets nothing: it has no batch
    setting and doesn't use autocast. batch / fp16: forced (retries after a failure).
    """
    arch = arch_of(model) if model else "mdxc"
    plain = {"arch": arch, "batch": 1, "fp16": False, "label": ""}
    if force_cpu or arch == "demucs":
        return plain
    try:
        import torch
    except ImportError:
        return plain
    if not torch.cuda.is_available():
        return plain  # Apple GPUs: batching measured no faster; CPU: n/a
    saved = gpu_settings().get(arch, {})
    env_batch, env_fp16 = env("GPU_BATCH"), env("FP16")
    if batch is None:
        batch = int(env_batch) if env_batch and env_batch.isdigit() else int(saved.get("batch", 1))
    if fp16 is None:
        fp16 = env_fp16 == "1" if env_fp16 in ("0", "1") else bool(saved.get("fp16", False))
    batch = max(1, batch)
    label = " · ".join(x for x in (f"batch {batch}" if batch > 1 else "", "fp16" if fp16 else "") if x)
    return {"arch": arch, "batch": batch, "fp16": fp16, "label": label}


def _log_run(entry: dict) -> None:
    """One JSON line per separation in <data>/perf.log, to compare speeds over time."""
    try:
        with (data_dir() / "perf.log").open("a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def _is_oom(msg: str) -> bool:
    return any(k in msg for k in ("out of memory", "failed to allocate", "cudnn_status_alloc_failed",
                                  "cublas_status_alloc_failed"))


def _free_gpu_memory() -> None:
    try:
        import gc

        import torch

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def _mps_long_conv():
    from .mps_compat import mps_long_conv  # imports torch; only needed on Apple GPUs

    return mps_long_conv()


def _device_label(sep) -> str:
    dev = sep.torch_device
    kind = getattr(dev, "type", str(dev))
    if kind == "cuda":
        import torch
        return f"{torch.cuda.get_device_name(dev.index or 0)} (CUDA)"
    if kind == "mps":
        return "Apple GPU"
    if kind == "cpu":
        return "CPU"
    return "GPU (DirectML)"


def _demucs_passes(sep, model: str) -> int:
    """Progress bars Demucs will run: shifts x models in the bag."""
    bag = next((m.get("bag", 1) for m in MODELS if m["id"] == model), 1)
    shifts = int(getattr(sep.model_instance, "shifts", 0) or 0) or 1
    return bag * shifts


def _info(model: str) -> dict:
    """Catalogue entry for a model id (separation models and stem-tool models)."""
    for m in MODELS:
        if m["id"] == model:
            return m
    for t in TOOLS.values():
        if t["model"] == model:
            return t
    return {}


def _run_model(src: Path, out_dir: Path, model: str, emit: Progress) -> dict[str, Path]:
    """Run one audio-separator model on src; returns {stem name: file}. Caller holds _lock."""
    from audio_separator.separator import Separator

    out_dir.mkdir(parents=True, exist_ok=True)
    is_demucs = model.endswith(".yaml")
    info = _info(model)

    def run(force_cpu: bool, batch: int | None, fp16: bool | None = None) -> list[str]:
        emit(0.02, "Preparing model (downloads on first use)…")
        tuning = gpu_tuning(model, force_cpu, batch, fp16)
        extra = {}
        if tuning["arch"] != "demucs":  # Demucs: exactly audio-separator's defaults
            extra["use_autocast"] = tuning["fp16"]
            if tuning["arch"] == "mdx":
                extra["mdx_params"] = {**MDX_DEFAULTS, "batch_size": tuning["batch"]}
            else:
                extra["mdxc_params"] = {**MDXC_DEFAULTS, "batch_size": tuning["batch"]}
        # onnxruntime prints "Skip loading CUDA and cuDNN DLLs since torch is imported."
        # on every start on Windows (harmless); pass through anything else it prints.
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            sep = Separator(
                output_dir=str(out_dir),
                model_file_dir=str(model_dir()),
                output_format="WAV",
                use_soundfile=True,  # write WAVs directly instead of through pydub/ffmpeg
                use_directml=_has("torch_directml"),  # AMD/Intel GPUs on Windows, if installed
                log_level=logging.ERROR if QUIET else logging.INFO,
                **extra,
            )
        for line in printed.getvalue().splitlines():
            if line.strip() and not line.startswith("Skip loading CUDA and cuDNN DLLs"):
                print(line)
        run_info.update(device=_device_label(sep) if not force_cpu else "CPU", batch=tuning["batch"],
                        fp16=tuning["fp16"])
        if force_cpu:
            sep.torch_device = sep.torch_device_cpu
        device = _device_label(sep)
        if tuning["label"]:
            device += f" · {tuning['label']}"
        on_mps = getattr(sep.torch_device, "type", "") == "mps"
        # Demucs' long convolutions exceed an MPS size limit; mps_long_conv computes
        # them in chunks (identical results, ~6x faster than CPU on an M-series Mac).
        gpu_fix = _mps_long_conv() if on_mps else contextlib.nullcontext()
        with gpu_fix, _ProgressCapture(emit, device) as cap:
            sep.load_model(model_filename=model)
            cap.emit(SEPARATE[0], f"Model loaded · separating on {device}…")
            cap.passes = _demucs_passes(sep, model) if is_demucs else info.get("passes", 1)
            files = sep.separate(str(src))
        emit(SEPARATE[1], "Writing stems…")
        return files

    batch = None  # None = saved / default setting
    fp16: bool | None = None
    run_info: dict = {"model": model, "arch": arch_of(model), "fallback": None}
    started = time.time()
    while True:
        try:
            files = run(force_cpu=False, batch=batch, fp16=fp16)
            break
        except Exception as e:  # noqa: BLE001 - classified below
            msg = str(e).lower()
            if "finite" in msg and fp16 is not False and gpu_tuning(model, batch=batch, fp16=fp16)["fp16"]:
                # Half precision overflowed (seen with MDX23C DrumSep on a 6 GB RTX card):
                # the model output inf/NaN. Same GPU, same batch, full precision.
                log.warning("fp16 gave invalid audio with %s; retrying in fp32", model)
                fp16 = False
                run_info["fp16_failed"] = True
                emit(0.02, "Half precision gave invalid audio, retrying in full precision…")
                continue
            oom = _is_oom(msg)
            if oom:
                _free_gpu_memory()
                current = batch or gpu_tuning(model)["batch"]
                if current > 1:
                    # Too ambitious for this card / this model: same GPU, smaller batch.
                    batch = max(1, current // 2)
                    emit(0.02, f"GPU memory full, retrying with batch {batch}…")
                    continue
            gpu_error = oom or (isinstance(e, (RuntimeError, NotImplementedError))
                                and any(k in msg for k in ("mps", "cuda", "directml")))
            if not gpu_error:
                raise
            # GPU trouble (unsupported op, out of memory at batch 1): finish on CPU rather
            # than fail, but loudly: a silent CPU run looks like "the GPU got slow".
            log.error("GPU separation with %s failed, falling back to CPU", model, exc_info=True)
            run_info["fallback"] = str(e)[:300]
            emit(0.02, f"GPU failed ({str(e)[:80]}), finishing on CPU…")
            files = run(force_cpu=True, batch=1)
            break
    try:
        import soundfile as sf

        run_info["audio_seconds"] = round(sf.info(str(src)).duration, 1)
    except Exception:  # noqa: BLE001 - informational only
        pass
    run_info.update(seconds=round(time.time() - started, 1), at=time.strftime("%Y-%m-%d %H:%M:%S"))
    _log_run(run_info)
    global LAST_RUN
    LAST_RUN = run_info

    aliases = info.get("aliases", {})
    stems: dict[str, Path] = {}
    for f in files:
        p = Path(f)
        if not p.is_absolute():
            p = out_dir / p
        name = stem_name(p.name)
        stems[aliases.get(name, name)] = p
    if not stems:
        raise RuntimeError("separation produced no stems")
    return stems


def _stage_emit(emit: Progress, i: int, n: int, label: str) -> Progress:
    """Map a single model's progress (0.02-0.88) into stage i of n, prefixing messages."""
    lo, hi = 0.02, SEPARATE[1]

    def stage(p: float, msg: str) -> None:
        frac = min(1.0, max(0.0, (p - lo) / (hi - lo)))
        emit(lo + (i + frac) / n * (hi - lo), f"Stage {i + 1}/{n} · {label}: {msg}")

    return stage


def separate(src: Path, out_dir: Path, model: str, on_progress: Progress | None = None) -> dict[str, Path]:
    st = status()
    if not st["available"]:
        raise SeparationUnavailable(st["reason"])
    entry = _info(model)
    if not entry or entry not in MODELS:
        raise ValueError(f"unknown model: {model}")
    emit = on_progress or (lambda _p, _m: None)

    with _lock:
        stages = entry.get("stages")
        if not stages:
            return _run_model(src, out_dir, model, emit)
        # Two-stage: the vocal model first, then the multi-stem model on the
        # instrumental, where no vocals are left to confuse it.
        first, second = stages
        vox = _run_model(src, out_dir / "stage1", first,
                         _stage_emit(emit, 0, 2, f"vocals ({_info(first)['label']})"))
        rest = _run_model(vox["instrumental"], out_dir / "stage2", second,
                          _stage_emit(emit, 1, 2, f"instrumental ({_info(second)['label']})"))
        # The second model's own "vocals" is only leftover bleed; the first model's wins.
        return {"vocals": vox["vocals"], **{k: v for k, v in rest.items() if k != "vocals"}}


def run_tool(src: Path, out_dir: Path, tool: str, on_progress: Progress | None = None) -> dict[str, Path]:
    """Run a stem tool (drum split, de-reverb, lead/backing) on one track; returns
    {suffix: file}, e.g. {"kick": …, "snare": …} or {"dry": …, "reverb": …}."""
    st = status()
    if not st["available"]:
        raise SeparationUnavailable(st["reason"])
    if tool not in TOOLS:
        raise ValueError(f"unknown tool: {tool}")
    t = TOOLS[tool]
    with _lock:
        stems = _run_model(src, out_dir, t["model"], on_progress or (lambda _p, _m: None))
    names = t["outputs"]
    return {names[k]: stems[k] for k in names if k in stems}


# Stems that the separation models leave drum bleed in. Bass is left alone on purpose:
# a sweep there would pull 808s and kick-like bass notes out of it.
DEBLEED_STEMS = ("other", "guitar", "piano")
DEBLEED_MODEL = "htdemucs.yaml"  # the fast one: measured as good as htdemucs_ft for this


def debleed(stems: dict[str, Path], out_dir: Path, on_progress: Progress | None = None) -> dict[str, Path]:
    """Move drum bleed out of "other" (and guitar / piano) into "drums".

    The models leave faint drums in "other"; its sharp attacks then win onset detection,
    so sound bits cut from "other" come out as drum hits. A drum model run *on the
    stem itself* finds that bleed (measured on a test song: -35.6 dB -> -59.6 dB, with
    "other" itself unharmed). It's subtracted from the stem and added to "drums", so
    nothing is lost and the stems still sum to the mix. Only when there's a drums stem
    (a vocals/instrumental split's "instrumental" is meant to hold the drums).
    """
    import soundfile as sf

    emit = on_progress or (lambda _p, _m: None)
    targets = [n for n in DEBLEED_STEMS if n in stems]
    if "drums" not in stems or not targets:
        return stems
    drums, sr = sf.read(stems["drums"], dtype="float32", always_2d=True)
    out = dict(stems)
    with _lock:
        for i, name in enumerate(targets):
            emit(SEPARATE[1], f"Removing drum bleed from {name} ({i + 1}/{len(targets)})…")
            found = _run_model(stems[name], out_dir / f"debleed_{name}", DEBLEED_MODEL, lambda _p, _m: None)
            y, _ = sf.read(stems[name], dtype="float32", always_2d=True)
            bleed, _ = sf.read(found["drums"], dtype="float32", always_2d=True)
            n = min(len(y), len(bleed), len(drums))
            clean = out_dir / f"{name}_debleed.wav"
            sf.write(clean, y[:n] - bleed[:n], sr, subtype="FLOAT")
            drums = drums[:n] + bleed[:n]
            out[name] = clean
    fixed = out_dir / "drums_debleed.wav"
    sf.write(fixed, drums, sr, subtype="FLOAT")
    out["drums"] = fixed
    return out


def rest_name(keep: list[str]) -> str:
    """Name for the track the unselected stems are mixed into."""
    if keep == ["vocals"]:
        return "instrumental"
    if len(keep) == 1:
        return f"no_{keep[0]}"
    return "rest"


def combine(stems: dict[str, Path], keep: list[str] | None, rest: bool, out_dir: Path) -> dict[str, Path]:
    """Keep the chosen stems; optionally mix the others into one extra track.

    The remainder is the sum of the separated stems that weren't chosen (not the mix
    minus the chosen ones), so it carries no leftovers of the kept parts' artifacts.
    """
    if not keep or set(keep) >= set(stems):
        return stems
    unknown = set(keep) - set(stems)
    if unknown:
        raise ValueError(f"the model didn't produce: {', '.join(sorted(unknown))}")
    chosen = {name: path for name, path in stems.items() if name in keep}
    others = [path for name, path in stems.items() if name not in keep]
    if rest and others:
        from . import audio  # local import: keeps this module light for status checks

        parts = [audio.read(p) for p in others]
        sr = parts[0][1]
        channels = max(y.shape[0] for y, _ in parts)
        length = max(y.shape[1] for y, _ in parts)
        total = np.zeros((channels, length), dtype=np.float32)
        for y, _ in parts:
            total[:, : y.shape[1]] += y if y.shape[0] == channels else np.repeat(y, channels, axis=0)
        path = out_dir / f"{rest_name(keep)}.wav"
        audio.write_wav(path, total, sr)
        chosen[rest_name(keep)] = path
    return chosen
