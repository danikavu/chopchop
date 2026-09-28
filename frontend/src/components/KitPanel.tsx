import { useState } from 'react'
import { api } from '../api'
import type { Job, Track } from '../types'
import { label, useJob } from '../util'
import { Slider } from './Slider'

interface Props {
  pid: string
  tracks: Track[]
  onDone: () => void
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

export function KitPanel({ pid, tracks, onDone }: Props) {
  const [drumTrack, setDrumTrack] = useState(() => guessDrums(tracks))
  const [bitsTrack, setBitsTrack] = useState(() => guessMelodic(tracks))
  const [mode, setMode] = useState<'chops' | 'phrases'>('chops')
  const [drumSens, setDrumSens] = useState(0.5)
  const [drumSim, setDrumSim] = useState(0.93)
  const [drumMax, setDrumMax] = useState(6)
  const [bitSens, setBitSens] = useState(0.5)
  const [bitSim, setBitSim] = useState(0.9)
  const [bitMax, setBitMax] = useState(16)
  const [jobId, setJobId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [last, setLast] = useState<string | null>(null)

  const job = useJob(jobId, (j: Job) => {
    setJobId(null)
    if (j.status === 'error') setError(j.error)
    else {
      const n = (j.result as { samples: string[] }).samples.length
      setLast(`${n} samples added`)
      onDone()
    }
  })

  const run = async (track: string, m: string, sensitivity: number, similarity: number, max: number) => {
    setError(null)
    setLast(null)
    try {
      const j = await api.buildKit(pid, { track, mode: m, sensitivity, similarity, max_per_category: max })
      setJobId(j.id)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const busy = jobId !== null
  const trackSelect = (value: string, set: (v: string) => void) => (
    <select value={value} onChange={(e) => set(e.target.value)}>
      {tracks.map((t) => (
        <option key={t.name} value={t.name}>
          {label(t.name)}
        </option>
      ))}
    </select>
  )

  return (
    <div className="panel-body">
      <section>
        <h3>Drum kit</h3>
        <p className="tip">
          Finds every hit, labels it (kick, snare, hat…), and keeps only the cleanest, most distinct
          ones per type.
        </p>
        <label className="field">
          <span>Drum track</span>
          {trackSelect(drumTrack, setDrumTrack)}
        </label>
        <Slider label="Sensitivity" value={drumSens} min={0} max={1} step={0.01} onChange={setDrumSens}
          format={(v) => `${Math.round(v * 100)}%`} />
        <Slider label="Uniqueness" value={drumSim} min={0.7} max={0.995} step={0.005} onChange={setDrumSim}
          format={(v) => `${Math.round(v * 100)}%`}
          hint="Hits more similar than this to a kept one are treated as duplicates. Lower = fewer, more different sounds." />
        <Slider label="Max per type" value={drumMax} min={1} max={32} step={1} onChange={setDrumMax} />
        <button className="primary wide" disabled={busy || !drumTrack}
          onClick={() => run(drumTrack, 'drums', drumSens, drumSim, drumMax)}>
          Build drum kit
        </button>
      </section>

      <section>
        <h3>Sound bits</h3>
        <p className="tip">Cut one-shots (chops) or longer phrases from any stem, with key / note detection.</p>
        <label className="field">
          <span>Track</span>
          {trackSelect(bitsTrack, setBitsTrack)}
        </label>
        <div className="seg">
          <button className={mode === 'chops' ? 'on' : ''} onClick={() => setMode('chops')}>Chops</button>
          <button className={mode === 'phrases' ? 'on' : ''} onClick={() => setMode('phrases')}>Phrases</button>
        </div>
        <Slider label="Sensitivity" value={bitSens} min={0} max={1} step={0.01} onChange={setBitSens}
          format={(v) => `${Math.round(v * 100)}%`} />
        <Slider label="Uniqueness" value={bitSim} min={0.7} max={0.995} step={0.005} onChange={setBitSim}
          format={(v) => `${Math.round(v * 100)}%`} />
        <Slider label="Max bits" value={bitMax} min={1} max={64} step={1} onChange={setBitMax} />
        <button className="primary wide" disabled={busy || !bitsTrack}
          onClick={() => run(bitsTrack, mode, bitSens, bitSim, bitMax)}>
          Extract sound bits
        </button>
      </section>

      {job && busy && (
        <div className="progress">
          <div className="bar" style={{ width: `${Math.round(job.progress * 100)}%` }} />
          <span>{job.message || 'Working…'}</span>
        </div>
      )}
      {last && <div className="result">{last} — see the sample browser below.</div>}
      {error && <div className="error">{error}</div>}
      <p className="tip">Re-running on the same track replaces the samples it made last time (manual ones are kept).</p>
    </div>
  )
}
