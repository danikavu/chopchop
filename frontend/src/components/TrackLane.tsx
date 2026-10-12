import { useEffect, useRef, useState } from 'react'
import WaveSurfer from 'wavesurfer.js'
import RegionsPlugin from 'wavesurfer.js/dist/plugins/regions.esm.js'
import TimelinePlugin from 'wavesurfer.js/dist/plugins/timeline.esm.js'
import { api } from '../api'
import type { Slice, Track } from '../types'
import { label, withAlpha } from '../util'

type Regions = ReturnType<typeof RegionsPlugin.create>

/** Slice colours, cycled in order. No red: that's the beat grid's colour. */
const SLICE_COLORS = ['#f59e0b', '#22d3ee', '#a78bfa', '#34d399', '#f472b6', '#60a5fa']

export interface LaneEvents {
  onFocus: (name: string) => void
  onTime: (name: string, t: number) => void
  onPlayState: (name: string, playing: boolean) => void
  onScroll: (name: string, scrollLeft: number) => void
  onRegister: (name: string, ws: WaveSurfer | null) => void
  onSliceChange: (slice: Slice) => void
  /** Drawn on this lane (the lane decides nothing: ProjectView picks shared / only-this-stem / part). */
  onSliceCreate: (start: number, end: number, track: string) => void
  onSelectSlice: (id: string | null) => void
  onToggleExport: (name: string) => void
  onDelete?: (name: string) => void
  /** Stem tools (drum split, de-reverb, …), suitable ones listed first. */
  tools?: { id: string; label: string; description: string; suggested: boolean }[]
  onTool?: (track: string, tool: string) => void
}

interface Props extends LaneEvents {
  pid: string
  track: Track
  color: string
  zoom: number
  slices: Slice[]
  selectedId: string | null
  focused: boolean
  exportChecked: boolean
  timeline: boolean
  showScrollbar: boolean
}

export function TrackLane(props: Props) {
  const { pid, track, color, zoom, slices, selectedId, focused, exportChecked } = props
  const container = useRef<HTMLDivElement>(null)
  const wsRef = useRef<WaveSurfer | null>(null)
  const regionsRef = useRef<Regions | null>(null)
  const syncing = useRef(false)
  const zoomRef = useRef(zoom)
  zoomRef.current = zoom
  // Latest callbacks, so the wavesurfer listeners registered once never go stale.
  const cb = useRef(props)
  cb.current = props
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [toolsOpen, setToolsOpen] = useState(false)

  useEffect(() => {
    let ws: WaveSurfer | null = null
    let cancelled = false
    const n = Math.round(Math.min(50000, Math.max(4000, track.duration * 250)))
    api
      .trackPeaks(pid, track.name, n)
      .then(({ peaks, duration }) => {
        if (cancelled || !container.current) return
        const regions = RegionsPlugin.create()
        const plugins = [regions] as NonNullable<Parameters<typeof WaveSurfer.create>[0]['plugins']>
        if (props.timeline) {
          plugins.push(
            TimelinePlugin.create({
              height: 16,
              insertPosition: 'beforebegin',
              style: { color: 'var(--muted)', fontSize: '10px' },
            }),
          )
        }
        ws = WaveSurfer.create({
          container: container.current,
          height: 76,
          waveColor: withAlpha(color, 0.8),
          progressColor: withAlpha(color, 0.8),
          cursorWidth: 0,
          url: api.trackAudioUrl(pid, track.name),
          peaks: [peaks],
          duration,
          minPxPerSec: zoomRef.current,
          fillParent: false,
          autoScroll: true,
          autoCenter: false,
          hideScrollbar: !props.showScrollbar,
          normalize: true,
          plugins,
        })
        wsRef.current = ws
        regionsRef.current = regions

        regions.enableDragSelection({ color: withAlpha(color, 0.3) })
        regions.on('region-created', (r) => {
          if (syncing.current) return
          // Drawn by the user: turn it into a slice; state re-renders it.
          const { start, end } = r
          r.remove()
          if (end - start > 0.01) cb.current.onSliceCreate(start, end, track.name)
        })
        regions.on('region-updated', (r) => cb.current.onSliceChange({ id: r.id, start: r.start, end: r.end }))
        regions.on('region-clicked', (r, e) => {
          e.stopPropagation()
          cb.current.onFocus(track.name)
          cb.current.onSelectSlice(r.id)
          void ws?.play(r.start, r.end)
        })

        ws.on('interaction', (t) => {
          cb.current.onFocus(track.name)
          cb.current.onTime(track.name, t)
        })
        ws.on('timeupdate', (t) => cb.current.onTime(track.name, t))
        ws.on('play', () => cb.current.onPlayState(track.name, true))
        ws.on('pause', () => cb.current.onPlayState(track.name, false))
        ws.on('scroll', (_a, _b, left) => cb.current.onScroll(track.name, left))
        ws.on('error', (e) => setError(String(e)))
        cb.current.onRegister(track.name, ws)
        setReady(true)
      })
      .catch((e) => setError(String(e)))

    return () => {
      cancelled = true
      cb.current.onRegister(track.name, null)
      ws?.destroy()
      wsRef.current = null
      regionsRef.current = null
      setReady(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, track.name, track.file, track.duration])

  useEffect(() => {
    if (ready) wsRef.current?.zoom(zoom)
  }, [zoom, ready])

  useEffect(() => {
    const regions = regionsRef.current
    if (!ready || !regions) return
    syncing.current = true
    regions.clearRegions()
    slices.forEach((s, i) => {
      const sel = s.id === selectedId
      // Neighbouring slices get different hues (the same slice has the same hue on every
      // lane), so you can see where one chop ends and the next begins.
      const hue = SLICE_COLORS[i % SLICE_COLORS.length]
      const r = regions.addRegion({
        id: s.id,
        start: s.start,
        end: s.end,
        color: withAlpha(hue, sel ? 0.42 : focused ? 0.17 : 0.11),
        drag: true,
        resize: true,
        minLength: 0.005,
      })
      if (r.element) {
        // Chops on this stem only: dashed edges, so they stand out from the shared ones.
        const line = s.track ? 'dashed' : 'solid'
        r.element.style.borderLeft = `1px ${line} ${withAlpha(hue, sel ? 1 : 0.75)}`
        r.element.style.borderRight = s.track ? `1px dashed ${withAlpha(hue, 0.5)}` : ''
        r.element.style.boxShadow = sel ? `inset 0 0 0 1px ${withAlpha(hue, 0.9)}` : ''
        r.element.title = s.track ? `Only on ${label(s.track)}` : ''
      }
    })
    syncing.current = false
  }, [slices, selectedId, ready, color, focused])

  return (
    <div className={`lane ${focused ? 'focused' : ''}`}>
      <div className="lane-head" onClick={() => props.onFocus(track.name)} style={{ borderLeftColor: color }}>
        <div className="lane-name" title={track.name}>
          {label(track.name)}
        </div>
        <div className="lane-meta">{track.kind === 'mix' ? 'original' : 'stem'}</div>
        <div className="lane-actions" onClick={(e) => e.stopPropagation()}>
          <label className="check" title="Include this track when exporting slices">
            <input type="checkbox" checked={exportChecked} onChange={() => props.onToggleExport(track.name)} />
            export
          </label>
          {props.tools && props.tools.length > 0 && (
            <span className="lane-tools">
              <button className="icon-btn" title="Stem tools" onClick={() => setToolsOpen((o) => !o)}>✦</button>
              {toolsOpen && (
                <div className="lane-menu" onMouseLeave={() => setToolsOpen(false)}>
                  {props.tools.map((t) => (
                    <button key={t.id} onClick={() => {
                      setToolsOpen(false)
                      props.onTool?.(track.name, t.id)
                    }}>
                      <b>{t.label}</b>
                      <span className="muted">{t.description}{t.suggested ? '' : ' · meant for other stems'}</span>
                    </button>
                  ))}
                </div>
              )}
            </span>
          )}
          {props.onDelete && (
            <button className="icon-btn" title="Remove stem" onClick={() => props.onDelete?.(track.name)}>
              ×
            </button>
          )}
        </div>
      </div>
      <div className="lane-wave" ref={container}>
        {error && <div className="lane-error">{error}</div>}
      </div>
    </div>
  )
}
