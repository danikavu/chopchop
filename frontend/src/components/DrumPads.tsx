import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { chokeGroupFor, preload, trigger } from '../padEngine'
import type { ExportResult, Job, Sample, Track } from '../types'
import { categoryColor, isDrumPart, isDrumTrack, isTextEntry, label, NOT_DRUMS_NOTE, useJob } from '../util'
import { ExportResultLine } from './ExportFolder'
import { JobProgress } from './JobProgress'
import { ToCSelect, useToC } from './ToCSelect'

export const PADS_PER_BANK = 16
const BANKS = ['A', 'B', 'C', 'D']

// Pad n (1-16) → keyboard key. Right-hand block, laid out like the pads (13-16 on
// top), chosen so it never collides with the slicing shortcuts (Space, S, Del, …).
export const PAD_KEYS = ['m', ',', '.', '/', 'j', 'k', 'l', ';', 'u', 'i', 'o', 'p', '7', '8', '9', '0']
// Screen order: MPC pads 13-16 on the top row, 1-4 at the bottom.
const GRID = [12, 13, 14, 15, 8, 9, 10, 11, 4, 5, 6, 7, 0, 1, 2, 3]

export const SAMPLE_MIME = 'application/x-chopchop-sample'
const PAD_MIME = 'application/x-chopchop-pad'

interface Props {
  pid: string
  pads: (string | null)[]
  samples: Sample[]
  tracks: Track[]
  projectName: string
  selectedPad: number | null
  onSelectPad: (i: number | null) => void
  onPadsChange: (pads: (string | null)[], label?: string) => void
  chokes: (number | null)[]
  chokeAll: boolean
  onChokesChange: (chokes: (number | null)[], chokeAll: boolean, label: string) => void
  /** The keyboard plays the pads (else it drives the editor: L = loop, O = only this stem…). */
  padKeys: boolean
  onPadKeys: (on: boolean) => void
  onKitBuilt: () => Promise<void>
}

export function DrumPads(p: Props) {
  const [bank, setBank] = useState(0)
  // With a drum split, the isolated parts make the cleanest kit.
  const ALL_PARTS = '__parts__'
  const parts = p.tracks.filter((t) => isDrumPart(t.name)).map((t) => t.name)
  const guessDrums = () =>
    parts.length > 1 ? ALL_PARTS : p.tracks.find((t) => isDrumTrack(t.name))?.name ?? p.tracks[0]?.name ?? ''
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
  const onPads = useMemo(() => [...new Set(p.pads)].flatMap((x) => (x && byId.get(x)) || []), [p.pads, byId])
  const [toC] = useToC()
  const padSample = (i: number) => (p.pads[i] ? byId.get(p.pads[i]!) : undefined)

  // Follow the drum stem once it appears (after separation / import), unless the
  // user chose a track themselves.
  useEffect(() => {
    const valid = drumTrack === ALL_PARTS ? parts.length > 1 : p.tracks.some((t) => t.name === drumTrack)
    if (!userPicked.current || !valid) setDrumTrack(guessDrums())
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
    // Choke all > the pad's own group > the automatic one (a closed hat cuts an open hat).
    const group = p.chokeAll ? 'all' : p.chokes[i] ? `group${p.chokes[i]}` : chokeGroupFor(s?.category ?? '')
    if (s) void trigger(api.sampleAudioUrl(p.pid, s), group).catch((e) => setError(String(e)))
  }

  // Keyboard: play pads of the current bank.
  const hitRef = useRef(hit)
  hitRef.current = hit
  useEffect(() => {
    if (!p.padKeys) return
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat || e.metaKey || e.ctrlKey || e.altKey) return
      if (isTextEntry(e.target)) return
      if (document.querySelector('.modal')) return
      const n = PAD_KEYS.indexOf(e.key.toLowerCase())
      if (n < 0) return
      e.preventDefault()
      hitRef.current(bank * PADS_PER_BANK + n)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [bank, p.padKeys])

  const setPad = (i: number, sid: string | null) => {
    const next = [...p.pads]
    next[i] = sid
    p.onPadsChange(next, sid ? 'Load pad' : 'Clear pad')
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
      p.onPadsChange(next, 'Swap pads')
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
      const all = drumTrack === ALL_PARTS
      const j = await api.buildKit(p.pid, {
        track: all ? parts[0] : drumTrack,
        tracks: all ? parts : undefined,
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
      setResult(await api.exportKit(p.pid, { kit_name: `${p.projectName} pads`, normalize: false, from_pads: true, to_c: toC }))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const bankCount = (b: number) => p.pads.slice(b * PADS_PER_BANK, (b + 1) * PADS_PER_BANK).filter(Boolean).length
  const busy = jobId !== null

  return (
    <div className={`pads-section ${p.padKeys ? 'keys-on' : ''}`}>
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
        <button className={`grid-toggle ${p.padKeys ? 'on' : ''}`} onClick={() => p.onPadKeys(!p.padKeys)}
          title={p.padKeys
            ? 'The keyboard plays the pads (L and O too). Click the lanes, or press `, to give it back to the editor.'
            : 'Play the pads from the keyboard (or just hit a pad). Until then the keys drive the editor.'}>
          ⌨ Pad keys
        </button>
        <div className="spacer" />
        <label className="check" title="Every pad cuts off the one still ringing: one sound at a time">
          <input type="checkbox" checked={p.chokeAll}
            onChange={(e) => p.onChokesChange(p.chokes, e.target.checked, e.target.checked ? 'Choke all pads' : 'Pads overlap')} />
          Choke all
        </label>
        {p.selectedPad !== null && (
          <label className="check" title="Pads in the same group cut each other off (like an MPC mute group)">
            {BANKS[Math.floor(p.selectedPad / PADS_PER_BANK)]}{String((p.selectedPad % PADS_PER_BANK) + 1).padStart(2, '0')} choke
            <select value={p.chokes[p.selectedPad] ?? ''} disabled={p.chokeAll}
              onChange={(e) => {
                const next = [...p.chokes]
                next[p.selectedPad!] = e.target.value ? Number(e.target.value) : null
                p.onChokesChange(next, p.chokeAll, 'Set choke group')
              }}>
              <option value="">off</option>
              {[1, 2, 3, 4, 5, 6, 7, 8].map((g) => <option key={g} value={g}>group {g}</option>)}
            </select>
          </label>
        )}
        {p.chokes.some(Boolean) && !p.chokeAll && (
          <button onClick={() => p.onChokesChange(p.chokes.map(() => null), false, 'Clear choke groups')}
            title="Remove every pad's choke group">Clear chokes</button>
        )}
        <ToCSelect samples={onPads} />
        <button onClick={exportPads} disabled={!p.pads.some(Boolean)}>Export pads</button>
        <button
          onClick={() => {
            const next = [...p.pads]
            next.fill(null, bank * PADS_PER_BANK, (bank + 1) * PADS_PER_BANK)
            p.onPadsChange(next, `Clear bank ${BANKS[bank]}`)
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
                  p.onPadKeys(true) // playing the pads: the keyboard plays them too
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
                {!p.chokeAll && p.chokes[i] && <span className="pad-choke" title={`Choke group ${p.chokes[i]}`}>C{p.chokes[i]}</span>}
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
            <span>Source track</span>
            <select value={drumTrack} onChange={(e) => {
              userPicked.current = true
              setDrumTrack(e.target.value)
            }}>
              {parts.length > 1 && <option value={ALL_PARTS}>All drum parts ({parts.length})</option>}
              {p.tracks.map((t) => (
                <option key={t.name} value={t.name}>{label(t.name)}</option>
              ))}
            </select>
          </label>
          {drumTrack && drumTrack !== ALL_PARTS && !isDrumTrack(drumTrack) && (
            <div className="warn small note">{NOT_DRUMS_NOTE}</div>
          )}
          {drumTrack !== ALL_PARTS && parts.length === 0 && p.tracks.some((t) => isDrumTrack(t.name)) && (
            <p className="tip">Tip: ✦ → Split drums on the drum lane gives isolated kick / snare / hat parts for a cleaner kit.</p>
          )}
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
          {busy && <JobProgress job={job} fallback="Working…" />}
          <p className="tip">
            Click a pad to play it: the keyboard then plays the pads too (<kbd>M</kbd> <kbd>,</kbd> <kbd>.</kbd>{' '}
            <kbd>/</kbd> · <kbd>J</kbd>–<kbd>;</kbd> · <kbd>U</kbd>–<kbd>P</kbd> · <kbd>7</kbd>–<kbd>0</kbd>) until you click
            the lanes or press <kbd>`</kbd>. Drag samples from below onto a pad, drag pads to swap, right-click to clear.
          </p>
          {result && <ExportResultLine result={result} noun="sounds" />}
          {error && <div className="error">{error}</div>}
        </div>
      </div>
    </div>
  )
}
