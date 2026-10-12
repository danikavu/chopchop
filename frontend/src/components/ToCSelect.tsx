import { useEffect, useState } from 'react'
import type { Sample, ToC } from '../types'

// One remembered choice for every kit export (sample browser, drum pads).
const KEY = 'chopchop.toC'
const listeners = new Set<(v: ToC) => void>()

function stored(): ToC {
  try {
    return (localStorage.getItem(KEY) as ToC) || 'none'
  } catch {
    return 'none'
  }
}

export function useToC(): [ToC, (v: ToC) => void] {
  const [value, setValue] = useState<ToC>(stored)
  useEffect(() => {
    listeners.add(setValue)
    return () => {
      listeners.delete(setValue)
    }
  }, [])
  const set = (v: ToC) => {
    try {
      localStorage.setItem(KEY, v)
    } catch {
      /* private mode: just not remembered */
    }
    listeners.forEach((l) => l(v))
  }
  return [value, set]
}

/** Keyed samples a C export adds (C itself and unkeyed ones are skipped). */
export const transposable = (samples: Sample[]) =>
  samples.filter((s) => s.key && !/^C(?![#b])/i.test(s.key.trim())).length

export const TO_C_HELP =
  'Also export every keyed sample transposed to C, in its own folder.\n' +
  'Repitched: resampled like a sampler (length and tempo change).\nWarped: pitch-shifted, same length.\n' +
  'Samples already in C or without a key are skipped.'

/** "→ C [off|repitched|warped|both] n to move" */
export function ToCSelect({ samples }: { samples: Sample[] }) {
  const [toC, setToC] = useToC()
  return (
    <label className="check" title={TO_C_HELP}>
      → C
      <select value={toC} onChange={(e) => setToC(e.target.value as ToC)}>
        <option value="none">off</option>
        <option value="repitched">repitched</option>
        <option value="warped">warped</option>
        <option value="both">both</option>
      </select>
      {toC !== 'none' && <span className="muted small">{transposable(samples)} to move</span>}
    </label>
  )
}
