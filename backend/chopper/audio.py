"""Audio I/O helpers: import, read ranges, cut with fades, waveform peaks."""

from __future__ import annotations

import io
import json
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

ANALYSIS_SR = 22050


def import_audio(src: Path, dst: Path) -> dict:
    """Decode any supported file and store it as a 24-bit WAV at its native rate."""
    try:
        data, sr = sf.read(str(src), dtype="float32", always_2d=True)
        y = data.T
    except Exception:
        # Fallback decoder (audioread) for formats libsndfile can't open.
        y, sr = librosa.load(str(src), sr=None, mono=False)
        y = np.atleast_2d(y).astype(np.float32)
    if y.shape[1] == 0:
        raise ValueError("audio file is empty")
    write_wav(dst, y, sr)
    return {"sample_rate": int(sr), "duration": y.shape[1] / sr, "channels": int(y.shape[0])}


def write_wav(path: Path, y: np.ndarray, sr: int) -> None:
    """Write (channels, samples) float audio as 24-bit PCM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.clip(y, -1.0, 1.0).T, sr, subtype="PCM_24")


def wav_bytes(y: np.ndarray, sr: int, subtype: str = "PCM_16") -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.clip(y, -1.0, 1.0).T, sr, format="WAV", subtype=subtype)
    return buf.getvalue()


def wav_bytes24(y: np.ndarray, sr: int) -> bytes:
    return wav_bytes(y, sr, "PCM_24")


def info(path: Path) -> sf._SoundFileInfo:
    return sf.info(str(path))


def read(path: Path, start: float | None = None, end: float | None = None) -> tuple[np.ndarray, int]:
    """Read a time range as (channels, samples) float32."""
    sr = sf.info(str(path)).samplerate
    s = max(0, int(round((start or 0.0) * sr)))
    e = None if end is None else max(s, int(round(end * sr)))
    data, sr = sf.read(str(path), start=s, stop=e, dtype="float32", always_2d=True)
    return data.T, sr


def to_mono(y: np.ndarray) -> np.ndarray:
    return y.mean(axis=0) if y.ndim == 2 else y


def read_mono(path: Path, sr: int | None = ANALYSIS_SR, start=None, end=None) -> tuple[np.ndarray, int]:
    y, native = read(path, start, end)
    mono = to_mono(y)
    if sr is not None and sr != native:
        mono = librosa.resample(mono, orig_sr=native, target_sr=sr)
        return mono, sr
    return mono, native


def apply_fades(y: np.ndarray, sr: int, fade_in_ms: float = 2.0, fade_out_ms: float = 8.0) -> np.ndarray:
    y = y.copy()
    n = y.shape[-1]
    fi = min(n, int(sr * fade_in_ms / 1000))
    fo = min(n, int(sr * fade_out_ms / 1000))
    if fi > 0:
        y[..., :fi] *= np.linspace(0.0, 1.0, fi, dtype=y.dtype)
    if fo > 0:
        y[..., n - fo:] *= np.linspace(1.0, 0.0, fo, dtype=y.dtype)
    return y


def normalize(y: np.ndarray, peak_db: float = -0.3) -> np.ndarray:
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak < 1e-6:
        return y
    return y * (10 ** (peak_db / 20) / peak)


def cut(path: Path, start: float, end: float, fade_in_ms=2.0, fade_out_ms=8.0, norm=False) -> tuple[np.ndarray, int]:
    y, sr = read(path, start, end)
    y = apply_fades(y, sr, fade_in_ms, fade_out_ms)
    if norm:
        y = normalize(y)
    return y, sr


def peaks(path: Path, n: int = 4000) -> list[float]:
    """Max-abs envelope with ~n buckets, cached next to the file."""
    cache = path.with_suffix(f".peaks{n}.json")
    if cache.exists() and cache.stat().st_mtime >= path.stat().st_mtime:
        return json.loads(cache.read_text())
    frames = sf.info(str(path)).frames
    bucket = max(1, int(np.ceil(frames / n)))
    out = []
    for block in sf.blocks(str(path), blocksize=bucket * 512, dtype="float32", always_2d=True):
        m = np.abs(block).max(axis=1)
        k = int(np.ceil(len(m) / bucket))
        m = np.pad(m, (0, k * bucket - len(m))).reshape(k, bucket).max(axis=1)
        out.append(m)
    result = [round(float(v), 4) for v in (np.concatenate(out) if out else np.zeros(0))]
    cache.write_text(json.dumps(result))
    return result
