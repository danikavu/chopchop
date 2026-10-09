"""Drum-kit and sound-bit extraction: find hits, score them, keep the best unique ones."""

from __future__ import annotations

import time
import zipfile
from pathlib import Path
from typing import Callable

import librosa
import numpy as np

from . import analysis, audio
from .naming import export_name
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


def _priority(c: dict) -> float:
    """Best-sounding first, with a nudge towards sounds the track uses a lot."""
    return c["score"] * (1 + 0.15 * np.log(c.get("count") or c.get("occurrences") or 1))


def _dedupe(cands: list[dict], max_similarity: float, max_per: int) -> list[dict]:
    """Cluster near-identical hits and keep one representative per cluster.

    Hits are visited best-first, so each cluster's representative is its cleanest
    hit; every other hit joins the most similar representative (``count`` = how many
    times that sound occurs). Representatives that are near-identical across
    categories (a snare once misread as a clap) are merged too. Finally each category
    keeps its ``max_per`` highest-priority sounds.
    """
    reps: list[dict] = []
    for c in sorted(cands, key=lambda c: c["score"], reverse=True):
        same = [r for r in reps if r["category"] == c["category"]]
        sims = [similarity(c["emb"], r["emb"]) for r in same]
        if sims and max(sims) >= max_similarity:
            same[int(np.argmax(sims))]["count"] += 1
        else:
            reps.append({**c, "count": 1})

    merged: list[dict] = []
    for r in sorted(reps, key=_priority, reverse=True):
        twin = next((m for m in merged if similarity(r["emb"], m["emb"]) >= max_similarity), None)
        if twin:
            twin["count"] += r["count"]
        else:
            merged.append(r)

    kept: list[dict] = []
    for cat in dict.fromkeys(r["category"] for r in merged):
        group = sorted((r for r in merged if r["category"] == cat), key=_priority, reverse=True)
        kept.extend(group[:max_per])
    return kept


# Order drum types are drawn in when a kit has to be trimmed to a size.
KIT_ORDER = ["kick", "snare", "hihat", "clap", "open_hat", "perc", "tom", "cymbal"]


def select_kit(recs: list[dict], size: int) -> list[dict]:
    """Pick ``size`` sounds: round-robin across drum types (kick, snare, hat, …), each
    type contributing its highest-priority sounds first, so a 16-pad kit gets variety
    before depth."""
    order = KIT_ORDER + sorted({r["category"] for r in recs} - set(KIT_ORDER))
    queues = {cat: sorted((r for r in recs if r["category"] == cat), key=_priority, reverse=True) for cat in order}
    out: list[dict] = []
    while len(out) < size and any(queues.values()):
        for cat in order:
            if queues[cat] and len(out) < size:
                out.append(queues[cat].pop(0))
    return out


def extract(
    project_dir: Path,
    track: dict,
    mode: str = "drums",
    sensitivity: float = 0.5,
    similarity: float = 0.93,
    max_per_category: int = 8,
    max_len: float | None = None,
    min_len: float | None = None,
    kit_size: int | None = None,
    progress: Progress = _noop,
    ref_peak: float | None = None,
    floor_peak: float | None = None,
) -> list[dict]:
    """Build sample records (not yet saved) from a track.

    mode: 'drums'   -> classify each hit as kick/snare/hat/…
          'chops'   -> short onset-based bits labelled by the stem type
          'phrases' -> longer silence-separated bits labelled by the stem type
    kit_size: if set, trim the result to this many sounds with ``select_kit``.
    ref_peak: level that "loud" is measured against (default: this track's peak). Pass
        the loudest of several related tracks (e.g. split drum parts) so a part that only
        holds faint leftovers from the split yields nothing.
    floor_peak: absolute level a sound must reach to count at all, e.g. "audible in the
        song" (the mix's peak -40 dB). Without it a near-silent stem (an "other" that
        only holds traces of drum bleed) still looks full of hits relative to itself.
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

    # Sound bits from a pitched stem (other, vocals, bass, guitar…): stems carry some drum
    # bleed, and bleed is all sharp attacks, so it wins onset detection over soft synth /
    # pad attacks and every bit comes out a drum hit. Find the cuts on the harmonic part
    # (drum-like transients removed) and drop bits that are mostly percussive. Audio is
    # still cut from the original. Drum tracks and the full mix keep their hits.
    melodic = mode != "drums" and not analysis.is_drum_track(track["name"]) \
        and analysis.category_for_track(track["name"]) != "mix"
    if melodic:
        progress(0.04, "Separating tones from drum-like transients…")
        y_det = librosa.effects.harmonic(y, margin=2.0)
        perc = y - y_det
    else:
        y_det, perc = y, None

    progress(0.08, "Detecting hits…")
    min_gap = 0.05 if mode == "drums" else 0.15
    onsets = detect_onsets(y_det, sr, sensitivity=sensitivity, min_gap=min_gap, gate_db=-35.0)
    if mode == "phrases":
        regions = _regions_from_silence(y_det, sr, onsets, min_len, max_len)
    else:
        regions = _regions_from_onsets(onsets, sr, len(y), max_len)
    if not regions:
        return []

    ref_peak = (ref_peak or float(np.max(np.abs(y)))) + 1e-12
    default_cat = analysis.category_for_track(track["name"])
    # Drum labels only make sense on a drum track; anything else (vocals, bass, the
    # full mix…) is labelled, and named, by its general category.
    # Sounds cut from a drum track are drum hits in any mode (chops / phrases too), so they're
    # labelled kick / snare / hat… rather than landing in a catch-all category.
    drum_labels = analysis.is_drum_track(track["name"])
    part = analysis.drum_part(track["name"]) if drum_labels else None
    cands = []
    for i, (a, b) in enumerate(regions):
        if i % 10 == 0:
            progress(0.1 + 0.6 * i / len(regions), f"Analysing hit {i + 1}/{len(regions)}…")
        if mode != "phrases":
            b = a + trim_tail(y_det[a:b], sr, pre=y_det[max(0, a - int(0.04 * sr)):a])
        if b - a < min_len * sr:
            continue
        seg = y[a:b]
        if perc is not None and np.sum(perc[a:b] ** 2) > 0.5 * np.sum(seg ** 2):
            continue  # mostly drum bleed, not a sound from this stem
        # Skip near-silent debris: quiet within this track, or inaudible in the song.
        peak = float(np.max(np.abs(seg)))
        if 20 * np.log10(peak / ref_peak + 1e-12) < -30 or (floor_peak and peak < floor_peak):
            continue
        f = analysis.features(seg, sr, pre=y[max(0, a - int(0.04 * sr)):a])
        if melodic and default_cat != "bass" and f["decay"] < 0.35 \
                and analysis.classify_drum(f)[0] == "kick":
            continue  # kick bleed: its tonal body slips past the percussive check above
        if part == "hihat":  # the hat part holds closed and open hats: tell them apart
            cat, conf = analysis.classify_drum(f)
            cat = cat if cat in ("hihat", "open_hat") else "hihat"
        elif part:
            cat, conf = part, 1.0
        elif drum_labels:
            cat, conf = analysis.classify_drum(f)
        else:
            cat, conf = default_cat, 0.5
        cands.append({
            "start": a / sr, "end": b / sr, "category": cat, "category_confidence": conf,
            "score": _quality(y, sr, a, b, ref_peak), "features": f, "emb": analysis.embedding(f),
        })

    progress(0.72, f"Picking best unique sounds from {len(cands)} candidates…")
    kept = _dedupe(cands, similarity, max_per_category)
    if kit_size:
        kept = select_kit(kept, kit_size)

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
            "occurrences": c["count"],
            "origin": mode,
            **tonal,
            "features": {k: round(v, 4) for k, v in f.items() if isinstance(v, float)},
        })
    if kit_size:
        return out  # keep select_kit's order: it decides pad placement priority
    return sorted(out, key=lambda s: (analysis.CATEGORIES.index(s["category"])
                                      if s["category"] in analysis.CATEGORIES else 99, -s["score"]))


PADS_PER_BANK = 16
BANKS = 4
# MPC layout: pad 1 is bottom-left. Rows (bottom to top) and the drum types they host.
PAD_ROWS = [["kick"], ["snare", "clap"], ["hihat", "open_hat", "cymbal"], ["perc", "tom"]]


def empty_pads() -> list[str | None]:
    return [None] * (PADS_PER_BANK * BANKS)


def layout_pads(samples: list[dict]) -> list[str | None]:
    """Place samples on pads MPC-style: kicks on the bottom row (pads 1-4), snares and
    claps on 5-8, hats on 9-12, perc/toms on 13-16. Overflow fills free pads in bank A,
    then continues into banks B, C, D."""
    pads = empty_pads()
    overflow: list[dict] = []
    placed = {cat: row for row, cats in enumerate(PAD_ROWS) for cat in cats}
    fill = [0] * len(PAD_ROWS)
    for s in samples:
        row = placed.get(s["category"])
        if row is not None and fill[row] < 4:
            pads[row * 4 + fill[row]] = s["id"]
            fill[row] += 1
        else:
            overflow.append(s)
    free = [i for i, p in enumerate(pads) if p is None]
    for s, i in zip(overflow, free):
        pads[i] = s["id"]
    return pads


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
        if analysis.is_drum_track(track["name"]):
            category, conf = analysis.classify_drum(f)
        else:
            category, conf = analysis.category_for_track(track["name"]), 0.5
    else:
        conf = 1.0
    y22 = librosa.resample(y, orig_sr=sr, target_sr=audio.ANALYSIS_SR)
    return {
        "category": category,
        "category_confidence": round(conf, 3),
        **analysis.tonal(y22, audio.ANALYSIS_SR, category),
        "features": {k: round(v, 4) for k, v in f.items() if isinstance(v, float)},
    }


def pad_label(index: int) -> str:
    """0 -> 'A01', 17 -> 'B02' (MPC bank + pad number)."""
    return f"{'ABCD'[index // PADS_PER_BANK]}{index % PADS_PER_BANK + 1:02d}"


def kit_files(project_dir: Path, samples: list[dict], norm: bool = False,
              pad_labels: dict[str, str] | None = None, bpm: float | None = None):
    """Yield (relative path, wav bytes) for a kit: <category>/<name>[_<bpm>bpm][_<key>].wav,
    or, with pad_labels ({sample id: 'A01'}), a flat A01_<name>….wav pad kit. BPM only on
    sounds at least ~a beat long (loops, phrases), not one-shots."""
    used: set[str] = set()
    for s in samples:
        y, sr = audio.read(project_dir / s["file"])
        if norm:
            y = audio.normalize(y)
        # Drum hits are one-shots however long their tail rings: never a BPM in the name.
        tempo = None if s.get("origin") == "drums" else bpm
        name = export_name(safe_name(s["name"]), tempo, s.get("duration") or y.shape[1] / sr, s.get("key"))
        base = f"{pad_labels[s['id']]}_{name}" if pad_labels else f"{safe_name(s['category'])}/{name}"
        path, n = f"{base}.wav", 2
        while path in used:
            path, n = f"{base}_{n}.wav", n + 1
        used.add(path)
        yield path, audio.wav_bytes24(y, sr)


def export_kit(
    project_dir: Path,
    project: dict,
    samples: list[dict],
    kit_name: str,
    norm: bool = False,
    pad_labels: dict[str, str] | None = None,
    bpm: float | None = None,
) -> Path:
    """Zip a kit (see kit_files) under a <kit>/ folder."""
    if not samples:
        raise ValueError("no samples to export")
    kit = safe_name(kit_name) or "kit"
    zip_path = project_dir / "exports" / f"{kit}-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in kit_files(project_dir, samples, norm, pad_labels, bpm):
            zf.writestr(f"{kit}/{path}", data)
    return zip_path


def write_kit(project_dir: Path, samples: list[dict], out_dir: Path, norm: bool = False,
              pad_labels: dict[str, str] | None = None, bpm: float | None = None) -> list[Path]:
    """Write a kit (see kit_files) straight into a folder; returns the files written."""
    written = []
    for path, data in kit_files(project_dir, samples, norm, pad_labels, bpm):
        target = out_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(target)
    return written
