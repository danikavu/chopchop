import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { Job } from './types'

const TRACK_COLORS: Record<string, string> = {
  mix: '#9aa4b8',
  vocals: '#f472b6',
  drums: '#fbbf24',
  bass: '#34d399',
  guitar: '#fb923c',
  piano: '#a78bfa',
  other: '#60a5fa',
  instrumental: '#60a5fa',
}
const FALLBACK = ['#22d3ee', '#f87171', '#a3e635', '#e879f9', '#facc15', '#2dd4bf']

export function trackColor(name: string, index = 0): string {
  for (const [key, color] of Object.entries(TRACK_COLORS)) {
    if (name === key || name.includes(key)) return color
  }
  return FALLBACK[index % FALLBACK.length]
}

const CATEGORY_COLORS: Record<string, string> = {
  kick: '#f87171',
  snare: '#fbbf24',
  clap: '#fb923c',
  hihat: '#a3e635',
  open_hat: '#4ade80',
  cymbal: '#2dd4bf',
  tom: '#f472b6',
  perc: '#e879f9',
  vocal: '#f9a8d4',
  bass: '#34d399',
  keys: '#a78bfa',
  guitar: '#fdba74',
  synth: '#22d3ee',
  melodic: '#60a5fa',
  fx: '#c084fc',
  mix: '#cbd5e1',
  other: '#9aa4b8',
}

export function categoryColor(cat: string): string {
  return CATEGORY_COLORS[cat] ?? '#9aa4b8'
}

/** Mirrors the backend: only drum tracks get kick/snare/hat labels. */
export function isDrumTrack(name: string): boolean {
  const n = name.toLowerCase()
  return n.includes('drum') || n.includes('perc')
}

/** Tracks made by the "Split drums" tool: drums_kick, drums_snare, … (mirrors the backend). */
export function isDrumPart(name: string): boolean {
  return /_(kick|snare|toms|hihat|ride|crash)$/.test(name) && isDrumTrack(name)
}

export const NOT_DRUMS_NOTE =
  'Not a drum track: sounds are labelled and named by the track type (vocal_01, bass_01, …) instead of kick / snare / hat.'

export function label(s: string): string {
  return s.replace(/_/g, ' ')
}

export function fmtTime(t: number): string {
  if (!isFinite(t)) return '0:00.000'
  const m = Math.floor(t / 60)
  const s = t - m * 60
  return `${m}:${s.toFixed(3).padStart(6, '0')}`
}

export function withAlpha(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

/** Poll a background job until it finishes. */
export function useJob(jobId: string | null, onDone: (job: Job) => void): Job | null {
  const [job, setJob] = useState<Job | null>(null)
  const done = useRef(onDone)
  done.current = onDone

  useEffect(() => {
    if (!jobId) {
      setJob(null)
      return
    }
    let alive = true
    let timer: number | undefined
    const tick = async () => {
      try {
        const j = await api.job(jobId)
        if (!alive) return
        setJob(j)
        if (j.status === 'done' || j.status === 'error') {
          done.current(j)
          return
        }
      } catch {
        /* transient; retry */
      }
      timer = window.setTimeout(tick, 600)
    }
    tick()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [jobId])

  return job
}

/** One shared audio element for auditioning samples, so previews never overlap. */
const preview = new Audio()
export function playPreview(url: string) {
  if (preview.src.endsWith(url) && !preview.paused) {
    preview.pause()
    preview.currentTime = 0
    return
  }
  preview.src = url
  preview.currentTime = 0
  void preview.play()
}
export function stopPreview() {
  preview.pause()
}

/** Every note name from C0 to B7, plus major/minor keys — suggestions for the key field. */
export function keySuggestions(notes: string[], keys: string[]): string[] {
  const out: string[] = []
  for (let oct = 0; oct <= 7; oct++) for (const n of notes) out.push(`${n}${oct}`)
  return [...keys, ...out]
}

/** Grid resolutions, in beats. */
export const GRID_STEPS: { beats: number; label: string }[] = [
  { beats: 0.25, label: '1/16' },
  { beats: 0.5, label: '1/8' },
  { beats: 1, label: '1 beat' },
  { beats: 2, label: '2 beats' },
  { beats: 4, label: '1 bar' },
  { beats: 8, label: '2 bars' },
  { beats: 16, label: '4 bars' },
]

/**
 * Grid lines every `step` beats, built from the tracked beats (so the grid follows a
 * live drummer's tempo drift). Steps under a beat split each beat evenly; bar-sized
 * steps start at the downbeat. One extra line past the last beat closes the final cell.
 */
export function gridPoints(beats: number[], step: number, downbeat = 0): number[] {
  if (beats.length < 2) return []
  const ibi = (beats[beats.length - 1] - beats[0]) / (beats.length - 1)
  const ext = [...beats, beats[beats.length - 1] + ibi]
  const out: number[] = []
  if (step < 1) {
    const n = Math.round(1 / step)
    for (let i = 0; i < ext.length - 1; i++)
      for (let k = 0; k < n; k++) out.push(ext[i] + ((ext[i + 1] - ext[i]) * k) / n)
    out.push(ext[ext.length - 1])
    return out
  }
  const k = Math.round(step)
  for (let i = 0; i < ext.length; i++) if (i >= downbeat && (i - downbeat) % k === 0) out.push(ext[i])
  // Close the last cell even if it runs past the tracked beats.
  const last = out[out.length - 1]
  if (last !== undefined && ext[ext.length - 1] - last < k * ibi * 0.5) return out
  if (last !== undefined) out.push(last + k * ibi)
  return out
}

/** Nearest grid line to t (binary search). */
export function snapTo(points: number[], t: number): number {
  if (!points.length) return t
  let lo = 0
  let hi = points.length - 1
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (points[mid] <= t) lo = mid
    else hi = mid
  }
  return Math.abs(points[lo] - t) <= Math.abs(points[hi] - t) ? points[lo] : points[hi]
}
