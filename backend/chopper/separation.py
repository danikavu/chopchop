"""Stem separation using UVR models through the audio-separator package."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .store import data_dir

MODELS = [
    {
        "id": "htdemucs_6s.yaml",
        "label": "Demucs 6-stem",
        "stems": ["vocals", "drums", "bass", "guitar", "piano", "other"],
        "default": True,
    },
    {
        "id": "htdemucs_ft.yaml",
        "label": "Demucs 4-stem fine-tuned (best, slow)",
        "stems": ["vocals", "drums", "bass", "other"],
    },
    {
        "id": "htdemucs.yaml",
        "label": "Demucs 4-stem (fast)",
        "stems": ["vocals", "drums", "bass", "other"],
    },
    {
        "id": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        "label": "BS-Roformer vocals / instrumental",
        "stems": ["vocals", "instrumental"],
    },
]


class SeparationUnavailable(RuntimeError):
    pass


def status() -> dict:
    """Report whether separation can run on this machine."""
    try:
        from audio_separator.separator import Separator  # noqa: F401
    except ImportError:
        return {"available": False, "reason": "audio-separator is not installed (pip install -e '.[separation]')"}
    if shutil.which("ffmpeg") is None:
        return {"available": False, "reason": "ffmpeg is not installed (macOS: brew install ffmpeg)"}
    return {"available": True, "reason": None}


def model_dir() -> Path:
    d = data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def stem_name(filename: str) -> str:
    """'song_(Vocals)_htdemucs_6s.wav' -> 'vocals'."""
    m = re.search(r"_\(([^)]+)\)", filename)
    return (m.group(1) if m else Path(filename).stem).strip().lower().replace(" ", "_")


def separate(src: Path, out_dir: Path, model: str, on_progress=None) -> dict[str, Path]:
    st = status()
    if not st["available"]:
        raise SeparationUnavailable(st["reason"])
    if model not in {m["id"] for m in MODELS}:
        raise ValueError(f"unknown model: {model}")

    from audio_separator.separator import Separator

    out_dir.mkdir(parents=True, exist_ok=True)

    def run(force_cpu: bool) -> list[str]:
        if on_progress:
            on_progress(0.05, "Loading model (first run downloads it)…")
        sep = Separator(
            output_dir=str(out_dir),
            model_file_dir=str(model_dir()),
            output_format="WAV",
            use_soundfile=True,  # write WAVs directly instead of through pydub/ffmpeg
        )
        if force_cpu:
            sep.torch_device = sep.torch_device_cpu
        sep.load_model(model_filename=model)
        if on_progress:
            on_progress(0.2, "Separating stems" + (" on CPU…" if force_cpu else "…"))
        return sep.separate(str(src))

    # Demucs hits "Output channels > 65536 not supported at the MPS device" on Apple
    # Silicon, so it always runs on CPU; other models try the GPU and fall back.
    if model.endswith(".yaml"):
        files = run(force_cpu=True)
    else:
        try:
            files = run(force_cpu=False)
        except (RuntimeError, NotImplementedError) as e:
            if "mps" not in str(e).lower():
                raise
            files = run(force_cpu=True)

    stems: dict[str, Path] = {}
    for f in files:
        p = Path(f)
        if not p.is_absolute():
            p = out_dir / p
        stems[stem_name(p.name)] = p
    if not stems:
        raise RuntimeError("separation produced no stems")
    return stems
