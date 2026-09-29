# Chopper

A local app for chopping music. Load a track, and Chopper splits it into stems with UVR
models. From there you can:

- **Slice by peaks.** Detect hits on any stem and export the same slices from every stem, or
  only from the ones you pick.
- **Build drum kits.** Pull every hit from a drum stem, label it (kick, snare, clap, hihat,
  open hat, cymbal, tom, perc), and keep only the cleanest, most distinct hits of each type.
- **Cut sound bits.** Take one-shots ("chops") or longer phrases from any other stem, with
  note and key detection.

You can see the mix and all stems as waveforms, drag slice edges by hand, trim samples with
sliders, and set the category and key of each sample before exporting a kit.

## Setup

Requirements: Python 3.10+, Node 18+, and **ffmpeg** (needed for stem separation).

The same commands work on macOS, Linux and Windows:

```sh
python scripts/run.py install   # .venv with the Python engine + audio-separator/PyTorch, and npm deps
python scripts/run.py run       # builds the UI and opens http://127.0.0.1:8765
```

To install ffmpeg:

- macOS: `brew install ffmpeg`
- Windows: `winget install Gyan.FFmpeg`, then open a new terminal.
- Linux: your package manager, e.g. `sudo apt install ffmpeg`.

**GPU acceleration for stem separation.** `install` picks the right PyTorch build
automatically:

- **NVIDIA (Windows / Linux):** it detects the GPU with `nvidia-smi` and installs a CUDA
  build of PyTorch that your driver supports. The default PyTorch download on Windows is
  CPU-only.
- **Apple Silicon:** uses the Metal GPU. Demucs models still run on CPU, because PyTorch's
  Metal backend can't run them.
- **AMD / Intel on Windows:** `install --gpu directml` (experimental).
- **Force CPU:** `install --gpu cpu`.

The install ends by printing the device separation will use, and the app shows it on the
**Separate stems** banner. If you installed before GPU support existed, just run
`install` again.

On Windows, use `py scripts\run.py …` if `python` isn't on your PATH. On macOS and Linux,
`make install` / `make run` are shortcuts for the same commands.

The first separation with each model downloads it into `data/models/`. Projects are stored
in `data/projects/`. Set `CHOPPER_DATA=/some/path` to keep them somewhere else.

If you don't need separation, `python scripts/run.py install --no-separation` installs a much
lighter engine without PyTorch. You can then import stems you already have (e.g. from the UVR GUI) with
**Import stems…**.

## Using it

1. Drop a track on the home page. Pick a model and which stems you want, then click
   **Separate stems**.
   - **All stems** is the default; Demucs 6-stem gives vocals, drums, bass, guitar,
     piano and other.
   - **Presets:** **Vocals + instrumental** and **Drums + rest**, or tick any combination.
   - **The stems you leave out** can be mixed into one extra track: `instrumental` when
     only vocals are kept, `no_drums` when only drums are, `rest` otherwise. Or drop them.
   - **Separate…** in the top bar re-runs it later with another model or choice. It
     replaces the current stems, and one undo brings them back.
2. **Slice** tab: choose a track, tune sensitivity, gap and gate, then click **Detect**.
   Slices appear on every lane, so you can listen to the same chop across all stems.
   - Drag on a waveform to draw a slice; drag its edges or body to adjust it.
   - Click a slice to play it on that lane; click a lane's name to make it the one that plays.
   - <kbd>Space</kbd> play/pause · <kbd>S</kbd> split at the playhead ·
     <kbd>Del</kbd> delete · <kbd>←</kbd>/<kbd>→</kbd> step through and audition ·
     <kbd>Enter</kbd> save the selected slice as a sample.
   - **Beat grid**, once the tempo is known:
     - pick a resolution from 1/16 up to 4 bars;
     - **Chop to grid** makes one slice per cell, e.g. 1-bar loops;
     - **Snap slices** moves every slice edge onto the grid;
     - **Snap while editing** does the same when you drag, draw or split.
     The grid follows the tracked beats, so it stays aligned with a drummer who drifts.
     Bar lines are highlighted, and "Bar starts at beat ◀ n ▶" corrects the detected downbeat.
   - **Export** writes a zip with one folder per selected track. It runs in the background
     with a progress bar.
3. **Kits & bits** tab: **Build drum kit** on the drum stem (optionally loading it onto the
   pads), or **Extract sound bits**
   (chops or phrases) from any stem. Running it again on the same track replaces the samples
   it made last time; samples you made by hand are kept.
4. **Drum pads**, 4 banks × 16 pads laid out like an MPC:
   - **Auto kit → pads** builds a kit from the drum stem and loads it onto bank A. Kicks go
     on the bottom row (A01–A04), then snares and claps (A05–A08), hats (A09–A12), and
     perc and toms on top (A13–A16).
   - Each pad shows how often its sound occurs in the track (×24).
   - Play pads by clicking, or with <kbd>M</kbd> <kbd>,</kbd> <kbd>.</kbd> <kbd>/</kbd> ·
     <kbd>J</kbd> <kbd>K</kbd> <kbd>L</kbd> <kbd>;</kbd> · <kbd>U</kbd> <kbd>I</kbd>
     <kbd>O</kbd> <kbd>P</kbd> · <kbd>7</kbd> <kbd>8</kbd> <kbd>9</kbd> <kbd>0</kbd>
     (bottom row to top row).
   - A closed hat chokes a ringing open hat.
   - Load sounds by dragging a sample onto a pad, or by selecting a pad and pressing ⊞ on a
     sample. Drag one pad onto another to swap them; right-click a pad to clear it.
   - **Export pads** writes `A01_kick_01.wav`, `A05_snare_01.wav`, … in pad order.
5. The **sample browser** groups samples by category. You can play, rename, recategorize and
   set the key of each sample, trim it (✂), or re-detect its key (♪). **Export kit** zips the
   samples currently shown as `kit/<category>/<name>_<key>.wav`.

### Export file names

Every export uses `<name>[_<bpm>bpm][_<key>].wav`:

| Example | Why |
|---|---|
| `bass_001_96bpm_A2.wav` | a 2-beat bass loop: BPM plus its detected note |
| `drums_004_96bpm.wav` | a 1-bar drum loop: BPM, but no key (a drum loop has none) |
| `kick_01_F#1.wav` | a one-shot: no BPM, plus the kick's pitch |
| `A09_hihat_01.wav` | a pad export; hats and snares never get a key |

- **BPM** is added only to sounds at least about one beat long (loops, phrases), using
  the project tempo.
- **Key** comes from the sample's detected (or hand-edited) key. Slice exports detect
  it per slice; turn this off with **Key in file names** in the Slice panel.

### Undo / redo

**Ctrl+Z** on Windows and Linux, **⌘Z** on macOS, or the ↶ ↷ buttons in the top bar.
Redo is **Ctrl+Shift+Z** / **⌘⇧Z** (or **Ctrl+Y**). The tooltips name the step, e.g.
"Undo Delete slice".

- **What it covers:** slice edits, pad changes, sample deletes, renames and trims, kit
  rebuilds, stem imports or removals, stem separation (one step), and tempo changes.
- **Deleted and replaced audio** is kept until no undo step needs it, so undoing a
  delete or a trim brings the sound back.
- **Limits:** up to 100 steps per project. The history lasts until the server restarts.
- **In text fields**, Ctrl+Z / ⌘Z does the normal text undo instead.

## How the detection works

Everything is heuristic DSP (librosa/numpy), with no training data.

- **Peaks.** Onsets use a SuperFlux onset envelope. Each onset must raise the energy in at
  least one frequency band, so a kick's pitch sweep doesn't trigger a false slice, while a
  hat over a ringing kick still does. It also has to pass a loudness gate, and the slice
  start is moved back to the preceding energy minimum.
- **Drum hits.** Each hit's start is snapped to its attack using fine-resolution spectral
  flux. The spectrum of whatever was ringing just before the hit is subtracted as bleed. The
  hit is then labelled from its band energies (sub, low, mid, high, air), noisiness, decay
  time and clap-style repeated bursts.
- **Keeping the best unique hits.** Each hit gets a timbre fingerprint: its mel spectrum in
  five time windows. The same drum sound re-triggered scores about 0.97–1.0; two different
  kicks score about 0.8–0.88.
  - Hits are visited from cleanest to messiest. Quality means loud, isolated from what came
    before, and not cut off by the next hit.
  - A hit joins the cluster of the most similar sound already kept when their similarity is
    above the **Uniqueness** threshold; otherwise it starts a new cluster. Each cluster's
    representative is therefore its best-sounding take, and the cluster size gives the
    occurrence count.
  - Near-identical sounds in different categories are merged, e.g. a snare that was once
    misread as a clap.
- **Auto kit.** Picks the kit round-robin across drum types (kick, snare, hat, clap, open
  hat, perc, …), so a 16-pad kit gets variety before depth.
- **Tempo (BPM).** Detected automatically once there's a drum stem; the **BPM** chip in the
  top bar has details and controls. Swing and unquantized live playing are handled like this:
  - It scores candidate tempos by how periodic the drum hits are at 1, 2 and 4 beats.
    Swing shifts the off-beat hats but not the quarter-note pulse, so swung and straight
    grooves read the same tempo.
  - It tracks individual beats and snaps each one to a real hit, so the grid follows a
    drummer who speeds up or slows down.
  - Steady tracks get a sub-frame precise BPM from a straight-line fit over all the beats.
  - Drifting tracks report the median tempo and the range, e.g. `92.3–95.4`.
  - Swing is measured from where the off-beat hits fall between beats, MPC-style, e.g.
    `60% (16ths)`.
  - **Confidence** is shown as a breakdown:
    - *clarity*: how clearly one tempo stands out;
    - *stability*: whether the same tempo holds across the track;
    - *on grid*: how many beats land on a hit;
    - *timing*: how tight the playing is;
    - *half/double*: how sure it isn't half or double time.
  - Half/double time is the classic ambiguity (72 vs 144). The panel lists the
    alternatives, and ÷2 / ×2 buttons, typing a value or tap tempo override it.
- **Key.** One-shots under 1.5 s get a note from pYIN pitch tracking (e.g. `F#1`). Longer
  bits get a key from chroma matched against Krumhansl–Kessler profiles (e.g. `A min`).
  Relative major and minor keys can be confused on short phrases, so keys are editable.

## Development

```sh
python scripts/run.py dev    # API with auto-reload on :8765 + Vite hot reload on http://localhost:5173
python scripts/run.py test   # pytest (synthetic drums/tones with known answers) + TypeScript typecheck
```

- `backend/chopper/`
  - `app.py`: FastAPI routes
  - `slicing.py`: onsets and slice export
  - `analysis.py`: features, classification, note and key
  - `kits.py`: drum kits, sound bits, kit export
  - `separation.py`: audio-separator wrapper
  - `store.py`: on-disk projects
  - `jobs.py`: background jobs
- `frontend/src/`: React + wavesurfer.js UI. `ProjectView.tsx` coordinates the lanes
  (`TrackLane.tsx`), side panels and sample browser.

Notes:

- On Apple Silicon, Demucs models run on CPU (PyTorch's MPS backend can't run their
  convolutions). Other models try the GPU first and fall back to CPU.
- Stems from the separator use the model's sample rate (44.1 kHz). All slice and sample
  positions are stored in seconds, so tracks with different sample rates still line up.
