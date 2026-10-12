"""Heuristic DSP analysis: features, drum-hit classification, note & key detection."""

from __future__ import annotations

import re
import warnings

import librosa
import numpy as np
from scipy.signal import find_peaks

DRUM_CATEGORIES = ["kick", "snare", "clap", "hihat", "open_hat", "cymbal", "tom", "perc"]
MELODIC_CATEGORIES = ["vocal", "bass", "keys", "guitar", "synth", "other", "instrumental", "melodic", "fx", "mix"]
CATEGORIES = DRUM_CATEGORIES + MELODIC_CATEGORIES

# Longest cut from a drum track that still counts as a single hit (for key naming).
DRUM_ONE_SHOT_MAX_S = 1.0

# Drum hits that don't usefully carry a pitch label.
UNPITCHED = {"snare", "clap", "hihat", "open_hat", "cymbal"}

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
KEYS = [f"{n} maj" for n in NOTE_NAMES] + [f"{n} min" for n in NOTE_NAMES]

# Krumhansl-Kessler key profiles.
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

BANDS = {
    "sub": (20, 90),
    "low": (90, 250),
    "lowmid": (250, 1000),
    "mid": (1000, 4000),
    "high": (4000, 10000),
    "air": (10000, 24000),
}

N_FFT = 2048
HOP = 256


def _pad(y: np.ndarray, n: int = N_FFT) -> np.ndarray:
    return np.pad(y, (0, n - len(y))) if len(y) < n else y


def _power(y: np.ndarray, n_fft: int, hop: int, pre: np.ndarray | None) -> np.ndarray:
    """Power spectrogram with the spectrum of whatever was ringing before the hit
    (pre) subtracted, so a hat on top of a kick tail is analysed as a hat."""
    power = np.abs(librosa.stft(_pad(y, n_fft), n_fft=n_fft, hop_length=hop)) ** 2
    if pre is not None and pre.size >= n_fft // 4:
        bleed = (np.abs(librosa.stft(_pad(pre, n_fft), n_fft=n_fft, hop_length=hop)) ** 2).mean(axis=1)
        # Over-subtract (x2): bleed only decays, and residue skews the fingerprint.
        power = np.maximum(power - 2.0 * bleed[:, None], 0.0)
    return power


def features(y: np.ndarray, sr: int, pre: np.ndarray | None = None) -> dict:
    """Spectral/temporal descriptors for one mono sound.

    pre: optional audio just before the sound; its spectrum is treated as bleed.
    """
    y = y.astype(np.float32)
    pre = None if pre is None else pre.astype(np.float32)
    power = _power(y, N_FFT, HOP, pre)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=N_FFT)
    spec = power.sum(axis=1)
    total = spec.sum() + 1e-12

    ratios = {k: float(spec[(freqs >= lo) & (freqs < hi)].sum() / total) for k, (lo, hi) in BANDS.items()}
    centroid = float((freqs * spec).sum() / total)
    # Noisiness: share of energy NOT in the strongest 2% of bins. Tones ~0, noise ~0.7+.
    top = np.sort(spec)[::-1][: max(1, len(spec) // 50)]
    noisiness = float(1.0 - top.sum() / total)

    env = np.sqrt(_power(y, 512, 64, pre).sum(axis=0))
    peak_i = int(np.argmax(env))
    peak = env[peak_i] + 1e-12
    after = np.nonzero(env[peak_i:] < peak * 0.1)[0]  # -20 dB
    decay = float((after[0] if after.size else len(env) - peak_i) * 64 / sr)
    attack = float(peak_i * 64 / sr)

    # Clap detection: several close bursts in the first ~60 ms.
    fine = librosa.feature.rms(y=y[: int(0.08 * sr)], frame_length=128, hop_length=32)[0]
    bursts = len(find_peaks(fine, prominence=fine.max() * 0.15)[0]) if fine.size > 3 else 1

    # Timbre fingerprint: compressed mel spectrum over five time windows
    # (0-15, 15-50, 50-120, 120-300, 300+ ms). Linear-ish (power^0.75), not log, so
    # near-silent bins barely matter; one norm across all windows so the envelope
    # shape counts. Same hit in a different context: >0.96; different kicks: ~0.8.
    mel = librosa.feature.melspectrogram(S=power, sr=sr, n_mels=96, fmin=20, fmax=min(16000, sr / 2))
    edges = [0] + [int(ms / 1000 * sr / HOP) + 1 for ms in (15, 50, 120, 300)] + [mel.shape[1]]
    parts = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        hi = min(hi, mel.shape[1])
        parts.append(mel[:, lo:hi].mean(axis=1) ** 0.75 if hi > lo else np.zeros(mel.shape[0]))
    timbre = np.concatenate(parts)
    timbre /= np.linalg.norm(timbre) + 1e-12

    return {
        **{f"band_{k}": v for k, v in ratios.items()},
        "centroid": centroid,
        "noisiness": noisiness,
        "decay": decay,
        "attack": attack,
        "bursts": int(bursts),
        "peak_db": float(20 * np.log10(np.max(np.abs(y)) + 1e-12)),
        "timbre": timbre.tolist(),
    }


def embedding(f: dict) -> np.ndarray:
    """Timbre vector used for similarity / de-duplication (unit length)."""
    return np.asarray(f["timbre"])


def classify_drum(f: dict) -> tuple[str, float]:
    """Kick / snare / clap / hihat / open_hat / cymbal / tom / perc with a rough confidence."""
    sub, low = f["band_sub"], f["band_low"]
    lowmid, mid = f["band_lowmid"], f["band_mid"]
    high = f["band_high"] + f["band_air"]
    bottom = sub + low
    noisy = f["noisiness"] > 0.4

    if bottom >= 0.55 and f["centroid"] < 1200:
        if sub >= low * 0.6:
            return "kick", min(1.0, bottom)
        return "tom", min(1.0, bottom * 0.8)
    if high >= 0.5 and (noisy or f["centroid"] > 5000):
        if f["decay"] < 0.12:
            return "hihat", min(1.0, high)
        if f["decay"] < 0.6:
            return "open_hat", min(1.0, high * 0.9)
        return "cymbal", min(1.0, high * 0.9)
    if mid + high + lowmid >= 0.55 and noisy:
        if f["bursts"] >= 3 and bottom < 0.2:
            return "clap", 0.6
        return "snare", min(1.0, mid + high)
    if low + lowmid >= 0.5 and not noisy:
        return "tom", 0.5
    return "perc", 0.4


def is_drum_track(track_name: str) -> bool:
    """Only drum stems get per-hit drum labels (kick, snare, hat…). "no_drums" (everything
    but the drums) is not one."""
    name = track_name.lower()
    return not name.startswith("no_") and ("drum" in name or "perc" in name)


# Tracks made by the drum-split tool ("drums_kick", …): the model already did the labelling.
DRUM_PART_CATEGORIES = {"kick": "kick", "snare": "snare", "toms": "tom", "hihat": "hihat",
                        "ride": "cymbal", "crash": "cymbal"}


def drum_part(track_name: str) -> str | None:
    """'drums_kick' -> 'kick' category; None for anything that isn't a split drum part."""
    suffix = track_name.lower().rsplit("_", 1)[-1]
    return DRUM_PART_CATEGORIES.get(suffix) if "_" in track_name else None


def category_for_track(track_name: str) -> str:
    """Category (and file-name prefix / export folder) for sounds cut from a non-drum
    track. Known stem types map to their kind; anything else uses the track's own name,
    so a folder always says where its sounds came from. In particular nothing falls back
    to "other": that name belongs to the "other" stem."""
    name = track_name.lower()
    if not name.startswith("no_"):  # "no_bass" is everything *except* the bass
        for key, cat in (
            ("vocal", "vocal"), ("voice", "vocal"), ("bass", "bass"), ("piano", "keys"),
            ("keys", "keys"), ("guitar", "guitar"), ("synth", "synth"), ("other", "other"),
            ("instrumental", "instrumental"), ("mix", "mix"),
        ):
            if key in name:
                return cat
    return re.sub(r"[^a-z0-9_]+", "_", name).strip("_") or "sounds"


def detect_note(y: np.ndarray, sr: int) -> tuple[str | None, float | None, float]:
    """Dominant pitch of a one-shot: (note name, Hz, confidence 0..1)."""
    y = _pad(y)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        f0, voiced, prob = librosa.pyin(y, fmin=30, fmax=2000, sr=sr, frame_length=N_FFT, hop_length=HOP)
    good = voiced & np.isfinite(f0)
    if good.sum() < 3:
        return None, None, 0.0
    conf = float(np.nanmean(prob[good]))
    if conf < 0.25:
        return None, None, conf
    hz = float(np.median(f0[good]))
    return librosa.hz_to_note(hz, unicode=False), hz, conf


def detect_key(y: np.ndarray, sr: int) -> tuple[str | None, float]:
    """Musical key via chroma + Krumhansl profiles: ('A min', confidence)."""
    if len(y) < sr * 0.5:
        return None, 0.0
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=512)
    c = chroma.mean(axis=1)
    if c.max() <= 1e-6:
        return None, 0.0
    scores = []
    for profile in (_MAJOR, _MINOR):
        for shift in range(12):
            scores.append(np.corrcoef(c, np.roll(profile, shift))[0, 1])
    scores = np.nan_to_num(np.array(scores), nan=-1.0)
    order = np.argsort(scores)[::-1]
    best, second = scores[order[0]], scores[order[1]]
    conf = float(np.clip((best - second) * 5 + best * 0.5, 0, 1))
    return KEYS[order[0]], conf


def tonal(y: np.ndarray, sr: int, category: str | None = None) -> dict:
    """Note for short one-shots, key for longer phrases; skipped for unpitched drums."""
    with warnings.catch_warnings():  # librosa is chatty about very short sounds
        warnings.simplefilter("ignore")
        return _tonal(y, sr, category)


def key_of(y: np.ndarray, sr: int, drum_track: bool) -> str | None:
    """Key / note for an arbitrary cut. On drum tracks only single pitched hits (kick /
    808, tom, perc) get a note: never hats or snares, and never multi-hit drum loops,
    whose "key" would just be noise in the chroma."""
    category = None
    if drum_track and y.size > sr * DRUM_ONE_SHOT_MAX_S:
        return None
    if drum_track:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            category = classify_drum(features(y, sr))[0]
    if y.size < sr * 0.03:
        return None
    y22 = librosa.resample(y, orig_sr=sr, target_sr=22050) if sr != 22050 else y
    return tonal(y22, 22050, category)["key"]


def _tonal(y: np.ndarray, sr: int, category: str | None) -> dict:
    if category in UNPITCHED:
        return {"key": None, "pitch_hz": None, "key_confidence": 0.0}
    dur = len(y) / sr
    if dur < 1.5:
        note, hz, conf = detect_note(y, sr)
        if note:
            return {"key": note, "pitch_hz": hz, "key_confidence": conf}
        if dur < 0.5:
            return {"key": None, "pitch_hz": None, "key_confidence": conf}
    key, conf = detect_key(y, sr)
    return {"key": key, "pitch_hz": None, "key_confidence": conf}
