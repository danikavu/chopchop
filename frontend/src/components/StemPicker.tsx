import { useMemo, useState } from 'react'
import type { Meta } from '../types'
import { label } from '../util'

export interface SeparateOptions {
  model: string
  stems: string[] | null
  rest: boolean
  replace: boolean
}

interface Props {
  meta: Meta
  hasStems: boolean
  onStart: (opts: SeparateOptions) => void
  onCancel?: () => void
}

const ROFORMER = 'model_bs_roformer_ep_317_sdr_12.9755.ckpt'

/** Mirrors the backend's name for the track the unselected stems are mixed into. */
function restName(keep: string[]) {
  if (keep.length === 1 && keep[0] === 'vocals') return 'instrumental'
  if (keep.length === 1) return `no_${keep[0]}`
  return 'rest'
}

/** Choose the separation model and which stems end up in the project. */
export function StemPicker({ meta, hasStems, onStart, onCancel }: Props) {
  const [model, setModel] = useState(() => meta.models.find((m) => m.default)?.id ?? meta.models[0]?.id ?? '')
  const stems = useMemo(() => meta.models.find((m) => m.id === model)?.stems ?? [], [meta.models, model])
  const [picked, setPicked] = useState<string[]>(stems)
  const [rest, setRest] = useState(true)
  const [replace, setReplace] = useState(true)

  const keep = stems.filter((s) => picked.includes(s))
  const all = keep.length === stems.length
  const result = all || !rest ? keep : [...keep, restName(keep)]
  const presets = [
    { name: 'All stems', stems },
    ...(stems.includes('vocals') && stems.length > 2 ? [{ name: 'Vocals + instrumental', stems: ['vocals'] }] : []),
    ...(stems.includes('drums') ? [{ name: 'Drums + rest', stems: ['drums'] }] : []),
  ]
  const same = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x))

  const pickModel = (id: string) => {
    setModel(id)
    setPicked(meta.models.find((m) => m.id === id)?.stems ?? [])
  }
  const toggle = (s: string) => setPicked((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]))

  return (
    <div className="stem-picker">
      <label className="field">
        <span>Model</span>
        <select value={model} onChange={(e) => pickModel(e.target.value)}>
          {meta.models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label} — {m.stems.join(', ')}
            </option>
          ))}
        </select>
      </label>

      <div className="row presets">
        {presets.map((p) => (
          <button key={p.name} className={`chip ${same(keep, p.stems) && (p.stems === stems || rest) ? 'on' : ''}`}
            onClick={() => {
              setPicked(p.stems)
              setRest(true)
            }}>
            {p.name}
          </button>
        ))}
      </div>

      <div className="chips">
        {stems.map((s) => (
          <label key={s} className={`chip ${picked.includes(s) ? 'on' : ''}`}>
            <input type="checkbox" checked={picked.includes(s)} onChange={() => toggle(s)} />
            {label(s)}
          </label>
        ))}
      </div>

      {!all && keep.length > 0 && (
        <label className="check">
          <input type="checkbox" checked={rest} onChange={(e) => setRest(e.target.checked)} />
          Mix the other stems into one “{restName(keep)}” track
        </label>
      )}
      {hasStems && (
        <label className="check">
          <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
          Replace the current stems (undo brings them back)
        </label>
      )}
      {same(keep, ['vocals']) && model !== ROFORMER && meta.models.some((m) => m.id === ROFORMER) && (
        <div className="tip">
          Tip: BS-Roformer is built for vocal / instrumental splits and is usually cleaner.{' '}
          <button className="link" onClick={() => pickModel(ROFORMER)}>Use it</button>
        </div>
      )}

      <div className="row">
        <span className="muted">
          {keep.length ? <>You’ll get: <b>{result.map(label).join(', ')}</b></> : 'Pick at least one stem'}
        </span>
        <div className="spacer" />
        {onCancel && <button onClick={onCancel}>Cancel</button>}
        <button className="primary" disabled={!keep.length}
          onClick={() => onStart({ model, stems: all ? null : keep, rest, replace })}>
          Separate stems
        </button>
      </div>
    </div>
  )
}
