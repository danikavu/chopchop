"""Transpose keyed samples to C, for kits that drop straight into a project in C.

Two ways, as on a sampler:
  repitched  resample, like playing the sample on another key: length and tempo change
             (a loop's BPM in its name is updated).
  warped     pitch-shift keeping the length (phase vocoder): tempo stays.
"""

from __future__ import annotations

import re
import warnings

import librosa
import numpy as np

from .analysis import NOTE_NAMES

MODES = ("repitched", "warped")
FOLDERS = {"repitched": "In C (repitched)", "warped": "In C (warped)"}

_FLATS = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#", "Cb": "B", "Fb": "E"}
_KEY = re.compile(r"^\s*([A-Ga-g])([#b]?)\s*(-?\d+)?\s*(maj|min|m)?\s*$", re.IGNORECASE)


def parse(key: str | None) -> tuple[int, int | None, str | None] | None:
    """'F#1' -> (6, 1, None), 'A min' -> (9, None, 'min'), 'Bb maj' -> (10, None, 'maj')."""
    m = _KEY.match(key or "")
    if not m:
        return None
    letter, acc, octave, quality = m.groups()
    name = letter.upper() + acc
    name = _FLATS.get(name, name)
    if name not in NOTE_NAMES:  # B#, E#
        name = NOTE_NAMES[(NOTE_NAMES.index(letter.upper()) + 1) % 12]
    quality = {"m": "min"}.get((quality or "").lower(), (quality or "").lower() or None)
    return NOTE_NAMES.index(name), int(octave) if octave else None, quality


def steps_to_c(key: str | None) -> int | None:
    """Semitones to the nearest C (-6..+5); None if the key is unknown or already C."""
    p = parse(key)
    if not p or p[0] == 0:
        return None
    up = (12 - p[0]) % 12
    return up if up < 6 else up - 12


def key_in_c(key: str) -> str:
    """The key after transposing: 'F#1' -> 'C2' (nearest C), 'A min' -> 'C min'."""
    pc, octave, quality = parse(key)
    if octave is not None:
        midi = (octave + 1) * 12 + pc + steps_to_c(key)
        return f"C{midi // 12 - 1}"
    return f"C {quality}" if quality else "C"


def shift(y: np.ndarray, sr: int, steps: int, mode: str) -> np.ndarray:
    """y (channels, n) moved by steps semitones."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if mode == "warped":
            out = librosa.effects.pitch_shift(y, sr=sr, n_steps=steps)
        elif mode == "repitched":
            # Played back faster / slower: resample from a pretend rate, keep sr.
            out = librosa.resample(y, orig_sr=sr * 2 ** (steps / 12), target_sr=sr)
        else:
            raise ValueError(f"unknown transpose mode: {mode}")
    return np.ascontiguousarray(out, dtype=np.float32)


def modes(option: str | None) -> tuple[str, ...]:
    """API option -> modes: 'none' | 'repitched' | 'warped' | 'both'."""
    if option in (None, "", "none"):
        return ()
    if option == "both":
        return MODES
    if option in MODES:
        return (option,)
    raise ValueError(f"unknown transpose option: {option}")
