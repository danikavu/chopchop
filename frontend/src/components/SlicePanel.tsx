import { useState } from 'react'
import { api } from '../api'
import type { ExportResult, Job, Slice, Tempo, Track } from '../types'
import { GRID_STEPS, label, snapTo, useJob } from '../util'
import { ExportFolder, ExportResultLine } from './ExportFolder'
import { JobProgress } from './JobProgress'
import { Slider } from './Slider'

interface Props {
  pid: string
  tracks: Track[]
  focused: string
  slices: Slice[]
  selectedId: string | null
  exportTracks: string[]
  onFocus: (name: string) => void
  onSlices: (slices: Slice[], label: string | null) => void
  onToggleExport: (name: string) => void
  onSaveSelectedAsSample: () => void
  duration: number
  tempo: Tempo | null
  gridStep: number
  onGridStep: (beats: number) => void
  grid: number[]
  snapEdits: boolean
  onSnapEdits: (v: boolean) => void
  onDownbeat: (beatIndex: number) => void
  onAnchor: () => void
  onNudgeGrid: (seconds: number) => void
  dragGrid: boolean
  onDragGrid: (v: boolean) => void
  showGrid: boolean
  onShowGrid: (v: boolean) => void
}

const newId = () => Math.random().toString(16).slice(2, 12).padEnd(10, '0')

export function SlicePanel(p: Props) {
  const [sensitivity, setSensitivity] = useState(0.5)
  const [minGap, setMinGap] = useState(0.08)
  const [maxLen, setMaxLen] = useState(0)
  const [gate, setGate] = useState(-40)
  const [fadeIn, setFadeIn] = useState(2)
  const [fadeOut, setFadeOut] = useState(8)
  const [normalize, setNormalize] = useState(false)
  const [busy, setBusy] = useState<'detect' | 'export' | null>(null)
  const [keyNames, setKeyNames] = useState(true)
  const [bpmNames, setBpmNames] = useState(true)
  const [skipSilent, setSkipSilent] = useState(true)
  const [exportJob, setExportJob] = useState<string | null>(null)
  const job = useJob(exportJob, (j: Job) => {
    setExportJob(null)
    setBusy(null)
    if (j.status === 'error') setError(j.error)
    else setResult(j.result as ExportResult)
  })
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ExportResult | null>(null)

  const detect = async () => {
    setBusy('detect')
    setError(null)
    try {
      const r = await api.detectSlices(p.pid, {
        track: p.focused,
        sensitivity,
        min_gap: minGap,
        max_len: maxLen > 0 ? maxLen : null,
        gate_db: gate,
      })
      p.onSlices(r.slices, null) // the server already recorded "Detect slices"
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setBusy(null)
    }
  }

  const doExport = async (onlySelected: boolean) => {
    setBusy('export')
    setError(null)
    setResult(null)
    try {
      // Make sure the latest hand edits are on the server first.
      await api.saveSlices(p.pid, p.slices)
      const j = await api.exportSlices(p.pid, {
        tracks: p.exportTracks,
        slice_ids: onlySelected && p.selectedId ? [p.selectedId] : undefined,
        fade_in_ms: fadeIn,
        fade_out_ms: fadeOut,
        normalize,
        key_names: keyNames,
        bpm_names: bpmNames,
        skip_silent: skipSilent,
      })
      setExportJob(j.id) // busy until the job finishes
    } catch (e) {
      setError(String((e as Error).message))
      setBusy(null)
    }
  }

  const hasGrid = p.grid.length > 1
  /** One slice per grid cell (e.g. 1-bar loops), clipped to the track. */
  const chopToGrid = () => {
    const cells = p.grid.slice(0, -1).map((start, i) => ({
      id: newId(),
      start,
      end: Math.min(p.grid[i + 1], p.duration),
    }))
    p.onSlices(cells.filter((c) => c.end - c.start > 0.01), 'Chop to grid')
  }
  const snapAll = () => {
    const out = p.slices.map((s) => {
      const start = snapTo(p.grid, s.start)
      let end = snapTo(p.grid, s.end)
      if (end <= start) end = p.grid.find((t) => t > start) ?? s.end
      return { ...s, start, end }
    })
    // Slices that collapsed onto the same cell become one.
    const seen = new Set<string>()
    p.onSlices(out.filter((s) => (seen.has(`${s.start}-${s.end}`) ? false : seen.add(`${s.start}-${s.end}`))), 'Snap slices')
  }
  const downbeat = p.tempo?.downbeat ?? 0

  return (
    <div className="panel-body">
      <section>
        <h3>Detect peaks</h3>
        <label className="field">
          <span>Detect on</span>
          <select value={p.focused} onChange={(e) => p.onFocus(e.target.value)}>
            {p.tracks.map((t) => (
              <option key={t.name} value={t.name}>
                {label(t.name)}
              </option>
            ))}
          </select>
        </label>
        <Slider label="Sensitivity" value={sensitivity} min={0} max={1} step={0.01} onChange={setSensitivity}
          format={(v) => `${Math.round(v * 100)}%`} hint="Higher finds quieter / softer hits" />
        <Slider label="Min gap" value={minGap} min={0.02} max={1} step={0.01} onChange={setMinGap}
          format={(v) => `${Math.round(v * 1000)} ms`} hint="Minimum time between two slices" />
        <Slider label="Max length" value={maxLen} min={0} max={4} step={0.05} onChange={setMaxLen}
          format={(v) => (v === 0 ? 'until next' : `${v.toFixed(2)} s`)} hint="Cap each slice's length" />
        <Slider label="Gate" value={gate} min={-80} max={-6} step={1} onChange={setGate}
          format={(v) => `${v} dB`} hint="Ignore hits quieter than this" />
        <button className="primary wide" onClick={detect} disabled={busy !== null}>
          {busy === 'detect' ? 'Detecting…' : `Detect on ${label(p.focused)}`}
        </button>
        <div className="stat">
          {p.slices.length} slice{p.slices.length === 1 ? '' : 's'}
          {p.slices.length > 0 && (
            <button className="link" onClick={() => p.onSlices([], 'Clear slices')}>
              clear
            </button>
          )}
        </div>
        <p className="tip">
          Drag on a waveform to draw a slice · drag edges to adjust · click to play ·{' '}
          <kbd>S</kbd> split at playhead · <kbd>Del</kbd> delete · <kbd>Enter</kbd> save as sample ·{' '}
          <kbd>←</kbd>/<kbd>→</kbd> step through slices
        </p>
        <button className="wide" onClick={p.onSaveSelectedAsSample} disabled={!p.selectedId}>
          Save selected slice as sample
        </button>
      </section>

      <section>
        <h3>Beat grid</h3>
        {!hasGrid ? (
          <p className="tip">Detect the tempo (BPM chip at the top) to chop and snap to the beat grid.</p>
        ) : (
          <>
            <div className="chips">
              {GRID_STEPS.map((g) => (
                <button key={g.beats} className={`chip ${p.gridStep === g.beats ? 'on' : ''}`} onClick={() => p.onGridStep(g.beats)}>
                  {g.label}
                </button>
              ))}
            </div>
            <div className="row">
              <button className="primary" onClick={chopToGrid}
                title="Replace the slices with one slice per grid cell (e.g. 1-bar loops)">
                Chop to grid
              </button>
              <button onClick={snapAll} disabled={!p.slices.length} title="Move every slice edge to the nearest grid line">
                Snap slices
              </button>
            </div>
            <label className="check" style={{ marginTop: 8 }}>
              <input type="checkbox" checked={p.snapEdits} onChange={(e) => p.onSnapEdits(e.target.checked)} />
              Snap while editing (drag, draw, split)
            </label>
            <div className="grid-fix">
              <div className="row">
                <button onClick={p.onAnchor} title="Put the playhead on the hit where a bar really starts, then press this (or B)">
                  Bar 1 at playhead <kbd>B</kbd>
                </button>
              </div>
              <div className="field" title="Shift the whole grid in time: 10 ms per click, 1 ms with Shift">
                <span>Shift grid</span>
                <span className="row nudge">
                  <button onClick={(e) => p.onNudgeGrid(e.shiftKey ? -0.001 : -0.01)}>◀</button>
                  <span className="muted">{'10 ms · ⇧ 1 ms'}</span>
                  <button onClick={(e) => p.onNudgeGrid(e.shiftKey ? 0.001 : 0.01)}>▶</button>
                </span>
              </div>
              <label className="check">
                <input type="checkbox" checked={p.showGrid} onChange={(e) => p.onShowGrid(e.target.checked)} />
                Show grid lines on the waveforms
              </label>
              <label className="check">
                <input type="checkbox" checked={p.dragGrid} onChange={(e) => p.onDragGrid(e.target.checked)} />
                Drag grid (grab a red bar line on the waveforms)
              </label>
            </div>
            <div className="field" style={{ marginTop: 8 }} title="Which tracked beat is beat 1 of the bar (used by bar-sized grids)">
              <span>Bar starts at</span>
              <span className="row nudge">
                <button onClick={() => p.onDownbeat((downbeat + 3) % 4)}>◀</button>
                <span>beat {downbeat + 1}</span>
                <button onClick={() => p.onDownbeat((downbeat + 1) % 4)}>▶</button>
              </span>
            </div>
          </>
        )}
      </section>

      <section>
        <h3>Export slices</h3>
        <div className="chips">
          {p.tracks.map((t) => (
            <label key={t.name} className={`chip ${p.exportTracks.includes(t.name) ? 'on' : ''}`}>
              <input type="checkbox" checked={p.exportTracks.includes(t.name)} onChange={() => p.onToggleExport(t.name)} />
              {label(t.name)}
            </label>
          ))}
        </div>
        <Slider label="Fade in" value={fadeIn} min={0} max={50} step={1} onChange={setFadeIn} format={(v) => `${v} ms`} />
        <Slider label="Fade out" value={fadeOut} min={0} max={200} step={1} onChange={setFadeOut} format={(v) => `${v} ms`} />
        <label className="check">
          <input type="checkbox" checked={normalize} onChange={(e) => setNormalize(e.target.checked)} />
          Normalize each slice
        </label>
        <label className="check" title="Detect each slice's key / note (pitched sounds only) and add it to the file name">
          <input type="checkbox" checked={keyNames} onChange={(e) => setKeyNames(e.target.checked)} />
          Key in file names
        </label>
        <label className="check" title={p.tempo?.bpm ? `Adds ${Math.round(p.tempo.bpm)}bpm to slices at least ~1 beat long` : 'Detect the tempo first'}>
          <input type="checkbox" checked={bpmNames && !!p.tempo?.bpm} disabled={!p.tempo?.bpm}
            onChange={(e) => setBpmNames(e.target.checked)} />
          BPM in loop names
        </label>
        <label className="check" title="Leave out chops that are (nearly) silent on a track, e.g. a vocal stem before the vocals come in. Numbers stay in step across tracks.">
          <input type="checkbox" checked={skipSilent} onChange={(e) => setSkipSilent(e.target.checked)} />
          Skip silent chops
        </label>
        <ExportFolder />
        <div className="row">
          <button className="primary" onClick={() => doExport(false)}
            disabled={busy !== null || !p.slices.length || !p.exportTracks.length}>
            {busy === 'export' ? 'Exporting…' : `Export ${p.slices.length} × ${p.exportTracks.length}`}
          </button>
          <button onClick={() => doExport(true)} disabled={busy !== null || !p.selectedId || !p.exportTracks.length}>
            Selected only
          </button>
        </div>
        {busy === 'export' && <JobProgress job={job} fallback="Exporting…" />}
        {result && <ExportResultLine result={result} noun="files" />}
      </section>
      {error && <div className="error">{error}</div>}
    </div>
  )
}
