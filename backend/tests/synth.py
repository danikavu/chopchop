"""Synthetic test sounds with known ground truth."""

import numpy as np
from scipy.signal import butter, sosfilt

SR = 44100


def _t(dur):
    return np.arange(int(dur * SR)) / SR


def _fade(y, ms=10):
    """Fade the tail so the synthetic hit doesn't end in a click."""
    n = min(y.size, int(SR * ms / 1000))
    y[-n:] *= np.linspace(1, 0, n)
    return y


def kick(dur=0.4, seed=0):
    t = _t(dur)
    f = 45 + 110 * np.exp(-t * 30)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)
    return _fade(y * 0.9).astype(np.float32)


def snare(dur=0.25, seed=1):
    rng = np.random.default_rng(seed)
    t = _t(dur)
    noise = sosfilt(butter(4, [1500, 9000], "bandpass", fs=SR, output="sos"), rng.standard_normal(t.size))
    body = np.sin(2 * np.pi * 190 * t) * np.exp(-t * 30) * 0.3
    y = noise / np.abs(noise).max() * np.exp(-t * 18) + body
    return _fade(y / np.abs(y).max() * 0.7).astype(np.float32)


def hat(dur=0.06, seed=2):
    rng = np.random.default_rng(seed)
    t = _t(dur)
    noise = sosfilt(butter(4, 7000, "highpass", fs=SR, output="sos"), rng.standard_normal(t.size))
    y = noise / np.abs(noise).max() * np.exp(-t * 70)
    return _fade(y * 0.5).astype(np.float32)


def tone(midi, dur=0.8):
    t = _t(dur)
    hz = 440 * 2 ** ((midi - 69) / 12)
    y = sum(np.sin(2 * np.pi * hz * k * t) / k for k in (1, 2, 3)) * np.exp(-t * 2)
    return _fade(y / np.abs(y).max() * 0.6).astype(np.float32)


def place(events, total):
    """events: list of (time_s, signal)."""
    y = np.zeros(int(total * SR), dtype=np.float32)
    for at, sig in events:
        a = int(at * SR)
        n = min(sig.size, y.size - a)
        y[a:a + n] += sig[:n]
    return y


def drum_loop(bars=4, bpm=120):
    """Kick on 1&3, snare on 2&4, hats on every 8th. Returns (audio, onset times)."""
    beat = 60 / bpm
    events, times = [], []
    for b in range(bars * 4):
        at = b * beat
        events.append((at, kick() if b % 2 == 0 else snare()))
        events.append((at + beat / 2, hat()))
        times += [at, at + beat / 2]
    return place(events, bars * 4 * beat + 0.5), sorted(times)
