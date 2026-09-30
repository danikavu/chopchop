"""Tempo (BPM) estimation from a drum track, robust to swing and to live, unquantized
playing.

Pipeline
1. Onset-strength envelope of the drums.
2. Global periodicity: autocorrelation of the envelope, scored at the beat, bar-half
   and bar lags (swing moves off-beat hats around but leaves the quarter-note pulse
   intact, so scoring at beat level and above is swing-proof), times a mild prior
   around common tempos.
3. Local tempo in sliding windows -> stability and drift range (live drummers speed
   up and slow down).
4. Beat tracking locked to that tempo, each beat snapped to the nearest real hit, so
   the grid follows a drifting drummer; the final BPM comes from the tracked beats.
5. Swing: where the off-beat hits fall between tracked beats.
"""

from __future__ import annotations

import librosa
import numpy as np

from .slicing import detect_onsets

MIN_BPM, MAX_BPM = 60.0, 200.0
HOP = 256
WINDOW_S = 8.0  # local tempo window
STEADY_PCT = 1.5  # drift (p10..p90 of local tempo) below this counts as a steady tempo


def _periodicity(env: np.ndarray, fps: float) -> np.ndarray:
    """Normalised autocorrelation of the (mean-removed) onset envelope."""
    x = env - env.mean()
    ac = librosa.autocorrelate(x, max_size=int(fps * 60 / MIN_BPM * 4) + 4)
    return ac / (ac[0] + 1e-12)


def _at(ac: np.ndarray, lag: float) -> float:
    """Autocorrelation at a fractional lag, taking the local max (±1 frame) so small
    timing wobble doesn't hide the peak."""
    if lag + 2 >= len(ac):
        return 0.0
    i = int(round(lag))
    return float(max(ac[i - 1], ac[i], ac[i + 1]))


def _score(ac: np.ndarray, fps: float, bpm: float) -> float:
    """Evidence for a beat period: periodicity at 1, 2 and 4 beats."""
    lag = 60.0 * fps / bpm
    return _at(ac, lag) + 0.5 * _at(ac, 2 * lag) + 0.25 * _at(ac, 4 * lag)


def _prior(bpm: float) -> float:
    """Mild preference for everyday tempos (log-normal around 110 BPM, ~1 octave wide)."""
    return float(np.exp(-0.5 * (np.log2(bpm / 110.0) / 1.0) ** 2))


GRID = np.arange(MIN_BPM, MAX_BPM + 0.01, 0.1)


def _best(ac: np.ndarray, fps: float, lo=MIN_BPM, hi=MAX_BPM, prior=True) -> tuple[float, np.ndarray]:
    grid = GRID[(GRID >= lo) & (GRID <= hi)]
    scores = np.array([_score(ac, fps, b) * (_prior(b) if prior else 1.0) for b in grid])
    return float(grid[int(np.argmax(scores))]), scores


def _related(a: float, b: float, tol=0.04) -> bool:
    """True if b is a in a different metrical level (x1/3 … x3)."""
    return any(abs(b / (a * r) - 1) < tol for r in (1 / 3, 0.5, 2 / 3, 1, 1.5, 2, 3))


def _snap_beats(beats: np.ndarray, onsets: np.ndarray, tol: float) -> np.ndarray:
    """Move each tracked beat onto the nearest real hit within tol seconds."""
    if onsets.size == 0:
        return beats
    idx = np.clip(np.searchsorted(onsets, beats), 1, len(onsets) - 1)
    near = np.where(np.abs(onsets[idx] - beats) < np.abs(onsets[idx - 1] - beats), onsets[idx], onsets[idx - 1])
    hit = np.abs(near - beats) <= tol
    if not hit.any():
        return beats
    # Tracked beats sit on onset-envelope peaks, a little after the attacks. Beats with
    # no hit on them (a kick pushed off the beat, a rest) get the same average shift,
    # so they don't look like timing jitter next to their snapped neighbours.
    shift = float(np.median(near[hit] - beats[hit]))
    return np.where(hit, near, beats + shift)


def _grid_period(beats: np.ndarray, period: float) -> float:
    """Beat period from a straight-line fit of beat time vs beat number, with outlier
    rejection: sub-frame precision for steady tempos, and a stray or skipped beat
    can't bend the line (beats are numbered by grid position, not list order)."""
    n = np.round((beats - beats[0]) / period)
    keep = np.ones(len(beats), bool)
    for _ in range(2):
        slope, icpt = np.polyfit(n[keep], beats[keep], 1)
        resid = np.abs(beats - (slope * n + icpt))
        keep = resid < max(0.02, 3 * np.median(resid[keep]))
        if keep.sum() < 4:
            break
    return float(slope)


def estimate_downbeat(y: np.ndarray, sr: int, beats: np.ndarray, meter: int = 4) -> int:
    """Index (0..meter-1) of the first downbeat in ``beats``, assuming 4/4: the phase
    where kicks land on beat 1 (and 3) and snares on the backbeats 2 and 4."""
    if len(beats) < meter * 2:
        return 0
    mel_f = librosa.mel_frequencies(n_mels=66, fmax=sr / 2)[1:-1]
    lo, hi = int(np.searchsorted(mel_f, 150)), int(np.searchsorted(mel_f, 5000))
    strength = librosa.onset.onset_strength_multi(y=y, sr=sr, hop_length=HOP, n_mels=64,
                                                  channels=[0, lo, hi])
    kick, snare = strength[0], strength[1]
    frames = librosa.time_to_frames(beats, sr=sr, hop_length=HOP)

    def at(env, idx):
        vals = [env[max(0, f - 2):f + 3].max() for f in frames[idx::meter] if f < len(env)]
        return float(np.mean(vals)) if vals else 0.0

    k = [at(kick, p) / (kick.max() + 1e-9) for p in range(meter)]
    s = [at(snare, p) / (snare.max() + 1e-9) for p in range(meter)]
    scores = [k[p] + 0.5 * k[(p + 2) % meter] + s[(p + 1) % meter] + s[(p + 3) % meter]
              - 0.5 * s[p] for p in range(meter)]
    return int(np.argmax(scores))


def _timing_tightness(beats: np.ndarray) -> float:
    """0..1: how tight beat timing is. Uses beat-to-beat acceleration (second
    difference), which ignores slow tempo drift but catches human jitter; for timing
    noise of s, that difference has std sqrt(6)*s. ~1 when quantized, ~0.4 at 15 ms."""
    if len(beats) < 8:
        return 0.0
    acc = np.diff(beats, 2)
    jitter = np.median(np.abs(acc - np.median(acc))) / 0.6745 / np.sqrt(6)
    return float(np.clip(1 - jitter / 0.025, 0, 1))


def estimate_swing(onsets: np.ndarray, beats: np.ndarray) -> dict:
    """MPC-style swing %: where the off-beat hits sit between beats.

    16th swing: the 2nd 16th of each beat (straight at 25% of the beat) is delayed;
    percent = its position within the first 8th (50 = straight, 66 = triplet).
    8th swing:  the off-beat 8th (straight at 50%); percent = its position in the beat.
    """
    if len(beats) < 4 or onsets.size < 8:
        return {"percent": None, "grid": None}
    i = np.searchsorted(beats, onsets, side="right") - 1
    ok = (i >= 0) & (i < len(beats) - 1)
    i, t = i[ok], onsets[ok]
    phase = (t - beats[i]) / (beats[i + 1] - beats[i])
    sixteenths = phase[(phase > 0.15) & (phase < 0.42)]
    eighths = phase[(phase > 0.42) & (phase < 0.8)]
    enough = max(4, 0.08 * len(phase))
    if len(sixteenths) >= enough:
        return {"percent": round(float(np.median(sixteenths)) / 0.5 * 100, 1), "grid": "16th"}
    if len(eighths) >= enough:
        return {"percent": round(float(np.median(eighths)) * 100, 1), "grid": "8th"}
    return {"percent": None, "grid": None}


def rescale(result: dict, bpm: float) -> dict:
    """Apply a user-chosen tempo to a detection result, keeping the beat grid aligned:
    x2 adds the in-between beats, ÷2 keeps every other one, and any other tempo lays
    a steady grid from the first beat."""
    old = result.get("bpm") or bpm
    beats = np.array(result.get("beats") or [])
    ratio = bpm / old
    if beats.size >= 2 and abs(ratio - 2) < 0.01:
        mids = (beats[:-1] + beats[1:]) / 2
        beats = np.sort(np.concatenate([beats, mids]))
    elif beats.size >= 2 and abs(ratio - 0.5) < 0.01:
        beats = beats[::2]
    elif beats.size >= 2:
        beats = np.arange(beats[0], beats[-1] + 1e-6, 60.0 / bpm)
    rng = result.get("range")
    down = int(result.get("downbeat") or 0)
    down = down * 2 if abs(ratio - 2) < 0.01 else down // 2 if abs(ratio - 0.5) < 0.01 else 0
    return {
        **result,
        "downbeat": down % 4,
        "bpm": round(float(bpm), 1),
        "range": [round(r * ratio, 1) for r in rng] if rng else None,
        "beats": [round(float(b), 4) for b in beats],
        "alternatives": [{**a, "bpm": round(a["bpm"] * ratio, 1)} for a in result.get("alternatives", [])],
        "source": "manual",
    }


def _empty() -> dict:
    return {"bpm": None, "confidence": 0.0, "components": {}, "range": None, "steady": None,
            "alternatives": [], "beats": [], "downbeat": 0, "swing": {"percent": None, "grid": None}}


def estimate(y: np.ndarray, sr: int) -> dict:
    """Estimate tempo from mono drum audio."""
    if len(y) < sr * 3:
        return _empty()
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    fps = sr / HOP
    if env.max() <= 1e-6:
        return _empty()
    ac = _periodicity(env, fps)

    # --- global tempo
    bpm0, scores = _best(ac, fps)
    raw = {round(b, 1): s for b, s in zip(GRID, scores)}
    best = max(scores)
    unrelated = [s for b, s in zip(GRID, scores) if not _related(bpm0, b)]
    clarity = float(np.clip((best - max(unrelated, default=0)) / (best + 1e-9) * 2, 0, 1)) if best > 0 else 0.0

    alternatives = []
    for r in (0.5, 2.0, 2 / 3, 1.5):
        b = bpm0 * r
        if MIN_BPM <= b <= MAX_BPM:
            s = _score(ac, fps, b) * _prior(b)
            alternatives.append({"bpm": round(b, 1), "relation": {0.5: "half", 2.0: "double", 2 / 3: "×2/3",
                                                                     1.5: "×3/2"}[r],
                                 "strength": round(float(s / (best + 1e-9)), 2)})
    alternatives.sort(key=lambda a: -a["strength"])
    rival = max((a["strength"] for a in alternatives if a["relation"] in ("half", "double")), default=0.0)
    octave = float(np.clip((1 - rival) * 2, 0, 1))

    # --- local tempo (sliding windows) -> stability and drift
    win, step = int(WINDOW_S * fps), int(2.0 * fps)
    local, agree = [], []
    for start in range(0, max(1, len(env) - win + 1), step):
        seg = env[start:start + win]
        if seg.max() <= 1e-6 or len(seg) < win // 2:
            continue
        lac = _periodicity(seg, fps)
        free, _ = _best(lac, fps)
        agree.append(_related(bpm0, free, tol=0.03) and _score(lac, fps, bpm0) > 0.1)
        near, _ = _best(lac, fps, bpm0 * 0.9, bpm0 * 1.1, prior=False)
        local.append(near)
    stability = float(np.mean(agree)) if agree else 0.5

    # --- beats locked to the tempo, snapped to real hits
    onsets = detect_onsets(y, sr, sensitivity=0.5)
    _, frames = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=HOP, bpm=bpm0,
                                        tightness=80, trim=False)
    beats = librosa.frames_to_time(frames, sr=sr, hop_length=HOP)
    tol = 0.06 * 120 / bpm0
    if onsets.size:  # no beats in leading / trailing silence
        beats = beats[(beats >= onsets[0] - tol) & (beats <= onsets[-1] + tol)]
    on_grid = 0.0
    if len(beats) >= 4 and onsets.size >= 2:
        idx = np.clip(np.searchsorted(onsets, beats), 1, len(onsets) - 1)
        dist = np.minimum(np.abs(onsets[idx] - beats), np.abs(onsets[idx - 1] - beats))
        on_grid = float(np.mean(dist <= tol))  # beats that land on an actual hit
    beats = _snap_beats(beats, onsets, tol=tol)
    timing = _timing_tightness(beats)

    # --- final BPM from the tracked beats
    bpm = bpm0
    steady = True
    rng = None
    if len(beats) >= 8:
        ibi = np.diff(beats)
        ok = np.abs(ibi / np.median(ibi) - 1) < 0.2  # skip gaps / glitches
        if ok.sum() >= 6:
            per_beat = 60 / ibi[ok]
            smooth = np.convolve(per_beat, np.ones(4) / 4, mode="valid") if len(per_beat) > 8 else per_beat
            lo, hi = np.percentile(smooth, [10, 90])
            rng = [round(float(lo), 1), round(float(hi), 1)]
            steady = (hi - lo) / np.median(smooth) * 100 < STEADY_PCT
            if steady:
                bpm = 60 / _grid_period(beats, float(np.median(ibi)))
            else:
                bpm = float(np.median(per_beat))
    elif local:
        rng = [round(float(np.percentile(local, 10)), 1), round(float(np.percentile(local, 90)), 1)]

    components = {"clarity": round(clarity, 2), "stability": round(stability, 2),
                  "on_grid": round(on_grid, 2), "timing": round(timing, 2), "octave": round(octave, 2)}
    # Half/double doubt scales the whole score rather than adding to it, so it can't
    # prop up a track with no clear tempo at all.
    confidence = (0.3 * clarity + 0.25 * stability + 0.2 * on_grid + 0.25 * timing) * (0.75 + 0.25 * octave)
    return {
        "bpm": round(float(bpm), 1),
        "confidence": round(float(confidence), 2),
        "components": components,
        "range": rng,
        "steady": bool(steady),
        "alternatives": alternatives,
        "beats": [round(float(b), 4) for b in beats],
        "downbeat": estimate_downbeat(y, sr, beats),
        "swing": estimate_swing(onsets, beats),
    }
