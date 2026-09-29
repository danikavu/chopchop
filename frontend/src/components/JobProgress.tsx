import { useEffect, useState } from 'react'
import type { Job } from '../types'

export interface Step {
  label: string
  /** The step is finished once overall progress reaches this value. */
  until: number
}

/** Steps of a stem separation, matching the progress ranges the backend reports. */
export const SEPARATION_STEPS: Step[] = [
  { label: 'Prepare / download model', until: 0.16 },
  { label: 'Load model', until: 0.18 },
  { label: 'Separate', until: 0.88 },
  { label: 'Write stems', until: 0.9 },
  { label: 'Import stems', until: 1 },
]

function clock(s: number) {
  s = Math.max(0, Math.round(s))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** Progress bar with the current step, percentage, elapsed time and a rough ETA. */
export function JobProgress({ job, steps, fallback = 'Starting…' }: { job: Job | null; steps?: Step[]; fallback?: string }) {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => window.clearInterval(t)
  }, [])

  const p = job?.progress ?? 0
  const elapsed = job?.started_at ? now - job.started_at : 0
  // Only guess once there's enough signal; early phases (downloads) skew it.
  const eta = p > 0.2 && p < 1 && elapsed > 5 ? (elapsed * (1 - p)) / p : null
  const current = steps ? steps.findIndex((s) => p < s.until) : -1

  return (
    <div className="job">
      {steps && (
        <ol className="job-steps">
          {steps.map((s, i) => (
            <li key={s.label} className={i < current || current < 0 ? 'done' : i === current ? 'now' : ''}>
              {s.label}
            </li>
          ))}
        </ol>
      )}
      <div className="progress">
        <div className="bar" style={{ width: `${Math.round(p * 100)}%` }} />
        <span>{job?.message || (job?.status === 'pending' ? 'Waiting for another job to finish…' : fallback)}</span>
      </div>
      <div className="job-meta">
        <span>{Math.round(p * 100)}%</span>
        {job?.started_at && <span>elapsed {clock(elapsed)}</span>}
        {eta !== null && <span>~{clock(eta)} left</span>}
      </div>
    </div>
  )
}
