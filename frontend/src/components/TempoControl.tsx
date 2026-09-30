import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Tempo, Track } from '../types'
import { isDrumTrack, label } from '../util'

interface Props {
  pid: string
  tempo: Tempo | null
  tracks: Track[]
  showBeats: boolean
  onShowBeats: (v: boolean) => void
  onChange: (t: Tempo) => void
}

const COMPONENTS: [keyof Tempo['components'], string, string][] = [
  ['clarity', 'Clarity', 'How clearly one tempo stands out from unrelated ones'],
  ['stability', 'Stability', 'Does the same tempo hold across the whole track'],
  ['on_grid', 'On grid', 'Share of beats that land on an actual drum hit'],
  ['timing', 'Timing', 'How tight the hits are (quantized ≈ 100%, loose live playing lower)'],
  ['octave', 'Half / double', 'How sure it is not half or double time'],
]

function level(c: number) {
  return c >= 0.8 ? 'high' : c >= 0.6 ? 'mid' : 'low'
}

function swingText(t: Tempo) {
  const { percent, grid } = t.swing
  if (percent == null || !grid) return null
  return percent < 53 ? `straight ${grid}s` : `swing ${Math.round(percent)}% (${grid}s)`
}

export function TempoControl({ pid, tempo, tracks, showBeats, onShowBeats, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [track, setTrack] = useState('')
  const [draft, setDraft] = useState('')
  const [taps, setTaps] = useState<number[]>([])
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setTrack(tempo?.track ?? tracks.find((t) => isDrumTrack(t.name))?.name ?? 'mix')
  }, [tempo?.track, tracks])
  useEffect(() => setDraft(tempo?.bpm ? String(tempo.bpm) : ''), [tempo?.bpm])
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false)
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [open])

  const run = async (fn: () => Promise<Tempo>) => {
    setBusy(true)
    setError(null)
    try {
      onChange(await fn())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const detect = () => run(() => api.detectTempo(pid, track))
  const setBpm = (bpm: number) => bpm > 20 && bpm < 400 && run(() => api.setTempo(pid, { bpm: Math.round(bpm * 10) / 10 }))

  const tap = () => {
    const now = performance.now()
    const recent = [...taps.filter((t) => now - t < 2500), now].slice(-8)
    setTaps(recent)
    if (recent.length >= 4) {
      const gaps = recent.slice(1).map((t, i) => t - recent[i])
      setDraft((60000 / (gaps.reduce((a, b) => a + b, 0) / gaps.length)).toFixed(1))
    }
  }

  const bpm = tempo?.bpm
  const conf = tempo?.confidence ?? 0
  return (
    <div className="tempo" ref={box}>
      <button className={`tempo-chip ${bpm ? level(conf) : ''}`} onClick={() => setOpen((o) => !o)}
        title="Tempo — click for details">
        {bpm ? (
          <>
            <b>{bpm.toFixed(1)}</b> BPM
            {tempo?.source === 'manual' ? <span className="tag">set</span> : <span className="tag">{Math.round(conf * 100)}%</span>}
            {tempo && tempo.steady === false && tempo.range && <span className="tag">{tempo.range[0]}–{tempo.range[1]}</span>}
            {tempo && swingText(tempo)?.startsWith('swing') && <span className="tag">{swingText(tempo)}</span>}
          </>
        ) : busy ? 'Detecting tempo…' : 'Detect BPM'}
      </button>

      {open && (
        <div className="tempo-pop">
          <div className="row">
            <label className="field grow">
              <span>Detect from</span>
              <select value={track} onChange={(e) => setTrack(e.target.value)}>
                {tracks.map((t) => <option key={t.name} value={t.name}>{label(t.name)}</option>)}
              </select>
            </label>
            <button className="primary" onClick={detect} disabled={busy}>{busy ? 'Detecting…' : bpm ? 'Re-detect' : 'Detect'}</button>
          </div>
          {track && !isDrumTrack(track) && <div className="warn small">Most reliable from a drum stem.</div>}

          {tempo?.bpm != null && (
            <>
              <div className="tempo-big">
                <span className="tempo-num">{tempo.bpm.toFixed(1)}</span> BPM
                {tempo.source === 'manual' && tempo.detected_bpm != null && (
                  <span className="muted"> · detected {tempo.detected_bpm}</span>
                )}
              </div>
              <div className="muted small">
                {tempo.steady === false && tempo.range
                  ? `Live / variable tempo, drifting ${tempo.range[0]}–${tempo.range[1]} BPM (value is the median)`
                  : 'Steady tempo'}
                {swingText(tempo) ? ` · ${swingText(tempo)}` : ''}
              </div>

              <div className="row tempo-adjust">
                <button onClick={() => setBpm(tempo.bpm! / 2)} disabled={busy} title="Half time">÷2</button>
                <button onClick={() => setBpm(tempo.bpm! * 2)} disabled={busy} title="Double time">×2</button>
                <input className="bpm-input" value={draft} onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && setBpm(parseFloat(draft))} />
                <button onClick={() => setBpm(parseFloat(draft))} disabled={busy || !parseFloat(draft)}>Set</button>
                <button onClick={tap} title="Tap along 4+ times, then Set">Tap{taps.length ? ` (${taps.length})` : ''}</button>
              </div>

              {tempo.source !== 'manual' && (
                <>
                  <div className="conf-title">
                    Confidence <b className={`conf ${level(conf)}`}>{Math.round(conf * 100)}%</b>
                  </div>
                  {COMPONENTS.map(([k, name, hint]) =>
                    tempo.components[k] == null ? null : (
                      <div key={k} className="conf-row" title={hint}>
                        <span>{name}</span>
                        <div className="conf-bar"><div style={{ width: `${Math.round(tempo.components[k]! * 100)}%` }} /></div>
                        <span className="muted">{Math.round(tempo.components[k]! * 100)}%</span>
                      </div>
                    ),
                  )}
                </>
              )}
              {tempo.alternatives.length > 0 && (
                <div className="alts">
                  <span className="muted">Could also be:</span>
                  {tempo.alternatives.slice(0, 3).map((a) => (
                    <button key={a.relation} onClick={() => setBpm(a.bpm)} disabled={busy}
                      title={`${a.relation} — relative strength ${Math.round(a.strength * 100)}%`}>
                      {a.bpm} <span className="muted">{a.relation}</span>
                    </button>
                  ))}
                </div>
              )}
              <label className="check">
                <input type="checkbox" checked={showBeats} onChange={(e) => onShowBeats(e.target.checked)} />
                Show beat grid on waveforms ({tempo.beats.length} beats)
              </label>
            </>
          )}
          {error && <div className="error">{error}</div>}
        </div>
      )}
    </div>
  )
}
