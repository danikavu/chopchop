import { useEffect, useRef, useState } from 'react'
import WaveSurfer from 'wavesurfer.js'
import RegionsPlugin from 'wavesurfer.js/dist/plugins/regions.esm.js'
import { api } from '../api'
import type { Sample, Track } from '../types'
import { categoryColor, fmtTime, withAlpha } from '../util'
import { Slider } from './Slider'

interface Props {
  pid: string
  sample: Sample
  track: Track
  onClose: () => void
  onSaved: (s: Sample) => void
}

const PAD = 1.0

/** Trim a sample against its source track: drag region edges or use the sliders. */
export function SampleEditor({ pid, sample, track, onClose, onSaved }: Props) {
  const winStart = Math.max(0, sample.start - PAD)
  const winEnd = Math.min(track.duration, sample.end + PAD)
  const [start, setStart] = useState(sample.start)
  const [end, setEnd] = useState(sample.end)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const container = useRef<HTMLDivElement>(null)
  const ws = useRef<WaveSurfer | null>(null)
  const region = useRef<ReturnType<ReturnType<typeof RegionsPlugin.create>['addRegion']> | null>(null)
  const color = categoryColor(sample.category)

  useEffect(() => {
    if (!container.current) return
    const regions = RegionsPlugin.create()
    const w = WaveSurfer.create({
      container: container.current,
      height: 140,
      waveColor: withAlpha(color, 0.55),
      progressColor: withAlpha(color, 0.55),
      cursorColor: '#fff',
      url: api.trackWindowUrl(pid, track.name, winStart, winEnd),
      normalize: true,
      plugins: [regions],
    })
    w.on('ready', () => {
      region.current = regions.addRegion({
        start: sample.start - winStart,
        end: sample.end - winStart,
        color: withAlpha(color, 0.25),
        drag: true,
        resize: true,
        minLength: 0.005,
      })
    })
    regions.on('region-update', (r) => {
      setStart(winStart + r.start)
      setEnd(winStart + r.end)
    })
    regions.on('region-clicked', (r, e) => {
      e.stopPropagation()
      void w.play(r.start, r.end)
    })
    ws.current = w
    return () => w.destroy()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const setBounds = (s: number, e: number) => {
    setStart(s)
    setEnd(e)
    region.current?.setOptions({ start: s - winStart, end: e - winStart })
  }

  const play = () => void ws.current?.play(start - winStart, end - winStart)

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      onSaved(await api.updateSample(pid, sample.id, { start, end }))
      onClose()
    } catch (e) {
      setError((e as Error).message)
      setSaving(false)
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === ' ') {
        e.preventDefault()
        play()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>Trim “{sample.name}”</h3>
          <span className="muted">from {track.name}</span>
        </div>
        <div ref={container} className="editor-wave" />
        <Slider label="Start" value={start} min={winStart} max={end - 0.005} step={0.001}
          onChange={(v) => setBounds(v, end)} format={fmtTime} />
        <Slider label="End" value={end} min={start + 0.005} max={winEnd} step={0.001}
          onChange={(v) => setBounds(start, v)} format={fmtTime} />
        <div className="row">
          <span className="muted">length {(end - start).toFixed(3)} s</span>
          <div className="spacer" />
          <button onClick={play}>▶ Play</button>
          <button onClick={() => setBounds(sample.start, sample.end)}>Reset</button>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
        </div>
        {error && <div className="error">{error}</div>}
      </div>
    </div>
  )
}
