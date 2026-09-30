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

/** Mirrors the backend's name for the track the unselected stems are mixed into. */
function restName(keep: string[]) {
  if (keep.length === 1 && keep[0] === 'vocals') return 'instrumental'
  if (keep.length === 1) return `no_${keep[0]}`
  return 'rest'
}

/** Choose the separation model and which stems end up in the project. */
export function StemPicker({ meta, hasStems, onStart, onCancel }: Props) {
  const [model, setModel] = useState(() => meta.models.find((m) => m.default)?.id ?? meta.models[0]?.id ?? '')
  const current = meta.models.find((m) => m.id === model)
  const stems = useMemo(() => current?.stems ?? [], [current])
  const groups = [...new Set(meta.models.map((m) => m.group ?? 'Models'))]
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
  // A model that's the best at every stem you kept (e.g. only vocals → Mel-Band Roformer).
  const better =
    !all && keep.length
      ? meta.models.find((m) => m.id !== model && keep.every((s) => m.best_for?.includes(s)))
      : undefined

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
          {groups.map((g) => (
            <optgroup key={g} label={g}>
              {meta.models.filter((m) => (m.group ?? 'Models') === g).map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}{m.note ? ` — ${m.note}` : ''}
                </option>
              ))}
            </optgroup>
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
      {better && (
        <div className="tip">
          Tip: <b>{better.label}</b> is the strongest model for {keep.map(label).join(' + ')} — {better.note}.{' '}
          <button className="link" onClick={() => {
            const want = keep
            pickModel(better.id)
            setPicked(want.filter((s) => better.stems.includes(s)))
          }}>Use it</button>
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
