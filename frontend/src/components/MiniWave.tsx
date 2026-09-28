import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Sample } from '../types'

export function MiniWave({ pid, sample, color }: { pid: string; sample: Sample; color: string }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const [peaks, setPeaks] = useState<number[] | null>(null)

  useEffect(() => {
    let alive = true
    api.samplePeaks(pid, sample).then((r) => alive && setPeaks(r.peaks)).catch(() => undefined)
    return () => {
      alive = false
    }
  }, [pid, sample.id, sample.start, sample.end])

  useEffect(() => {
    const c = canvas.current
    if (!c || !peaks) return
    const dpr = window.devicePixelRatio || 1
    const w = c.clientWidth
    const h = c.clientHeight
    c.width = w * dpr
    c.height = h * dpr
    const ctx = c.getContext('2d')!
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, w, h)
    ctx.fillStyle = color
    const max = Math.max(...peaks, 1e-6)
    const bw = w / peaks.length
    for (let i = 0; i < peaks.length; i++) {
      const v = (peaks[i] / max) * (h / 2 - 1)
      ctx.fillRect(i * bw, h / 2 - v, Math.max(1, bw - 0.5), v * 2 || 1)
    }
  }, [peaks, color])

  return <canvas ref={canvas} className="miniwave" />
}
