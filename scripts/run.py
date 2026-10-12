#!/usr/bin/env python3
"""Cross-platform task runner for ChopChop (macOS, Linux, Windows). Standard library only,
so it runs with any Python 3.10+ before the virtualenv exists.

    python scripts/run.py install [--gpu auto|cuda|directml|cpu] [--no-separation]
    python scripts/run.py build                       # build the UI into frontend/dist
    python scripts/run.py run [chopchop args…]         # build the UI and start ChopChop
    python scripts/run.py dev                         # API (auto-reload) + Vite hot reload
    python scripts/run.py test                        # pytest + TypeScript typecheck
    python scripts/run.py batch FOLDER -w "Drum kit per song" [options]   # see: batch --help
    python scripts/run.py workflows                   # list batch workflows

On Windows use `py scripts\\run.py …` if `python` isn't on PATH.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
VENV = ROOT / ".venv"
WINDOWS = os.name == "nt"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")


def npm() -> str:
    # On Windows npm is a .cmd shim, which subprocess only finds via its full path.
    exe = shutil.which("npm")
    if not exe:
        sys.exit("npm not found. Install Node.js 18+ from https://nodejs.org and try again.")
    return exe


def sh(*cmd: str | Path, cwd: Path = ROOT) -> None:
    print("›", " ".join(str(c) for c in cmd), flush=True)
    code = subprocess.call([str(c) for c in cmd], cwd=cwd)
    if code:
        sys.exit(code)


def require_venv() -> Path:
    py = venv_python()
    if not py.exists():
        sys.exit("No .venv yet: run `python scripts/run.py install` first.")
    return py


def ffmpeg_hint() -> None:
    if shutil.which("ffmpeg"):
        return
    how = {
        "Windows": "winget install Gyan.FFmpeg   (then open a new terminal)",
        "Darwin": "brew install ffmpeg",
    }.get(platform.system(), "sudo apt install ffmpeg   (or your distro's package manager)")
    print(f"\nNote: ffmpeg is not installed; stem separation needs it.\n  Install: {how}\n")


# CUDA builds PyTorch publishes (download.pytorch.org/whl/cuXXX), newest first.
TORCH_CUDA = [(13, 2), (13, 0), (12, 8), (12, 6), (12, 4), (12, 1), (11, 8)]


def driver_cuda() -> tuple[int, int] | None:
    """Highest CUDA version the NVIDIA driver supports, from nvidia-smi; None if no NVIDIA GPU."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else None


def pick_gpu(choice: str) -> str:
    if choice != "auto":
        return choice
    if platform.system() == "Darwin":
        return "cpu"  # the regular PyTorch build already uses the Apple GPU (Metal)
    return "cuda" if driver_cuda() else "cpu"


def venv_eval(code: str) -> str:
    r = subprocess.run([str(venv_python()), "-c", code], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def install_cuda_torch(py: Path) -> None:
    """Swap in a CUDA build of PyTorch that the installed driver supports."""
    if venv_eval("import torch; print(torch.version.cuda or '')"):
        print("› PyTorch already has CUDA support")
        return
    driver = driver_cuda() or (99, 0)
    candidates = [v for v in TORCH_CUDA if v <= driver]
    if not candidates:
        sys.exit(f"Your NVIDIA driver only supports CUDA {driver[0]}.{driver[1]}; update the driver "
                 "(https://www.nvidia.com/drivers) or install with --gpu cpu.")
    # A CPU build is "already satisfied" for pip, so remove it first.
    subprocess.call([str(py), "-m", "pip", "uninstall", "-y", "torch", "torchvision", "torchaudio"])
    for major, minor in candidates:
        url = f"https://download.pytorch.org/whl/cu{major}{minor}"
        print(f"› trying PyTorch for CUDA {major}.{minor}")
        if subprocess.call([str(py), "-m", "pip", "install", "torch", "torchvision", "--index-url", url]) == 0:
            return
    sys.exit("Couldn't install a CUDA build of PyTorch for this Python/driver. "
             "Try a different Python version (3.10-3.13), or install with --gpu cpu.")


def install(args: argparse.Namespace) -> None:
    if sys.version_info < (3, 10):
        sys.exit(f"Python 3.10+ required (this is {platform.python_version()}).")
    if sys.version_info >= (3, 14) and not args.no_separation:
        print(f"Warning: stem separation (audio-separator) supports Python 3.10-3.13; this is "
              f"{platform.python_version()}. If the install fails, use Python 3.13 or --no-separation.")
    if not venv_python().exists():
        print(f"› creating {VENV.name} with Python {platform.python_version()}")
        venv.create(VENV, with_pip=True)
    py = venv_python()
    sh(py, "-m", "pip", "install", "--upgrade", "pip")
    if args.no_separation:
        sh(py, "-m", "pip", "install", "-e", ".[dev]")
    else:
        gpu = pick_gpu(args.gpu)
        label = {"cuda": "NVIDIA CUDA", "directml": "DirectML (AMD/Intel)",
                 "cpu": "Apple GPU (Metal) / CPU" if platform.system() == "Darwin" else "CPU"}[gpu]
        print(f"› stem separation build: {label}")
        # CPU and GPU onnxruntime packages clash; drop whichever one isn't wanted.
        unwanted = {"cuda": ["onnxruntime", "onnxruntime-directml"],
                    "directml": ["onnxruntime", "onnxruntime-gpu"],
                    "cpu": ["onnxruntime-gpu", "onnxruntime-directml"]}[gpu]
        subprocess.call([str(py), "-m", "pip", "uninstall", "-y", *unwanted],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if gpu == "cuda":
            install_cuda_torch(py)
        extra = {"cuda": "separation-cuda", "directml": "separation-directml", "cpu": "separation"}[gpu]
        sh(py, "-m", "pip", "install", "-e", f".[{extra},dev]")
        device = venv_eval("from chopchop.separation import device_info as d; i = d(); "
                           "print(i['name'] + ('  — ' + i['hint'] if i['hint'] else ''))")
        print(f"\nStem separation will run on: {device or 'unknown (check the log above)'}")
    sh(npm(), "install", cwd=FRONTEND)
    ffmpeg_hint()
    print("Done. Start ChopChop with: python scripts/run.py run")


def build(_args: argparse.Namespace | None = None) -> None:
    sh(npm(), "run", "build", cwd=FRONTEND)


def run(args: argparse.Namespace) -> None:
    py = require_venv()
    build()
    ffmpeg_hint()
    sh(py, "-m", "chopchop", *args.extra)


def dev(_args: argparse.Namespace) -> None:
    """Run the API with auto-reload and the Vite dev server; Ctrl+C stops both."""
    py = require_venv()
    procs = [
        subprocess.Popen(
            [str(py), "-m", "uvicorn", "--factory", "chopchop.app:create_app",
             "--port", "8765", "--reload", "--reload-dir", "backend"],
            cwd=ROOT,
        ),
        subprocess.Popen([npm(), "run", "dev"], cwd=FRONTEND),
    ]
    print("\nAPI on http://127.0.0.1:8765 · UI with hot reload on http://localhost:5173 · Ctrl+C to stop\n")
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def chopchop_cli(args: argparse.Namespace) -> None:
    """Pass straight through to the chopchop command line (batch, workflows, workflow)."""
    py = require_venv()
    code = subprocess.call([str(py), "-m", "chopchop", args.cmd, *args.extra], cwd=ROOT)
    sys.exit(code)


def test(_args: argparse.Namespace) -> None:
    py = require_venv()
    sh(py, "-m", "pytest", "-q")
    sh(npm(), "run", "typecheck", cwd=FRONTEND)


def main() -> None:
    parser = argparse.ArgumentParser(description="ChopChop task runner")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("install", help="create .venv and install Python + npm dependencies")
    p.add_argument("--no-separation", action="store_true", help="skip audio-separator/PyTorch (much smaller)")
    p.add_argument("--gpu", choices=["auto", "cuda", "directml", "cpu"], default="auto",
                   help="separation backend: auto (NVIDIA → cuda, else cpu; Apple Silicon uses Metal "
                        "either way), cuda, directml (AMD/Intel on Windows, experimental) or cpu")
    p.set_defaults(fn=install)
    sub.add_parser("build", help="build the UI").set_defaults(fn=build)
    sub.add_parser("run", help="build the UI and start ChopChop; extra flags (e.g. --port 9000 "
                   "--no-browser) are passed on").set_defaults(fn=run)
    sub.add_parser("dev", help="API with auto-reload + Vite hot reload").set_defaults(fn=dev)
    sub.add_parser("test", help="pytest + typecheck").set_defaults(fn=test)
    for name, text in (("batch", "run a workflow over a folder, e.g. batch ~/Music/Album -w \"Drum kit per song\""),
                       ("workflows", "list batch workflows"), ("workflow", "print a workflow as JSON"),
                       ("benchmark", "time separation models / GPU settings on this machine")):
        sub.add_parser(name, help=text, add_help=False).set_defaults(fn=chopchop_cli)
    # Unknown flags are forwarded to chopchop by `run`; other commands reject them.
    args, extra = parser.parse_known_args()
    if extra and args.fn not in (run, chopchop_cli):
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    args.extra = extra
    args.fn(args)


if __name__ == "__main__":
    main()
