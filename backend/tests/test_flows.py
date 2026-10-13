import time
from pathlib import Path

import pytest

from chopchop import flows

from .test_workflows import env, run  # noqa: F401  (fixture + batch helper)


def preset(name):
    return next(f for f in flows.PRESETS if f.name == name).model_dump()


def wait(client, job):
    for _ in range(600):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] not in ("pending", "running"):
            return j
        time.sleep(0.05)
    raise TimeoutError


def flow(nodes, edges, **kw):
    return {"name": "t", "nodes": [{"id": i, "type": t, "params": p} for i, t, p in nodes],
            "edges": [{"source": a, "port": b, "target": c} for a, b, c in edges], **kw}


def test_check_catches_mistakes(env):
    client, _, _ = env
    bad = [
        (flow([("a", "input", {}), ("b", "input", {})], []), "exactly one Input"),
        (flow([("in", "input", {}), ("s", "save_samples", {})], [("in", "mix", "s")]), "can't connect audio"),
        (flow([("in", "input", {}), ("x", "separate", {"model": "nope"})], []), "unknown model"),
        (flow([("in", "input", {}), ("x", "separate", {})], [("in", "drums", "x")]), "no output 'drums'"),
        (flow([("in", "input", {}), ("a", "tool", {}), ("b", "tool", {})],
              [("in", "mix", "a"), ("a", "kick", "b"), ("b", "kick", "a")]), "loop"),
        (flow([("in", "input", {}), ("k", "kit", {"kit_size": 999})], []), "Drum kit"),
    ]
    for f, needle in bad:
        r = client.post("/api/flows/check", json=f)
        assert r.status_code == 400 and needle in r.json()["detail"], (needle, r.json())
    ok = client.post("/api/flows/check", json=preset("Vocals, then bits from the instrumental in C")).json()
    assert ok["order"][0] == "in" and ok["order"].index("mdx") < ok["order"].index("demucs") < ok["order"].index("bits")


def test_two_stage_workflow_on_a_project(env, tmp_path):
    client, album, _ = env
    client.put("/api/settings", json={"export_dir": str(tmp_path / "exp")})
    src = album / "01 Song 1.wav"
    pid = client.post("/api/projects", files={"file": (src.name, src.read_bytes(), "audio/wav")},
                      data={"name": "Song"}).json()["id"]
    f = preset("Vocals, then bits from the instrumental in C")
    j = wait(client, client.post(f"/api/projects/{pid}/flow", json={"flow": f}).json())
    assert j["status"] == "done", j["error"]
    res = j["result"]
    out = Path(res["output"])
    assert out.parent == tmp_path / "exp" / "Song" and out.name.startswith(f["name"])

    # Stage 1: both MDX stems saved; stage 2: Demucs on the instrumental, kept as its own tracks.
    names = {t["name"] for t in client.get(f"/api/projects/{pid}").json()["tracks"]}
    assert {"mix", "vocals", "instrumental", "instrumental_drums", "instrumental_bass", "instrumental_other"} <= names
    stems = sorted(p.name.split("_")[0] for p in (out / "1 stems").glob("*.wav"))
    assert stems == ["instrumental", "vocals"]
    # Bits from every instrumental stem, plus their copies in C.
    bits = out / "2 bits in C"
    origin = {s["track"] for s in client.get(f"/api/projects/{pid}").json()["samples"]}
    assert origin and origin <= {"instrumental_drums", "instrumental_bass", "instrumental_other"}
    assert res["samples"] > 0 and any(bits.glob("*/*.wav"))
    assert (bits / "In C (repitched)").exists() or (bits / "In C (warped)").exists() or not any(
        s.get("key") for s in client.get(f"/api/projects/{pid}").json()["samples"])
    assert res["files"] == len(list(out.rglob("*.wav")))


def test_workflow_as_a_batch_with_slices_and_kit(env, tmp_path):
    client, album, _ = env
    f = flow(
        [("in", "input", {}), ("sep", "separate", {"model": "htdemucs_ft.yaml", "stems": ["drums", "bass"]}),
         ("split", "tool", {"tool": "drumsep"}), ("kit", "kit", {"kit_size": 8}),
         ("pads", "save_samples", {"folder": "kit", "pad_names": True, "bpm_names": False}),
         ("loops", "slices", {"mode": "grid", "grid_beats": 4}), ("save", "save_slices", {"folder": "loops"})],
        [("in", "mix", "sep"), ("sep", "drums", "split"),
         *[("split", s, "kit") for s in ("kick", "snare", "hihat")], ("kit", "samples", "pads"),
         ("sep", "bass", "loops"), ("sep", "drums", "loops"), ("loops", "slices", "save")],
        name="Kit + loops", keep_projects=False)
    j = run(client, input_dir=str(album), files=["01 Song 1.wav", "02 Song 2.wav"], output_dir=str(tmp_path / "o"),
            flow=f)
    assert j["status"] == "done", j["error"]
    songs = j["result"]["songs"]
    assert [s["status"] for s in songs] == ["done", "done"]
    for s in songs:
        song = Path(s["output"])
        kit = sorted(p.name for p in (song / "kit").glob("*.wav"))
        assert kit and kit[0].startswith("A01_kick")  # pad-named, in pad order
        loops = song / "loops"
        assert sorted(p.name for p in loops.iterdir()) == ["bass", "drums"]
        assert any("bpm" in p.name for p in (loops / "drums").glob("*.wav"))  # 1-bar loops carry the tempo
        assert "project" not in s  # keep_projects off
    assert client.get("/api/projects").json() == []
    # The run shows in the batch history under the workflow's name.
    runs = client.get("/api/batches").json()
    assert runs[0]["detail"]["workflow"] == "Kit + loops"


def test_save_and_delete_flows(env):
    client, _, _ = env
    f = preset("Drum kit from split drums") | {"name": "Mine"}
    assert [x["name"] for x in client.put("/api/flows", json=f).json()["saved"]] == ["Mine"]
    listed = client.get("/api/flows").json()
    assert {p["name"] for p in listed["presets"]} >= {"Drum kit from split drums"} and listed["saved"][0]["name"] == "Mine"
    assert client.delete("/api/flows/Mine").json()["saved"] == []


@pytest.mark.parametrize("step,expect", [(4, 3), (1, 9), (0.5, 17)])
def test_grid_points(step, expect):
    beats = [0.5 * i for i in range(8)]  # 8 beats, 120 BPM
    assert len(flows.grid_points(beats, step)) == expect
