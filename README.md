<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="frontend/src/assets/chopchop-logo.png">
    <img src="frontend/src/assets/chopchop-logo-dark.png" alt="ChopChop" width="432">
  </picture>
</p>

Automatically extract drum kits, sound bits, loops, and chopped up vocals from tracks with CHOPCHOP. 

Slice tracks, stems (slice over multiple channels), and try out various parameters. 

The best part is that you can automate everything with batch workflows.

- **Separate** a track into stems on your CPU/GPU.
- **Slice** on the hits or the beat grid, and export the same chops from every stem.
- **Build drum kits** of the cleanest, most distinct hits, and play them on MPC-style pads.
- **Cut sound bits** (one-shots or phrases) from any stem, named with their key and tempo.
- **Automate it:** batch a whole folder, or build node workflows.

## Setup

You need Python 3.10–3.13, Node 20.19+ (or 22.12+) and **ffmpeg** (for stem separation).
The same commands work on macOS, Linux and Windows:

```sh
python scripts/run.py install   # .venv with the engine + audio-separator/PyTorch, and npm deps
python scripts/run.py run       # builds the UI and opens http://127.0.0.1:8765
```

- **ffmpeg:** `brew install ffmpeg` (macOS), `winget install Gyan.FFmpeg` then a new terminal
  (Windows), or your package manager (Linux).
- **GPU:** `install` picks the right PyTorch build: CUDA for NVIDIA (detected with
  `nvidia-smi`), Metal on Apple Silicon. AMD / Intel on Windows: `install --gpu directml`
  (experimental). Force CPU: `install --gpu cpu`. The install ends by printing the device
  separation will use.
- **No separation needed?** `install --no-separation` skips PyTorch; you can still import
  stems you already have.
- On Windows, use `py scripts\run.py …` if `python` isn't on your PATH. On macOS and Linux,
  `make install` / `make run` do the same.
- Everything is stored in `data/` (projects, models, settings). Set `CHOPCHOP_DATA=/some/path`
  to keep it somewhere else.

## System requirements

Stem separation does the heavy lifting; everything else runs on any reasonable computer.
These are the figures the tool and model makers recommend
([UVR](https://github.com/Anjok07/ultimatevocalremovergui),
[Demucs](https://github.com/facebookresearch/demucs),
[audio-separator](https://github.com/nomadkaraoke/python-audio-separator)):

|  | Minimum | Recommended |
|---|---|---|
| **OS** | Windows 10, macOS Sonoma (14), or 64-bit Debian/Arch-based Linux | same |
| **CPU** | 64-bit; Intel Pentium/Celeron not guaranteed (UVR) | a recent multi-core CPU |
| **GPU: NVIDIA** | GTX 1060 6 GB (UVR's minimum); Demucs needs ≥ 3 GB VRAM | **8 GB+ VRAM** (UVR); Demucs uses about 7 GB at default settings |
| **GPU: Apple** | M1 or newer on macOS Sonoma+ (audio-separator) | any M-series chip |
| **GPU: AMD / Intel on Windows** | DirectML, experimental; Demucs isn't supported, so it runs on CPU | an NVIDIA card |
| **Software** | Python 3.10–3.13, Node 20.19+ (or 22.12+), ffmpeg | same |

- **Without a supported GPU,** everything still works, just slower: Demucs takes roughly
  1.5× the track's length on the CPU, and the Roformer models longer.
- **Intel Macs** can't run separation (PyTorch no longer publishes Intel-Mac builds); import
  stems made elsewhere instead. **Python 3.14.1** isn't supported by audio-separator.
- **Disk:** PyTorch takes a few GB; each model downloads once (about 50 MB to several hundred
  MB); allow a few hundred MB per song for stems and samples.
- **NVIDIA:** `chopchop benchmark` measures the fastest settings for your card (see
  [GPU speed and tuning](walkthrough.md#13-gpu-speed-and-tuning)).

## Using it

See the **[walkthrough](walkthrough.md)**: every screen, with screenshots, from separating a
track to batch runs, node workflows, models, the command line and keyboard shortcuts.

## Development

```sh
python scripts/run.py dev    # API with auto-reload on :8765 + Vite hot reload on http://localhost:5173
python scripts/run.py test   # pytest (synthetic drums/tones with known answers) + TypeScript typecheck
```

- `backend/chopchop/`: the FastAPI app (`app.py`) and the engine: `separation.py`
  (audio-separator wrapper), `models.py` (model registry), `slicing.py`, `analysis.py`,
  `kits.py`, `tempo.py`, `workflows.py` (batch recipes), `flows.py` (node workflows),
  `store.py` (projects, undo), `jobs.py` (background jobs), `cli.py`.
- `frontend/src/`: React + wavesurfer.js UI. `ProjectView.tsx` coordinates the lanes
  (`TrackLane.tsx`), side panels and sample browser; `WorkflowsView.tsx` is the node editor
  (React Flow).
- On Apple Silicon, Demucs runs on the GPU through `mps_compat.py`, which computes the long
  1-D convolutions PyTorch's Metal backend rejects in chunks (identical results). If a model
  still fails on the GPU, separation retries on CPU.
- Slice and sample positions are stored in seconds, so tracks with different sample rates
  line up.
