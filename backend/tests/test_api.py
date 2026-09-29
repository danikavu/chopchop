import io
import json
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


def new_project_with_drums(client, y):
    pid = client.post("/api/projects", files={"file": ("loop.wav", wav(y), "audio/wav")}).json()["id"]
    client.post(f"/api/projects/{pid}/tracks", files={"file": ("Drums.wav", wav(y), "audio/wav")})
    return pid


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


def test_auto_kit_loads_pads(client):
    y, _ = synth.drum_loop(bars=2)
    pid = new_project_with_drums(client, y)
    j = wait(client, client.post(f"/api/projects/{pid}/kits",
                                 json={"track": "drums", "mode": "drums", "kit_size": 16, "load_pads": True}).json())
    assert j["status"] == "done", j["error"]
    p = client.get(f"/api/projects/{pid}").json()
    cat = {s["id"]: s["category"] for s in p["samples"]}
    assert len(p["pads"]) == 64
    assert (cat[p["pads"][0]], cat[p["pads"][4]], cat[p["pads"][8]]) == ("kick", "snare", "hihat")

    # Rearrange: move the kick to pad 2 and put the hat on it too
    pads = p["pads"][:]
    kick, hat = pads[0], pads[8]
    pads[0], pads[1], pads[9] = None, kick, hat
    assert client.put(f"/api/projects/{pid}/pads", json={"pads": pads}).status_code == 200
    assert client.put(f"/api/projects/{pid}/pads", json={"pads": ["nope"] + [None] * 63}).status_code == 400

    r = client.post(f"/api/projects/{pid}/samples/export", json={"kit_name": "Pads", "from_pads": True}).json()
    names = sorted(zipfile.ZipFile(io.BytesIO(client.get(r["url"]).content)).namelist())
    assert names[0].startswith("pads/A02_kick") and any(n.startswith("pads/A05_snare") for n in names)
    assert len(names) == 3  # hat on two pads is exported once (as A09)

    # Deleting a sample empties its pads
    client.delete(f"/api/projects/{pid}/samples/{hat}")
    assert hat not in client.get(f"/api/projects/{pid}").json()["pads"]


def test_old_projects_get_pads(client, tmp_path):
    y, _ = synth.drum_loop(bars=1)
    pid = client.post("/api/projects", files={"file": ("loop.wav", wav(y), "audio/wav")}).json()["id"]
    meta = tmp_path / "projects" / pid / "project.json"
    data = json.loads(meta.read_text())
    del data["pads"]
    meta.write_text(json.dumps(data))
    assert client.get(f"/api/projects/{pid}").json()["pads"] == [None] * 64


def test_bulk_delete_samples(client):
    y, _ = synth.drum_loop(bars=2)
    pid = new_project_with_drums(client, y)
    wait(client, client.post(f"/api/projects/{pid}/kits", json={"track": "drums", "load_pads": True}).json())
    p = client.get(f"/api/projects/{pid}").json()
    ids = [s["id"] for s in p["samples"]]
    assert len(ids) == 3 and any(p["pads"])
    r = client.post(f"/api/projects/{pid}/samples/delete", json={"ids": ids[:2]})
    assert r.json() == {"deleted": 2}
    p = client.get(f"/api/projects/{pid}").json()
    assert [s["id"] for s in p["samples"]] == ids[2:]
    assert set(filter(None, p["pads"])) == {ids[2]}


def test_ui_served_with_js_mime_even_if_os_registry_is_wrong(tmp_path, monkeypatch):
    # Many Windows machines register .js as text/plain; Python's mimetypes reads the
    # registry, and browsers refuse module scripts served as text/plain (blank page).
    import mimetypes

    import chopper.app as app_module

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<script type=module src=/assets/app.js></script>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (dist / "assets" / "app.css").write_text("body{}")
    monkeypatch.setattr(app_module, "FRONTEND_DIST", dist)
    mimetypes.add_type("text/plain", ".js")
    mimetypes.add_type("text/plain", ".css")

    client = TestClient(app_module.create_app(Store(tmp_path / "data")))
    assert "javascript" in client.get("/assets/app.js").headers["content-type"]
    assert client.get("/assets/app.css").headers["content-type"].startswith("text/css")
    assert client.get("/").headers["content-type"].startswith("text/html")


def test_non_drum_tracks_use_their_general_category(client):
    # Drum-like audio on a vocals / bass / mix track must NOT be labelled kick/snare/hat.
    y, _ = synth.drum_loop(bars=2)
    pid = client.post("/api/projects", files={"file": ("loop.wav", wav(y), "audio/wav")}).json()["id"]
    for name in ("Vocals", "Bass"):
        client.post(f"/api/projects/{pid}/tracks", files={"file": (f"{name}.wav", wav(y), "audio/wav")})
    for track in ("vocals", "bass", "mix"):
        j = wait(client, client.post(f"/api/projects/{pid}/kits",
                                     json={"track": track, "kit_size": 16, "load_pads": True}).json())
        assert j["status"] == "done", j["error"]
    samples = client.get(f"/api/projects/{pid}").json()["samples"]
    by_track = {}
    for s in samples:
        by_track.setdefault(s["track"], set()).add(s["category"])
        assert s["name"].startswith(s["category"] + "_"), s["name"]
    assert by_track == {"vocals": {"vocal"}, "bass": {"bass"}, "mix": {"mix"}}

    # Manually saved regions follow the same rule.
    s = client.post(f"/api/projects/{pid}/samples", json={"track": "mix", "start": 0.0, "end": 0.3}).json()
    assert (s["category"], s["name"]) == ("mix", "mix_" + s["name"].split("_")[1])
