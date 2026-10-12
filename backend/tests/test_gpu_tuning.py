import json
import types

import pytest
import torch

from chopchop import separation

GB = 1024 ** 3


@pytest.fixture
def cuda(monkeypatch, tmp_path):
    """Pretend there's a 6 GB NVIDIA card, with a fresh data folder."""
    monkeypatch.setenv("CHOPCHOP_DATA", str(tmp_path))
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda *a: (5.3 * GB, 6 * GB))
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: None)
    monkeypatch.setattr(torch.cuda, "get_device_name", lambda *a: "Test RTX")
    monkeypatch.delenv("CHOPCHOP_GPU_BATCH", raising=False)
    monkeypatch.delenv("CHOPCHOP_FP16", raising=False)
    return tmp_path


def test_defaults_are_audio_separators_own(cuda):
    for model in ("Kim_Vocal_2.onnx", "MDX23C-DrumSep-aufr33-jarredou.ckpt", "htdemucs_ft.yaml"):
        t = separation.gpu_tuning(model)
        assert (t["batch"], t["fp16"], t["label"]) == (1, False, "")


def test_saved_settings_per_architecture_and_overrides(cuda, monkeypatch):
    separation.save_gpu_settings({"mdxc": {"batch": 3, "fp16": True}, "mdx": {"batch": 8, "fp16": False}})
    assert separation.gpu_tuning("vocals_mel_band_roformer.ckpt")["label"] == "batch 3 · fp16"
    assert separation.gpu_tuning("Kim_Vocal_2.onnx")["batch"] == 8
    assert separation.gpu_tuning("htdemucs_ft.yaml")["batch"] == 1  # Demucs: never tuned
    monkeypatch.setenv("CHOPCHOP_GPU_BATCH", "2")
    monkeypatch.setenv("CHOPCHOP_FP16", "0")
    t = separation.gpu_tuning("vocals_mel_band_roformer.ckpt")
    assert (t["batch"], t["fp16"]) == (2, False)


def test_auto_batch_candidates(cuda):
    assert (separation.auto_batch("mdxc"), separation.auto_batch("mdx")) == (3, 8)


def test_cpu_and_apple_untouched(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert separation.gpu_tuning("Kim_Vocal_2.onnx")["batch"] == 1


class FakeSeparator:
    """Records what ChopChop passes; runs out of memory above `limit` on the GPU."""

    created: list = []
    limit = 99

    def __init__(self, **kw):
        self.kw = kw
        self.batch = (kw.get("mdxc_params") or kw.get("mdx_params") or {}).get("batch_size", 1)
        self.torch_device = types.SimpleNamespace(type="cuda", index=0)
        self.torch_device_cpu = types.SimpleNamespace(type="cpu", index=None)
        self.model_instance = None
        FakeSeparator.created.append(self)

    def load_model(self, model_filename):
        self.out = self.kw["output_dir"]

    def separate(self, src):
        if self.torch_device.type != "cpu" and self.batch > FakeSeparator.limit:
            raise RuntimeError("CUDA out of memory. Tried to allocate 1.20 GiB")
        from pathlib import Path

        out = Path(self.out) / "song_(Vocals)_m.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"")
        return [str(out)]


@pytest.fixture
def fake(monkeypatch):
    import audio_separator.separator as pkg

    FakeSeparator.created = []
    FakeSeparator.limit = 99
    monkeypatch.setattr(pkg, "Separator", FakeSeparator)
    return FakeSeparator


def test_demucs_gets_no_tuning_parameters(cuda, fake, tmp_path):
    separation.save_gpu_settings({"mdxc": {"batch": 4, "fp16": True}, "mdx": {"batch": 4, "fp16": True}})
    separation._run_model(tmp_path / "in.wav", tmp_path / "o", "htdemucs_ft.yaml", lambda p, m: None)
    kw = fake.created[-1].kw
    assert not {"use_autocast", "mdx_params", "mdxc_params"} & set(kw)


def test_out_of_memory_halves_then_falls_back_loudly(cuda, fake, tmp_path):
    separation.save_gpu_settings({"mdxc": {"batch": 8, "fp16": False}})
    fake.limit = 2
    separation._run_model(tmp_path / "in.wav", tmp_path / "o", "MDX23C-DrumSep-aufr33-jarredou.ckpt", lambda p, m: None)
    assert [s.batch for s in fake.created] == [8, 4, 2]  # halved until it fits, still on the GPU

    fake.created.clear()
    fake.limit = 0  # doesn't fit at all
    msgs = []
    separation._run_model(tmp_path / "in.wav", tmp_path / "o2", "MDX23C-DrumSep-aufr33-jarredou.ckpt",
                          lambda p, m: msgs.append(m))
    assert [s.batch for s in fake.created][:4] == [8, 4, 2, 1]
    assert any(m.startswith("GPU failed (CUDA out of memory") and "finishing on CPU" in m for m in msgs)
    runs = [json.loads(line) for line in (cuda / "perf.log").read_text().splitlines()]
    assert runs[-1]["fallback"].startswith("CUDA out of memory") and runs[-1]["seconds"] >= 0
    assert runs[0]["fallback"] is None and runs[0]["batch"] == 2


def _bench(tmp_path, capsys, monkeypatch, cost):
    """Run `chopchop benchmark` on a fake CUDA card where cost(batch, fp16) is the run time."""
    import time as _time

    import soundfile as sf

    from chopchop import cli

    from . import synth

    monkeypatch.setattr(separation, "status", lambda: {"available": True, "reason": None})
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda *a: (7, 5))
    monkeypatch.setattr(separation, "device_info", lambda: {"name": "Test RTX · 6 GB"})

    def slow_separate(self, src):
        _time.sleep(cost(self.batch, self.kw.get("use_autocast", False)))  # may raise, like a real model
        return FakeSeparator.separate(self, src)

    import audio_separator.separator as pkg

    class Timed(FakeSeparator):
        separate = slow_separate

    monkeypatch.setattr(FakeSeparator, "limit", 99)  # plenty of memory: no out-of-memory fallbacks

    monkeypatch.setattr(pkg, "Separator", Timed)
    song = tmp_path / "song.wav"
    sf.write(song, synth.groove(bpm=100, bars=2)[0], synth.SR)
    code = cli.main(["benchmark", str(song), "--models", "drumsep", "--seconds", "3"])
    return code, capsys.readouterr().out


def test_benchmark_saves_a_clearly_faster_setting(cuda, capsys, monkeypatch, tmp_path):
    code, out = _bench(tmp_path, capsys, monkeypatch, lambda batch, fp16: 0.3 / batch * (0.6 if fp16 else 1))
    assert code == 0
    assert "batch 3  fp16" in out and "Result: Roformer / MDX23C is fastest with batch 3 + fp16" in out
    assert separation.gpu_settings() == {"mdxc": {"batch": 3, "fp16": True}}


def test_benchmark_keeps_default_when_bigger_batches_spill(cuda, capsys, monkeypatch, tmp_path):
    # Windows' sysmem fallback: an oversized batch doesn't error, it just crawls.
    code, out = _bench(tmp_path, capsys, monkeypatch, lambda batch, fp16: 0.15 if batch == 1 else 0.5)
    assert code == 0 and "keeping it" in out
    assert separation.gpu_settings() == {}


def test_fp16_overflow_retries_in_fp32_on_the_gpu(cuda, fake, tmp_path, monkeypatch):
    from audio_separator.separator.exceptions import InvalidAudioDataError

    separation.save_gpu_settings({"mdxc": {"batch": 1, "fp16": True}})
    real = FakeSeparator.separate

    def overflow(self, src):
        if self.kw.get("use_autocast"):
            raise InvalidAudioDataError("Audio data must contain only finite values")
        return real(self, src)

    monkeypatch.setattr(FakeSeparator, "separate", overflow)
    msgs = []
    separation._run_model(tmp_path / "in.wav", tmp_path / "o", "MDX23C-DrumSep-aufr33-jarredou.ckpt",
                          lambda p, m: msgs.append(m))
    used = [(s.kw["use_autocast"], s.torch_device.type) for s in fake.created]
    assert used == [(True, "cuda"), (False, "cuda")]  # same GPU, full precision; no CPU fallback
    assert "Half precision gave invalid audio, retrying in full precision…" in msgs
    assert separation.LAST_RUN["fp16_failed"] and separation.LAST_RUN["fallback"] is None


def test_benchmark_survives_a_setting_that_breaks_the_model(cuda, capsys, monkeypatch, tmp_path):
    from audio_separator.separator.exceptions import InvalidAudioDataError

    def cost(batch, fp16):
        if fp16:
            raise InvalidAudioDataError("Audio data must contain only finite values")
        return 0.3 / batch

    code, out = _bench(tmp_path, capsys, monkeypatch, cost)
    assert code == 0
    assert "fp16 gave invalid audio; ran in fp32" in out  # reported, not a crash
    assert separation.gpu_settings() == {"mdxc": {"batch": 3, "fp16": False}}  # never picks fp16
