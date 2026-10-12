import { useMemo, useState } from 'react'
import type { Meta, Part, Slice } from '../types'
import { fmtTime, label } from '../util'

export interface SeparateOptions {
  model: string
  stems: string[] | null
  rest: boolean
  replace: boolean
  debleed: boolean
  parts?: Part[]
}

interface Props {
  meta: Meta
  hasStems: boolean
  onStart: (opts: SeparateOptions) => void
  onCancel?: () => void
  /** Separate only parts of the track: drawn on the waveform while picking. */
  parts: Part[]
  onParts: (parts: Part[]) => void
  picking: boolean
  onPicking: (v: boolean) => void
  selected?: Slice
  duration: number
}

/** Stems the models leave drum bleed in (mirrors separation.DEBLEED_STEMS). */
export const DEBLEED = ['other', 'guitar', 'piano']
export const canDebleed = (stems: string[]) => stems.includes('drums') && stems.some((s) => DEBLEED.includes(s))

/** Mirrors the backend's name for the track the unselected stems are mixed into. */
function restName(keep: string[]) {
  if (keep.length === 1 && keep[0] === 'vocals') return 'instrumental'
  if (keep.length === 1) return `no_${keep[0]}`
  return 'rest'
}

/** Choose the separation model and which stems end up in the project. */
export function StemPicker({ meta, hasStems, onStart, onCancel, parts, onParts, picking, onPicking, selected, duration }: Props) {
  const [model, setModel] = useState(() => meta.models.find((m) => m.default)?.id ?? meta.models[0]?.id ?? '')
  const current = meta.models.find((m) => m.id === model)
  const stems = useMemo(() => current?.stems ?? [], [current])
  const groups = [...new Set(meta.models.map((m) => m.group ?? 'Models'))]
  const [picked, setPicked] = useState<string[]>(stems)
  const [rest, setRest] = useState(true)
  const [replace, setReplace] = useState(true)
  const [debleed, setDebleed] = useState(true)

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
      {canDebleed(stems) && (
        <label className="check" title="The models leave faint drums in these stems, so sound bits cut from them come out as drum hits. One extra fast pass per stem moves that bleed into drums.">
          <input type="checkbox" checked={debleed} onChange={(e) => setDebleed(e.target.checked)} />
          Remove drum bleed from {stems.filter((s) => DEBLEED.includes(s)).join(' / ')} (extra fast pass)
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

      <label className="check" title="Separate just the parts you mark (much faster for a short section). The stems are silent elsewhere but stay in line with the mix.">
        <input type="checkbox" checked={picking} onChange={(e) => onPicking(e.target.checked)} />
        Only parts of the track
      </label>
      {picking && (
        <div className="sep-parts">
          <div className="muted small">
            Drag on the waveform to mark each part{selected ? ', or add the selected slice' : ''}.
            {parts.length > 0 && <> · {fmtTime(parts.reduce((n, r) => n + r.end - r.start, 0))} of {fmtTime(duration)}</>}
          </div>
          <div className="chips">
            {parts.map((r, i) => (
              <span key={i} className="chip on">
                {fmtTime(r.start)} – {fmtTime(r.end)}
                <button className="link" title="Remove this part" onClick={() => onParts(parts.filter((_, j) => j !== i))}>×</button>
              </span>
            ))}
            {selected && (
              <button className="chip" onClick={() => onParts([...parts, { start: selected.start, end: selected.end }]
                .sort((a, b) => a.start - b.start))}>+ selected slice</button>
            )}
            {parts.length > 0 && <button className="link" onClick={() => onParts([])}>clear</button>}
          </div>
        </div>
      )}

      <div className="row">
        <span className="muted">
          {keep.length ? <>You’ll get: <b>{result.map(label).join(', ')}</b>{picking && parts.length ? ` (${parts.length} part${parts.length > 1 ? 's' : ''})` : ''}</> : 'Pick at least one stem'}
        </span>
        <div className="spacer" />
        {onCancel && <button onClick={onCancel}>Cancel</button>}
        <button className="primary" disabled={!keep.length || (picking && !parts.length)}
          title={picking && !parts.length ? 'Mark at least one part on the waveform' : undefined}
          onClick={() => onStart({ model, stems: all ? null : keep, rest, replace, debleed, parts: picking ? parts : undefined })}>
          {picking ? 'Separate parts' : 'Separate stems'}
        </button>
      </div>
    </div>
  )
}
