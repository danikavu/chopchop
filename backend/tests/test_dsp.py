import numpy as np
import pytest
import soundfile as sf

from chopper import analysis, kits, slicing

from . import synth
from .synth import SR


def test_detect_onsets_matches_loop():
    y, truth = synth.drum_loop(bars=2)
    onsets = slicing.detect_onsets(y, SR, sensitivity=0.5)
    assert len(onsets) == len(truth)
    assert np.max(np.abs(onsets - np.array(truth))) < 0.02


def test_sensitivity_and_gate():
    quiet_hat = synth.hat() * 0.02  # ~ -34 dB below the kick
    y = synth.place([(0.1, synth.kick()), (0.6, quiet_hat), (1.0, synth.kick())], 1.6)
    assert len(slicing.detect_onsets(y, SR, sensitivity=0.9, gate_db=-60)) == 3
    assert len(slicing.detect_onsets(y, SR, sensitivity=0.9, gate_db=-20)) == 2


def test_slices_from_onsets_respects_max_len():
    s = slicing.slices_from_onsets(np.array([0.0, 1.0, 3.0]), 5.0, max_len=0.5)
    assert [(x["start"], x["end"]) for x in s] == [(0.0, 0.5), (1.0, 1.5), (3.0, 3.5)]


@pytest.mark.parametrize("sig,expected", [
    (synth.kick(), "kick"),
    (synth.snare(), "snare"),
    (synth.hat(), "hihat"),
])
def test_classify_drum(sig, expected):
    cat, _ = analysis.classify_drum(analysis.features(sig, SR))
    assert cat == expected


def test_detect_note():
    note, hz, conf = analysis.detect_note(synth.tone(57), SR)  # A3
    assert note == "A3"
    assert abs(hz - 220) < 3


def test_detect_key_minor_progression():
    # A minor: Am - Dm - Em - Am arpeggios
    chords = [(57, 60, 64), (62, 65, 69), (64, 67, 71), (57, 60, 64)]
    events = []
    for i, ch in enumerate(chords):
        for j, m in enumerate(ch):
            events.append((i * 1.0 + j * 0.25, synth.tone(m, 0.9)))
    y = synth.place(events, 4.5)
    key, conf = analysis.detect_key(y, SR)
    assert key in ("A min", "C maj")  # relative keys are indistinguishable without context


def test_drum_kit_keeps_one_of_each(tmp_path):
    y, _ = synth.drum_loop(bars=4)
    (tmp_path / "tracks").mkdir()
    sf.write(tmp_path / "tracks" / "drums.wav", y, SR)
    track = {"name": "drums", "file": "tracks/drums.wav", "duration": y.size / SR}
    recs = kits.extract(tmp_path, track, "drums", similarity=0.93, max_per_category=8)
    cats = sorted(r["category"] for r in recs)
    # Identical repeated hits collapse to one sample per type.
    assert cats == ["hihat", "kick", "snare"]
    kick = next(r for r in recs if r["category"] == "kick")
    assert kick["key"] is not None
    assert next(r for r in recs if r["category"] == "hihat")["key"] is None


def test_distinct_hits_are_kept(tmp_path):
    events = [(0.0, synth.kick()), (0.5, synth.kick()), (1.0, synth.tone(40, 0.4) * 0 + synth.kick(0.4) * 0.8)]
    # Second kick variant: much longer decay, different pitch sweep.
    t = np.arange(int(0.9 * SR)) / SR
    boom = np.sin(2 * np.pi * np.cumsum(35 + 60 * np.exp(-t * 12)) / SR) * np.exp(-t * 3) * 0.9
    events.append((1.5, boom.astype(np.float32)))
    y = synth.place(events, 3.0)
    (tmp_path / "tracks").mkdir()
    sf.write(tmp_path / "tracks" / "drums.wav", y, SR)
    track = {"name": "drums", "file": "tracks/drums.wav", "duration": 3.0}
    recs = kits.extract(tmp_path, track, "drums", similarity=0.93)
    assert [r["category"] for r in recs].count("kick") == 2
