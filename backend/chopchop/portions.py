"""Separate only parts of a track.

The parts (plus a little context either side, so the models hear how each part starts
and ends) are joined into one short file with silence between them. That file is
separated in one go (one model load), then each part is put back at its own time in
full-length stems that are silent everywhere else, so every lane still lines up with
the mix.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import audio

CONTEXT_S = 1.0  # audio kept either side of a part for the model, cut off again after
GAP_S = 1.0  # silence between parts in the joined file
FADE_S = 0.01  # fade at each part's edges in the stems: no clicks


def merge(portions: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    """Clip to the track, drop empty ones, join overlapping / touching parts; sorted."""
    spans = sorted((max(0.0, a), min(duration, b)) for a, b in portions if min(duration, b) - max(0.0, a) > 0.05)
    out: list[list[float]] = []
    for a, b in spans:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def excerpt(src: Path, portions: list[tuple[float, float]], out: Path) -> tuple[Path, list[dict], float]:
    """Write the joined file. Returns (its path, layout, track duration); the layout says
    where each part sits in it: {"start", "end"} in the track, "at" in the joined file
    (all seconds)."""
    y, sr = audio.read(src)
    duration = y.shape[1] / sr
    parts = merge(portions, duration)
    if not parts:
        raise ValueError("no parts to separate (each part needs to be at least 0.05 s, inside the track)")
    gap = np.zeros((y.shape[0], int(GAP_S * sr)), np.float32)
    pieces, layout, pos = [], [], 0.0
    for a, b in parts:
        lo, hi = max(0.0, a - CONTEXT_S), min(duration, b + CONTEXT_S)
        chunk = y[:, int(lo * sr):int(hi * sr)]
        layout.append({"start": a, "end": b, "at": pos + (a - lo)})
        pieces += [chunk, gap]
        pos += chunk.shape[1] / sr + GAP_S
    out.mkdir(parents=True, exist_ok=True)
    path = out / "parts.wav"
    audio.write_wav(path, np.concatenate(pieces, axis=1), sr)
    return path, layout, duration


def stitch(stems: dict[str, Path], layout: list[dict], duration: float, out: Path) -> dict[str, Path]:
    """Full-length stems from the joined file's stems: each part back at its own time."""
    out.mkdir(parents=True, exist_ok=True)
    result = {}
    for name, path in stems.items():
        y, sr = audio.read(path)
        full = np.zeros((y.shape[0], int(round(duration * sr))), np.float32)
        for part in layout:
            n = int(round((part["end"] - part["start"]) * sr))
            src = int(round(part["at"] * sr))
            dst = int(round(part["start"] * sr))
            seg = y[:, src:src + n]
            n = min(seg.shape[1], full.shape[1] - dst)
            full[:, dst:dst + n] = audio.apply_fades(seg[:, :n], sr, FADE_S * 1000, FADE_S * 1000)
        target = out / f"{name}.wav"
        audio.write_wav(target, full, sr)
        result[name] = target
    return result
