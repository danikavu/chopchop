import io

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
