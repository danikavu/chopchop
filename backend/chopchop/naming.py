"""Export file names: <name>[_<bpm>bpm][_<key>], e.g. vocal_03_96bpm_Amin, kick_01_F#1."""

from __future__ import annotations

# A sound needs to span at least ~a beat for its tempo to mean anything; one-shots
# (a kick, a hat) don't get a BPM in their name.
MIN_BEATS_FOR_BPM = 0.9


def bpm_tag(bpm: float | None, duration: float) -> str:
    if not bpm or duration < MIN_BEATS_FOR_BPM * 60.0 / bpm:
        return ""
    value = f"{round(bpm):d}" if abs(bpm - round(bpm)) < 0.05 else f"{bpm:.1f}"
    return f"{value}bpm"


def key_tag(key: str | None) -> str:
    """'A min' -> 'Amin', 'F#1' -> 'F#1'."""
    return key.replace(" ", "") if key else ""


def export_name(base: str, bpm: float | None = None, duration: float = 0.0, key: str | None = None) -> str:
    return "_".join(p for p in (base, bpm_tag(bpm, duration), key_tag(key)) if p)
