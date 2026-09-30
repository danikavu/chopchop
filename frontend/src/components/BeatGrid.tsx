import { useRef, useState } from 'react'
import { gridPoints } from '../util'

interface Props {
  beats: number[]
  downbeat: number
  /** Grid resolution in beats; below 1 draws faint subdivision lines too. */
  step: number
  zoom: number
  scrollLeft: number
  width: number
  left: number
  /** "Drag grid" mode: bar lines can be dragged, moving the whole grid. */
  draggable: boolean
  onShift: (seconds: number) => void
}

/**
 * The beat grid, drawn once over all lanes: thin red lines, glowing a little brighter on
 * bar lines. Only lines in view are rendered.
 */
export function BeatGrid({ beats, downbeat, step, zoom, scrollLeft, width, left, draggable, onShift }: Props) {
  const [drag, setDrag] = useState<number>(0) // live preview offset while dragging (s)
  const start = useRef<{ x: number } | null>(null)

  const t0 = scrollLeft / zoom - 1
  const t1 = (scrollLeft + width) / zoom + 1
  const x = (t: number) => left + (t + drag) * zoom - scrollLeft
  const visible = (t: number) => t + drag >= t0 && t + drag <= t1

  const subs = step < 1 ? gridPoints(beats, step, downbeat).filter(visible) : []
  const lines = beats.map((t, i) => ({ t, i, bar: i >= downbeat && (i - downbeat) % 4 === 0 })).filter((b) => visible(b.t))

  const onPointerDown = (e: React.PointerEvent) => {
    e.preventDefault()
    e.stopPropagation()
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
    start.current = { x: e.clientX }
  }
  const onPointerMove = (e: React.PointerEvent) => {
    if (start.current) setDrag((e.clientX - start.current.x) / zoom)
  }
  const onPointerUp = () => {
    if (!start.current) return
    start.current = null
    const moved = drag
    setDrag(0)
    if (Math.abs(moved) > 0.0005) onShift(moved)
  }

  return (
    <div className={`beat-grid ${draggable ? 'editing' : ''}`} style={{ left, width }}>
      {subs.map((t) => (
        <div key={`s${t}`} className="grid-line sub" style={{ left: x(t) - left }} />
      ))}
      {lines.map((b) => (
        <div key={b.i} className={`grid-line ${b.bar ? 'bar' : 'beat'}`} style={{ left: x(b.t) - left }}>
          {b.bar && draggable && (
            <span className="grid-handle" title="Drag to move the whole grid"
              onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp} />
          )}
        </div>
      ))}
      {drag !== 0 && (
        <div className="grid-drag-label" style={{ left: Math.min(width - 90, Math.max(4, x(lines[0]?.t ?? 0) - left + 6)) }}>
          {drag > 0 ? '+' : ''}{(drag * 1000).toFixed(0)} ms
        </div>
      )}
    </div>
  )
}
