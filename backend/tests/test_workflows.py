import json
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from chopchop import separation, workflows
from chopchop.app import create_app
from chopchop.store import Store

from . import synth
from .synth import SR


def fake_separate(src, out, model, progress=None):
    """Every stem the model promises: drums = the song itself, others = quiet tones."""
    out.mkdir(parents=True, exist_ok=True)
    y, sr = sf.read(src)
    stems = next(m for m in separation.MODELS if m["id"] == model)["stems"]
    paths = {}
    for i, name in enumerate(stems):
        sig = y if name == "drums" else 0.05 * np.sin(2 * np.pi * (110 * (i + 1)) * np.arange(len(y)) / sr)
        paths[name] = out / f"x_({name})_m.wav"
        sf.write(paths[name], sig, sr)
    return paths


def fake_tool(src, out, tool, progress=None):
    """DrumSep stand-in: kick / snare / hat parts at 120 BPM."""
    out.mkdir(parents=True, exist_ok=True)
    beat, paths = 0.5, {}
    for name, sig, off in (("kick", synth.kick(), 0), ("snare", synth.snare(), beat), ("hihat", synth.hat(), beat / 2)):
        y = synth.place([(off + i * 2 * beat, sig) for i in range(8)], 8.5)
        paths[name] = out / f"{name}.wav"
        sf.write(paths[name], y, SR)
    return paths


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHOPCHOP_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(separation, "status", lambda: {"available": True, "reason": None})
    monkeypatch.setattr(separation, "separate", fake_separate)
    monkeypatch.setattr(separation, "run_tool", fake_tool)
    monkeypatch.setattr(separation, "debleed", lambda stems, out, progress=None: stems)
    album = tmp_path / "My Album"
    album.mkdir()
    for i, bpm in enumerate((96, 120)):
        sf.write(album / f"0{i + 1} Song {i + 1}.wav", synth.groove(bpm=bpm, bars=4)[0], SR)
    (album / "03 Broken.wav").write_bytes(b"not audio")
    (album / "notes.txt").write_text("ignored")
    client = TestClient(create_app(Store(tmp_path / "data")))
    return client, album, tmp_path


def preset(name):
    return next(w for w in workflows.PRESETS if w.name == name).model_dump()


def run(client, **body):
    r = client.post("/api/batches", json=body)
    assert r.status_code == 200, r.text
    for _ in range(600):
        j = client.get(f"/api/jobs/{r.json()['id']}").json()
        if j["status"] not in ("pending", "running"):
            return j
        time.sleep(0.05)
    raise TimeoutError


def test_folder_listing(env):
    client, album, _ = env
    r = client.get("/api/fs", params={"path": str(album)}).json()
    assert r["files"] == ["01 Song 1.wav", "02 Song 2.wav", "03 Broken.wav"] and r["dirs"] == []
    assert client.get("/api/fs", params={"path": str(album / "nope")}).status_code == 400


def test_drum_kit_per_song(env):
    client, album, tmp = env
    out = tmp / "out"
    j = run(client, input_dir=str(album), output_dir=str(out), workflow=preset("Drum kit per song"))
    assert j["status"] == "done", j["error"]
    songs = j["result"]["songs"]
    assert [s["status"] for s in songs] == ["done", "done", "error"]  # a broken file doesn't stop the batch
    assert "could not read audio" in songs[2]["message"]

    batch = Path(j["result"]["output"])
    assert batch.parent == out and batch.name.startswith("My Album – ")
    kit = sorted(p.name for p in (batch / "01 Song 1" / "kit").iterdir())
    assert kit[0].startswith("A01_kick") and any(n.startswith("A05_snare") for n in kit) \
        and any(n.startswith("A09_hihat") for n in kit)
    assert not any("bpm" in n for n in kit)  # one-shots never carry a BPM
    assert not (batch / "01 Song 1" / "stems").exists()  # this preset exports only the kit
    summary = json.loads((batch / "chopchop-batch.json").read_text())
    assert summary["workflow"]["name"] == "Drum kit per song" and len(summary["songs"]) == 3
    # Songs stay as projects to tweak later.
    assert {p["name"] for p in client.get("/api/projects").json()} == {"01 Song 1", "02 Song 2"}


def test_stems_with_names_and_no_projects(env):
    client, album, _ = env
    wf = preset("Vocals + bass stems") | {"keep_projects": False}
    j = run(client, input_dir=str(album), files=["02 Song 2.wav"], workflow=wf)
    assert j["status"] == "done", j["error"]
    song = j["result"]["songs"][0]
    stems = sorted(p.name for p in (Path(song["output"]) / "stems").iterdir())
    assert [n.split("_")[0] for n in stems] == ["bass", "vocals"]
    assert all(f"_{round(song['bpm'])}bpm" in n for n in stems)  # BPM in the names
    assert Path(j["result"]["output"]).parent == Path(client.get("/api/workflows").json()["default_output"])
    assert client.get("/api/projects").json() == []  # keep_projects off


def test_cancel(env, monkeypatch):
    client, album, _ = env
    slow = fake_separate

    def slow_separate(*a, **k):
        time.sleep(0.5)
        return slow(*a, **k)

    monkeypatch.setattr(separation, "separate", slow_separate)
    r = client.post("/api/batches", json={"input_dir": str(album), "workflow": preset("All stems")}).json()
    time.sleep(0.2)
    assert client.post(f"/api/jobs/{r['id']}/cancel").status_code == 200
    for _ in range(200):
        j = client.get(f"/api/jobs/{r['id']}").json()
        if j["status"] not in ("pending", "running"):
            break
        time.sleep(0.05)
    assert j["status"] == "cancelled"
    assert j["detail"]["songs"][-1]["status"] == "cancelled"


def test_bad_requests(env):
    client, album, _ = env
    bad = preset("All stems")
    bad["separate"]["stems"] = ["kazoo"]
    assert client.post("/api/batches", json={"input_dir": str(album), "workflow": bad}).status_code == 400
    assert client.post("/api/batches", json={"input_dir": "/nope", "workflow": preset("All stems")}).status_code == 400
    assert client.post("/api/batches", json={"input_dir": str(album), "files": ["x.wav"],
                                             "workflow": preset("All stems")}).status_code == 400


def test_save_and_delete_workflow(env):
    client, _, _ = env
    wf = preset("Acapellas") | {"name": "My acapellas"}
    assert [w["name"] for w in client.put("/api/workflows", json=wf).json()["saved"]] == ["My acapellas"]
    assert client.get("/api/workflows").json()["saved"][0]["tools"][0]["tool"] == "dereverb"
    assert client.delete("/api/workflows/My acapellas").json()["saved"] == []


def test_sample_folders_say_where_sounds_came_from(env):
    """Bits from the "other" stem go to samples/other; drum chops are kick / snare / hihat,
    never a catch-all "other" folder."""
    client, album, _ = env
    wf = preset("All stems") | {"bits": {"enabled": True, "stems": ["other", "drums"], "mode": "chops",
                                         "max": 16, "uniqueness": 0.9}}
    wf["export"] |= {"samples": True}
    j = run(client, input_dir=str(album), files=["01 Song 1.wav"], workflow=wf)
    assert j["status"] == "done", j["error"]
    song = j["result"]["songs"][0]
    folders = {p.name: sorted(f.name for f in p.iterdir()) for p in (Path(song["output"]) / "samples").iterdir()}
    project = client.get(f"/api/projects/{song['project']}").json()
    by_track = {}
    for s in project["samples"]:
        by_track.setdefault(s["track"], set()).add(s["category"])
    assert by_track.get("other", {"other"}) == {"other"}
    assert by_track["drums"] <= {"kick", "snare", "clap", "hihat", "open_hat", "cymbal", "tom", "perc"}
    assert all(n.startswith("other_") for n in folders.get("other", []))
    assert "drums" not in folders  # drum chops are filed by drum type


def test_remove_and_clear_runs_keep_files(env, monkeypatch):
    client, album, _ = env
    done = [run(client, input_dir=str(album), files=["01 Song 1.wav"], workflow=preset("All stems")) for _ in range(2)]
    outputs = [Path(j["result"]["output"]) for j in done]

    slow = fake_separate
    monkeypatch.setattr(separation, "separate", lambda *a, **k: (time.sleep(0.6), slow(*a, **k))[1])
    running = client.post("/api/batches", json={"input_dir": str(album), "files": ["02 Song 2.wav"],
                                                 "workflow": preset("All stems")}).json()
    assert client.delete(f"/api/batches/{running['id']}").status_code == 409  # cancel first
    assert client.delete(f"/api/batches/{done[0]['id']}").json() == {"ok": True}
    assert client.delete(f"/api/batches/{done[0]['id']}").status_code == 404
    assert {b["id"] for b in client.get("/api/batches").json()} == {done[1]["id"], running["id"]}

    assert client.delete("/api/batches").json() == {"removed": 1}  # the finished one, not the running one
    assert [b["id"] for b in client.get("/api/batches").json()] == [running["id"]]
    assert all(o.exists() for o in outputs)  # results on disk are untouched


def test_batch_defaults_to_the_saved_export_folder(env):
    client, album, tmp = env
    saved = tmp / "Exports"
    client.put("/api/settings", json={"export_dir": str(saved)})
    wf = preset("Vocals + bass stems") | {"keep_projects": False}
    j = run(client, input_dir=str(album), files=["01 Song 1.wav"], workflow=wf)
    assert j["status"] == "done", j["error"]
    assert Path(j["result"]["output"]).parent == saved


def test_earlier_runs_are_found_on_disk(env):
    client, album, tmp = env
    out = tmp / "Elsewhere"
    j = run(client, input_dir=str(album), files=["01 Song 1.wav"], output_dir=str(out), workflow=preset("All stems"))
    run_dir = Path(j["result"]["output"])
    # This session's run is listed once, not again from its folder.
    assert [b["id"] for b in client.get("/api/batches", params={"scan": str(out)}).json()] == [j["id"]]

    (out / "junk").mkdir()
    (out / "junk" / "chopchop-batch.json").write_text("{not json")
    # A new session (server restart): the run comes back from its chopchop-batch.json, when
    # the batch page looks at the output folder or at the run folder itself.
    fresh = TestClient(create_app(Store(tmp / "data")))
    assert fresh.get("/api/batches").json() == []  # not in a folder ChopChop knows about
    for scan in (out, run_dir):
        runs = fresh.get("/api/batches", params={"scan": str(scan)}).json()
        assert len(runs) == 1
        r = runs[0]
        assert r["id"].startswith("disk:") and r["status"] == "done" and r["detail"]["from_disk"]
        assert r["detail"]["output"] == str(run_dir.resolve()) and r["detail"]["workflow"] == "All stems"
        assert r["detail"]["songs"][0]["status"] == "done" and r["detail"]["songs"][0]["project"]

    song_project = r["detail"]["songs"][0]["project"]
    fresh.delete(f"/api/projects/{song_project}")
    r = fresh.get("/api/batches", params={"scan": str(out)}).json()[0]
    assert "project" not in r["detail"]["songs"][0]  # no "open" link to a deleted project

    # Remove hides it for good (files stay); runs in the saved export folder show up unasked.
    assert fresh.delete(f"/api/batches/{r['id']}", params={"scan": str(out)}).json() == {"ok": True}
    assert fresh.get("/api/batches", params={"scan": str(out)}).json() == []
    assert run_dir.exists()
    j2 = run(fresh, input_dir=str(album), files=["02 Song 2.wav"], output_dir=str(out), workflow=preset("All stems"))
    fresh.put("/api/settings", json={"export_dir": str(out)})
    again = TestClient(create_app(Store(tmp / "data")))
    runs = again.get("/api/batches").json()
    assert [Path(b["detail"]["output"]) for b in runs] == [Path(j2["result"]["output"]).resolve()]
    assert again.delete("/api/batches").json() == {"removed": 1}
    assert again.get("/api/batches").json() == []


def test_runs_from_before_the_rename_are_still_listed(env):
    client, album, tmp = env
    out = tmp / "Old"
    j = run(client, input_dir=str(album), files=["01 Song 1.wav"], output_dir=str(out), workflow=preset("All stems"))
    run_dir = Path(j["result"]["output"])
    assert (run_dir / "chopchop-batch.json").is_file()
    (run_dir / "chopchop-batch.json").rename(run_dir / "chopper-batch.json")  # as an older version wrote it
    fresh = TestClient(create_app(Store(tmp / "data")))
    runs = fresh.get("/api/batches", params={"scan": str(out)}).json()
    assert [r["detail"]["output"] for r in runs] == [str(run_dir.resolve())]


def test_old_environment_names_still_work(tmp_path, monkeypatch):
    from chopchop.store import data_dir

    monkeypatch.delenv("CHOPCHOP_DATA")
    monkeypatch.setenv("CHOPPER_DATA", str(tmp_path / "old"))
    assert data_dir() == tmp_path / "old"
    monkeypatch.setenv("CHOPCHOP_DATA", str(tmp_path / "new"))  # the new name wins
    assert data_dir() == tmp_path / "new"
