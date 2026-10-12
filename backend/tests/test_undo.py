import io
import time
from urllib.parse import unquote

import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from chopchop import store as store_module
from chopchop.app import create_app
from chopchop.store import Store

from . import synth
from .synth import SR


def wav(y):
    buf = io.BytesIO()
    sf.write(buf, y, SR, format="WAV")
    buf.seek(0)
    return buf


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path)
    client = TestClient(create_app(store))
    y, _ = synth.drum_loop(bars=2)
    pid = client.post("/api/projects", files={"file": ("loop.wav", wav(y), "audio/wav")}).json()["id"]
    client.post(f"/api/projects/{pid}/tracks", files={"file": ("Drums.wav", wav(y), "audio/wav")})
    return client, store, pid


def get(client, pid):
    return client.get(f"/api/projects/{pid}").json()


def wait(client, job):
    for _ in range(300):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.05)
    raise TimeoutError


def test_undo_redo_slices_with_labels_and_headers(env):
    client, _, pid = env
    a = [{"id": "a", "start": 0.0, "end": 0.5}]
    b = [{"id": "a", "start": 0.0, "end": 0.25}, {"id": "b", "start": 0.25, "end": 0.5}]
    client.put(f"/api/projects/{pid}/slices", json={"slices": a, "label": "Draw slice"})
    r = client.put(f"/api/projects/{pid}/slices", json={"slices": b, "label": "Split slice"})
    assert unquote(r.headers["X-ChopChop-Undo"]) == "Split slice"

    r = client.post(f"/api/projects/{pid}/undo")
    assert r.json()["undone"] == "Split slice"
    assert [s["id"] for s in get(client, pid)["slices"]] == ["a"]
    assert unquote(r.headers["X-ChopChop-Redo"]) == "Split slice"

    client.post(f"/api/projects/{pid}/redo")
    assert [s["id"] for s in get(client, pid)["slices"]] == ["a", "b"]

    # A new edit after undo drops the redo branch.
    client.post(f"/api/projects/{pid}/undo")
    client.put(f"/api/projects/{pid}/slices", json={"slices": [], "label": "Clear slices"})
    assert client.get(f"/api/projects/{pid}/history").json() == {"undo": "Clear slices", "redo": None}
    assert client.post(f"/api/projects/{pid}/redo").status_code == 409


def test_undo_brings_back_deleted_samples_audio_and_pads(env):
    client, _, pid = env
    wait(client, client.post(f"/api/projects/{pid}/kits", json={"track": "drums", "load_pads": True}).json())
    before = get(client, pid)
    ids = [s["id"] for s in before["samples"]]
    client.post(f"/api/projects/{pid}/samples/delete", json={"ids": ids})
    assert get(client, pid)["samples"] == [] and not any(get(client, pid)["pads"])

    assert client.post(f"/api/projects/{pid}/undo").json()["undone"] == "Delete 3 samples"
    after = get(client, pid)
    assert [s["id"] for s in after["samples"]] == ids and after["pads"] == before["pads"]
    for sid in ids:  # the audio is still there
        assert client.get(f"/api/projects/{pid}/samples/{sid}/audio").status_code == 200


def test_undo_trim_restores_original_audio(env):
    client, _, pid = env
    s = client.post(f"/api/projects/{pid}/samples", json={"track": "drums", "start": 0.0, "end": 0.4}).json()
    original = client.get(f"/api/projects/{pid}/samples/{s['id']}/audio").content
    client.patch(f"/api/projects/{pid}/samples/{s['id']}", json={"end": 0.1})
    assert client.post(f"/api/projects/{pid}/undo").json()["undone"] == "Trim sample"
    restored = get(client, pid)["samples"][0]
    assert restored["duration"] == pytest.approx(0.4, abs=0.002)
    assert client.get(f"/api/projects/{pid}/samples/{s['id']}/audio").content == original


def test_undo_kit_rebuild_restores_previous_kit(env):
    client, _, pid = env
    wait(client, client.post(f"/api/projects/{pid}/kits", json={"track": "drums"}).json())
    first = {s["id"] for s in get(client, pid)["samples"]}
    wait(client, client.post(f"/api/projects/{pid}/kits", json={"track": "drums"}).json())
    assert {s["id"] for s in get(client, pid)["samples"]}.isdisjoint(first)  # replaced
    assert client.post(f"/api/projects/{pid}/undo").json()["undone"] == "Build drum kit from drums"
    assert {s["id"] for s in get(client, pid)["samples"]} == first


def test_undo_group_is_one_step(env):
    client, store, pid = env
    with store.undo_group(pid, "Separate stems"):
        with store.edit(pid, "a") as p:
            p["name"] = "one"
        with store.edit(pid, "b") as p:
            p["name"] = "two"
    assert store.history(pid)["undo"] == "Separate stems"
    store.undo(pid)
    assert get(client, pid)["name"] == "loop"


def test_garbage_collection_keeps_files_undo_needs(env, monkeypatch):
    client, store, pid = env
    monkeypatch.setattr(store_module, "GC_MIN_AGE_S", 0)
    s = client.post(f"/api/projects/{pid}/samples", json={"track": "drums", "start": 0.0, "end": 0.3}).json()
    path = store.path(pid) / s["file"]
    client.delete(f"/api/projects/{pid}/samples/{s['id']}")
    store.collect_garbage(pid)
    assert path.exists()  # the undo step still refers to it

    client.post(f"/api/projects/{pid}/undo")
    client.post(f"/api/projects/{pid}/redo")  # deleted again; the file is only needed by "undo"
    store._history[pid].undo.clear()  # e.g. history ran out
    store.collect_garbage(pid)
    assert not path.exists()


def test_history_is_bounded(env, monkeypatch):
    client, store, pid = env
    monkeypatch.setattr(store_module, "MAX_UNDO", 5)
    for i in range(8):
        client.put(f"/api/projects/{pid}/slices", json={"slices": [{"start": 0, "end": 0.1 + i / 100}], "label": f"e{i}"})
    assert len(store._history[pid].undo) == 5


def test_project_creation_is_not_undoable(env):
    client, _, pid = env
    client.post(f"/api/projects/{pid}/undo")  # undoes "Import stem drums"
    r = client.post(f"/api/projects/{pid}/undo")
    assert r.status_code == 409
    assert [t["name"] for t in get(client, pid)["tracks"]] == ["mix"]
