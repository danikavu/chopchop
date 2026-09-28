import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { chokeGroupFor, preload, trigger } from '../padEngine'
import type { ExportResult, Job, Sample, Track } from '../types'
import { categoryColor, label, useJob } from '../util'

export const PADS_PER_BANK = 16
const BANKS = ['A', 'B', 'C', 'D']

// Pad n (1-16) → keyboard key. Right-hand block, laid out like the pads (13-16 on
// top), chosen so it never collides with the slicing shortcuts (Space, S, Del, …).
const PAD_KEYS = ['m', ',', '.', '/', 'j', 'k', 'l', ';', 'u', 'i', 'o', 'p', '7', '8', '9', '0']
// Screen order: MPC pads 13-16 on the top row, 1-4 at the bottom.
const GRID = [12, 13, 14, 15, 8, 9, 10, 11, 4, 5, 6, 7, 0, 1, 2, 3]

export const SAMPLE_MIME = 'application/x-chopper-sample'
const PAD_MIME = 'application/x-chopper-pad'

interface Props {
  pid: string
  pads: (string | null)[]
  samples: Sample[]
  tracks: Track[]
  projectName: string
  selectedPad: number | null
  onSelectPad: (i: number | null) => void
  onPadsChange: (pads: (string | null)[]) => void
  onKitBuilt: () => Promise<void>
}

export function DrumPads(p: Props) {
  const [bank, setBank] = useState(0)
  const guessDrums = () => p.tracks.find((t) => t.name.includes('drum'))?.name ?? p.tracks[0]?.name ?? ''
  const [drumTrack, setDrumTrack] = useState(guessDrums)
  const userPicked = useRef(false)
  const [kitSize, setKitSize] = useState(16)
  const [uniqueness, setUniqueness] = useState(0.9)
  const [jobId, setJobId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ExportResult | null>(null)
  const [dragOver, setDragOver] = useState<number | null>(null)
  const padEls = useRef(new Map<number, HTMLButtonElement>())

  const byId = useMemo(() => new Map(p.samples.map((s) => [s.id, s])), [p.samples])
  const padSample = (i: number) => (p.pads[i] ? byId.get(p.pads[i]!) : undefined)

  // Follow the drum stem once it appears (after separation / import), unless the
  // user chose a track themselves.
  useEffect(() => {
    if (!userPicked.current || !p.tracks.some((t) => t.name === drumTrack)) setDrumTrack(guessDrums())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.tracks])

  // Decode everything on the pads up front so hits are instant.
  useEffect(() => {
    preload(p.pads.filter((x): x is string => !!x && byId.has(x)).map((x) => api.sampleAudioUrl(p.pid, byId.get(x)!)))
  }, [p.pads, byId, p.pid])

  const hit = (i: number) => {
    const s = padSample(i)
    const el = padEls.current.get(i)
    if (el) {
      el.classList.remove('hit')
      void el.offsetWidth // restart the flash animation
      el.classList.add('hit')
    }
    if (s) void trigger(api.sampleAudioUrl(p.pid, s), chokeGroupFor(s.category)).catch((e) => setError(String(e)))
  }

  // Keyboard: play pads of the current bank.
  const hitRef = useRef(hit)
  hitRef.current = hit
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat || e.metaKey || e.ctrlKey || e.altKey) return
      if ((e.target as HTMLElement).closest('input, select, textarea, [contenteditable]')) return
      if (document.querySelector('.modal')) return
      const n = PAD_KEYS.indexOf(e.key.toLowerCase())
      if (n < 0) return
      e.preventDefault()
      hitRef.current(bank * PADS_PER_BANK + n)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [bank])

  const setPad = (i: number, sid: string | null) => {
    const next = [...p.pads]
    next[i] = sid
    p.onPadsChange(next)
  }

  const onDrop = (i: number, e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(null)
    const sid = e.dataTransfer.getData(SAMPLE_MIME)
    const from = e.dataTransfer.getData(PAD_MIME)
    if (sid) {
      setPad(i, sid)
      p.onSelectPad(i)
    } else if (from !== '') {
      const j = Number(from)
      if (j === i) return
      const next = [...p.pads]
      ;[next[i], next[j]] = [next[j], next[i]] // swap, like moving pads on an MPC
      p.onPadsChange(next)
      p.onSelectPad(i)
    }
  }

  const job = useJob(jobId, (j: Job) => {
    setJobId(null)
    if (j.status === 'error') setError(j.error)
    else {
      setBank(0)
      void p.onKitBuilt()
    }
  })

  const autoKit = async () => {
    setError(null)
    setResult(null)
    const bankA = p.pads.slice(0, PADS_PER_BANK)
    if (bankA.some(Boolean) && !window.confirm('Replace the sounds on your pads with a new auto kit?')) return
    try {
      const j = await api.buildKit(p.pid, {
        track: drumTrack,
        mode: 'drums',
        sensitivity: 0.5,
        similarity: uniqueness,
        max_per_category: 16,
        kit_size: kitSize,
        load_pads: true,
      })
      setJobId(j.id)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const exportPads = async () => {
    setError(null)
    try {
      setResult(await api.exportKit(p.pid, { kit_name: `${p.projectName} pads`, normalize: false, from_pads: true }))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const bankCount = (b: number) => p.pads.slice(b * PADS_PER_BANK, (b + 1) * PADS_PER_BANK).filter(Boolean).length
  const busy = jobId !== null

  return (
    <div className="pads-section">
      <div className="pads-bar">
        <h2>Drum pads</h2>
        <div className="banks">
          {BANKS.map((b, i) => (
            <button key={b} className={bank === i ? 'on' : ''} onClick={() => setBank(i)} title={`Bank ${b}`}>
              {b}
              {bankCount(i) > 0 && <span className="count"> {bankCount(i)}</span>}
            </button>
          ))}
        </div>
        <div className="spacer" />
        <button onClick={exportPads} disabled={!p.pads.some(Boolean)}>Export pads</button>
        <button
          onClick={() => {
            const next = [...p.pads]
            next.fill(null, bank * PADS_PER_BANK, (bank + 1) * PADS_PER_BANK)
            p.onPadsChange(next)
          }}
          disabled={bankCount(bank) === 0}
        >
          Clear bank
        </button>
      </div>

      <div className="pads-layout">
        <div className="pad-grid">
          {GRID.map((n) => {
            const i = bank * PADS_PER_BANK + n
            const s = padSample(i)
            const color = s ? categoryColor(s.category) : undefined
            return (
              <button
                key={i}
                ref={(el) => {
                  if (el) padEls.current.set(i, el)
                  else padEls.current.delete(i)
                }}
                className={`pad ${s ? 'loaded' : ''} ${p.selectedPad === i ? 'selected' : ''} ${dragOver === i ? 'over' : ''}`}
                style={color ? ({ '--c': color } as React.CSSProperties) : undefined}
                draggable={!!s}
                onMouseDown={(e) => {
                  if (e.button !== 0) return
                  hit(i)
                  p.onSelectPad(i)
                }}
                onDragStart={(e) => {
                  e.dataTransfer.setData(PAD_MIME, String(i))
                  e.dataTransfer.effectAllowed = 'move'
                }}
                onDragOver={(e) => {
                  e.preventDefault()
                  setDragOver(i)
                }}
                onDragLeave={() => setDragOver((d) => (d === i ? null : d))}
                onDrop={(e) => onDrop(i, e)}
                onContextMenu={(e) => {
                  e.preventDefault()
                  if (s) setPad(i, null)
                }}
                title={s ? `${s.name} — right-click to clear, drag to move` : 'Empty — drop a sample here'}
              >
                <span className="pad-num">
                  {BANKS[bank]}
                  {String(n + 1).padStart(2, '0')}
                </span>
                <kbd className="pad-key">{PAD_KEYS[n].toUpperCase()}</kbd>
                {s ? (
                  <>
                    <span className="pad-name">{s.name}</span>
                    <span className="pad-meta">
                      {label(s.category)}
                      {s.key ? ` · ${s.key}` : ''}
                      {s.occurrences && s.occurrences > 1 ? ` · ×${s.occurrences}` : ''}
                    </span>
                  </>
                ) : (
                  <span className="pad-empty">empty</span>
                )}
              </button>
            )
          })}
        </div>

        <div className="pads-side">
          <h3>Auto kit</h3>
          <p className="tip">
            Pulls every hit from the drum track, groups identical ones, and keeps the cleanest take of each unique
            sound, spread across kicks, snares, hats and percussion.
          </p>
          <label className="field">
            <span>Drum track</span>
            <select value={drumTrack} onChange={(e) => {
              userPicked.current = true
              setDrumTrack(e.target.value)
            }}>
              {p.tracks.map((t) => (
                <option key={t.name} value={t.name}>{label(t.name)}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Kit size</span>
            <select value={kitSize} onChange={(e) => setKitSize(Number(e.target.value))}>
              {[8, 12, 16, 32, 64].map((n) => (
                <option key={n} value={n}>{n} pads</option>
              ))}
            </select>
          </label>
          <label className="slider" title="Two hits more similar than this count as the same sound. Lower = only clearly different sounds.">
            <span className="slider-top">
              <span>Uniqueness</span>
              <span className="slider-val">{uniqueness < 0.87 ? 'strict' : uniqueness > 0.94 ? 'loose' : 'balanced'}</span>
            </span>
            <input type="range" min={0.8} max={0.98} step={0.005} value={0.8 + 0.98 - uniqueness}
              onChange={(e) => setUniqueness(0.8 + 0.98 - parseFloat(e.target.value))} />
          </label>
          <button className="primary wide" onClick={autoKit} disabled={busy || !drumTrack}>
            {busy ? 'Building…' : 'Auto kit → pads'}
          </button>
          {job && busy && (
            <div className="progress">
              <div className="bar" style={{ width: `${Math.round(job.progress * 100)}%` }} />
              <span>{job.message || 'Working…'}</span>
            </div>
          )}
          <p className="tip">
            Click or press <kbd>M</kbd> <kbd>,</kbd> <kbd>.</kbd> <kbd>/</kbd> · <kbd>J</kbd>–<kbd>;</kbd> ·{' '}
            <kbd>U</kbd>–<kbd>P</kbd> · <kbd>7</kbd>–<kbd>0</kbd> to play. Drag samples from below onto a pad, drag pads to
            swap, right-click to clear.
          </p>
          {result && (
            <div className="result">
              {result.count} sounds → <a href={result.url}>download zip</a>
            </div>
          )}
          {error && <div className="error">{error}</div>}
        </div>
      </div>
    </div>
  )
}
