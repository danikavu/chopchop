import numpy as np
import pytest

from chopper import tempo

from . import synth
from .synth import SR


def est(**kw):
    y, beats = synth.groove(**kw)
    return tempo.estimate(y, SR), beats


@pytest.mark.parametrize("bpm", [72, 88, 96, 124, 140])
def test_straight_grooves(bpm):
    r, _ = est(bpm=bpm)
    assert r["bpm"] == pytest.approx(bpm, abs=0.3)
    # Slow grooves are honestly less certain: double time (e.g. 72 vs 144) is plausible.
    assert r["confidence"] > (0.8 if 90 <= bpm <= 130 else 0.6)
    assert r["swing"]["percent"] == pytest.approx(50, abs=3)


def test_swung_16ths():
    r, _ = est(bpm=90, hats="16th", swing=62)
    assert r["bpm"] == pytest.approx(90, abs=0.3)
    assert r["swing"]["grid"] == "16th"
    assert r["swing"]["percent"] == pytest.approx(62, abs=3)


def test_triplet_swung_8ths():
    r, _ = est(bpm=100, hats="8th", swing=66)
    assert r["bpm"] == pytest.approx(100, abs=0.3)
    assert r["swing"]["grid"] == "8th"
    assert r["swing"]["percent"] == pytest.approx(66, abs=3)


def test_live_drift_and_jitter():
    r, beats = est(bpm=94, drift_to=99, bars=12, jitter_ms=12, seed=3, ghost=True)
    assert 94 <= r["bpm"] <= 99
    assert r["range"][0] < 95.5 and r["range"][1] > 97.5  # the drift is reported
    assert not r["steady"]
    # Tracked beats follow the drummer: every true beat has a tracked beat nearby.
    tracked = np.array(r["beats"])
    err = [np.min(np.abs(tracked - b)) for b in beats[2:-2]]
    assert np.median(err) < 0.03


def test_quantized_more_confident_than_live():
    tight, _ = est(bpm=96)
    loose, _ = est(bpm=96, drift_to=100, jitter_ms=15, seed=1)
    assert tight["confidence"] > loose["confidence"]


def test_alternatives_offer_half_and_double():
    r, _ = est(bpm=96)
    alts = {round(a["bpm"]) for a in r["alternatives"]}
    assert 192 in alts  # double time; half (48) is below the 60 BPM floor


def test_noise_has_low_confidence():
    y = np.random.default_rng(0).standard_normal(SR * 10).astype(np.float32) * 0.1
    r = tempo.estimate(y, SR)
    assert r["confidence"] < 0.4


def test_too_short():
    r = tempo.estimate(synth.kick(), SR)
    assert r["bpm"] is None and r["confidence"] == 0


def _backbeat_song(pickup_beats: int):
    """Kick on 1 and the 'and' of 3, snare on 2 and 4; starts with `pickup_beats`
    beats of hats only, so the first downbeat is beat index `pickup_beats`."""
    beat = 60 / 100
    events = []
    for i in range(pickup_beats + 32):
        t = 0.2 + i * beat
        events.append((t, synth.hat() * 0.4))
        pos = (i - pickup_beats) % 4
        if i >= pickup_beats:
            if pos == 0:
                events.append((t, synth.kick()))
            if pos == 2:
                events.append((t + beat / 2, synth.kick()))
            if pos in (1, 3):
                events.append((t, synth.snare()))
    return synth.place(events, 0.2 + (pickup_beats + 33) * beat)


@pytest.mark.parametrize("pickup", [0, 1, 3])
def test_downbeat(pickup):
    r = tempo.estimate(_backbeat_song(pickup), SR)
    first = r["beats"][r["downbeat"]]
    assert first == pytest.approx(0.2 + pickup * 0.6, abs=0.03)


def test_rescale_keeps_downbeat_on_the_bar():
    r = {"bpm": 100.0, "beats": [0.0, 0.6, 1.2, 1.8, 2.4, 3.0, 3.6, 4.2], "downbeat": 1, "alternatives": [], "range": None}
    assert tempo.rescale(r, 200.0)["downbeat"] == 2  # same time (0.6 s), finer beats
    assert tempo.rescale(r, 200.0)["beats"][2] == pytest.approx(0.6)
