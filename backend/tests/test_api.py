import io
import time
import zipfile

import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from chopper.app import create_app
from chopper.store import Store

from . import synth
from .synth import SR


def wav(y):
    buf = io.BytesIO()
    sf.write(buf, y, SR, format="WAV")
    buf.seek(0)
    return buf


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Store(tmp_path)))


def wait(client, job):
    for _ in range(300):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.05)
    raise TimeoutError


def test_full_flow(client):
    y, truth = synth.drum_loop(bars=2)
    r = client.post("/api/projects", files={"file": ("loop.wav", wav(y), "audio/wav")}, data={"name": "Loop"})
    assert r.status_code == 200, r.text
    p = r.json()
    pid = p["id"]
    assert p["tracks"][0]["name"] == "mix"

    # Import a stem manually
    r = client.post(f"/api/projects/{pid}/tracks", files={"file": ("Drums.wav", wav(y), "audio/wav")})
    assert r.json()["name"] == "drums"

    peaks = client.get(f"/api/projects/{pid}/tracks/drums/peaks?n=500").json()
    assert 400 <= len(peaks["peaks"]) <= 510

    # Detect slices on the drum stem, export over both tracks
    slices = client.post(f"/api/projects/{pid}/slices/detect", json={"track": "drums"}).json()["slices"]
    assert len(slices) == len(truth)
    r = client.post(f"/api/projects/{pid}/slices/export", json={"tracks": ["drums", "mix"]})
    assert r.status_code == 200, r.text
    z = zipfile.ZipFile(io.BytesIO(client.get(r.json()["url"]).content))
    names = z.namelist()
    assert len(names) == 2 * len(truth)
    assert "drums/drums_001.wav" in names and "mix/mix_001.wav" in names

    # Edit slices by hand (e.g. dragged in the UI)
    edited = [{"start": 0.0, "end": 0.2}, {"start": 1.0, "end": 1.3}]
    assert len(client.put(f"/api/projects/{pid}/slices", json={"slices": edited}).json()["slices"]) == 2

    # Drum kit job
    job = client.post(f"/api/projects/{pid}/kits", json={"track": "drums", "mode": "drums"}).json()
    j = wait(client, job)
    assert j["status"] == "done", j["error"]
    samples = client.get(f"/api/projects/{pid}").json()["samples"]
    assert sorted(s["category"] for s in samples) == ["hihat", "kick", "snare"]

    # Re-running replaces instead of duplicating
    wait(client, client.post(f"/api/projects/{pid}/kits", json={"track": "drums", "mode": "drums"}).json())
    samples = client.get(f"/api/projects/{pid}").json()["samples"]
    assert len(samples) == 3

    # Recategorise + set key + trim
    sid = samples[0]["id"]
    r = client.patch(f"/api/projects/{pid}/samples/{sid}",
                     json={"category": "perc", "key": "F#2", "name": "my hit", "end": samples[0]["start"] + 0.1})
    assert r.status_code == 200, r.text
    s = r.json()
    assert (s["category"], s["key"], s["name"]) == ("perc", "F#2", "my hit")
    assert abs(s["duration"] - 0.1) < 0.002

    # Manual sample from a region
    r = client.post(f"/api/projects/{pid}/samples", json={"track": "drums", "start": 0.0, "end": 0.3})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["category"] == "kick" and s["origin"] == "manual"

    # Export kit
    r = client.post(f"/api/projects/{pid}/samples/export", json={"kit_name": "Test Kit"}).json()
    z = zipfile.ZipFile(io.BytesIO(client.get(r["url"]).content))
    assert any(n.startswith("test_kit/perc/my_hit_F#2") for n in z.namelist())
    assert len(z.namelist()) == 4


def test_bad_upload(client):
    r = client.post("/api/projects", files={"file": ("x.wav", io.BytesIO(b"nope"), "audio/wav")})
    assert r.status_code == 400
    assert client.get("/api/projects").json() == []


def test_unknown_project(client):
    assert client.get("/api/projects/0123456789").status_code == 404
    assert client.get("/api/projects/../etc").status_code == 404
