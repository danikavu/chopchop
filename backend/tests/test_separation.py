import io
from pathlib import Path

import pytest

from chopper import separation

pytest.importorskip("audio_separator.separator")


def test_progress_capture_reports_passes_and_restores_tqdm():
    import tqdm as tqdm_module
    from audio_separator.separator.architectures import mdxc_separator

    original = tqdm_module.tqdm
    events = []
    with separation._ProgressCapture(lambda p, m: events.append((p, m)), "Test GPU") as cap:
        cap.passes = 2
        for _pass in range(2):  # what Demucs does: one bar per shift
            for _ in tqdm_module.tqdm(range(4), file=io.StringIO(), mininterval=0):
                pass
        dl = mdxc_separator.tqdm(total=2_000_000, unit="iB", file=io.StringIO())
        dl.update(1_000_000)
        dl.close()
    assert tqdm_module.tqdm is original and mdxc_separator.tqdm is original

    msgs = [m for _, m in events]
    assert "Separating on Test GPU · pass 1/2 · 0%" in msgs
    assert "Separating on Test GPU · pass 2/2 · 100%" in msgs
    assert "Downloading model · 1/2 MB" in msgs
    progress = [p for p, _ in events]
    assert progress == sorted(progress), "progress must never go backwards"
    assert max(progress) == pytest.approx(separation.SEPARATE[1])


def test_device_info_shape():
    info = separation.device_info()
    assert info["device"] in {"cuda", "mps", "directml", "cpu"} and info["name"]


def test_stem_name_uses_last_group():
    assert separation.stem_name("song_(Vocals)_htdemucs_6s.wav") == "vocals"
    assert separation.stem_name("song_(Instrumental)_vocals_mel_band_roformer_(Drums)_htdemucs_6s.wav") == "drums"
    assert separation.stem_name("drums-ab12_(hh)_MDX23C-DrumSep.wav") == "hh"


def test_debleed_moves_drum_bleed_from_other_into_drums(tmp_path, monkeypatch):
    import numpy as np
    import soundfile as sf

    sr = 44100
    t = np.arange(sr) / sr
    pad = 0.3 * np.sin(2 * np.pi * 220 * t)
    hits = np.zeros(sr)
    hits[::11025] = 1.0
    drums_true = np.convolve(hits, np.exp(-np.arange(2000) / 200))[:sr] * 0.5
    leak = 0.1 * drums_true  # what the separation model left in "other"
    files = {}
    for name, y in {"drums": drums_true - leak, "other": pad + leak, "bass": 0.2 * np.sin(2 * np.pi * 55 * t)}.items():
        files[name] = tmp_path / f"{name}.wav"
        sf.write(files[name], np.stack([y, y], 1), sr, subtype="FLOAT")

    def fake_run(src, out, model, emit):  # a perfect drum model run on the stem
        assert model == separation.DEBLEED_MODEL and Path(src).name == "other.wav"
        out.mkdir(parents=True, exist_ok=True)
        sf.write(out / "x_(Drums).wav", np.stack([leak, leak], 1), sr, subtype="FLOAT")
        return {"drums": out / "x_(Drums).wav"}

    monkeypatch.setattr(separation, "_run_model", fake_run)
    out = separation.debleed(files, tmp_path / "work")
    other = sf.read(out["other"])[0][:, 0]
    drums = sf.read(out["drums"])[0][:, 0]
    assert np.abs(other - pad).max() < 1e-5  # bleed gone from "other"
    assert np.abs(drums - drums_true).max() < 1e-5  # and back in "drums": nothing lost
    assert out["bass"] == files["bass"]  # bass is left alone

    no_drums = {"vocals": files["other"], "instrumental": files["bass"]}
    assert separation.debleed(no_drums, tmp_path / "w2") == no_drums  # nothing to move it to
