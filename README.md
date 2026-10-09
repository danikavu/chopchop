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

## System requirements

Stem separation does the heavy lifting. Everything else (slicing, kits, pads, tempo)
runs on any reasonable computer. The figures below are what the tool and model makers
recommend:
[UVR](https://github.com/Anjok07/ultimatevocalremovergui) (the app these models come
from), [Demucs](https://github.com/facebookresearch/demucs) and
[audio-separator](https://github.com/nomadkaraoke/python-audio-separator), the library
Chopper uses.

|  | Minimum | Recommended |
|---|---|---|
| **OS** | Windows 10, macOS Sonoma (14), or 64-bit Debian/Arch-based Linux | same |
| **CPU** | 64-bit; Intel Pentium/Celeron not guaranteed (UVR) | a recent multi-core CPU |
| **GPU: NVIDIA** | GTX 1060 6 GB (UVR's minimum); Demucs needs ≥ 3 GB VRAM | **8 GB+ VRAM** (UVR); Demucs uses about 7 GB at default settings |
| **GPU: Apple** | M1 or newer on macOS Sonoma+ (audio-separator) | any M-series chip |
| **GPU: AMD / Intel on Windows** | DirectML, experimental; Demucs isn't supported, so it runs on CPU | an NVIDIA card |
| **Software** | Python 3.10–3.13, Node 20.19+ (or 22.12+), ffmpeg | same |

- **Without a supported GPU,** everything still works; separation just runs on the CPU.
  Demucs takes roughly **1.5× the track's length** (so about 5 minutes for a 3-minute
  song), and the Roformer models are slower still. The GPU is several times faster.
- **Intel Macs:** stem separation isn't supported, because audio-separator needs
  PyTorch 2.3+ and PyTorch no longer publishes Intel-Mac builds. Import stems made
  elsewhere with **Import stems…**; the rest of Chopper works.
- **Python 3.14.1** isn't supported by audio-separator.
- **Getting the most out of an NVIDIA GPU:** Chopper uses the separation library's defaults
  (one chunk at a time, full precision), which are safe on every card. Larger batches
  and half precision (fp16) can speed up the Roformer, MDX23C and MDX-Net models on some
  cards and slow them down on others. On Windows, a batch too big for the card doesn't
  even fail: the driver quietly spills into system RAM and everything crawls. So measure
  instead of guessing:

  ```sh
  chopper benchmark "path/to/a typical song.wav"     # or: python scripts/run.py benchmark …
  ```

  - It times each model on a 30-second clip with each setting: batch 1 or a size based
    on free VRAM, fp16 on or off.
  - It prints the times, and saves a setting only if it was clearly faster (≥ 7%)
    without falling back to CPU. Chopper then uses it for those models.
  - `--no-save` only reports.
  - Overrides, set before starting Chopper: `CHOPPER_GPU_BATCH=N` and `CHOPPER_FP16=0|1`.
  - **Demucs isn't tunable.** It processes one ~7.8 s segment at a time, has no batch
    setting, and doesn't use fp16, so it uses about 2 GB of VRAM whatever the card. That's
    expected, not a sign the GPU is idle.
  - **Every separation is logged** to `perf.log` in the data folder: model, device,
    settings, seconds, and whether it fell back to CPU. If a GPU run fails, Chopper finishes
    on the CPU and says so in the progress ("GPU failed (…), finishing on CPU"), so a
    silent CPU run can't pass for "the GPU got slow".
  - To see real GPU load on Windows, use Task Manager → Performance → GPU, and switch one of
    the small graphs from "3D" to **"Cuda"** (or "Compute_0").
- **Disk:** PyTorch takes a few GB. Each separation model downloads once, on first use,
  and ranges from about 50 MB (Demucs) to several hundred MB (Roformer, MDX23C).
  Projects keep their stems and samples as WAV, so allow a few hundred MB per song.

## Setup

Requirements: see [System requirements](#system-requirements). You need Python, Node
and **ffmpeg** (for stem separation).

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
- **Apple Silicon:** uses the Metal GPU for every model, Demucs included (about 6× faster
  than CPU).
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
   - **Models**, with audio-separator's published quality scores (SDR, higher is cleaner).
     When you keep only one kind of stem, the panel suggests the best model for it.

     | Model | Stems | Best for |
     |---|---|---|
     | Demucs 6-stem (default) | vocals, drums, bass, guitar, piano, other | the only one with guitar/piano |
     | Demucs 4-stem fine-tuned | vocals, drums, bass, other | **drums** (10.0 dB); slow |
     | Demucs MMI | vocals, drums, bass, other | **bass** (12.2 dB) |
     | Demucs 4-stem | vocals, drums, bass, other | speed |
     | Mel-Band Roformer | vocals, instrumental | **vocals** (12.6 dB); slow |
     | BS-Roformer | vocals, instrumental | instrumental (16.5 dB) |
     | MDX-Net Kim Vocal 2 | vocals, instrumental | the classic UVR vocal model (10.2 dB); fast |
     | MDX-Net Inst HQ 4 | vocals, instrumental | a clean instrumental (15.5 dB); fast |

     The bass-*specialist* models audio-separator offers (kuielab bass, 10.4 dB) score
     lower than Demucs, so they aren't included.
   - **Remove drum bleed** is on by default whenever the model has a drums stem:
     - The separation models leave faint drums in "other" (and guitar / piano). Those
       sharp attacks then won onset detection, so sound bits cut from "other" came out
       as drum hits.
     - One extra fast Demucs pass per stem finds that bleed. It's subtracted from the
       stem and added to "drums", so nothing is lost and the stems still sum to the mix.
     - On a test song, the drums left in "other" fell from −35.6 dB to −60.7 dB.
     - Bass is left alone, because the pass would pull out 808s.
     - Turn it off in the panel, or with `--no-debleed` on the command line.
   - **Two-stage** (Mel-Band vocals → Demucs 6-stem, or → Demucs fine-tuned):
     - Stage 1 takes the vocals out with the best vocal model.
     - Stage 2 runs Demucs on the *instrumental*, so no vocals leak into drums, bass,
       guitar, piano or other.
     - On the test song with known stems, the instrument stems summed together scored
       29–31 dB against the true instrumental, versus 2.5 dB for Demucs alone. Vocals
       scored 39.8 dB vs ~0 dB.
     - The cost: isolated drums and bass come out about 1 dB lower.
   - **Stem tools** (✦ on any stem lane) run on a single stem and add new lanes. Each run
     is one undo step:
     - **Split drums** (MDX23C DrumSep) makes `drums_kick`, `drums_snare`, `drums_toms`,
       `drums_hihat`, `drums_ride` and `drums_crash`. In the drum pads, the **All drum
       parts** source builds one kit from all of them: each part's category comes from the
       model, and hits are judged against the loudest part, so faint leftovers of the split
       don't become fake toms.
     - **Remove reverb** (Mel-Band Roformer de-reverb) makes `<stem>_dry` and
       `<stem>_reverb`.
     - **Lead / backing vocals** (Mel-Band Roformer karaoke) makes `<stem>_lead` and
       `<stem>_backing`.
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
     It's drawn as thin red lines over every lane, with bar lines glowing brighter.
   - **Fixing a wrong grid:**
     - **Bar 1 at playhead** (<kbd>B</kbd>): put the playhead on the hit where a bar
       really starts. The grid shifts so a beat lands exactly on that hit, and it becomes
       beat 1. A rough position is fine, because it snaps to the nearest drum attack.
     - **Drag grid**: tick it, then drag a red bar line to slide the whole grid.
     - **Shift grid ◀ ▶** moves the grid 10 ms per click (1 ms with Shift).
     - **Bar starts at beat ◀ n ▶** moves only which beat counts as 1.
     - All of these are undoable.
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

### Batch: a whole folder at once

**Batch a whole folder →** on the home page runs a workflow over every track in a folder,
for example an album → a drum kit per song.

1. **Input folder:** browse to it, then untick any tracks to skip. Optionally include
   subfolders.
2. **Workflow:** start from a preset and change any step, or save your own:

   | Preset | What it does |
   |---|---|
   | Drum kit per song | best-drums Demucs → keep drums → split drums → 16-pad kit from the parts → export the kit |
   | Vocals + bass stems | two-stage separation, keep vocals and bass → export the stems |
   | All stems | two-stage 6-stem separation → export every stem |
   | Stems + samples | all stems + 16-pad kit from the split drums + vocal and bass chops |
   | Acapellas | best vocal model → de-reverbed acapella + instrumental |

   The steps are **Separate** (model, stems, remainder) → **Stem tools** (any tool on any
   stem) → **Analyse** (tempo, song key) → **Drum kit** (source, size, uniqueness) →
   **Sound bits** (stems, chops/phrases, how many) → **Export** (which stems, original
   mix, kit, sound bits, BPM/key in names, normalize). You can also choose whether to
   keep each song as a Chopper project to fine-tune later.
3. **Output folder:** optional; the default is Chopper's data folder. Each run makes
   `<output>/<folder> – <date>/<song>/{stems,kit,samples}/` plus a `chopper-batch.json`
   summary.

Songs run one after another, with per-song progress. A file that fails is reported and
the batch moves on to the next one. **Cancel** stops between steps, and **open** /
**folder** jump to a song's project or its files.

### Command line

Batches also run from a terminal, with no browser or server needed. Inside the venv the
command is `chopper`; anywhere, `python scripts/run.py batch …` does the same.

```sh
chopper workflows                                        # list presets + saved workflows
chopper batch ~/Music/Album -w "Drum kit per song"       # a drum kit per song
chopper batch ~/Music/Album -w "All stems" --stems vocals,bass -o ~/Stems
chopper batch ~/Music/Album -w "Drum kit per song" --kit-size 32 --only "0[1-3]*" --dry-run
chopper workflow "Acapellas" > mine.json                 # export a workflow, edit it…
chopper batch ~/Music/Album --workflow-file mine.json    # …and run it
```

- **Overrides:** flags tweak the chosen workflow for that run only:
  - separation: `--model`, `--stems`, `--rest` / `--no-rest`, `--no-separate`;
  - tools: `--tool drumsep:drums` (repeatable), `--no-tools`;
  - kit and sound bits: `--kit-size`, `--kit-source`, `--no-kit`, `--uniqueness`,
    `--bits vocals,bass`, `--bits-mode`;
  - export: `--export-stems all|kept|none|LIST`, `--export-mix`, `--no-bpm-names`,
    `--no-key-names`, `--normalize`, `--keep-projects` / `--no-keep-projects`;
  - input/output: `-o DIR`, `-r` (subfolders), `--only PATTERN`.

  `chopper batch --help` lists them all.
- **`--dry-run`** prints the plan and the track list without processing anything.
- **Progress:** a live status line (song, step, model download, GPU progress), then one
  ✓/✗ line per song. `-q` keeps only the per-song lines.
- **Ctrl+C** stops after the current step, and the results so far stay in the output
  folder. Press it twice to quit immediately.
- **Scripting:** `--json` prints the result as JSON on stdout, with progress on stderr.
  Exit codes: `0` all songs done · `1` some failed · `2` bad usage · `130` cancelled.
- **Data folder:** the command line and the app share the same data folder, so kept
  projects show up in the app (`--data DIR` uses another one).

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
- **Sound bits from pitched stems** (other, vocals, guitar, piano, bass):
  - Drum bleed is mostly removed during separation (see *Remove drum bleed*).
  - Bits must also be audible in the song (no more than 40 dB below its peak), so a
    near-empty stem gives nothing rather than leftovers.
  - Kick-like bits are dropped, except on bass, where 808s belong.
  - Only the bits about to be kept are checked for being mostly percussive, and the
    next-best candidate replaces one that is. This costs a few short clips per stem,
    not a pass over the whole track.
  - Drum stems and the full mix keep their hits, filed by drum type.
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

- On Apple Silicon, Demucs runs on the GPU through a small shim
  (`backend/chopper/mps_compat.py`). PyTorch's Metal backend rejects 1-D convolutions
  longer than 65536 steps, and Demucs convolves whole ~7.8 s segments, so the shim computes
  those convolutions in chunks. The results are identical: Metal-vs-CPU output differs
  exactly as much as two CPU runs do, because of Demucs' random shifts. If a model still
  fails on the GPU, separation retries on CPU automatically.
- Stems from the separator use the model's sample rate (44.1 kHz). All slice and sample
  positions are stored in seconds, so tracks with different sample rates still line up.
