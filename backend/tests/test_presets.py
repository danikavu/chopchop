import json

from fastapi.testclient import TestClient

from chopchop import cli, flows, workflows
from chopchop.app import create_app
from chopchop.store import Store


def test_export_and_import_round_trip(tmp_path, capsys):
    client = TestClient(create_app(Store(tmp_path)))
    flow = next(f for f in flows.PRESETS if f.name.startswith("Drum kit")).model_dump() | {"name": "My kit flow"}
    recipe = workflows.PRESETS[0].model_dump() | {"name": "My recipe"}

    # One file with both kinds, as {"presets": [...]}.
    r = client.post("/api/presets/import", json={"presets": [flow, recipe]})
    assert r.status_code == 200, r.text
    assert r.json() == {"flows": ["My kit flow"], "workflows": ["My recipe"], "renamed": {}}
    assert client.get("/api/flows").json()["saved"][0]["nodes"] == json.loads(json.dumps(flow["nodes"]))
    assert client.get("/api/workflows").json()["saved"][0]["name"] == "My recipe"

    # Importing again doesn't overwrite: the copy gets a new name.
    r = client.post("/api/presets/import", json=flow).json()
    assert r["flows"] == ["My kit flow (imported)"] and r["renamed"] == {"My kit flow": "My kit flow (imported)"}
    r = client.post("/api/presets/import", json=[flow]).json()
    assert r["flows"] == ["My kit flow (imported 2)"]
    # A preset name that clashes with a built-in example is renamed too.
    assert client.post("/api/presets/import", json=flows.PRESETS[0].model_dump()).json()["renamed"]

    # What `chopchop workflow NAME` prints imports as is.
    assert cli.main(["workflow", "Acapellas"]) == cli.EXIT_OK
    printed = json.loads(capsys.readouterr().out)
    assert client.post("/api/presets/import", json=printed).json()["workflows"] == ["Acapellas (imported)"]


def test_bad_files_import_nothing(tmp_path):
    client = TestClient(create_app(Store(tmp_path)))
    good = workflows.PRESETS[0].model_dump() | {"name": "Fine"}
    unknown_model = good | {"name": "Odd", "separate": {**good["separate"], "model": "someone_elses.ckpt"}}
    cases = [
        ({"hello": 1}, "isn't a ChopChop workflow preset"),
        ([], "no workflow presets"),
        ([good, unknown_model], "add it on the Models page"),
        ({"kind": "flow", "name": "x", "nodes": [{"id": "a", "type": "nope"}]}, "“x”: nodes.0.type"),
        ({"kind": "flow", "name": "x", "nodes": []}, "exactly one Input"),
    ]
    for data, needle in cases:
        r = client.post("/api/presets/import", json=data)
        assert r.status_code == 400 and needle in r.json()["detail"], (needle, r.json())
    # The good preset in the rejected file wasn't saved either.
    assert client.get("/api/workflows").json()["saved"] == []
