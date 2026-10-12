"""Peak (onset) detection and slice export."""

from __future__ import annotations

import time
import zipfile
from pathlib import Path

import librosa
import numpy as np

from . import analysis, audio
from .naming import export_name
from .store import new_id, safe_name

HOP = 256


def detect_onsets(
    y: np.ndarray,
    sr: int,
    sensitivity: float = 0.5,
    min_gap: float = 0.08,
    gate_db: float = -40.0,
) -> np.ndarray:
    """Onset times (seconds), placed just before each hit's attack.

    sensitivity: 0..1, higher finds more (quieter / less distinct) hits.
    min_gap:     minimum seconds between two onsets.
    gate_db:     ignore hits quieter than this relative to the loudest moment.
    """
    if y.size < HOP * 4:
        return np.zeros(0)
    sensitivity = float(np.clip(sensitivity, 0.0, 1.0))
    # Leading silence so a hit at t=0 still produces a rising edge.
    pad = HOP * 16
    y = np.concatenate([np.zeros(pad, dtype=y.dtype), y])
    # max_size>1 (SuperFlux) suppresses false onsets from pitch sweeps / vibrato.
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP, max_size=3)
    frame_s = HOP / sr
    frames = librosa.onset.onset_detect(
        onset_envelope=env,
        sr=sr,
        hop_length=HOP,
        backtrack=False,
        delta=0.01 + (1.0 - sensitivity) ** 2 * 0.35,
        wait=max(1, int(min_gap / frame_s)),
        pre_max=max(1, int(0.03 / frame_s)),
        post_max=max(1, int(0.03 / frame_s)),
        pre_avg=max(1, int(0.1 / frame_s)),
        post_avg=max(1, int(0.1 / frame_s)),
    )
    if frames.size == 0:
        return np.zeros(0)

    rms = librosa.feature.rms(y=y, frame_length=HOP * 4, hop_length=HOP)[0]
    ref = rms.max() + 1e-12
    look = max(1, int(0.05 / frame_s))
    after = np.array([rms[f:f + look].max() if f < len(rms) else 0.0 for f in frames])
    # Gate: loudness just after the onset relative to the loudest moment.
    loud = 20 * np.log10(after / ref + 1e-12) >= gate_db
    # A real hit raises the energy in at least one frequency band; a decaying tail
    # with spectral movement (e.g. a kick's pitch sweep) doesn't. Per-band so a hat
    # over a ringing kick still counts.
    rise = _band_rise_db(y, sr, frames, look)
    rising = rise >= 3.0 + (1.0 - sensitivity) * 6.0
    frames = frames[loud & rising]
    if frames.size == 0:
        return np.zeros(0)

    # Snap each onset to its actual attack. (Backtracking to the previous energy
    # minimum lands anywhere in a silent gap, leaving silence at the slice start.)
    peaks = frames * HOP
    starts = []
    prev = 0
    for s in peaks:
        lo = max(prev, s - int(0.05 * sr))
        start = snap_to_attack(y, sr, lo, s + int(0.03 * sr))
        starts.append(start)
        prev = start + int(0.01 * sr)
    times = (np.array(starts) - pad) / sr
    return np.unique(np.clip(times, 0.0, None))


def snap_to_attack(y: np.ndarray, sr: int, lo: int, hi: int, margin: float = 0.002) -> int:
    """Sample index of the attack inside y[lo:hi], minus a small safety margin.

    Uses fine-grained log-spectral flux, which reacts to *new* energy, so it finds the
    attack even when an earlier sound (kick tail, bass) is ringing louder underneath.
    lo may be negative (hit at the very start): treated as silence before the file.
    """
    win = y[max(0, lo):max(0, hi)]
    if lo < 0:
        win = np.concatenate([np.zeros(-lo, dtype=win.dtype), win])
    n_fft, hop = 256, 32
    if win.size < n_fft * 2:
        return max(0, lo)
    S = np.abs(librosa.stft(win, n_fft=n_fft, hop_length=hop, center=False))
    # Floor 50 dB below the loudest bin: faint splatter (e.g. a fade-out) mustn't
    # look like an attack just because it rises out of near-silence.
    L = np.log(S + S.max() * 3e-3)
    flux = np.maximum(np.diff(L, axis=1), 0).sum(axis=0)
    f = int(np.argmax(flux)) + 1
    # New energy enters at the window's leading edge; take the frame centre plus a
    # small margin so the attack itself is never clipped.
    onset = lo + f * hop + n_fft // 2
    return max(0, onset - int(margin * sr))


def _band_rise_db(y: np.ndarray, sr: int, frames: np.ndarray, look: int, n_bands: int = 4) -> np.ndarray:
    """Largest energy increase (dB) across frequency bands around each onset frame."""
    mel = librosa.feature.melspectrogram(y=y, sr=sr, n_fft=HOP * 4, hop_length=HOP, n_mels=16 * n_bands)
    bands = mel.reshape(n_bands, 16, -1).sum(axis=1)
    db = 10 * np.log10(bands + bands.max() * 1e-10)
    out = np.zeros(len(frames))
    for i, f in enumerate(frames):
        pre = db[:, max(0, f - 2 * look):max(1, f - 2)]
        post = db[:, f:f + look]
        if post.shape[1]:
            out[i] = float(np.max(post.max(axis=1) - pre.min(axis=1)))
    return out


def slices_from_onsets(
    onsets: np.ndarray,
    duration: float,
    max_len: float | None = None,
    min_len: float = 0.03,
) -> list[dict]:
    """Each slice runs from its onset to the next one (or max_len / end of track)."""
    out = []
    for i, start in enumerate(onsets):
        end = onsets[i + 1] if i + 1 < len(onsets) else duration
        if max_len:
            end = min(end, start + max_len)
        if end - start >= min_len:
            out.append({"id": new_id(), "start": round(float(start), 4), "end": round(float(end), 4)})
    return out


class _FolderSink:
    """Same writestr() as a ZipFile, writing plain files under a folder."""

    def __init__(self, root: Path):
        self.root = root

    def writestr(self, name: str, data: bytes) -> None:
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, *exc):
        return False


def _sink(out_dir: Path | None, zip_path: Path):
    return _FolderSink(out_dir) if out_dir else zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED)


def export_slices(
    project_dir: Path,
    tracks: list[dict],
    slices: list[dict],
    fade_in_ms: float = 2.0,
    fade_out_ms: float = 8.0,
    norm: bool = False,
    bpm: float | None = None,
    keys: bool = False,
    progress=None,
    out_dir: Path | None = None,
) -> Path:
    """Cut every slice out of every selected track into one zip, one folder per track.

    bpm:  tag slices at least ~a beat long with the tempo (drums_004_96bpm.wav).
    keys: detect each slice's key / note and add it (bass_012_E1.wav). Slow-ish
          (~35 ms per slice per track), hence the progress callback.
    out_dir: write plain files into this folder instead of a zip (returns the folder).
    """
    if not slices:
        raise ValueError("no slices to export")
    if not tracks:
        raise ValueError("no tracks selected")
    ordered = sorted(slices, key=lambda s: s["start"])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    zip_path = out_dir or project_dir / "exports" / f"slices-{stamp}-{new_id()[:4]}.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    width = max(3, len(str(len(ordered))))
    total, done = len(tracks) * len(ordered), 0
    with _sink(out_dir, zip_path) as zf:
        for t_i, track in enumerate(tracks, 1):
            src = project_dir / track["file"]
            y_full, sr = audio.read(src)
            label = safe_name(track["name"])
            drums = analysis.is_drum_track(track["name"])
            for i, s in enumerate(ordered, 1):
                a, b = int(s["start"] * sr), int(s["end"] * sr)
                raw = y_full[:, a:b]
                key = analysis.key_of(audio.to_mono(raw), sr, drums) if keys and raw.shape[1] else None
                seg = audio.apply_fades(raw, sr, fade_in_ms, fade_out_ms)
                if norm:
                    seg = audio.normalize(seg)
                name = export_name(f"{label}_{i:0{width}d}", bpm, s["end"] - s["start"], key)
                zf.writestr(f"{label}/{name}.wav", audio.wav_bytes24(seg, sr))
                done += 1
                if progress and (done % 10 == 0 or done == total):
                    progress(done / total, f"Track {t_i}/{len(tracks)} ({track['name']}) · slice {i}/{len(ordered)}")
    return zip_path
