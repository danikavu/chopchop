"""Project operations shared by the HTTP API and batch workflows.

Each method does the work synchronously; callers decide whether it runs in a
background job. Invalid requests raise ValueError (the API turns them into HTTP 400),
missing tracks raise KeyError (404).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

import numpy as np

from . import analysis, audio, kits, portions, separation, tempo
from .store import Store, find_track, new_id, safe_name

Progress = Callable[[float, str], None]


def _quiet(_p: float, _m: str) -> None:
    pass


class ChopChop:
    def __init__(self, store: Store):
        self.store = store

    # ---------- projects & tracks ----------

    def add_track(self, pid: str, name: str, kind: str, src: Path, label: str | None = None) -> dict:
        folder = self.store.path(pid)
        fname = safe_name(name)
        # A fresh file per import: re-importing a stem must not overwrite audio that an
        # undo step still points to.
        rel = f"tracks/{fname}-{new_id()[:6]}.wav"
        meta = audio.import_audio(src, folder / rel)
        track = {"name": fname, "kind": kind, "file": rel, **meta}
        with self.store.edit(pid, label) as p:
            p["tracks"] = [t for t in p["tracks"] if t["name"] != fname] + [track]
            if kind == "mix":
                p["duration"] = meta["duration"]
                p["sample_rate"] = meta["sample_rate"]
        return track

    def create_project(self, src: Path, name: str) -> dict:
        """New project whose mix is the audio file at src."""
        project = self.store.create(name)
        try:
            self.add_track(project["id"], "mix", "mix", src)
        except Exception as e:
            self.store.delete(project["id"])
            raise ValueError(f"could not read audio: {e}") from e
        return self.store.get(project["id"])

    # ---------- separation ----------

    def check_separate(self, model: str, stems: list[str] | None) -> list[str]:
        """Validate a separation request; returns the stems to keep (model order)."""
        st = separation.status()
        if not st["available"]:
            raise ValueError(st["reason"])
        info = next((m for m in separation.models() if m["id"] == model), None)
        if info is None:
            raise ValueError(f"unknown model: {model}")
        unknown = set(stems or []) - set(info["stems"])
        if unknown:
            raise ValueError(f"{info['label']} doesn't make: {', '.join(sorted(unknown))}")
        keep = [x for x in info["stems"] if stems is None or x in stems]
        if not keep:
            raise ValueError("pick at least one stem")
        return keep

    def separate(self, pid: str, model: str, stems: list[str] | None = None, rest: bool = True,
                 replace: bool = True, progress: Progress = _quiet, tempo_after: bool = True,
                 debleed: bool = True, parts: list[tuple[float, float]] | None = None) -> list[str]:
        """Separate the mix; returns the names of the new stem tracks. One undo step.

        parts: only separate these (start, end) seconds; the stems are silent elsewhere
        but still full length, in line with the mix (see portions.py)."""
        return list(self.separate_track(pid, None, model, stems, rest, replace, progress, tempo_after,
                                        debleed, parts).values())

    def separate_track(self, pid: str, source: str | None, model: str, stems: list[str] | None = None,
                       rest: bool = True, replace: bool = False, progress: Progress = _quiet,
                       tempo_after: bool = False, debleed: bool = True,
                       parts: list[tuple[float, float]] | None = None, overwrite: bool = True) -> dict[str, str]:
        """Separate any track (None = the mix); returns {stem: new track name}.

        Stems of the mix are named as the model names them (vocals, drums…); stems of
        another track get its name in front (instrumental_drums), numbered if taken.
        replace: drop the project's current stems first (only for the mix).
        overwrite: a stem of the mix replaces a same-named track (as a re-separation
        does); off, it is numbered instead (vocals_2), e.g. in workflows."""
        keep = self.check_separate(model, stems)
        p = self.store.get(pid)
        folder = self.store.path(pid)
        mix = next(t for t in p["tracks"] if t["kind"] == "mix")
        src_track = mix if source is None else find_track(p, source)
        prefix = "" if src_track["kind"] == "mix" else f"{src_track['name']}_"
        out = folder / "separation" / new_id()
        try:
            src, layout = folder / src_track["file"], None
            if parts:
                src, layout, length = portions.excerpt(src, parts, out / "parts")
                progress(0.01, f"Separating {len(layout)} part{'s' if len(layout) > 1 else ''} "
                         f"({sum(p['end'] - p['start'] for p in layout):.0f} s of {length:.0f} s)…")
            found = separation.separate(src, out, model, progress)
            if debleed:
                found = separation.debleed(found, out, progress)
            if layout:
                progress(0.88, "Putting the parts back in place…")
                found = portions.stitch(found, layout, length, out / "stitched")
            if set(keep) < set(found):
                dropped = [n for n in found if n not in keep]
                progress(0.89, f"Mixing {', '.join(dropped)} into {separation.rest_name(keep)}…"
                         if rest else f"Keeping {', '.join(keep)}…")
            found = separation.combine(found, keep, rest, out)
            # Lanes in the model's usual order (vocals, drums, bass, …), remainder last.
            order = next(m for m in separation.models() if m["id"] == model)["stems"]
            found = dict(sorted(found.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99))
            added: dict[str, str] = {}
            what = "Separate stems" if prefix == "" else f"Separate {src_track['name']}"
            with self.store.undo_group(pid, f"{what} ({len(layout)} parts)" if layout else what):
                replace = replace and prefix == ""
                if replace:
                    with self.store.edit(pid) as proj:
                        proj["tracks"] = [t for t in proj["tracks"] if t["kind"] != "stem"]
                taken = {t["name"] for t in self.store.get(pid)["tracks"]}
                for i, (name, path) in enumerate(found.items()):
                    progress(0.9 + 0.1 * i / len(found), f"Importing stem {i + 1}/{len(found)}: {name}")
                    new = safe_name(prefix + name)
                    # The mix's own stems replace same-named ones, as before; stems of
                    # another track never overwrite anything.
                    if (prefix or not overwrite) and new in taken:
                        n = 2
                        while f"{new}_{n}" in taken:
                            n += 1
                        new = f"{new}_{n}"
                    taken.add(new)
                    added[name] = self.add_track(pid, new, "stem", path)["name"]
                if tempo_after and any(analysis.is_drum_track(n) for n in added.values()):
                    progress(0.99, "Estimating tempo from the drums…")
                    self.detect_tempo(pid, None)
        finally:
            shutil.rmtree(out, ignore_errors=True)
        return added

    def check_tool(self, tool: str) -> dict:
        if tool not in separation.TOOLS:
            raise ValueError(f"unknown tool: {tool}")
        st = separation.status()
        if not st["available"]:
            raise ValueError(st["reason"])
        return separation.TOOLS[tool]

    def run_tool(self, pid: str, track: str, tool: str, progress: Progress = _quiet) -> list[str]:
        """Run a stem tool on one track; returns the new track names. One undo step."""
        info = self.check_tool(tool)
        t = find_track(self.store.get(pid), track)
        folder = self.store.path(pid)
        out = folder / "separation" / new_id()
        try:
            parts = separation.run_tool(folder / t["file"], out, tool, progress)
            added = []
            with self.store.undo_group(pid, f"{info['label']} ({track})"):
                for i, (suffix, path) in enumerate(parts.items()):
                    progress(0.9 + 0.1 * i / len(parts), f"Importing {track}_{suffix}")
                    added.append(self.add_track(pid, f"{track}_{suffix}", "stem", path)["name"])
        finally:
            shutil.rmtree(out, ignore_errors=True)
        return added

    # ---------- tempo ----------

    def detect_tempo(self, pid: str, track: str | None = None) -> dict:
        """Tempo from the given track, else the drum stem, else the mix."""
        p = self.store.get(pid)
        if track:
            t = find_track(p, track)
        else:
            t = next((x for x in p["tracks"] if analysis.is_drum_track(x["name"])), None) or \
                next(x for x in p["tracks"] if x["kind"] == "mix")
        y, sr = audio.read_mono(self.store.path(pid) / t["file"], sr=None)
        result = {**tempo.estimate(y, sr), "track": t["name"], "source": "detected"}
        result["detected_bpm"] = result["bpm"]
        with self.store.edit(pid, "Detect tempo") as proj:
            proj["tempo"] = result
        return result

    # ---------- kits & sound bits ----------

    def build_kit(self, pid: str, tracks: list[str], mode: str = "drums", sensitivity: float = 0.5,
                  similarity: float = 0.93, max_per_category: int = 8, kit_size: int | None = None,
                  load_pads: bool = False, replace: bool = True, progress: Progress = _quiet) -> list[dict]:
        """Extract samples from one or more tracks (one kit across all of them); returns
        the saved samples. One undo step."""
        if mode not in ("drums", "chops", "phrases"):
            raise ValueError("mode must be drums, chops or phrases")
        p = self.store.get(pid)
        sources = [find_track(p, n) for n in tracks]
        by_name = {t["name"]: t for t in sources}
        folder = self.store.path(pid)
        # Several drum parts (split drums): judge every hit against the loudest part, so a
        # part holding only faint leftovers of the split yields nothing. Sound bits from
        # several stems are judged per stem: quiet vocals next to loud bass are still vocals.
        ref = max(float(np.abs(audio.read(folder / t["file"])[0]).max()) for t in sources) \
            if len(sources) > 1 and mode == "drums" else None
        # And everything against the song: a sound more than 40 dB below the mix's peak
        # is inaudible in it, i.e. leftovers (e.g. drum traces in a near-empty "other").
        mix = next((t for t in p["tracks"] if t["kind"] == "mix"), None)
        floor = float(np.abs(audio.read(folder / mix["file"])[0]).max()) * 10 ** (-40 / 20) if mix else None
        recs: list[dict] = []
        for i, t in enumerate(sources):
            def sub(f, m, i=i, t=t):
                prefix = f"{t['name']} ({i + 1}/{len(sources)}): " if len(sources) > 1 else ""
                progress((i + f) / len(sources) * 0.95, prefix + m)
            # A multi-track kit is trimmed to size once, across all tracks.
            size = kit_size if len(sources) == 1 else None
            recs += kits.extract(folder, t, mode, sensitivity, similarity, max_per_category,
                                 kit_size=size, progress=sub, ref_peak=ref, floor_peak=floor)
        if kit_size and len(sources) > 1:
            recs = kits.select_kit(recs, kit_size)
        progress(0.96, f"Saving {len(recs)} samples…")
        step = "Build drum kit" if mode == "drums" else "Extract sound bits"
        where = sources[0]["name"] if len(sources) == 1 else f"{len(sources)} tracks"
        with self.store.edit(pid, f"{step} from {where}") as proj:
            if replace:
                old = [s for s in proj["samples"] if s.get("track") in by_name and s.get("origin") == mode]
                gone = {s["id"] for s in old}
                proj["samples"] = [s for s in proj["samples"] if s["id"] not in gone]
                proj["pads"] = [None if x in gone else x for x in proj["pads"]]
            saved = [kits.save_sample(folder, proj, by_name[r["track"]], r) for r in recs]
            if load_pads:
                proj["pads"] = kits.layout_pads(saved)
        return saved
