import io
import json

import numpy as np
import time
import zipfile
from pathlib import Path

import pytest
import soundfile as sf
from fastapi.testclient import TestClient

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
    r = client.post(f"/api/projects/{pid}/slices/export",
                    json={"tracks": ["drums", "mix"], "key_names": False, "bpm_names": False})
    assert r.status_code == 200, r.text
    j = wait(client, r.json())
    assert j["status"] == "done", j["error"]
    z = zipfile.ZipFile(io.BytesIO(client.get(j["result"]["url"]).content))
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

    import chopchop.app as app_module

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


def test_tempo_detect_and_adjust(client):
    y, beats = synth.groove(bpm=96, bars=8)
    pid = client.post("/api/projects", files={"file": ("g.wav", wav(y), "audio/wav")}).json()["id"]
    assert client.get(f"/api/projects/{pid}").json()["tempo"] is None
    client.post(f"/api/projects/{pid}/tracks", files={"file": ("Drums.wav", wav(y), "audio/wav")})

    t = client.post(f"/api/projects/{pid}/tempo", json={}).json()
    assert t["track"] == "drums" and t["bpm"] == pytest.approx(96, abs=0.3) and t["confidence"] > 0.8
    n = len(t["beats"])
    assert abs(n - len(beats)) <= 2

    doubled = client.put(f"/api/projects/{pid}/tempo", json={"bpm": t["bpm"] * 2}).json()
    assert doubled["bpm"] == pytest.approx(192, abs=0.6) and doubled["source"] == "manual"
    assert len(doubled["beats"]) == 2 * n - 1
    halved = client.put(f"/api/projects/{pid}/tempo", json={"bpm": doubled["bpm"] / 2}).json()
    assert len(halved["beats"]) == n
    assert client.get(f"/api/projects/{pid}").json()["tempo"]["bpm"] == halved["bpm"]
    assert halved["detected_bpm"] == t["bpm"]  # original detection kept for reference


def test_export_names_carry_bpm_and_key(client):
    # A 96 BPM groove plus a sustained A2 bass note under it.
    y, _ = synth.groove(bpm=96, bars=4)
    bass = synth.place([(0.2, synth.tone(45, 2.4))], len(y) / SR)[: len(y)]
    pid = client.post("/api/projects", files={"file": ("g.wav", wav(y + bass), "audio/wav")}).json()["id"]
    client.post(f"/api/projects/{pid}/tracks", files={"file": ("Drums.wav", wav(y), "audio/wav")})
    client.post(f"/api/projects/{pid}/tracks", files={"file": ("Bass.wav", wav(bass), "audio/wav")})
    assert client.post(f"/api/projects/{pid}/tempo", json={}).json()["bpm"] == pytest.approx(96, abs=0.3)

    # One 2-beat loop and one kick-length one-shot.
    beat = 60 / 96
    client.put(f"/api/projects/{pid}/slices", json={"slices": [
        {"start": 0.2, "end": 0.2 + 2 * beat}, {"start": 0.2 + 4 * beat, "end": 0.2 + 4 * beat + 0.2}]})
    assert client.post(f"/api/projects/{pid}/slices/export", json={"tracks": []}).status_code == 400
    j = wait(client, client.post(f"/api/projects/{pid}/slices/export", json={"tracks": ["drums", "bass"]}).json())
    assert j["status"] == "done", j["error"]
    names = sorted(zipfile.ZipFile(io.BytesIO(client.get(j["result"]["url"]).content)).namelist())
    # Loop: BPM (+ key where there is one); one-shot: no BPM; hats/snares never get a key.
    assert "bass/bass_001_96bpm_A2.wav" in names, names
    assert "drums/drums_001_96bpm.wav" in names, names  # a drum loop has no key
    assert any(n.startswith("drums/drums_002") and "bpm" not in n for n in names), names

    # Kit export: phrase-length samples get the BPM, one-shots don't.
    loop = client.post(f"/api/projects/{pid}/samples", json={"track": "bass", "start": 0.2, "end": 0.2 + 2 * beat}).json()
    shot = client.post(f"/api/projects/{pid}/samples", json={"track": "drums", "start": 0.19, "end": 0.45}).json()
    r = client.post(f"/api/projects/{pid}/samples/export", json={"kit_name": "k"}).json()
    names = zipfile.ZipFile(io.BytesIO(client.get(r["url"]).content)).namelist()
    assert f"k/bass/{loop['name']}_96bpm_{loop['key'].replace(' ', '')}.wav" in names, names
    assert any(n.startswith(f"k/kick/{shot['name']}") and "bpm" not in n for n in names), names


def test_anchor_snaps_to_the_hit_near_the_playhead(client):
    y, beats = synth.groove(bpm=100, bars=8)
    pid = client.post("/api/projects", files={"file": ("g.wav", wav(y), "audio/wav")}).json()["id"]
    client.post(f"/api/projects/{pid}/tracks", files={"file": ("Drums.wav", wav(y), "audio/wav")})
    client.post(f"/api/projects/{pid}/tempo", json={})
    client.put(f"/api/projects/{pid}/tempo", json={"shift": 0.08})  # knock the grid off
    target = beats[9]  # a snare on beat 2 of bar 3 -> make it "beat 1"
    t = client.put(f"/api/projects/{pid}/tempo", json={"anchor": target + 0.02}).json()  # rough click
    bar_starts = t["beats"][t["downbeat"]::4]
    assert min(abs(b - target) for b in bar_starts) < 0.006
    assert client.get(f"/api/projects/{pid}/history").json()["undo"] == "Set bar start"


@pytest.mark.parametrize("level_db,expect_bits", [(-90, False), (-30, True)])
def test_sound_bits_must_be_audible_in_the_song(client, level_db, expect_bits):
    # An "other" stem far below the mix (only traces of bleed) yields nothing; a quiet
    # but real part 30 dB down still gives bits.
    drums, _ = synth.groove(bpm=100, bars=6)
    n = len(drums)
    pad = np.zeros(n, np.float32)
    for k in range(6):
        a = int((0.2 + k * 2.4) * SR)
        pad[a:a + int(1.2 * SR)] = synth.tone(57 + (k % 3) * 3, 1.2)[: n - a]
    quiet = pad * 10 ** (level_db / 20) / np.abs(pad).max() * np.abs(drums).max()
    pid = client.post("/api/projects", files={"file": ("s.wav", wav(drums + quiet), "audio/wav")}).json()["id"]
    client.post(f"/api/projects/{pid}/tracks", files={"file": ("Other.wav", wav(quiet), "audio/wav")})
    j = wait(client, client.post(f"/api/projects/{pid}/kits", json={"track": "other", "mode": "chops"}).json())
    assert j["status"] == "done", j["error"]
    assert bool(j["result"]["samples"]) is expect_bits


def test_export_folder_setting_persists(client, tmp_path):
    s = client.get("/api/settings").json()
    assert s["export_dir"] is None and s["default_batch_output"]
    assert client.put("/api/settings", json={"export_dir": "relative/dir"}).status_code == 400
    out = tmp_path / "My Exports"
    assert client.put("/api/settings", json={"export_dir": str(out)}).json()["export_dir"] == str(out)
    assert out.is_dir()
    # A fresh server (new session) still has it, and batches default to it.
    fresh = TestClient(create_app(Store(tmp_path / "other")))
    assert fresh.get("/api/settings").json()["export_dir"] == str(out)
    assert fresh.get("/api/workflows").json()["default_output"] == str(out)
    assert client.put("/api/settings", json={"export_dir": None}).json()["export_dir"] is None


def test_project_exports_go_into_the_export_folder(client, tmp_path):
    out = tmp_path / "exports"
    client.put("/api/settings", json={"export_dir": str(out)})
    y, _ = synth.drum_loop(bars=2)
    pid = new_project_with_drums(client, y)
    client.patch(f"/api/projects/{pid}", json={"name": "Nestor"})

    client.post(f"/api/projects/{pid}/slices/detect", json={"track": "drums"})
    j = wait(client, client.post(f"/api/projects/{pid}/slices/export", json={"tracks": ["drums"]}).json())
    assert j["status"] == "done", j["error"]
    r = j["result"]
    assert r["folder"] and r["url"] is None
    folder = out / "Nestor"
    slices_dir = next(folder.glob("Slices – *"))
    assert r["path"] == str(slices_dir)
    assert len(list((slices_dir / "drums").glob("*.wav"))) == r["count"] > 0

    j = wait(client, client.post(f"/api/projects/{pid}/kits",
                                 json={"track": "drums", "mode": "drums", "kit_size": 16, "load_pads": True}).json())
    assert j["status"] == "done", j["error"]
    kit = client.post(f"/api/projects/{pid}/samples/export", json={"kit_name": "k"}).json()
    pads = client.post(f"/api/projects/{pid}/samples/export", json={"kit_name": "p", "from_pads": True}).json()
    assert Path(kit["path"]).parent == folder and Path(kit["path"]).name.startswith("Kit – ")
    assert Path(pads["path"]).name.startswith("Pads – ")
    assert len(list(Path(kit["path"]).rglob("*.wav"))) == kit["count"]
    assert any(p.name.startswith("A01_kick") for p in Path(pads["path"]).rglob("*.wav"))
    # Same minute again: a new folder, nothing overwritten.
    again = client.post(f"/api/projects/{pid}/samples/export", json={"kit_name": "k"}).json()
    assert again["path"] != kit["path"]


def test_export_keyed_samples_transposed_to_c(client, tmp_path):
    import librosa

    from chopchop import transpose

    # Three pitched sounds (E3, A3, C4) and one with no key.
    y = synth.place([(0.0, synth.tone(52)), (1.0, synth.tone(57)), (2.0, synth.tone(60)), (3.0, synth.tone(57))], 4.0)
    pid = client.post("/api/projects", files={"file": ("bass.wav", wav(y), "audio/wav")}).json()["id"]
    ids = []
    for i, key in enumerate(["E3", "A min", "C4", None]):
        s = client.post(f"/api/projects/{pid}/samples", json={"track": "mix", "start": i, "end": i + 0.8,
                                                               "name": f"bit{i}"}).json()
        body = {"key": key} if key else {"clear_key": True}
        client.patch(f"/api/projects/{pid}/samples/{s['id']}", json=body)
        ids.append(s["id"])

    out = tmp_path / "exp"
    client.put("/api/settings", json={"export_dir": str(out)})
    r = client.post(f"/api/projects/{pid}/samples/export", json={"kit_name": "k", "to_c": "both"}).json()
    assert r["count"] == 4 and r["transposed"] == 4  # E3 and A min, two ways each; C4 and the unkeyed one skipped
    root = Path(r["path"])
    for mode in ("repitched", "warped"):
        files = sorted(p.name for p in (root / transpose.FOLDERS[mode]).rglob("*.wav"))
        assert files == ["bit0_C3.wav", "bit1_Cmin.wav"], files
        moved, sr = sf.read(next((root / transpose.FOLDERS[mode]).rglob("bit0_C3.wav")))
        f0 = np.median(librosa.yin(moved.T[0] if moved.ndim > 1 else moved, fmin=60, fmax=600, sr=sr)[:20])
        assert librosa.hz_to_note(f0, unicode=False) == "C3", (mode, f0)
        n_orig = sf.info(next(root.rglob("bit0_E3.wav"))).frames
        n = len(moved)
        assert (n > n_orig * 1.2) if mode == "repitched" else (n == n_orig)  # E3 -> C3 down 4: slower
    assert len(list(root.rglob("*.wav"))) == 8

    plain = client.post(f"/api/projects/{pid}/samples/export", json={"kit_name": "k"}).json()
    assert plain["transposed"] == 0 and not any(p.parent.name.startswith("In C") for p in Path(plain["path"]).rglob("*"))
    assert client.post(f"/api/projects/{pid}/samples/export", json={"to_c": "sideways"}).status_code == 422
    # Pads: same option, pad-named.
    client.put(f"/api/projects/{pid}/pads", json={"pads": [ids[0], ids[2]] + [None] * 62})
    pads = client.post(f"/api/projects/{pid}/samples/export", json={"from_pads": True, "to_c": "repitched"}).json()
    assert pads["transposed"] == 1
    assert [p.name for p in (Path(pads["path"]) / "In C (repitched)").iterdir()] == ["A01_bit0_C3.wav"]


def test_silent_chops_are_not_exported(client):
    # Drums all through; vocals only from bar 3 (a beat intro before them), with faint bleed.
    drums, _ = synth.drum_loop(bars=4)
    n = drums.size
    vocals = np.zeros(n, np.float32)
    half = n // 2
    vocals[half:] = 0.3 * np.sin(2 * np.pi * 330 * np.arange(n - half) / SR)  # a held note
    vocals[:half] += np.random.default_rng(0).standard_normal(half).astype(np.float32) * 1e-4  # ~-70 dB hiss
    pid = new_project_with_drums(client, drums)
    client.post(f"/api/projects/{pid}/tracks", files={"file": ("Vocals.wav", wav(vocals), "audio/wav")})
    dur = n / SR
    grid = [{"id": f"s{i}", "start": i * dur / 8, "end": (i + 1) * dur / 8} for i in range(8)]  # chop to grid
    client.put(f"/api/projects/{pid}/slices", json={"slices": grid})

    j = wait(client, client.post(f"/api/projects/{pid}/slices/export",
                                 json={"tracks": ["drums", "vocals"], "key_names": False}).json())
    assert j["status"] == "done", j["error"]
    assert (j["result"]["count"], j["result"]["silent"]) == (12, 4)
    names = sorted(zipfile.ZipFile(io.BytesIO(client.get(j["result"]["url"]).content)).namelist())
    assert len([x for x in names if x.startswith("drums/")]) == 8
    # Vocal chops keep their slice numbers, so they line up with the drum chops.
    assert [x.split("/")[1][:10] for x in names if x.startswith("vocals/")] == \
        ["vocals_00" + str(i) for i in range(5, 9)]

    j = wait(client, client.post(f"/api/projects/{pid}/slices/export",
                                 json={"tracks": ["vocals"], "skip_silent": False, "key_names": False}).json())
    assert (j["result"]["count"], j["result"]["silent"]) == (8, 0)

    j = wait(client, client.post(f"/api/projects/{pid}/slices/export",
                                 json={"tracks": ["vocals"], "slice_ids": ["s0", "s1"]}).json())
    assert j["status"] == "error" and "silent" in j["error"]


def test_sound_bits_from_several_tracks_judge_each_on_its_own(client):
    # Loud bass and much quieter (-20 dB) vocals: the vocals still give bits.
    bass = synth.place([(i * 0.5, synth.tone(36 + (i % 3), 0.45)) for i in range(8)], 4.2)
    vox = synth.place([(0.25 + i * 0.5, synth.tone(64 + (i % 4), 0.4)) for i in range(7)], 4.2) * 0.1
    pid = client.post("/api/projects", files={"file": ("song.wav", wav(bass + vox), "audio/wav")}).json()["id"]
    client.post(f"/api/projects/{pid}/tracks", files={"file": ("Bass.wav", wav(bass), "audio/wav")})
    client.post(f"/api/projects/{pid}/tracks", files={"file": ("Vocals.wav", wav(vox), "audio/wav")})
    j = wait(client, client.post(f"/api/projects/{pid}/kits",
                                 json={"track": "bass", "tracks": ["bass", "vocals"], "mode": "chops"}).json())
    assert j["status"] == "done", j["error"]
    counts = j["result"]["by_track"]
    assert counts["bass"] > 0 and counts["vocals"] > 0, counts
    assert sum(counts.values()) == len(j["result"]["samples"])
    # One undo step for the whole run.
    assert client.get(f"/api/projects/{pid}/history").json()["undo"] == "Extract sound bits from 2 tracks"


def test_slices_on_one_track_only(client):
    drums, _ = synth.drum_loop(bars=2)
    vocals = (0.3 * np.sin(2 * np.pi * 330 * np.arange(drums.size) / SR)).astype(np.float32)
    pid = new_project_with_drums(client, drums)
    client.post(f"/api/projects/{pid}/tracks", files={"file": ("Vocals.wav", wav(vocals), "audio/wav")})
    shared = [{"id": "a", "start": 0.0, "end": 0.5}, {"id": "b", "start": 0.5, "end": 1.0}]
    solo = [{"id": "v1", "start": 1.2, "end": 1.6, "track": "vocals"}, {"id": "v2", "start": 2.0, "end": 2.4, "track": "vocals"}]
    saved = client.put(f"/api/projects/{pid}/slices", json={"slices": shared + solo}).json()["slices"]
    assert [s.get("track") for s in saved] == [None, None, "vocals", "vocals"]  # kept through a save

    j = wait(client, client.post(f"/api/projects/{pid}/slices/export",
                                 json={"tracks": ["drums", "vocals"], "key_names": False, "bpm_names": False}).json())
    assert j["status"] == "done", j["error"]
    names = sorted(zipfile.ZipFile(io.BytesIO(client.get(j["result"]["url"]).content)).namelist())
    # Shared chops on both, numbered in step; vocals' own chops only on vocals, numbered apart.
    assert names == ["drums/drums_001.wav", "drums/drums_002.wav",
                     "vocals/vocals_001.wav", "vocals/vocals_002.wav",
                     "vocals/vocals_only_001.wav", "vocals/vocals_only_002.wav"]
    # A vocals-only chop on its own can't be exported from the drums.
    r = wait(client, client.post(f"/api/projects/{pid}/slices/export", json={"tracks": ["drums"], "slice_ids": ["v1"]}).json())
    assert r["status"] == "error" and "other tracks" in r["error"]

    # Detect on one track only: replaces that track's own chops, keeps everything else.
    got = client.post(f"/api/projects/{pid}/slices/detect", json={"track": "drums", "only_track": True}).json()["slices"]
    assert {s["id"] for s in got} >= {"a", "b", "v1", "v2"}
    drum_own = [s for s in got if s.get("track") == "drums"]
    assert len(drum_own) >= 4
    got = client.post(f"/api/projects/{pid}/slices/detect", json={"track": "drums", "only_track": True}).json()["slices"]
    assert len([s for s in got if s.get("track") == "drums"]) == len(drum_own)  # replaced, not added again

    # Removing a stem removes its own chops too.
    client.delete(f"/api/projects/{pid}/tracks/vocals")
    left = client.get(f"/api/projects/{pid}").json()["slices"]
    assert not any(s.get("track") == "vocals" for s in left) and {"a", "b"} <= {s["id"] for s in left}


def test_pad_choke_groups(client):
    y, _ = synth.drum_loop(bars=1)
    pid = new_project_with_drums(client, y)
    p = client.get(f"/api/projects/{pid}").json()
    assert p["pad_chokes"] == [None] * 64 and p["choke_all"] is False  # defaults, also for old projects
    chokes = [None] * 64
    chokes[0] = chokes[1] = 2
    r = client.put(f"/api/projects/{pid}/pads", json={"pads": p["pads"], "chokes": chokes, "choke_all": True,
                                                       "label": "Choke group"})
    assert r.status_code == 200, r.text
    p = client.get(f"/api/projects/{pid}").json()
    assert p["pad_chokes"][:3] == [2, 2, None] and p["choke_all"] is True
    # Left out = unchanged; bad values refused.
    client.put(f"/api/projects/{pid}/pads", json={"pads": p["pads"]})
    assert client.get(f"/api/projects/{pid}").json()["pad_chokes"][0] == 2
    assert client.put(f"/api/projects/{pid}/pads", json={"pads": p["pads"], "chokes": [9] + [None] * 63}).status_code == 400
    # Undoable like any pad edit.
    client.post(f"/api/projects/{pid}/undo")
    client.post(f"/api/projects/{pid}/undo")
    assert client.get(f"/api/projects/{pid}").json()["choke_all"] is False


def test_track_onsets_for_snapping(client):
    y, truth = synth.drum_loop(bars=2)
    pid = new_project_with_drums(client, y)
    hits = client.get(f"/api/projects/{pid}/tracks/drums/onsets").json()["onsets"]
    assert len(hits) == len(truth) and max(abs(a - b) for a, b in zip(hits, truth)) < 0.02
    assert client.get(f"/api/projects/{pid}/tracks/drums/onsets").json()["onsets"] == hits  # cached
