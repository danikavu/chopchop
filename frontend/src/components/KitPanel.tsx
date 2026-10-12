import { useRef, useState } from 'react'
import { api } from '../api'
import type { ExportResult, Job, Track } from '../types'
import { isDrumTrack, label, NOT_DRUMS_NOTE, useJob } from '../util'
import { ExportResultLine } from './ExportFolder'
import { JobProgress } from './JobProgress'
import { Slider } from './Slider'
import { ToCSelect, useToC } from './ToCSelect'

interface Props {
  pid: string
  projectName: string
  tracks: Track[]
  onDone: () => void
}

interface KitResult {
  samples: string[]
  by_track?: Record<string, number>
}

function guessDrums(tracks: Track[]) {
  return tracks.find((t) => t.name.includes('drum'))?.name ?? tracks[0]?.name ?? ''
}

function guessMelodic(tracks: Track[]) {
  return (
    tracks.find((t) => t.name.includes('vocal'))?.name ??
    tracks.find((t) => t.kind === 'stem' && !t.name.includes('drum'))?.name ??
    tracks[0]?.name ??
    ''
  )
}

const toggle = (list: string[], name: string) => (list.includes(name) ? list.filter((n) => n !== name) : [...list, name])

export function KitPanel({ pid, projectName, tracks, onDone }: Props) {
  const [drumTracks, setDrumTracks] = useState<string[]>(() => [guessDrums(tracks)].filter(Boolean))
  const [bitsTracks, setBitsTracks] = useState<string[]>(() => [guessMelodic(tracks)].filter(Boolean))
  const [mode, setMode] = useState<'chops' | 'phrases'>('chops')
  const [drumSens, setDrumSens] = useState(0.5)
  const [drumSim, setDrumSim] = useState(0.93)
  const [drumMax, setDrumMax] = useState(6)
  const [toPads, setToPads] = useState(true)
  const [bitSens, setBitSens] = useState(0.5)
  const [bitSim, setBitSim] = useState(0.9)
  const [bitMax, setBitMax] = useState(16)
  const [jobId, setJobId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [last, setLast] = useState<string | null>(null)
  const [exported, setExported] = useState<ExportResult | null>(null)
  const [exporting, setExporting] = useState(false)
  const [toC] = useToC()
  // "Extract & export": export what the job made as soon as it is done.
  const exportAfter = useRef(false)

  const job = useJob(jobId, (j: Job) => {
    setJobId(null)
    if (j.status === 'error') {
      setError(j.error)
      return
    }
    const r = j.result as KitResult
    const n = r.samples.length
    const empty = Object.entries(r.by_track ?? {}).filter(([, c]) => c === 0).map(([t]) => label(t))
    const per = Object.entries(r.by_track ?? {}).filter(([, c]) => c > 0)
    const parts = per.length > 1 ? ` (${per.map(([t, c]) => `${label(t)} ${c}`).join(', ')})` : ''
    if (!n) setLast(`Nothing cut: ${empty.length > 1 ? 'these tracks have' : 'this track has'} no audible sounds of their own (only faint leftovers)`)
    else setLast(`${n} samples added${parts}${empty.length ? ` · nothing audible on ${empty.join(', ')}` : ''}`)
    onDone()
    if (exportAfter.current && n) void exportSamples(r.samples)
    exportAfter.current = false
  })

  const exportSamples = async (ids: string[]) => {
    setExporting(true)
    try {
      setExported(await api.exportKit(pid, { sample_ids: ids, kit_name: `${projectName} bits`, normalize: false, to_c: toC }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setExporting(false)
    }
  }

  const run = async (names: string[], m: string, sensitivity: number, similarity: number, max: number,
    opts: { loadPads?: boolean; thenExport?: boolean } = {}) => {
    setError(null)
    setLast(null)
    setExported(null)
    try {
      const j = await api.buildKit(pid, {
        track: names[0], tracks: names, mode: m, sensitivity, similarity, max_per_category: max, load_pads: !!opts.loadPads,
      })
      exportAfter.current = !!opts.thenExport
      setJobId(j.id)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const busy = jobId !== null || exporting
  const trackChips = (value: string[], set: (v: string[]) => void) => (
    <div className="chips">
      {tracks.map((t) => (
        <label key={t.name} className={`chip ${value.includes(t.name) ? 'on' : ''}`}>
          <input type="checkbox" checked={value.includes(t.name)} onChange={() => set(toggle(value, t.name))} />
          {label(t.name)}
        </label>
      ))}
    </div>
  )

  return (
    <div className="panel-body">
      <section>
        <h3>Drum kit</h3>
        <p className="tip">
          Finds every hit, labels it (kick, snare, hat…), and keeps only the cleanest, most distinct
          ones per type.
        </p>
        <div className="field-label">Drum tracks <span className="muted small">(several = one kit across them, e.g. split drum parts)</span></div>
        {trackChips(drumTracks, setDrumTracks)}
        {drumTracks.some((n) => !isDrumTrack(n)) && <div className="warn small note">{NOT_DRUMS_NOTE}</div>}
        <Slider label="Sensitivity" value={drumSens} min={0} max={1} step={0.01} onChange={setDrumSens}
          format={(v) => `${Math.round(v * 100)}%`} />
        <Slider label="Uniqueness" value={drumSim} min={0.7} max={0.995} step={0.005} onChange={setDrumSim}
          format={(v) => `${Math.round(v * 100)}%`}
          hint="Hits more similar than this to a kept one are treated as duplicates. Lower = fewer, more different sounds." />
        <Slider label="Max per type" value={drumMax} min={1} max={32} step={1} onChange={setDrumMax} />
        <label className="check" style={{ marginBottom: 10 }}>
          <input type="checkbox" checked={toPads} onChange={(e) => setToPads(e.target.checked)} />
          Load onto drum pads
        </label>
        <button className="primary wide" disabled={busy || !drumTracks.length}
          onClick={() => run(drumTracks, 'drums', drumSens, drumSim, drumMax, { loadPads: toPads })}>
          Build drum kit
        </button>
      </section>

      <section>
        <h3>Sound bits</h3>
        <p className="tip">Cut one-shots (chops) or longer phrases from any stem, with key / note detection.</p>
        <div className="field-label">Tracks <span className="muted small">(each judged on its own)</span></div>
        {trackChips(bitsTracks, setBitsTracks)}
        <div className="seg">
          <button className={mode === 'chops' ? 'on' : ''} onClick={() => setMode('chops')}>Chops</button>
          <button className={mode === 'phrases' ? 'on' : ''} onClick={() => setMode('phrases')}>Phrases</button>
        </div>
        <Slider label="Sensitivity" value={bitSens} min={0} max={1} step={0.01} onChange={setBitSens}
          format={(v) => `${Math.round(v * 100)}%`} />
        <Slider label="Uniqueness" value={bitSim} min={0.7} max={0.995} step={0.005} onChange={setBitSim}
          format={(v) => `${Math.round(v * 100)}%`} />
        <Slider label="Max bits" value={bitMax} min={1} max={64} step={1} onChange={setBitMax} />
        <div className="row">
          <button className="primary grow" disabled={busy || !bitsTracks.length}
            onClick={() => run(bitsTracks, mode, bitSens, bitSim, bitMax)}>
            Extract sound bits
          </button>
          <button className="grow" disabled={busy || !bitsTracks.length}
            title="Extract, then export just these new bits (to the export folder, or as a zip)"
            onClick={() => run(bitsTracks, mode, bitSens, bitSim, bitMax, { thenExport: true })}>
            Extract &amp; export
          </button>
        </div>
        <ToCSelect />
      </section>

      {jobId !== null && <JobProgress job={job} fallback="Working…" />}
      {exporting && <div className="muted small">Exporting…</div>}
      {last && <div className="result">{last}{last.includes('added') && !exported && !exporting ? ' — see the sample browser below.' : ''}</div>}
      {exported && <ExportResultLine result={exported} noun="bits" />}
      {error && <div className="error">{error}</div>}
      <p className="tip">Re-running on the same track replaces the samples it made last time (manual ones are kept).</p>
    </div>
  )
}
