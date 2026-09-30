import io
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from chopper import separation
from chopper.app import create_app
from chopper.store import Store

from . import synth
from .synth import SR

STEMS = ["vocals", "drums", "bass", "guitar", "piano", "other"]


def fake_stems(out: Path) -> dict[str, Path]:
    """Six stems, each a sine at its own frequency, so mixes are easy to check."""
    out.mkdir(parents=True, exist_ok=True)
    t = np.arange(SR * 3) / SR
    paths = {}
    for i, name in enumerate(STEMS):
        y = 0.1 * np.sin(2 * np.pi * (200 + 100 * i) * t)
        paths[name] = out / f"song_({name.title()})_htdemucs_6s.wav"
        sf.write(paths[name], np.stack([y, y], 1), SR)
    return paths


def test_combine_mixes_unselected_stems(tmp_path):
    stems = fake_stems(tmp_path)
    out = separation.combine(stems, ["vocals"], rest=True, out_dir=tmp_path)
    assert list(out) == ["vocals", "instrumental"]
    inst, _ = sf.read(out["instrumental"])
    expected = sum(sf.read(stems[n])[0] for n in STEMS[1:])
    assert np.abs(inst - expected).max() < 1e-3  # 24-bit rounding

    assert list(separation.combine(stems, ["drums"], True, tmp_path)) == ["drums", "no_drums"]
    assert list(separation.combine(stems, ["vocals", "drums"], True, tmp_path)) == ["vocals", "drums", "rest"]
    assert list(separation.combine(stems, ["bass", "drums"], False, tmp_path)) == ["drums", "bass"]
    assert separation.combine(stems, None, True, tmp_path) == stems  # default: all


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(separation, "status", lambda: {"available": True, "reason": None})
    monkeypatch.setattr(separation, "separate", lambda src, out, model, progress=None: fake_stems(out))
    c = TestClient(create_app(Store(tmp_path)))
    buf = io.BytesIO()
    sf.write(buf, synth.drum_loop(bars=1)[0], SR, format="WAV")
    buf.seek(0)
    c.pid = c.post("/api/projects", files={"file": ("song.wav", buf, "audio/wav")}).json()["id"]
    return c


def run(client, **body):
    r = client.post(f"/api/projects/{client.pid}/separate", json={"model": "htdemucs_6s.yaml", **body})
    assert r.status_code == 200, r.text
    for _ in range(200):
        j = client.get(f"/api/jobs/{r.json()['id']}").json()
        if j["status"] in ("done", "error"):
            assert j["status"] == "done", j["error"]
            return [t["name"] for t in client.get(f"/api/projects/{client.pid}").json()["tracks"]]
        time.sleep(0.05)
    raise TimeoutError


def test_default_is_all_stems(client):
    assert run(client) == ["mix", *STEMS]


def test_vocals_and_instrumental_replaces_and_undoes(client):
    run(client)
    assert run(client, stems=["vocals"]) == ["mix", "vocals", "instrumental"]  # old six replaced
    assert client.post(f"/api/projects/{client.pid}/undo").json()["undone"] == "Separate stems"
    names = [t["name"] for t in client.get(f"/api/projects/{client.pid}").json()["tracks"]]
    assert names == ["mix", *STEMS]


def test_custom_without_rest(client):
    assert run(client, stems=["bass", "drums"], rest=False) == ["mix", "drums", "bass"]  # model order


def test_invalid_choices(client):
    url = f"/api/projects/{client.pid}/separate"
    assert client.post(url, json={"model": "htdemucs.yaml", "stems": ["piano"]}).status_code == 400
    assert client.post(url, json={"stems": []}).status_code == 400
