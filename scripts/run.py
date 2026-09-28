#!/usr/bin/env python3
"""Cross-platform task runner for Chopper (macOS, Linux, Windows). Standard library only,
so it runs with any Python 3.10+ before the virtualenv exists.

    python scripts/run.py install [--no-separation]   # .venv + Python deps + npm deps
    python scripts/run.py build                       # build the UI into frontend/dist
    python scripts/run.py run [chopper args…]         # build the UI and start Chopper
    python scripts/run.py dev                         # API (auto-reload) + Vite hot reload
    python scripts/run.py test                        # pytest + TypeScript typecheck

On Windows use `py scripts\\run.py …` if `python` isn't on PATH.
"""

from __future__ import annotations

import argparse
import os
import platform
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


def install(args: argparse.Namespace) -> None:
    if sys.version_info < (3, 10):
        sys.exit(f"Python 3.10+ required (this is {platform.python_version()}).")
    if not venv_python().exists():
        print(f"› creating {VENV.name} with Python {platform.python_version()}")
        venv.create(VENV, with_pip=True)
    py = venv_python()
    sh(py, "-m", "pip", "install", "--upgrade", "pip")
    extras = "dev" if args.no_separation else "separation,dev"
    sh(py, "-m", "pip", "install", "-e", f".[{extras}]")
    sh(npm(), "install", cwd=FRONTEND)
    ffmpeg_hint()
    print("Done. Start Chopper with: python scripts/run.py run")


def build(_args: argparse.Namespace | None = None) -> None:
    sh(npm(), "run", "build", cwd=FRONTEND)


def run(args: argparse.Namespace) -> None:
    py = require_venv()
    build()
    ffmpeg_hint()
    sh(py, "-m", "chopper", *args.extra)


def dev(_args: argparse.Namespace) -> None:
    """Run the API with auto-reload and the Vite dev server; Ctrl+C stops both."""
    py = require_venv()
    procs = [
        subprocess.Popen(
            [str(py), "-m", "uvicorn", "--factory", "chopper.app:create_app",
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


def test(_args: argparse.Namespace) -> None:
    py = require_venv()
    sh(py, "-m", "pytest", "-q")
    sh(npm(), "run", "typecheck", cwd=FRONTEND)


def main() -> None:
    parser = argparse.ArgumentParser(description="Chopper task runner")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("install", help="create .venv and install Python + npm dependencies")
    p.add_argument("--no-separation", action="store_true", help="skip audio-separator/PyTorch (much smaller)")
    p.set_defaults(fn=install)
    sub.add_parser("build", help="build the UI").set_defaults(fn=build)
    sub.add_parser("run", help="build the UI and start Chopper; extra flags (e.g. --port 9000 "
                   "--no-browser) are passed on").set_defaults(fn=run)
    sub.add_parser("dev", help="API with auto-reload + Vite hot reload").set_defaults(fn=dev)
    sub.add_parser("test", help="pytest + typecheck").set_defaults(fn=test)
    # Unknown flags are forwarded to chopper by `run`; other commands reject them.
    args, extra = parser.parse_known_args()
    if extra and args.fn is not run:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    args.extra = extra
    args.fn(args)


if __name__ == "__main__":
    main()
