"""Drum-kit and sound-bit extraction: find hits, score them, keep the best unique ones."""

from __future__ import annotations

import time
import zipfile
from pathlib import Path
from typing import Callable

import librosa
import numpy as np

from . import analysis, audio
from .slicing import detect_onsets
from .store import new_id, safe_name

Progress = Callable[[float, str], None]


def _noop(_p: float, _m: str) -> None:
    pass


def trim_tail(seg: np.ndarray, sr: int, floor_db: float = -45.0, pre: np.ndarray | None = None) -> int:
    """Length (samples) of seg once its tail falls below floor_db relative to its peak.

    pre: audio before the hit; its spectrum is treated as bleed so a ringing earlier
    sound (e.g. a kick under a hat) doesn't keep the hit "alive".
    """
    if seg.size < 256:
        return seg.size
    hop = 128
    env = np.sqrt(analysis._power(seg, 512, hop, pre).sum(axis=0))
    above = np.nonzero(env > env.max() * 10 ** (floor_db / 20))[0]
    if above.size == 0:
        return seg.size
    return int(min(seg.size, (above[-1] + 2) * hop + int(0.005 * sr)))


def snap_to_transient(y: np.ndarray, sr: int, a: int, b: int) -> int:
    """Move a hit's start to the rising edge right before its peak, minus 1 ms.

    Onsets come quantised to analysis frames; snapping makes identical hits line up
    (for de-duplication) and makes the exported one-shots start right at the attack.
    """
    # Fine-grained log-spectral flux: reacts to *new* energy, so it still finds the
    # attack when an earlier sound (kick tail, bass) is ringing louder underneath.
    lo = a - int(0.02 * sr)
    win = y[max(0, lo):min(b, a + int(0.06 * sr))]
    if lo < 0:  # hit at the very start of the file: pretend there was silence before
        win = np.concatenate([np.zeros(-lo, dtype=win.dtype), win])
    n_fft, hop = 256, 32
    if win.size < n_fft * 2:
        return a
    S = np.abs(librosa.stft(win, n_fft=n_fft, hop_length=hop, center=False))
    L = np.log1p(S * 1000)
    flux = np.maximum(np.diff(L, axis=1), 0).sum(axis=0)
    f = int(np.argmax(flux)) + 1
    # New energy enters at the window's leading edge; take the frame centre and a
    # 2 ms margin so the attack is never clipped.
    onset = lo + f * hop + n_fft // 2
    return max(0, onset - int(0.002 * sr))


def _quality(y: np.ndarray, sr: int, a: int, b: int, ref_peak: float) -> float:
    """0..1: loud, isolated from what came before, and not cut off by the next hit."""
    seg = y[a:b]
    peak = float(np.max(np.abs(seg))) + 1e-12
    level = np.clip(1 + 20 * np.log10(peak / ref_peak) / 30, 0, 1)
    pre = y[max(0, a - int(0.03 * sr)):a]
    iso = 1.0 - np.clip((np.sqrt(np.mean(pre ** 2)) if pre.size else 0.0) / (peak * 0.5), 0, 1)
    tail = seg[-int(0.015 * sr):] if seg.size > int(0.015 * sr) else seg
    clean_end = 1.0 - np.clip(np.max(np.abs(tail)) / peak / 0.3, 0, 1)
    return float(0.4 * level + 0.35 * iso + 0.25 * clean_end)


def _regions_from_onsets(onsets: np.ndarray, sr: int, n: int, max_len: float) -> list[tuple[int, int]]:
    starts = (onsets * sr).astype(int)
    out = []
    for i, a in enumerate(starts):
        b = starts[i + 1] if i + 1 < len(starts) else n
        out.append((a, min(b, a + int(max_len * sr))))
    return out


def _regions_from_silence(y: np.ndarray, sr: int, onsets: np.ndarray, min_len: float, max_len: float) -> list[tuple[int, int]]:
    """Phrases: non-silent spans, long ones split at the last onset before max_len."""
    spans = librosa.effects.split(y, top_db=35, frame_length=2048, hop_length=256)
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a - merged[-1][1] < int(0.15 * sr):
            merged[-1][1] = b
        else:
            merged.append([int(a), int(b)])
    on = (onsets * sr).astype(int)
    out = []
    for a, b in merged:
        while b - a > max_len * sr:
            limit = a + int(max_len * sr)
            cuts = on[(on > a + int(min_len * sr)) & (on <= limit)]
            cut = int(cuts[-1]) if cuts.size else limit
            out.append((a, cut))
            a = cut
        if b - a >= min_len * sr:
            out.append((a, b))
    return out


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """0..1 cosine similarity of two timbre fingerprints."""
    return float(np.clip(a @ b, 0.0, 1.0))


def _dedupe(cands: list[dict], max_similarity: float, max_per: int) -> list[dict]:
    """Per category, keep highest-scoring hits that aren't too similar to ones already kept."""
    kept: list[dict] = []
    for cat in dict.fromkeys(c["category"] for c in cands):
        group = sorted((c for c in cands if c["category"] == cat), key=lambda c: c["score"], reverse=True)
        chosen: list[dict] = []
        for c in group:
            if len(chosen) >= max_per:
                break
            if all(similarity(c["emb"], k["emb"]) < max_similarity for k in chosen):
                chosen.append(c)
        kept.extend(chosen)
    return kept


def extract(
    project_dir: Path,
    track: dict,
    mode: str = "drums",
    sensitivity: float = 0.5,
    similarity: float = 0.93,
    max_per_category: int = 8,
    max_len: float | None = None,
    min_len: float | None = None,
    progress: Progress = _noop,
) -> list[dict]:
    """Build sample records (not yet saved) from a track.

    mode: 'drums'   -> classify each hit as kick/snare/hat/…
          'chops'   -> short onset-based bits labelled by the stem type
          'phrases' -> longer silence-separated bits labelled by the stem type
    """
    src = project_dir / track["file"]
    progress(0.02, "Loading audio…")
    y, sr = audio.read_mono(src, sr=None)
    if mode == "drums":
        max_len = max_len or 1.2
        min_len = min_len or 0.03
    elif mode == "chops":
        max_len = max_len or 2.0
        min_len = min_len or 0.12
    elif mode == "phrases":
        max_len = max_len or 8.0
        min_len = min_len or 0.5
    else:
        raise ValueError(f"unknown mode: {mode}")

    progress(0.08, "Detecting hits…")
    min_gap = 0.05 if mode == "drums" else 0.15
    onsets = detect_onsets(y, sr, sensitivity=sensitivity, min_gap=min_gap, gate_db=-35.0)
    if mode == "phrases":
        regions = _regions_from_silence(y, sr, onsets, min_len, max_len)
    else:
        regions = _regions_from_onsets(onsets, sr, len(y), max_len)
    if not regions:
        return []

    ref_peak = float(np.max(np.abs(y))) + 1e-12
    default_cat = analysis.category_for_track(track["name"])
    cands = []
    for i, (a, b) in enumerate(regions):
        if i % 10 == 0:
            progress(0.1 + 0.6 * i / len(regions), f"Analysing hit {i + 1}/{len(regions)}…")
        if mode != "phrases":
            a = snap_to_transient(y, sr, a, b)
            b = a + trim_tail(y[a:b], sr, pre=y[max(0, a - int(0.04 * sr)):a])
        if b - a < min_len * sr:
            continue
        seg = y[a:b]
        # Skip near-silent debris.
        if 20 * np.log10(np.max(np.abs(seg)) / ref_peak + 1e-12) < -30:
            continue
        f = analysis.features(seg, sr, pre=y[max(0, a - int(0.04 * sr)):a])
        if mode == "drums":
            cat, conf = analysis.classify_drum(f)
        else:
            cat, conf = default_cat, 0.5
        cands.append({
            "start": a / sr, "end": b / sr, "category": cat, "category_confidence": conf,
            "score": _quality(y, sr, a, b, ref_peak), "features": f, "emb": analysis.embedding(f),
        })

    progress(0.72, f"Picking best unique sounds from {len(cands)} candidates…")
    kept = _dedupe(cands, similarity, max_per_category)

    out = []
    for i, c in enumerate(kept):
        progress(0.75 + 0.2 * i / max(1, len(kept)), f"Detecting key {i + 1}/{len(kept)}…")
        seg = y[int(c["start"] * sr):int(c["end"] * sr)]
        seg22 = librosa.resample(seg, orig_sr=sr, target_sr=audio.ANALYSIS_SR)
        tonal = analysis.tonal(seg22, audio.ANALYSIS_SR, c["category"])
        f = c["features"]
        out.append({
            "track": track["name"],
            "start": round(c["start"], 4),
            "end": round(c["end"], 4),
            "category": c["category"],
            "category_confidence": round(c["category_confidence"], 3),
            "score": round(c["score"], 3),
            "origin": mode,
            **tonal,
            "features": {k: round(v, 4) for k, v in f.items() if isinstance(v, float)},
        })
    return sorted(out, key=lambda s: (analysis.CATEGORIES.index(s["category"])
                                      if s["category"] in analysis.CATEGORIES else 99, -s["score"]))


def next_name(existing: list[dict], category: str) -> str:
    used = {s["name"] for s in existing}
    i = 1
    while f"{category}_{i:02d}" in used:
        i += 1
    return f"{category}_{i:02d}"


def save_sample(project_dir: Path, project: dict, track: dict, rec: dict) -> dict:
    """Cut rec's region from the full-quality track and register it on the project."""
    sid = new_id()
    y, sr = audio.cut(project_dir / track["file"], rec["start"], rec["end"])
    rel = f"samples/{sid}.wav"
    audio.write_wav(project_dir / rel, y, sr)
    sample = {
        **rec,
        "id": sid,
        "name": rec.get("name") or next_name(project["samples"], rec["category"]),
        "file": rel,
        "duration": round(y.shape[1] / sr, 4),
        "created_at": time.time(),
    }
    project["samples"].append(sample)
    return sample


def analyse_region(project_dir: Path, track: dict, start: float, end: float, category: str | None = None) -> dict:
    """Category + key for a manually chosen region."""
    y, sr = audio.read_mono(project_dir / track["file"], sr=None, start=start, end=end)
    if y.size == 0:
        raise ValueError("empty region")
    f = analysis.features(y, sr)
    if category is None:
        default = analysis.category_for_track(track["name"])
        if "drum" in track["name"].lower() or ((end - start) < 0.6 and default in ("other", "melodic")):
            category, conf = analysis.classify_drum(f)
        else:
            category, conf = default, 0.5
    else:
        conf = 1.0
    y22 = librosa.resample(y, orig_sr=sr, target_sr=audio.ANALYSIS_SR)
    return {
        "category": category,
        "category_confidence": round(conf, 3),
        **analysis.tonal(y22, audio.ANALYSIS_SR, category),
        "features": {k: round(v, 4) for k, v in f.items() if isinstance(v, float)},
    }


def export_kit(project_dir: Path, project: dict, samples: list[dict], kit_name: str, norm: bool = False) -> Path:
    """Zip samples into <kit>/<category>/<name>[_<key>].wav."""
    if not samples:
        raise ValueError("no samples to export")
    kit = safe_name(kit_name) or "kit"
    zip_path = project_dir / "exports" / f"{kit}-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    used: set[str] = set()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for s in samples:
            y, sr = audio.read(project_dir / s["file"])
            if norm:
                y = audio.normalize(y)
            key = f"_{s['key'].replace(' ', '')}" if s.get("key") else ""
            base = f"{kit}/{safe_name(s['category'])}/{safe_name(s['name'])}{key}"
            path, n = f"{base}.wav", 2
            while path in used:
                path, n = f"{base}_{n}.wav", n + 1
            used.add(path)
            zf.writestr(path, audio.wav_bytes24(y, sr))
    return zip_path
