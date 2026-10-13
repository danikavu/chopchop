<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="frontend/src/assets/chopchop-logo.png">
    <img src="frontend/src/assets/chopchop-logo-dark.png" alt="ChopChop" width="360">
  </picture>
</p>

# A tour of ChopChop

ChopChop turns a song into material you can play with. It splits a track into stems, chops
them on the hits or on the beat grid, and builds drum kits and sound bits named with their
key and tempo. Everything runs on your own computer, and the separation models use your
GPU when there is one.

This page walks through the app screen by screen. For installing and running it, see the
[README](README.md).

**Contents**

1. [Projects](#1-projects)
2. [Separate a track into stems](#2-separate-a-track-into-stems)
3. [The project view](#3-the-project-view)
4. [Chopping](#4-chopping)
5. [Exporting slices](#5-exporting-slices)
6. [Drum kits and sound bits](#6-drum-kits-and-sound-bits)
7. [Drum pads](#7-drum-pads)
8. [The sample browser](#8-the-sample-browser)
9. [Batch: a whole folder at once](#9-batch-a-whole-folder-at-once)
10. [Workflows](#10-workflows)
11. [Command line](#11-command-line)
12. [Keyboard shortcuts](#keyboard-shortcuts)

---

## 1. Projects

![Projects page](docs/screenshots/home.png)

The start page. **Drop a track** (WAV, FLAC, AIFF, MP3 or OGG) to create a project, or
open one you've worked on before. Each project keeps its stems, slices, samples and pads.

- **Search projects** finds a project by name (case doesn't matter).
- **×** deletes a project with all its stems and samples.
- **Batch a whole folder →** runs a workflow over an album (see [Batch](#9-batch-a-whole-folder-at-once)).

---

## 2. Separate a track into stems

![Separating a new track](docs/screenshots/separate.png)

A new project starts with just the mix. Choose a **model** and which **stems** you want:

- **Models:** Demucs (4 or 6 stems, with guitar and piano), MDX-Net, Roformer models
  (strongest on vocals), and a **two-stage** option. Two-stage first takes the vocals out
  with the best vocal model, then splits the rest. Each model's note says what it's best
  at. When you keep only stems another model is better at, a tip suggests switching.
- **Presets** (*All stems*, *Vocals + instrumental*, *Drums + rest*), or tick any mix of
  stems. The ones you leave out can be mixed into one extra track (`instrumental`,
  `no_drums`, `rest`).
- **Remove drum bleed** runs one extra fast pass. It moves the faint drums the models leave
  in *other*, *guitar* and *piano* into the drums stem.
- The banner shows where it runs, e.g. **Apple GPU (Metal)** or your NVIDIA card.

### Separate only part of the song

![Picking parts to separate](docs/screenshots/separate-parts.png)

Tick **Only parts of the track**, then drag across the waveform to mark each part (green).
Only those parts are separated, in a single model run, so a 40-second section takes a
fraction of the time a full song does. The stems are silent everywhere else but stay full
length, so they still line up with the mix.

---

## 3. The project view

![Project view with stems, beat grid and 1-bar chops](docs/screenshots/project.png)

Every stem gets its own lane under the original mix, all on one timeline.

- **Top bar:** the project name and length, the **tempo** chip (88.5 BPM here, with its
  confidence), undo / redo, **Separate…** to run it again with another model, and
  **Import stems…** if you already have stems.
- **Transport:**
  - play / pause, and which lane is playing (click a lane's name to switch);
  - **Grid** shows the red beat grid;
  - **Loop** repeats the selected chop;
  - **Only <stem>** chops the focused stem alone;
  - **Split** / **Delete** for slices;
  - the **zoom** slider.
- **Lanes:** each lane has an **export** tick (include it when exporting slices), the
  **✦** stem tools, and **×** to remove it.
- **Stem tools (✦):**
  - **Split drums** turns the drum stem into kick, snare, hi-hat, toms and cymbals;
  - **Remove reverb** makes `_dry` and `_reverb` versions;
  - **Lead / backing vocals** separates the two.

The tempo is estimated from the drums. It copes with swing and with live,
unquantized playing. If the grid starts in the wrong place, put the playhead on the real
bar start and press **B**, or nudge the grid in the Slice panel.

---

## 4. Chopping

![The Slice panel](docs/screenshots/slice-panel.png)

The **Slice** tab offers three ways to chop:

- **Detect peaks** finds every hit on the chosen track. Sensitivity, minimum gap, maximum
  length and a gate tune it. Slices start right on the attack, with no silence in front.
- **Beat grid:** pick a step (1/16 up to 4 bars) and **Chop to grid**. That gives one
  slice per cell, e.g. 1-bar loops. **Snap slices** moves every edge onto the grid.
- **By hand:** drag on any waveform to draw a slice, drag its edges to adjust it, press
  **S** to split at the playhead.

Slices are colour-coded so you can see where one chop ends and the next starts. They
appear on every lane, so you can audition the same chop across all stems. Click one to
play it, or use **← / →** to step through them. With **Loop** on, the selected chop keeps
repeating.

### Chop one stem only

![Chops on the vocals only](docs/screenshots/only-stem.png)

Hold **⌥/Alt** while drawing, or press **O** to stay in that mode. The new chops belong to
that stem alone: they have dashed edges and appear only on its lane (on the vocals above).
They export from that stem only, as `vocals_only_001.wav` …. The shared chops keep their
lined-up numbering.

---

## 5. Exporting slices

![Export section with the export folder](docs/screenshots/export.png)

- **Tracks:** choose which stems to export from (the lane ticks).
- **Options:** fades, normalizing, and **key** and **BPM** in the file names, e.g.
  `bass_012_E1.wav`, `drums_004_88bpm.wav`.
- **Skip silent chops** leaves out chops that are silent on a stem, e.g. a vocal stem
  during the beat intro. Above, 49 such files were skipped. The numbering stays in step
  across stems.
- **Export to** sets one export folder, which is remembered across sessions. Each export
  gets its own folder, `<export folder>/<project>/Slices – <date>/`, and the result has
  **Show folder**. With no folder set, exports download as a zip.

---

## 6. Drum kits and sound bits

![Kits & bits tab](docs/screenshots/kits-bits.png)

The **Kits & bits** tab works on one or more tracks:

- **Build drum kit** finds every hit and labels it (kick, snare, hat, clap, tom, perc…).
  It keeps the cleanest, most distinct take of each sound: identical hits are grouped and
  counted. Several tracks build one kit across them, e.g. split drum parts.
- **Sound bits** cuts one-shots (**Chops**) or longer **Phrases** from any stem, with key /
  note detection. Each stem is judged on its own. Leftover bleed that you can't hear in the
  song is ignored, so a near-empty stem doesn't turn into a folder of stray drum hits.
- **Extract & export** does both in one click: it extracts, then saves the new bits.
- **→ C** also exports every keyed sample **transposed to C**, in its own folder:
  - **repitched** resamples like a sampler, so length and tempo change;
  - **warped** pitch-shifts and keeps the length;
  - **both** writes the two folders.

  Samples already in C aren't repeated.

---

## 7. Drum pads

![MPC-style drum pads](docs/screenshots/pads.png)

The pads have 4 banks × 16, laid out like an MPC.

- **Auto kit → pads** builds a 16-pad kit from the drum stem. Kicks go on the bottom row,
  then snares and claps, hats, and toms / perc on top.
- Each pad shows how often its sound occurs in the song (`×163`) and its note, if it has one.
- **Playing:** click a pad, or use the keys on the pads (**M , . /**, **J K L ;**,
  **U I O P**, **7 8 9 0**). A closed hat chokes an open one.
- **Loading:** drag samples from the browser onto pads, drag pads onto each other to
  swap, right-click to clear.
- **Export pads** writes `A01_kick_04.wav` … in pad order, with the same **→ C** option.

---

## 8. The sample browser

![Sample browser](docs/screenshots/samples.png)

All samples, grouped by category, with a waveform, length, quality score and how often
each one occurs.

- **Edit:** play, rename, recategorize, set or re-detect the key (♪), trim (✂), or load a
  sample onto the selected pad (⊞).
- **Filter** by category or source track, then **Export kit** for what's shown. Files are
  named `<category>/<name>_<key>.wav`.
- With **→ C** set to *both*, the 43 keyed samples here also get C versions, repitched
  and warped.

---

## 9. Batch: a whole folder at once

![Batch page](docs/screenshots/batch.png)

Run a workflow over every track in a folder, e.g. a drum kit for each song of an album.

1. **Input folder:** browse to it and untick tracks you don't want (**Deselect all** /
   select all at the top of the list).
2. **Workflow:** start from a preset (*Drum kit per song*, *Vocals + bass stems*, *All
   stems*, *Acapellas*…) and adjust the steps:
   - **Separate** (model and stems);
   - **Stem tools**, e.g. split the drums;
   - **Analyse** tempo and key;
   - **Drum kit** (from the drum stem or its parts, kit size, uniqueness);
   - **Sound bits**;
   - **Export:** stems, kit, bits, names, and *also in C*.

   Save your own workflows by name.
3. **Output folder:** it defaults to your export folder. Each run gets its own folder with
   one subfolder per song and a `chopchop-batch.json` summary.

Songs run one after another, with live progress for each step:

![A batch run in progress](docs/screenshots/batch-running.png)

A song that fails is reported and the batch goes on. **Cancel** stops between steps.
**open** jumps to a song's project to fine-tune it, and **folder** opens its files.

Earlier runs are read back from their summaries, so they're still listed after a
restart. That includes runs found in a folder you load. **Remove** and **Clear all** only
hide runs from the list; their files stay on disk.

---

## 10. Workflows

![Workflow editor](docs/screenshots/workflows.png)

**Workflows** chains steps as a graph of nodes. The example above:

- **Stage 1:** MDX splits the song into vocals and instrumental, and both are saved.
- **Stage 2:** Demucs splits the instrumental into drums, bass and other. All three
  feed one **Sound bits** node, whose bits are saved together with copies transposed
  to C.

How it works:

- **Adding nodes:** use the palette on the left. **Separate** and **Stem tool** get one
  output per stem or part, so a 2-stem MDX model gives *vocals* and *instrumental*.
- **Connecting:** drag from an output to an input. Connections carry **audio** (orange),
  **slices** (cyan) or **samples** (purple), and only matching kinds connect. An input
  takes as many connections as you like.
- **Options:** select a node to see all of them on the right: the same choices as in the
  rest of the app.
- **Checking:** the bar shows **✓ ready**, or what's wrong.
- **Running:**
  - on one track: **Run on this project** here, or **Workflow…** in a project's top bar;
  - on a folder: **Run on a folder (batch)…**, or choose it on the Batch page under
    *Node workflows*.

  New stems, chops and samples stay in the project, so you can keep working on them.

---

## 11. Command line

The same workflows run from a terminal, without the browser:

```text
$ chopchop batch "~/ChopChop/Albums/Demo Album" -w "drum kit per song" --to-c both --dry-run
Workflow: Drum kit per song
  • separate with Demucs 4-stem fine-tuned → keep drums
  • Split drums on drums
  • detect tempo and key
  • 16-pad drum kit from parts
  • export stems: none, kit, keyed sounds also in C (both)
Tracks (3):
  01 Track A.wav
  02 Track B.wav
  03 Track C.wav
Output: ~/ChopChop/Exports/Demo Album – 2026-10-12 19.19
(dry run: nothing was processed)
```

`chopchop workflows` lists the presets, `chopchop workflow NAME > mine.json` saves one to
edit, and `chopchop benchmark` finds the fastest GPU settings for your machine. Every
option is in the [README](README.md#command-line).

---

## Keyboard shortcuts

| Key | Action |
| --- | --- |
| <kbd>Space</kbd> | play / pause the focused lane |
| <kbd>S</kbd> | split / add a slice at the playhead |
| <kbd>⌥/Alt</kbd> + drag, <kbd>⌥/Alt</kbd>+<kbd>S</kbd> | chop the focused stem only |
| <kbd>O</kbd> | toggle "only this stem" mode |
| <kbd>L</kbd> | toggle loop for the selected chop |
| <kbd>←</kbd> / <kbd>→</kbd> | step through slices and play them |
| <kbd>Del</kbd> | delete the selected slice |
| <kbd>Enter</kbd> | save the selected slice as a sample |
| <kbd>B</kbd> | make the hit at the playhead bar 1 |
| <kbd>Esc</kbd> | deselect |
| <kbd>⌘Z</kbd> / <kbd>Ctrl+Z</kbd> | undo (<kbd>⇧</kbd> or <kbd>Ctrl+Y</kbd> to redo) |
| pad keys | play the drum pads (see [Drum pads](#7-drum-pads)) |

---

<sub>Screenshots: "Medicine" by Gvidon (Pixabay), separated with Demucs 6-stem on an Apple
GPU. Folder paths are shown as `~/ChopChop/…`.</sub>
