import io
import time

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from chopper import portions, separation
from chopper.app import create_app
from chopper.store import Store

SR = 44100


def wav(y):
    buf = io.BytesIO()
    sf.write(buf, y, SR, format="WAV")
    buf.seek(0)
    return buf


def wait(client, job):
    for _ in range(300):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.05)
    raise TimeoutError


def test_merge_clips_joins_and_drops():
    got = portions.merge([(5, 7), (-1, 1), (6.5, 8), (9, 9.01), (9.5, 12)], duration=10)
    assert got == [(0.0, 1), (5, 8), (9.5, 10)]


def test_separate_only_parts(tmp_path, monkeypatch):
    calls = []

    def fake_separate(src, out, model, progress=None):
        """'vocals' = the input itself, 'other' = half of it: easy to check where it lands."""
        y, sr = sf.read(src, always_2d=True)
        calls.append(len(y) / sr)
        out.mkdir(parents=True, exist_ok=True)
        paths = {"vocals": out / "v.wav", "other": out / "o.wav"}
        sf.write(paths["vocals"], y, sr)
        sf.write(paths["other"], y * 0.5, sr)
        return paths

    monkeypatch.setattr(separation, "status", lambda: {"available": True, "reason": None})
    monkeypatch.setattr(separation, "separate", fake_separate)
    monkeypatch.setattr(separation, "MODELS", [{"id": "fake", "label": "Fake", "stems": ["vocals", "other"]}])
    monkeypatch.setattr(separation, "debleed", lambda stems, out, progress=None: stems)
    client = TestClient(create_app(Store(tmp_path)))

    # 10 s "song": a ramp, so every sample says where it came from.
    mix = np.linspace(-0.9, 0.9, 10 * SR).astype(np.float32)
    pid = client.post("/api/projects", files={"file": ("song.wav", wav(mix), "audio/wav")}).json()["id"]
    body = {"model": "fake", "parts": [{"start": 6, "end": 7}, {"start": 1, "end": 2.5}]}
    j = wait(client, client.post(f"/api/projects/{pid}/separate", json=body).json())
    assert j["status"] == "done", j["error"]

    # One model run, on a file far shorter than the song (the parts + context + gaps).
    assert len(calls) == 1 and calls[0] < 9
    p = client.get(f"/api/projects/{pid}").json()
    assert client.get(f"/api/projects/{pid}/history").json()["undo"] == "Separate stems (2 parts)"
    folder = tmp_path / "projects" / pid
    for name, gain in (("vocals", 1.0), ("other", 0.5)):
        t = next(t for t in p["tracks"] if t["name"] == name)
        y, sr = sf.read(folder / t["file"], always_2d=True)
        y = y[:, 0]
        assert len(y) == len(mix)  # full length, in line with the mix
        inside = np.zeros(len(y), bool)
        for a, b in ((1, 2.5), (6, 7)):
            core = slice(int((a + 0.02) * SR), int((b - 0.02) * SR))
            inside[int(a * SR):int(b * SR)] = True
            assert np.allclose(y[core], mix[core] * gain, atol=1e-3)  # each part back at its own time
        assert np.abs(y[~inside]).max() < 1e-6  # silent elsewhere

    bad = client.post(f"/api/projects/{pid}/separate", json={"model": "fake", "parts": [{"start": 20, "end": 30}]})
    assert bad.status_code == 400
