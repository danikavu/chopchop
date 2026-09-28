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

Requirements: Python 3.10+, Node 18+, and **ffmpeg** (needed for stem separation):

```sh
brew install ffmpeg      # macOS
make install             # .venv with the Python engine + audio-separator/PyTorch, and npm deps
make run                 # builds the UI and opens http://127.0.0.1:8765
```

The first separation with each model downloads it into `data/models/`. Projects are stored
in `data/projects/`. Set `CHOPPER_DATA=/some/path` to keep them somewhere else.

If you don't need separation, `pip install -e .` installs a much lighter engine without
PyTorch. You can then import stems you already have (e.g. from the UVR GUI) with
**Import stems…**.

## Using it

1. Drop a track on the home page. Pick a model and click **Separate stems**. Demucs 6-stem
   gives vocals, drums, bass, guitar, piano and other.
2. **Slice** tab: choose a track, tune sensitivity, gap and gate, then click **Detect**.
   Slices appear on every lane, so you can listen to the same chop across all stems.
   - Drag on a waveform to draw a slice; drag its edges or body to adjust it.
   - Click a slice to play it on that lane; click a lane's name to make it the one that plays.
   - <kbd>Space</kbd> play/pause · <kbd>S</kbd> split at the playhead ·
     <kbd>Del</kbd> delete · <kbd>←</kbd>/<kbd>→</kbd> step through and audition ·
     <kbd>Enter</kbd> save the selected slice as a sample.
   - **Export** writes a zip with one folder per selected track (`drums/drums_001.wav`, …).
3. **Kits & bits** tab: **Build drum kit** on the drum stem, or **Extract sound bits**
   (chops or phrases) from any stem. Running it again on the same track replaces the samples
   it made last time; samples you made by hand are kept.
4. The **sample browser** groups samples by category. You can play, rename, recategorize and
   set the key of each sample, trim it (✂), or re-detect its key (♪). **Export kit** zips the
   samples currently shown as `kit/<category>/<name>_<key>.wav`.

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
  five time windows. Within a category, hits are ranked by quality (loud, isolated from what
  came before, not cut off by the next hit). A hit is kept only if its similarity to every
  hit already kept is below the **Uniqueness** threshold. The same drum sound re-triggered
  scores about 0.97–1.0; two different kicks score about 0.8–0.88.
- **Key.** One-shots under 1.5 s get a note from pYIN pitch tracking (e.g. `F#1`). Longer
  bits get a key from chroma matched against Krumhansl–Kessler profiles (e.g. `A min`).
  Relative major and minor keys can be confused on short phrases, so keys are editable.

## Development

```sh
make dev    # API with auto-reload on :8765 + Vite hot reload on http://localhost:5173
make test   # pytest (synthetic drums/tones with known answers) + TypeScript typecheck
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
