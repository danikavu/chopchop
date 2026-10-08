import types

import pytest
import torch

from chopper import separation

GB = 1024 ** 3


@pytest.fixture
def cuda(monkeypatch):
    """Pretend there's an NVIDIA card; returns a setter for (free GB, compute capability)."""
    state = {"free": 5.3, "cc": (7, 5)}
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda *a: (state["free"] * GB, 6 * GB))
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda *a: state["cc"])
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: None)
    monkeypatch.delenv("CHOPPER_GPU_BATCH", raising=False)
    monkeypatch.delenv("CHOPPER_FP16", raising=False)
    return state


def test_six_gb_rtx_card(cuda):
    t = separation.gpu_tuning()
    assert (t["mdxc_batch"], t["mdx_batch"], t["fp16"]) == (3, 8, True)
    assert "batch 3/8" in t["label"] and "fp16" in t["label"]


def test_big_card_is_capped_and_gtx_skips_fp16(cuda):
    cuda.update(free=22.0, cc=(6, 1))  # e.g. a 24 GB card, but Pascal-era (GTX 10xx) compute
    t = separation.gpu_tuning()
    assert (t["mdxc_batch"], t["mdx_batch"], t["fp16"]) == (8, 16, False)


def test_nearly_full_card_and_overrides(cuda, monkeypatch):
    cuda["free"] = 1.2
    assert separation.gpu_tuning()["mdxc_batch"] == 1
    monkeypatch.setenv("CHOPPER_GPU_BATCH", "6")
    monkeypatch.setenv("CHOPPER_FP16", "0")
    t = separation.gpu_tuning()
    assert (t["mdxc_batch"], t["mdx_batch"], t["fp16"]) == (6, 6, False)


def test_cpu_and_apple_stay_at_batch_one(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert separation.gpu_tuning() == {"mdx_batch": 1, "mdxc_batch": 1, "fp16": False, "label": ""}
    assert separation.gpu_tuning(force_cpu=True)["mdxc_batch"] == 1


def test_out_of_memory_halves_the_batch_then_falls_back_to_cpu(cuda, tmp_path, monkeypatch):
    calls = []

    class FakeSeparator:
        def __init__(self, **kw):
            self.batch = kw["mdxc_params"]["batch_size"]
            self.fp16 = kw["use_autocast"]
            self.torch_device = types.SimpleNamespace(type="cuda", index=0)
            self.torch_device_cpu = types.SimpleNamespace(type="cpu", index=None)
            self.model_instance = None

        def load_model(self, model_filename):
            pass

        def separate(self, src):
            on_cpu = self.torch_device.type == "cpu"
            calls.append("cpu" if on_cpu else self.batch)
            if not on_cpu and self.batch > limit:
                raise RuntimeError("CUDA out of memory. Tried to allocate 1.20 GiB")
            out = tmp_path / "song_(Vocals)_m.wav"
            out.write_bytes(b"")
            return [str(out)]

    import audio_separator.separator as pkg

    monkeypatch.setattr(pkg, "Separator", FakeSeparator)
    monkeypatch.setattr(torch.cuda, "get_device_name", lambda *a: "Test RTX")
    cuda["free"] = 9.5  # starts at batch 7
    limit = 2
    separation._run_model(tmp_path / "in.wav", tmp_path / "o", "Kim_Vocal_2.onnx", lambda p, m: None)
    assert calls == [7, 3, 1]  # halved until it fits; still on the GPU

    calls.clear()
    limit = 0  # doesn't even fit at batch 1: finish on CPU
    separation._run_model(tmp_path / "in.wav", tmp_path / "o2", "Kim_Vocal_2.onnx", lambda p, m: None)
    assert calls == [7, 3, 1, "cpu"]
