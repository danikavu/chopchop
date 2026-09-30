"""Stem separation using UVR models through the audio-separator package."""

from __future__ import annotations

import contextlib
import functools
import importlib.util
import platform
import re
import shutil
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from .store import data_dir

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

Progress = Callable[[float, str], None]

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
        return {"device": "cuda", "name": torch.cuda.get_device_name(0), "hint": None}
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
    """'song_(Vocals)_htdemucs_6s.wav' -> 'vocals'."""
    m = re.search(r"_\(([^)]+)\)", filename)
    return (m.group(1) if m else Path(filename).stem).strip().lower().replace(" ", "_")


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
                super().__init__(*args, **kwargs)
                if getattr(self, "unit", "") != "iB":
                    cap.pass_no += 1
                cap._report(self)

            def update(self, n=1):
                out = super().update(n)
                cap._report(self)
                return out

            def close(self):
                if not getattr(self, "_chopper_closed", False):
                    self._chopper_closed = True
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


def separate(src: Path, out_dir: Path, model: str, on_progress: Progress | None = None) -> dict[str, Path]:
    st = status()
    if not st["available"]:
        raise SeparationUnavailable(st["reason"])
    if model not in {m["id"] for m in MODELS}:
        raise ValueError(f"unknown model: {model}")
    emit = on_progress or (lambda _p, _m: None)

    from audio_separator.separator import Separator

    out_dir.mkdir(parents=True, exist_ok=True)
    is_demucs = model.endswith(".yaml")

    def run(force_cpu: bool) -> list[str]:
        emit(0.02, "Preparing model (downloads on first use)…")
        sep = Separator(
            output_dir=str(out_dir),
            model_file_dir=str(model_dir()),
            output_format="WAV",
            use_soundfile=True,  # write WAVs directly instead of through pydub/ffmpeg
            use_directml=_has("torch_directml"),  # AMD/Intel GPUs on Windows, if installed
        )
        if force_cpu:
            sep.torch_device = sep.torch_device_cpu
        device = _device_label(sep)
        on_mps = getattr(sep.torch_device, "type", "") == "mps"
        # Demucs' long convolutions exceed an MPS size limit; mps_long_conv computes
        # them in chunks (identical results, ~6x faster than CPU on an M-series Mac).
        gpu_fix = _mps_long_conv() if on_mps else contextlib.nullcontext()
        with gpu_fix, _ProgressCapture(emit, device) as cap:
            sep.load_model(model_filename=model)
            cap.emit(SEPARATE[0], f"Model loaded · separating on {device}…")
            info = next(m for m in MODELS if m["id"] == model)
            cap.passes = _demucs_passes(sep, model) if is_demucs else info.get("passes", 1)
            files = sep.separate(str(src))
        emit(SEPARATE[1], "Writing stems…")
        return files

    with _lock:
        try:
            files = run(force_cpu=False)
        except (RuntimeError, NotImplementedError) as e:
            # GPU trouble (unsupported op, out of memory): retry on CPU rather than fail.
            msg = str(e).lower()
            if not any(k in msg for k in ("mps", "cuda", "out of memory", "directml")):
                raise
            emit(0.02, "GPU failed, retrying on CPU…")
            files = run(force_cpu=True)

    aliases = next(m for m in MODELS if m["id"] == model).get("aliases", {})
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
