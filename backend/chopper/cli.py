"""Command line: `chopper` (or `python -m chopper`).

    chopper                         start the app (same as `chopper serve`)
    chopper batch FOLDER -w NAME    run a workflow over every track in a folder
    chopper workflows               list presets and saved workflows
    chopper workflow NAME           print a workflow as JSON (edit it, use with --workflow-file)

Exit codes for `batch`: 0 every song done, 1 some songs failed, 2 bad usage,
130 cancelled with Ctrl+C.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import sys
import time
import warnings
from pathlib import Path

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_CANCELLED = 0, 1, 2, 130


# ---------- serve ----------

def cmd_serve(args) -> int:
    import webbrowser

    import uvicorn

    from .app import create_app

    logging.basicConfig(level=logging.INFO)
    if not args.no_browser:
        webbrowser.open(f"http://{args.host}:{args.port}")
    uvicorn.run(create_app(), host=args.host, port=args.port)
    return EXIT_OK


# ---------- workflows ----------

def _all_workflows():
    from . import workflows

    return [(w.name, "preset", w) for w in workflows.PRESETS] + \
        [(w["name"], "saved", workflows.Workflow(**w)) for w in workflows.saved()]


def _find_workflow(name: str):
    matches = [w for n, _, w in _all_workflows() if n.lower() == name.lower()]
    # A saved workflow with a preset's name wins (it was saved later).
    return matches[-1] if matches else None


def cmd_workflows(args) -> int:
    items = _all_workflows()
    if args.json:
        print(json.dumps([{"name": n, "kind": k, **w.model_dump()} for n, k, w in items], indent=1))
        return EXIT_OK
    width = max(len(n) for n, _, _ in items)
    for n, kind, w in items:
        print(f"  {n:<{width}}  [{kind}]  {w.description}")
    print('\nUse one with: chopper batch FOLDER --workflow "NAME"   (show it as JSON: chopper workflow "NAME")')
    return EXIT_OK


def cmd_workflow(args) -> int:
    wf = _find_workflow(args.name)
    if wf is None:
        return _usage(f"no workflow called {args.name!r}; see `chopper workflows`")
    print(json.dumps(wf.model_dump(), indent=2))
    return EXIT_OK


# ---------- batch ----------

def _csv(value: str) -> list[str] | None:
    return None if value.strip().lower() == "all" else [s.strip() for s in value.split(",") if s.strip()]


def _apply_overrides(wf, a):
    """Command-line flags tweak a copy of the chosen workflow."""
    from .workflows import ToolStep

    wf = wf.model_copy(deep=True)
    if a.model:
        wf.separate.model, wf.separate.stems, wf.separate.enabled = a.model, None, True
    if a.stems is not None:
        wf.separate.stems = _csv(a.stems)
    if a.no_separate:
        wf.separate.enabled = False
    if a.no_debleed:
        wf.separate.debleed = False
    if a.rest is not None:
        wf.separate.rest = a.rest
    if a.tool:
        for spec in a.tool:
            tool, _, on = spec.partition(":")
            if not on:
                raise ValueError(f"--tool needs TOOL:STEM, e.g. drumsep:drums (got {spec!r})")
            wf.tools.append(ToolStep(tool=tool, on=on))
    if a.no_tools:
        wf.tools = []
    if a.kit_size is not None:
        wf.kit.enabled, wf.kit.size = True, a.kit_size
        wf.export.kit = True
    if a.kit_source:
        wf.kit.enabled, wf.kit.source = True, a.kit_source
    if a.no_kit:
        wf.kit.enabled, wf.export.kit = False, False
    if a.uniqueness is not None:
        wf.kit.uniqueness = wf.bits.uniqueness = a.uniqueness
    if a.bits is not None:
        wf.bits.enabled, wf.bits.stems, wf.export.samples = True, _csv(a.bits) or [], True
    if a.bits_mode:
        wf.bits.mode = a.bits_mode
    if a.export_stems is not None:
        v = a.export_stems.strip().lower()
        wf.export.stems = v if v in ("all", "kept", "none") else _csv(a.export_stems)
    if a.export_mix:
        wf.export.mix = True
    if a.no_bpm_names:
        wf.export.bpm_names = False
    if a.no_key_names:
        wf.export.key_names = False
    if a.normalize:
        wf.export.normalize = True
    if a.keep_projects is not None:
        wf.keep_projects = a.keep_projects
    return wf


def _plan(wf) -> list[str]:
    from . import separation

    lines = []
    if wf.separate.enabled:
        m = next((m for m in separation.MODELS if m["id"] == wf.separate.model), {"label": wf.separate.model})
        keep = ", ".join(wf.separate.stems) if wf.separate.stems else "all stems"
        lines.append(f"separate with {m['label']} → keep {keep}{' (+ the rest mixed into one track)' if wf.separate.rest else ''}")
    for t in wf.tools:
        lines.append(f"{separation.TOOLS.get(t.tool, {}).get('label', t.tool)} on {t.on}")
    if wf.tempo or wf.key:
        lines.append("detect " + " and ".join(x for x, on in (("tempo", wf.tempo), ("key", wf.key)) if on))
    if wf.kit.enabled:
        lines.append(f"{wf.kit.size}-pad drum kit from {wf.kit.source}")
    if wf.bits.enabled:
        lines.append(f"{wf.bits.mode} from {', '.join(wf.bits.stems)} (up to {wf.bits.max} each)")
    e = wf.export
    what = [f"stems: {e.stems if isinstance(e.stems, str) else ', '.join(e.stems)}"]
    what += [x for x, on in (("mix", e.mix), ("kit", e.kit and wf.kit.enabled), ("sound bits", e.samples and wf.bits.enabled)) if on]
    lines.append("export " + ", ".join(what))
    if not wf.keep_projects:
        lines.append("delete each song's project afterwards")
    return lines


class _Terminal:
    """Progress for a batch: one live status line on a terminal, plain lines otherwise."""

    def __init__(self, stream, quiet: bool):
        self.stream = stream
        self.live = stream.isatty() and not quiet
        self.quiet = quiet
        self.last = 0.0

    def status(self, frac: float, msg: str) -> None:
        if not self.live:
            return
        now = time.time()
        if now - self.last < 0.1:
            return
        self.last = now
        width = shutil.get_terminal_size((100, 20)).columns
        line = f"[{frac * 100:3.0f}%] {msg}"
        self.stream.write("\r" + line[: width - 1].ljust(width - 1))
        self.stream.flush()

    def line(self, text: str) -> None:
        if self.live:
            width = shutil.get_terminal_size((100, 20)).columns
            self.stream.write("\r" + " " * (width - 1) + "\r")
        self.stream.write(text + "\n")
        self.stream.flush()


class _CliJob:
    """What run_batch expects from a job: update(), check_cancel(), detail."""

    def __init__(self, term: _Terminal):
        self.term = term
        self.detail = None
        self.cancel_requested = False

    def update(self, progress=None, message=None):
        if progress is not None and message is not None:
            self.term.status(progress, message)

    def check_cancel(self):
        from .jobs import Cancelled

        if self.cancel_requested:
            raise Cancelled()


def _song_line(s: dict) -> str:
    if s["status"] == "done":
        bits = [f"{s['bpm']} BPM" if s.get("bpm") else None, s.get("key"),
                f"{s['kit']} pads" if s.get("kit") else None,
                f"{s['samples']} sound bits" if s.get("samples") else None,
                f"{s.get('files', 0)} files", f"{s['seconds']:.0f}s" if s.get("seconds") else None]
        text = f"  ✓ {s['song']} — " + " · ".join(b for b in bits if b)
        if s.get("timings") and len(s["timings"]) > 1:
            text += "\n      " + " · ".join(f"{k} {v:.0f}s" for k, v in s["timings"].items())
        for w in s.get("warnings") or []:
            text += f"\n      ! {w}"
        return text
    return f"  ✗ {s['song']} — {s['message']}"


def cmd_batch(args) -> int:
    from . import separation, workflows
    from .jobs import Cancelled
    from .service import Chopper
    from .store import Store

    if args.data:
        os.environ["CHOPPER_DATA"] = str(Path(args.data).expanduser())
    out = sys.stderr if args.json else sys.stdout  # keep stdout clean for --json

    if args.workflow_file:
        try:
            wf = workflows.Workflow(**json.loads(Path(args.workflow_file).expanduser().read_text()))
        except (OSError, ValueError) as e:
            return _usage(f"can't read workflow file: {e}")
    elif args.workflow:
        wf = _find_workflow(args.workflow)
        if wf is None:
            names = ", ".join(f'"{n}"' for n, _, _ in _all_workflows())
            return _usage(f"no workflow called {args.workflow!r}. Available: {names}")
    else:
        return _usage('choose a workflow with --workflow "NAME" or --workflow-file FILE (list them: chopper workflows)')

    try:
        wf = _apply_overrides(wf, args)
        workflows.check(wf)
        files, out_root = workflows.prepare(args.folder, None, args.recursive, args.output)
    except ValueError as e:
        return _usage(str(e))
    if args.only:
        import fnmatch

        files = [f for f in files if any(fnmatch.fnmatch(f.name.lower(), p.lower()) for p in args.only)]
        if not files:
            return _usage(f"no files match {', '.join(args.only)}")

    print(f"Workflow: {wf.name}", file=out)
    for step in _plan(wf):
        print(f"  • {step}", file=out)
    print(f"Tracks ({len(files)}):", file=out)
    for f in files:
        print(f"  {f.name}", file=out)
    print(f"Output: {out_root}", file=out)
    if args.dry_run:
        print("(dry run: nothing was processed)", file=out)
        return EXIT_OK

    if (wf.separate.enabled or wf.tools) and not separation.status()["available"]:
        return _usage(f"stem separation unavailable: {separation.status()['reason']}")

    if not args.verbose:
        separation.QUIET = True
        warnings.filterwarnings("ignore")
        logging.basicConfig(level=logging.WARNING)
    else:
        logging.basicConfig(level=logging.INFO)

    term = _Terminal(out, args.quiet)
    job = _CliJob(term)

    def on_sigint(_sig, _frame):
        if job.cancel_requested:  # second Ctrl+C: stop right now
            raise KeyboardInterrupt
        job.cancel_requested = True
        term.line("Stopping after the current step… (Ctrl+C again to quit immediately)")

    previous = signal.signal(signal.SIGINT, on_sigint)
    print("", file=out)
    started = time.time()
    try:
        result = workflows.run_batch(Chopper(Store()), files, wf, out_root, job,
                                     on_song=lambda s: term.line(_song_line(s)))
    except Cancelled:
        songs = (job.detail or {}).get("songs", [])
        done = sum(s["status"] == "done" for s in songs)
        term.line(f"\nCancelled after {done}/{len(songs)} songs. Results so far: {out_root}")
        if args.json:
            print(json.dumps({"output": str(out_root), "cancelled": True, "songs": songs}, indent=1))
        return EXIT_CANCELLED
    finally:
        signal.signal(signal.SIGINT, previous)

    took = time.time() - started
    term.line(f"\nFinished {result['done']}/{len(files)} songs in {took / 60:.1f} min"
              + (f" ({result['failed']} failed)" if result["failed"] else "") + f"\n→ {result['output']}")
    if args.json:
        print(json.dumps(result, indent=1))
    return EXIT_OK if result["failed"] == 0 else EXIT_FAILED


# ---------- benchmark ----------

BENCH_MODELS = {
    "demucs": "htdemucs_ft.yaml",
    "drumsep": "MDX23C-DrumSep-aufr33-jarredou.ckpt",
    "vocals": "vocals_mel_band_roformer.ckpt",
    "mdx": "Kim_Vocal_2.onnx",
}


def cmd_benchmark(args) -> int:
    """Time each model with each GPU setting on this machine; save what's measurably faster."""
    import soundfile as sf

    from . import audio, separation
    from .store import data_dir

    if args.data:
        os.environ["CHOPPER_DATA"] = str(Path(args.data).expanduser())
    st = separation.status()
    if not st["available"]:
        return _usage(f"stem separation unavailable: {st['reason']}")
    names = [m.strip() for m in args.models.split(",") if m.strip()]
    unknown = [m for m in names if m not in BENCH_MODELS]
    if unknown:
        return _usage(f"unknown model {unknown[0]!r}; choose from {', '.join(BENCH_MODELS)}")

    src = Path(args.file).expanduser()
    try:
        y, sr = audio.read(src)
    except Exception as e:  # noqa: BLE001
        return _usage(f"can't read {src}: {e}")
    n = min(y.shape[1], int(args.seconds * sr))
    start = max(0, (y.shape[1] - n) // 2)  # the middle: intros are often quiet
    work = data_dir() / "benchmark"
    work.mkdir(parents=True, exist_ok=True)
    clip = work / "clip.wav"
    sf.write(clip, y[:, start:start + n].T, sr)
    clip_s = n / sr

    separation.QUIET = not args.verbose
    warnings.filterwarnings("ignore")
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    import torch

    cuda = torch.cuda.is_available()
    info = separation.device_info()
    print(f"Device: {info['name']} · clip: {clip_s:.0f} s from {src.name}")
    if not cuda:
        print("No NVIDIA GPU: batch size and fp16 only apply to CUDA, so this just times each model.")
    fp16_ok = cuda and torch.cuda.get_device_capability(0)[0] >= 7

    def timed(model: str, batch: int, fp16: bool) -> tuple[float, dict]:
        os.environ["CHOPPER_GPU_BATCH"], os.environ["CHOPPER_FP16"] = str(batch), "1" if fp16 else "0"
        out = work / "out"
        shutil.rmtree(out, ignore_errors=True)
        t = time.time()
        with separation._lock:
            separation._run_model(clip, out, model, lambda p, m: None)
        return time.time() - t, dict(separation.LAST_RUN)

    results: dict[str, list] = {}
    saved_env = {k: os.environ.get(k) for k in ("CHOPPER_GPU_BATCH", "CHOPPER_FP16")}
    try:
        for name in names:
            model = BENCH_MODELS[name]
            arch = separation.arch_of(model)
            print(f"\n{name} ({model})")
            print("  warm-up (download / load, not timed)…", end="", flush=True)
            timed(model, 1, False)
            print(" done")
            candidates = [(1, False)]
            if cuda and arch != "demucs":
                big = separation.auto_batch(arch)
                candidates += [(1, True)] if fp16_ok else []
                candidates += [(big, False)] + ([(big, True)] if fp16_ok else []) if big > 1 else []
            rows = []
            for batch, fp16 in candidates:
                setting = f"  batch {batch:<2} {'fp16' if fp16 else 'fp32'}"
                try:
                    secs, run = timed(model, batch, fp16)
                except Exception as e:  # noqa: BLE001 - a setting that breaks the model is a result too
                    rows.append({"batch": batch, "fp16": fp16, "seconds": float("inf"), "fallback": str(e)})
                    print(f"{setting}  failed: {str(e)[:90]}")
                    continue
                # A run that only worked after falling back (to CPU, or fp16 → fp32) doesn't count.
                failed = run.get("fallback") or (fp16 and run.get("fp16_failed") and "fp16 gave invalid audio")
                rows.append({"batch": batch, "fp16": fp16, "seconds": secs, "fallback": failed})
                note = " (fell back to CPU!)" if run.get("fallback") else \
                    " (fp16 gave invalid audio; ran in fp32)" if failed else ""
                speed = rows[0]["seconds"] / secs
                print(f"{setting}  {secs:6.1f}s  {clip_s / secs:5.1f}× realtime  {speed:4.2f}× vs default{note}")
            results[name] = rows
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # Best setting per architecture: total time over that architecture's models; settings
    # that fell back to CPU on any model are out.
    best: dict[str, dict] = {}
    for arch in ("mdx", "mdxc"):
        tested = [rows for n, rows in results.items() if separation.arch_of(BENCH_MODELS[n]) == arch]
        if not tested or len(tested[0]) < 2:
            continue
        totals: dict[tuple, float] = {}
        for rows in tested:
            for r in rows:
                key = (r["batch"], r["fp16"])
                totals[key] = totals.get(key, 0.0) + (float("inf") if r["fallback"] else r["seconds"])
        choice = min(totals, key=totals.get)
        if choice != (1, False) and totals[choice] < totals[(1, False)] * 0.93:  # clearly faster only
            best[arch] = {"batch": choice[0], "fp16": choice[1]}
    print()
    if not best:
        print("Result: the default (batch 1, fp32) is as fast as anything else here; keeping it.")
        if not args.no_save and separation.gpu_settings():
            separation.save_gpu_settings({})
            print("Cleared previously saved GPU settings.")
        return EXIT_OK
    for arch, b in best.items():
        label = {"mdx": "MDX-Net", "mdxc": "Roformer / MDX23C"}[arch]
        print(f"Result: {label} is fastest with batch {b['batch']}{' + fp16' if b['fp16'] else ''}.")
    if args.no_save:
        print("(not saved: --no-save)")
    else:
        separation.save_gpu_settings(best)
        print(f"Saved to {data_dir() / 'settings.json'}; ChopChop uses these from now on (restart the app).")
    return EXIT_OK


# ---------- parser ----------

def _usage(msg: str) -> int:
    print(f"chopper: {msg}", file=sys.stderr)
    return EXIT_USAGE


def _bool_flag(p, name: str, dest: str, help_on: str, help_off: str) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument(f"--{name}", dest=dest, action="store_true", default=None, help=help_on)
    g.add_argument(f"--no-{name}", dest=dest, action="store_false", help=help_off)


def build_parser() -> argparse.ArgumentParser:
    name = Path(sys.argv[0]).stem if sys.argv and Path(sys.argv[0]).stem in ("chopper", "chopchop") else "chopchop"
    p = argparse.ArgumentParser(prog=name, description="ChopChop: slice tracks, build drum kits and stems.",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog='Examples:\n'
                                       '  chopper                                    start the app\n'
                                       '  chopper workflows                          list workflows\n'
                                       '  chopper batch ~/Music/Album -w "Drum kit per song"\n'
                                       '  chopper batch ~/Music/Album -w "All stems" --stems vocals,bass -o ~/Stems\n'
                                       '  chopper batch ~/Music/Album -w "Drum kit per song" --kit-size 32 --dry-run')
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="start the app (the default)")
    for target in (p, s):
        target.add_argument("--host", default="127.0.0.1")
        target.add_argument("--port", type=int, default=8765)
        target.add_argument("--no-browser", action="store_true", help="don't open the browser")

    b = sub.add_parser("batch", help="run a workflow over every track in a folder",
                       formatter_class=argparse.RawDescriptionHelpFormatter,
                       description="Run a workflow over every track in FOLDER. Flags below tweak the chosen workflow "
                                   "for this run only.",
                       epilog="Exit codes: 0 all songs done · 1 some failed · 2 bad usage · 130 cancelled (Ctrl+C)")
    b.add_argument("folder", help="folder with the tracks")
    w = b.add_argument_group("workflow")
    w.add_argument("-w", "--workflow", help='preset or saved workflow, e.g. "Drum kit per song"')
    w.add_argument("--workflow-file", metavar="FILE", help="workflow JSON (see `chopper workflow NAME`)")
    w.add_argument("--model", help="separation model id, e.g. htdemucs_ft.yaml, two_stage_6s")
    w.add_argument("--stems", metavar="LIST", help='stems to keep, e.g. "vocals,bass" or "all"')
    _bool_flag(w, "rest", "rest", "mix the stems not kept into one extra track", "drop the stems not kept")
    w.add_argument("--no-separate", action="store_true", help="skip separation (work on the mix)")
    w.add_argument("--no-debleed", action="store_true",
                   help="don't move drum bleed out of other / guitar / piano (saves one fast pass per stem)")
    w.add_argument("--tool", action="append", metavar="TOOL:STEM",
                   help="add a stem tool, e.g. drumsep:drums, dereverb:vocals, karaoke:vocals (repeatable)")
    w.add_argument("--no-tools", action="store_true", help="drop the workflow's stem tools")
    w.add_argument("--kit-size", type=int, metavar="N", help="build an N-pad drum kit")
    w.add_argument("--kit-source", metavar="SRC", help='kit from "parts", "drums" or a track name')
    w.add_argument("--no-kit", action="store_true", help="no drum kit")
    w.add_argument("--uniqueness", type=float, metavar="0.8-0.98",
                   help="similarity above which two hits count as the same (lower = stricter)")
    w.add_argument("--bits", metavar="LIST", help='cut sound bits from these stems, e.g. "vocals,bass"')
    w.add_argument("--bits-mode", choices=["chops", "phrases"])
    o = b.add_argument_group("input / output")
    o.add_argument("-o", "--output", metavar="DIR", help="output folder (default: Chopper's data folder)")
    o.add_argument("-r", "--recursive", action="store_true", help="include subfolders")
    o.add_argument("--only", action="append", metavar="PATTERN", help='only files matching, e.g. "01*" (repeatable)')
    o.add_argument("--export-stems", metavar="WHICH", help='"all", "kept", "none" or a list like "vocals,bass"')
    o.add_argument("--export-mix", action="store_true", help="also copy the original mix")
    o.add_argument("--no-bpm-names", action="store_true")
    o.add_argument("--no-key-names", action="store_true")
    o.add_argument("--normalize", action="store_true")
    _bool_flag(o, "keep-projects", "keep_projects", "keep each song as a ChopChop project",
               "delete each song's project after export")
    o.add_argument("--data", metavar="DIR", help="ChopChop data folder (default: CHOPPER_DATA or ./data)")
    r = b.add_argument_group("run")
    r.add_argument("-n", "--dry-run", action="store_true", help="show the plan and tracks, process nothing")
    r.add_argument("--json", action="store_true", help="print the result as JSON on stdout (progress on stderr)")
    r.add_argument("-q", "--quiet", action="store_true", help="no live status line, just one line per song")
    r.add_argument("-v", "--verbose", action="store_true", help="show the separation libraries' own logs")

    bm = sub.add_parser("benchmark", help="time each separation model / GPU setting here, keep the fastest",
                        description="Times the separation models on a clip of FILE with each GPU setting "
                                    "(batch size, fp16), prints the results, and saves a setting only if it "
                                    "was clearly faster than the default.")
    bm.add_argument("file", help="an audio file to test with (a typical song)")
    bm.add_argument("--seconds", type=float, default=30, help="clip length (default 30)")
    bm.add_argument("--models", default="demucs,drumsep,vocals,mdx",
                    help="which to test: demucs, drumsep, vocals, mdx (default: all)")
    bm.add_argument("--no-save", action="store_true", help="just print, don't save settings")
    bm.add_argument("--data", metavar="DIR", help="ChopChop data folder")
    bm.add_argument("-v", "--verbose", action="store_true")

    ws = sub.add_parser("workflows", help="list presets and saved workflows")
    ws.add_argument("--json", action="store_true")
    wo = sub.add_parser("workflow", help="print a workflow as JSON")
    wo.add_argument("name")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {None: cmd_serve, "serve": cmd_serve, "batch": cmd_batch, "benchmark": cmd_benchmark,
                "workflows": cmd_workflows, "workflow": cmd_workflow}
    return handlers[args.cmd](args)
