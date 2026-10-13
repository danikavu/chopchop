from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chopchop import models, separation
from chopchop.app import create_app
from chopchop.store import Store

from .test_gpu_tuning import FakeSeparator, cuda, fake  # noqa: F401  (fixtures)


class LoadingSeparator(FakeSeparator):
    """Also goes through audio-separator's lookup, as the real load_model does."""

    def load_model(self, model_filename):
        super().load_model(model_filename)
        self.loaded = self.download_model_files(model_filename)
        if self.loaded[1] == "MDX" and self.loaded[4] is None:
            self.model_data = self.load_model_data_using_hash(self.loaded[3])

    def download_model_files(self, name):  # the real one only knows its catalog
        raise ValueError(f"Model file {name} not found in supported model files")

    def separate(self, src):
        out = Path(self.out) / "song_(Other)_m.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"")
        return [str(out)]


CATALOG = [
    {"arch": "vr", "name": "VR Arch Single Model v5: 1_HP-UVR", "filename": "1_HP-UVR.pth",
     "stems": ["instrumental", "vocals"], "target_stem": "instrumental",
     "scores": {"vocals": {"SDR": 7.9}}, "files": ["1_HP-UVR.pth"]},
    {"arch": "mdxc", "name": "MDX23C Model: InstVoc HQ", "filename": "MDX23C-8KFFT-InstVoc_HQ.ckpt",
     "stems": ["vocals", "instrumental"], "target_stem": None, "scores": {},
     "files": ["MDX23C-8KFFT-InstVoc_HQ.ckpt", "model_2_stem_full_band_8k.yaml"]},
]


@pytest.fixture
def client(cuda, monkeypatch):  # noqa: F811
    monkeypatch.setattr(models, "catalog", lambda refresh=False: CATALOG)
    monkeypatch.setattr(separation, "status", lambda: {"available": True, "reason": None})
    monkeypatch.setattr(separation, "device_info", lambda: None)  # the fake "CUDA card" has no properties
    return TestClient(create_app(Store(cuda)))


def test_settings_reach_the_model_and_are_checked(client, fake, tmp_path):  # noqa: F811
    r = client.patch("/api/models/Kim_Vocal_2.onnx", json={"settings": {"segment_size": 512, "enable_denoise": True}})
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["changed"] == {"segment_size": 512, "enable_denoise": True} and m["settings"]["overlap"] == 0.25
    separation._run_model(tmp_path / "in.wav", tmp_path / "o", "Kim_Vocal_2.onnx", lambda p, x: None)
    params = fake.created[-1].kw["mdx_params"]
    assert (params["segment_size"], params["enable_denoise"], params["batch_size"]) == (512, True, 1)

    for bad in ({"overlap": 3}, {"nope": 1}, {"enable_denoise": "yes"}):
        assert client.patch("/api/models/Kim_Vocal_2.onnx", json={"settings": bad}).status_code == 400
    # Back to the defaults = nothing stored.
    assert client.patch("/api/models/Kim_Vocal_2.onnx", json={"settings": {"segment_size": 256}}).json()["changed"] == {}

    # Demucs stays on audio-separator's defaults until something is changed for it.
    client.patch("/api/models/htdemucs.yaml", json={"settings": {"shifts": 1}})
    separation._run_model(tmp_path / "in.wav", tmp_path / "o2", "htdemucs.yaml", lambda p, x: None)
    assert fake.created[-1].kw["demucs_params"]["shifts"] == 1


def test_add_from_catalog_vr(client, fake, tmp_path):  # noqa: F811
    cat = client.get("/api/models/catalog").json()
    assert [c["filename"] for c in cat] == ["1_HP-UVR.pth", "MDX23C-8KFFT-InstVoc_HQ.ckpt"] and not cat[0]["added"]
    r = client.post("/api/models", json={"source": "catalog", "filename": "1_HP-UVR.pth", "label": "HP UVR",
                                         "best_for": ["vocals"]})
    assert r.status_code == 200, r.text
    m = r.json()
    assert (m["arch"], m["stems"], m["source"], m["scores"]["vocals"]["SDR"]) == ("vr", ["instrumental", "vocals"], "catalog", 7.9)
    assert client.post("/api/models", json={"source": "catalog", "filename": "1_HP-UVR.pth", "label": "x"}).status_code == 400
    assert client.post("/api/models", json={"source": "catalog", "filename": "nope.pth", "label": "x"}).status_code == 400

    # It's offered everywhere models are, and runs with VR options.
    meta = {x["id"]: x for x in client.get("/api/meta").json()["models"]}
    assert meta["1_HP-UVR.pth"]["group"] == "Added"
    client.patch("/api/models/1_HP-UVR.pth", json={"settings": {"aggression": 10, "window_size": 320}})
    separation._run_model(tmp_path / "in.wav", tmp_path / "o", "1_HP-UVR.pth", lambda p, x: None)
    vr = fake.created[-1].kw["vr_params"]
    assert (vr["aggression"], vr["window_size"]) == (10, 320)

    client.patch("/api/models/1_HP-UVR.pth", json={"hidden": True, "note": "for acapellas"})
    meta = {x["id"]: x for x in client.get("/api/meta").json()["models"]}
    assert meta["1_HP-UVR.pth"]["hidden"] and meta["1_HP-UVR.pth"]["note"] == "for acapellas"
    assert client.patch("/api/models/htdemucs.yaml", json={"label": "mine"}).status_code == 400  # built-ins keep their setup
    assert client.delete("/api/models/1_HP-UVR.pth").json() == {"ok": True}
    assert "1_HP-UVR.pth" not in {x["id"] for x in client.get("/api/meta").json()["models"]}


def test_own_roformer_files(client, monkeypatch, tmp_path):
    import audio_separator.separator as pkg

    FakeSeparator.created = []
    monkeypatch.setattr(pkg, "Separator", LoadingSeparator)
    ckpt, cfg = tmp_path / "my_roformer.ckpt", tmp_path / "my_roformer.yaml"
    ckpt.write_bytes(b"weights")
    cfg.write_text("training:\n  instruments: [Vocals, Other]\n  target_instrument: Vocals\n")
    assert client.post("/api/models/inspect", json={"path": str(cfg)}).json()["stems"] == ["vocals", "other"]

    r = client.post("/api/models", json={"source": "files", "arch": "mdxc", "model_path": str(ckpt),
                                         "config_path": str(cfg), "label": "My Roformer", "group": "Mine",
                                         "aliases": {"other": "instrumental"}})
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["downloaded"] and {f["name"] for f in m["files"]} == {"my_roformer.ckpt", "my_roformer.yaml"}
    assert (models.model_dir() / "my_roformer.ckpt").read_bytes() == b"weights"

    stems = separation._run_model(tmp_path / "in.wav", tmp_path / "o", "my_roformer.ckpt", lambda p, x: None)
    sep = FakeSeparator.created[-1]
    assert sep.loaded == ("my_roformer.ckpt", "MDXC", "My Roformer", str(models.model_dir() / "my_roformer.ckpt"),
                          "my_roformer.yaml")
    assert list(stems) == ["instrumental"]  # the model's "other", renamed by its alias

    # Deleting the files keeps the entry (it can't download them back: it says so).
    assert sorted(client.delete("/api/models/my_roformer.ckpt/files").json()["removed"]) == ["my_roformer.ckpt", "my_roformer.yaml"]
    mine = next(x for x in client.get("/api/models").json()["models"] if x["id"] == "my_roformer.ckpt")
    assert not mine["downloaded"] and mine["source"] == "files"


def test_own_mdx_onnx_gets_its_parameters(client, monkeypatch, tmp_path):
    import audio_separator.separator as pkg

    FakeSeparator.created = []
    monkeypatch.setattr(pkg, "Separator", LoadingSeparator)
    onnx = tmp_path / "my_mdx.onnx"
    onnx.write_bytes(b"onnx")
    bad = client.post("/api/models", json={"source": "files", "arch": "mdx", "model_path": str(onnx), "label": "x",
                                           "mdx_params": {"mdx_dim_t_set": -1}})
    assert bad.status_code == 400
    r = client.post("/api/models", json={"source": "files", "arch": "mdx", "model_path": str(onnx), "label": "My MDX",
                                         "mdx_params": {"primary_stem": "vocals", "mdx_dim_f_set": 2048}})
    assert r.status_code == 200, r.text
    assert r.json()["stems"] == ["vocals", "instrumental"]
    separation._run_model(tmp_path / "in.wav", tmp_path / "o", "my_mdx.onnx", lambda p, x: None)
    data = FakeSeparator.created[-1].model_data
    assert (data["primary_stem"], data["mdx_dim_f_set"], data["mdx_n_fft_scale_set"]) == ("Vocals", 2048, 6144)


def test_shared_files_are_kept_when_deleting(client, tmp_path):
    folder = models.model_dir()
    for name in ("MDX23C-8KFFT-InstVoc_HQ.ckpt", "model_2_stem_full_band_8k.yaml"):
        (folder / name).write_bytes(b"x")
    client.post("/api/models", json={"source": "catalog", "filename": "MDX23C-8KFFT-InstVoc_HQ.ckpt", "label": "A"})
    # A second model of your own that reuses the same config file.
    ckpt = tmp_path / "other.ckpt"
    ckpt.write_bytes(b"w")
    cfg = tmp_path / "model_2_stem_full_band_8k.yaml"
    cfg.write_text("training:\n  instruments: [vocals, instrumental]\n")
    client.post("/api/models", json={"source": "files", "arch": "mdxc", "model_path": str(ckpt),
                                     "config_path": str(cfg), "label": "B"})
    removed = client.delete("/api/models/MDX23C-8KFFT-InstVoc_HQ.ckpt/files").json()["removed"]
    assert removed == ["MDX23C-8KFFT-InstVoc_HQ.ckpt"]  # the shared .yaml stays for B
    assert (folder / "model_2_stem_full_band_8k.yaml").exists()


def test_added_model_runs_through_separate(client, monkeypatch, tmp_path):
    from chopchop.service import ChopChop

    client.post("/api/models", json={"source": "catalog", "filename": "1_HP-UVR.pth", "label": "HP UVR"})
    assert ChopChop(Store(tmp_path)).check_separate("1_HP-UVR.pth", ["vocals"]) == ["vocals"]
    with pytest.raises(ValueError, match="doesn't make"):
        ChopChop(Store(tmp_path)).check_separate("1_HP-UVR.pth", ["drums"])
