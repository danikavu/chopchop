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


def test_onsets_start_at_attack_not_in_silence():
    # Hits separated by long silent gaps: slices must start right at each attack
    # (at most a few ms early), never back in the silence before it.
    truth = [0.3, 1.1, 1.75, 2.9]
    sigs = [synth.kick(), synth.snare(), synth.hat(), synth.kick()]
    y = synth.place(list(zip(truth, sigs)), 3.6)
    y += np.random.default_rng(0).standard_normal(y.size).astype(np.float32) * 1e-4  # noise floor
    onsets = slicing.detect_onsets(y, SR)
    assert len(onsets) == len(truth)
    early = np.array(truth) - onsets
    assert np.all(early >= 0), early  # attack never clipped
    assert np.all(early < 0.006), early


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


def test_kit_counts_occurrences(tmp_path):
    y, _ = synth.drum_loop(bars=4)  # 8 kicks, 8 snares, 16 hats
    (tmp_path / "tracks").mkdir()
    sf.write(tmp_path / "tracks" / "drums.wav", y, SR)
    track = {"name": "drums", "file": "tracks/drums.wav", "duration": y.size / SR}
    recs = kits.extract(tmp_path, track, "drums")
    counts = {r["category"]: r["occurrences"] for r in recs}
    assert counts == {"kick": 8, "snare": 8, "hihat": 16}


def _cand(cat, emb, score, count=1):
    e = np.asarray(emb, dtype=float)
    return {"category": cat, "emb": e / np.linalg.norm(e), "score": score, "count": count}


def test_dedupe_merges_duplicates_across_categories():
    # The same sound labelled snare once and clap once must survive only once.
    cands = [_cand("snare", [1, 0, 0], 0.9), _cand("clap", [1, 0.01, 0], 0.5), _cand("kick", [0, 1, 0], 0.8)]
    kept = kits._dedupe(cands, 0.93, 8)
    assert sorted(k["category"] for k in kept) == ["kick", "snare"]
    assert next(k for k in kept if k["category"] == "snare")["count"] == 2


def test_select_kit_round_robins_types():
    recs = [_cand("kick", [1, i, 0], 0.9 - i / 10) for i in range(5)] + \
           [_cand("snare", [0, 1, i], 0.8) for i in range(3)] + [_cand("hihat", [i, 0, 1], 0.7) for i in range(2)]
    kit = kits.select_kit(recs, 6)
    assert [r["category"] for r in kit] == ["kick", "snare", "hihat", "kick", "snare", "hihat"]
    assert kit[0]["score"] == 0.9  # best kick first


def test_layout_pads_mpc_rows():
    samples = [{"id": f"k{i}", "category": "kick"} for i in range(5)] + \
              [{"id": "s1", "category": "snare"}, {"id": "h1", "category": "hihat"}, {"id": "p1", "category": "perc"}]
    pads = kits.layout_pads(samples)
    assert len(pads) == 64
    assert pads[0:4] == ["k0", "k1", "k2", "k3"]  # bottom row: kicks
    assert pads[4] == "s1" and pads[8] == "h1" and pads[12] == "p1"
    assert "k4" in pads[:16]  # overflow kick still lands in bank A


def test_sound_bits_from_other_ignore_drum_bleed(tmp_path):
    # Chords in some bars, drum bleed at -20 dB everywhere: bits must be chords, not hits.
    drums, _ = synth.groove(bpm=100, bars=8)
    n, beat = len(drums), 0.6
    pad = np.zeros(n, np.float32)
    for bar in (2, 3, 6):
        a, b = int((0.2 + bar * 4 * beat) * SR), int((0.2 + (bar + 1) * 4 * beat) * SR)
        tt = np.arange(b - a) / SR
        pad[a:b] = sum(np.sin(2 * np.pi * 440 * 2 ** ((m - 69) / 12) * tt) for m in (57, 60, 64)) * 0.12 \
            * np.minimum(1, tt / 0.05)
    (tmp_path / "tracks").mkdir()
    sf.write(tmp_path / "tracks" / "other.wav", pad + 0.1 * drums, SR)
    recs = kits.extract(tmp_path, {"name": "other", "file": "tracks/other.wav"}, "chops", max_per_category=16)
    assert recs

    def corr(x, y):
        return float(x @ y / np.sqrt((x @ x) * (y @ y) + 1e-12))

    mix = pad + 0.1 * drums
    for r in recs:
        a, b = int(r["start"] * SR), int(r["end"] * SR)
        assert corr(mix[a:b], pad[a:b]) > corr(mix[a:b], drums[a:b]), f"drum bit at {r['start']:.2f}s"
