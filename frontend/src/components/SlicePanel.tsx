import { useState } from 'react'
import { api } from '../api'
import type { ExportResult, Slice, Track } from '../types'
import { label } from '../util'
import { Slider } from './Slider'

interface Props {
  pid: string
  tracks: Track[]
  focused: string
  slices: Slice[]
  selectedId: string | null
  exportTracks: string[]
  onFocus: (name: string) => void
  onSlices: (slices: Slice[]) => void
  onToggleExport: (name: string) => void
  onSaveSelectedAsSample: () => void
}

export function SlicePanel(p: Props) {
  const [sensitivity, setSensitivity] = useState(0.5)
  const [minGap, setMinGap] = useState(0.08)
  const [maxLen, setMaxLen] = useState(0)
  const [gate, setGate] = useState(-40)
  const [fadeIn, setFadeIn] = useState(2)
  const [fadeOut, setFadeOut] = useState(8)
  const [normalize, setNormalize] = useState(false)
  const [busy, setBusy] = useState<'detect' | 'export' | null>(null)
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
      p.onSlices(r.slices)
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
      const r = await api.exportSlices(p.pid, {
        tracks: p.exportTracks,
        slice_ids: onlySelected && p.selectedId ? [p.selectedId] : undefined,
        fade_in_ms: fadeIn,
        fade_out_ms: fadeOut,
        normalize,
      })
      setResult(r)
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setBusy(null)
    }
  }

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
            <button className="link" onClick={() => p.onSlices([])}>
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
        <div className="row">
          <button className="primary" onClick={() => doExport(false)}
            disabled={busy !== null || !p.slices.length || !p.exportTracks.length}>
            {busy === 'export' ? 'Exporting…' : `Export ${p.slices.length} × ${p.exportTracks.length}`}
          </button>
          <button onClick={() => doExport(true)} disabled={busy !== null || !p.selectedId || !p.exportTracks.length}>
            Selected only
          </button>
        </div>
        {result && (
          <div className="result">
            {result.count} files → <a href={result.url}>download zip</a>
            <div className="path" title={result.path}>{result.path}</div>
          </div>
        )}
      </section>
      {error && <div className="error">{error}</div>}
    </div>
  )
}
