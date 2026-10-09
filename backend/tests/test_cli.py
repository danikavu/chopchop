import json
import signal
import threading
import time
from pathlib import Path

import pytest
import soundfile as sf

from chopper import cli, separation

from . import synth
from .synth import SR
from .test_workflows import fake_separate, fake_tool


@pytest.fixture
def album(tmp_path, monkeypatch):
    monkeypatch.setenv("CHOPPER_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(separation, "status", lambda: {"available": True, "reason": None})
    monkeypatch.setattr(separation, "separate", fake_separate)
    monkeypatch.setattr(separation, "run_tool", fake_tool)
    monkeypatch.setattr(separation, "debleed", lambda stems, out, progress=None: stems)
    monkeypatch.setattr(separation, "QUIET", False)
    folder = tmp_path / "Album"
    folder.mkdir()
    for i, bpm in enumerate((96, 120)):
        sf.write(folder / f"0{i + 1} Song {i + 1}.wav", synth.groove(bpm=bpm, bars=4)[0], SR)
    return folder


def test_batch_drum_kits(album, tmp_path, capsys):
    out = tmp_path / "out"
    code = cli.main(["batch", str(album), "-w", "drum kit per song", "-o", str(out), "-q"])
    text = capsys.readouterr().out
    assert code == cli.EXIT_OK, text
    assert "Workflow: Drum kit per song" in text and "• Split drums on drums" in text
    assert "✓ 01 Song 1 — " in text and "pads" in text and "Finished 2/2 songs" in text
    run = next(out.iterdir())
    assert sorted(p.name for p in run.iterdir()) == ["01 Song 1", "02 Song 2", "chopper-batch.json"]
    assert any(p.name.startswith("A01_kick") for p in (run / "01 Song 1" / "kit").iterdir())


def test_overrides_and_json(album, capsys):
    code = cli.main(["batch", str(album), "-w", "All stems", "--stems", "vocals,bass", "--kit-size", "8",
                     "--no-keep-projects", "--only", "02*", "--json"])
    captured = capsys.readouterr()
    assert code == cli.EXIT_OK, captured.err
    result = json.loads(captured.out)  # stdout is pure JSON; the progress went to stderr
    assert result["done"] == 1 and len(result["songs"]) == 1
    song = result["songs"][0]
    stems = sorted(p.name.split("_")[0] for p in (Path(song["output"]) / "stems").iterdir())
    assert stems == ["bass", "vocals"]
    assert "project" not in song  # --no-keep-projects
    assert "8-pad drum kit" in captured.err


def test_dry_run_processes_nothing(album, capsys):
    assert cli.main(["batch", str(album), "-w", "Acapellas", "--dry-run"]) == cli.EXIT_OK
    text = capsys.readouterr().out
    assert "Remove reverb on vocals" in text and "01 Song 1.wav" in text and "dry run" in text
    assert not (Path(text.split("Output: ")[1].splitlines()[0])).exists()


def test_workflow_file_roundtrip(album, tmp_path, capsys):
    assert cli.main(["workflow", "Vocals + bass stems"]) == cli.EXIT_OK
    wf = json.loads(capsys.readouterr().out)
    wf["name"] = "Just vocals"
    wf["separate"]["stems"] = ["vocals"]
    f = tmp_path / "wf.json"
    f.write_text(json.dumps(wf))
    assert cli.main(["batch", str(album), "--workflow-file", str(f), "--only", "01*", "-q"]) == cli.EXIT_OK
    assert "Workflow: Just vocals" in capsys.readouterr().out


@pytest.mark.parametrize("argv,needle", [
    (["batch", "/no/such/folder", "-w", "All stems"], "not a folder"),
    (["batch", "{album}", "-w", "Nope"], "no workflow called 'Nope'"),
    (["batch", "{album}"], "choose a workflow"),
    (["batch", "{album}", "-w", "All stems", "--stems", "kazoo"], "doesn't make: kazoo"),
    (["batch", "{album}", "-w", "All stems", "--tool", "drumsep"], "TOOL:STEM"),
    (["batch", "{album}", "-w", "All stems", "--only", "zz*"], "no files match"),
])
def test_usage_errors(album, capsys, argv, needle):
    argv = [a.replace("{album}", str(album)) for a in argv]
    assert cli.main(argv) == cli.EXIT_USAGE
    assert needle in capsys.readouterr().err


def test_failed_song_exit_code(album, capsys):
    (album / "03 Broken.wav").write_bytes(b"nope")
    assert cli.main(["batch", str(album), "-w", "All stems", "-q"]) == cli.EXIT_FAILED
    assert "✗ 03 Broken — could not read audio" in capsys.readouterr().out


def test_ctrl_c_cancels_between_steps(album, monkeypatch, capsys):
    def slow(*a, **k):
        time.sleep(0.4)
        return fake_separate(*a, **k)

    monkeypatch.setattr(separation, "separate", slow)
    threading.Timer(0.2, lambda: signal.raise_signal(signal.SIGINT)).start()
    assert cli.main(["batch", str(album), "-w", "All stems", "-q"]) == cli.EXIT_CANCELLED
    out = capsys.readouterr().out
    assert "Stopping after the current step" in out and "Cancelled after 0/2 songs" in out
