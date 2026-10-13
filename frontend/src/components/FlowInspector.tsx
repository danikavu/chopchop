import { Handle, Position, type NodeProps } from '@xyflow/react'
import { gridLabel, NODE_TYPES, outputsOf, portColor, summary } from '../flowCatalog'
import type { FlowNodeType, Meta } from '../types'
import { GRID_STEPS, label } from '../util'
import { Slider } from './Slider'
import { canDebleed, DEBLEED } from './StemPicker'
import { TO_C_HELP } from './ToCSelect'

export interface CardData extends Record<string, unknown> {
  type: FlowNodeType
  params: Record<string, unknown>
  meta: Meta
}

/** A node on the canvas: input on the left, one handle per output on the right. */
export function FlowCard({ data, selected }: NodeProps) {
  const { type, params, meta } = data as CardData
  const spec = NODE_TYPES[type]
  const outs = outputsOf(type, params, meta)
  return (
    <div className={`flow-card ${selected ? 'selected' : ''}`} style={{ '--c': spec.color } as React.CSSProperties}>
      {spec.input && (
        <Handle type="target" position={Position.Left} id="in" className="flow-handle"
          style={{ background: portColor(spec.input) }} title={`takes ${spec.input}`} />
      )}
      <div className="flow-card-head">{spec.label}</div>
      <div className="flow-card-body">{summary(type, params, meta)}</div>
      {outs.length > 0 && (
        <div className="flow-outs">
          {outs.map(([port, kind]) => (
            <div key={port} className="flow-out">
              <span>{label(port)}</span>
              <Handle type="source" position={Position.Right} id={port} className="flow-handle"
                style={{ background: portColor(kind) }} title={`${label(port)} (${kind})`} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

interface Props {
  type: FlowNodeType
  params: Record<string, unknown>
  meta: Meta
  onChange: (params: Record<string, unknown>) => void
  onDelete?: () => void
}

const pct = (v: number) => `${Math.round(v * 100)}%`

/** Every option of the selected node. */
export function FlowInspector({ type, params: p, meta, onChange, onDelete }: Props) {
  const spec = NODE_TYPES[type]
  const set = (patch: Record<string, unknown>) => onChange({ ...p, ...patch })
  const num = (k: string) => p[k] as number
  const check = (k: string, text: string, title?: string) => (
    <label className="check" title={title}>
      <input type="checkbox" checked={!!p[k]} onChange={(e) => set({ [k]: e.target.checked })} /> {text}
    </label>
  )
  const folder = (
    <label className="field">
      <span>Folder</span>
      <input value={p.folder as string} onChange={(e) => set({ folder: e.target.value })} />
    </label>
  )

  let body: React.ReactNode = null
  if (type === 'input') {
    body = <p className="tip">Run on a project, this is its mix; in a batch, each file of the folder.</p>
  } else if (type === 'separate') {
    const model = meta.models.find((m) => m.id === p.model)
    const all = model?.stems ?? []
    const picked = (p.stems as string[] | null) ?? all
    const offered = meta.models.filter((m) => !m.hidden || m.id === p.model)
    const groups = [...new Set(offered.map((m) => m.group ?? 'Models'))]
    const toggle = (s: string) => {
      const next = picked.includes(s) ? picked.filter((x) => x !== s) : [...picked, s]
      set({ stems: next.length === all.length ? null : all.filter((x) => next.includes(x)) })
    }
    body = (
      <>
        <label className="field">
          <span>Model</span>
          <select value={p.model as string} onChange={(e) => set({ model: e.target.value, stems: null })}>
            {groups.map((g) => (
              <optgroup key={g} label={g}>
                {offered.filter((m) => (m.group ?? 'Models') === g).map((m) => (
                  <option key={m.id} value={m.id}>{m.label}{m.note ? ` — ${m.note}` : ''}</option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
        <div className="field-label">Keep</div>
        <div className="chips">
          {all.map((s) => (
            <label key={s} className={`chip ${picked.includes(s) ? 'on' : ''}`}>
              <input type="checkbox" checked={picked.includes(s)} onChange={() => toggle(s)} /> {label(s)}
            </label>
          ))}
        </div>
        {picked.length < all.length && check('rest', 'Mix the others into one more track')}
        {canDebleed(all) && check('debleed', `Remove drum bleed from ${all.filter((s) => DEBLEED.includes(s)).join(' / ')}`)}
        <p className="tip">Stems of another stem are named after it, e.g. <code>instrumental_drums</code>.</p>
      </>
    )
  } else if (type === 'tool') {
    body = (
      <>
        <label className="field">
          <span>Tool</span>
          <select value={p.tool as string} onChange={(e) => set({ tool: e.target.value })}>
            {Object.entries(meta.tools).map(([id, t]) => <option key={id} value={id}>{t.label}</option>)}
          </select>
        </label>
        <p className="tip">{meta.tools[p.tool as string]?.description} Meant for {meta.tools[p.tool as string]?.for}.</p>
      </>
    )
  } else if (type === 'slices') {
    body = (
      <>
        <div className="seg">
          <button className={p.mode === 'detect' ? 'on' : ''} onClick={() => set({ mode: 'detect' })}>Detect hits</button>
          <button className={p.mode === 'grid' ? 'on' : ''} onClick={() => set({ mode: 'grid' })}>Beat grid</button>
        </div>
        {p.mode === 'detect' ? (
          <>
            <Slider label="Sensitivity" value={num('sensitivity')} min={0} max={1} step={0.01} format={pct}
              onChange={(v) => set({ sensitivity: v })} />
            <Slider label="Min gap" value={num('min_gap')} min={0.02} max={1} step={0.01}
              format={(v) => `${Math.round(v * 1000)} ms`} onChange={(v) => set({ min_gap: v })} />
            <Slider label="Max length" value={(p.max_len as number | null) ?? 0} min={0} max={4} step={0.05}
              format={(v) => (v === 0 ? 'until next' : `${v.toFixed(2)} s`)} onChange={(v) => set({ max_len: v || null })} />
            <Slider label="Gate" value={num('gate_db')} min={-80} max={-6} step={1} format={(v) => `${v} dB`}
              onChange={(v) => set({ gate_db: v })} />
          </>
        ) : (
          <>
            <div className="chips">
              {GRID_STEPS.map((g) => (
                <button key={g.beats} className={`chip ${p.grid_beats === g.beats ? 'on' : ''}`}
                  onClick={() => set({ grid_beats: g.beats })}>{g.label}</button>
              ))}
            </div>
            <p className="tip">One chop per {gridLabel(num('grid_beats'))}, from the tempo (detected if needed).</p>
          </>
        )}
      </>
    )
  } else if (type === 'kit') {
    body = (
      <>
        <Slider label="Sensitivity" value={num('sensitivity')} min={0} max={1} step={0.01} format={pct}
          onChange={(v) => set({ sensitivity: v })} />
        <Slider label="Uniqueness" value={num('uniqueness')} min={0.7} max={0.995} step={0.005} format={pct}
          onChange={(v) => set({ uniqueness: v })}
          hint="Hits more similar than this to a kept one are duplicates. Lower = fewer, more different sounds." />
        <Slider label="Max per type" value={num('max_per_type')} min={1} max={32} step={1} onChange={(v) => set({ max_per_type: v })} />
        <label className="field">
          <span>Kit size</span>
          <select value={String(p.kit_size ?? '')} onChange={(e) => set({ kit_size: e.target.value ? Number(e.target.value) : null })}>
            {[8, 16, 32, 64].map((n) => <option key={n} value={n}>{n} pads</option>)}
            <option value="">every distinct hit</option>
          </select>
        </label>
        {check('load_pads', 'Load onto the drum pads')}
        <p className="tip">Connect several tracks (e.g. split drum parts) for one kit across them.</p>
      </>
    )
  } else if (type === 'bits') {
    body = (
      <>
        <div className="seg">
          <button className={p.mode === 'chops' ? 'on' : ''} onClick={() => set({ mode: 'chops' })}>Chops</button>
          <button className={p.mode === 'phrases' ? 'on' : ''} onClick={() => set({ mode: 'phrases' })}>Phrases</button>
        </div>
        <Slider label="Sensitivity" value={num('sensitivity')} min={0} max={1} step={0.01} format={pct}
          onChange={(v) => set({ sensitivity: v })} />
        <Slider label="Uniqueness" value={num('uniqueness')} min={0.7} max={0.995} step={0.005} format={pct}
          onChange={(v) => set({ uniqueness: v })} />
        <Slider label="Max bits per stem" value={num('max')} min={1} max={64} step={1} onChange={(v) => set({ max: v })} />
        <p className="tip">Each incoming stem is judged on its own.</p>
      </>
    )
  } else if (type === 'save_stems') {
    body = (
      <>
        {folder}
        {check('normalize', 'Normalize')}
        {check('bpm_names', 'BPM in names')}
        {check('key_names', 'Song key in names (pitched stems)')}
      </>
    )
  } else if (type === 'save_slices') {
    body = (
      <>
        {folder}
        <Slider label="Fade in" value={num('fade_in_ms')} min={0} max={50} step={1} format={(v) => `${v} ms`}
          onChange={(v) => set({ fade_in_ms: v })} />
        <Slider label="Fade out" value={num('fade_out_ms')} min={0} max={200} step={1} format={(v) => `${v} ms`}
          onChange={(v) => set({ fade_out_ms: v })} />
        {check('normalize', 'Normalize each slice')}
        {check('bpm_names', 'BPM in loop names')}
        {check('key_names', 'Key in file names (slower)')}
        {check('skip_silent', 'Skip silent chops')}
      </>
    )
  } else if (type === 'save_samples') {
    body = (
      <>
        {folder}
        {check('normalize', 'Normalize')}
        {check('bpm_names', 'BPM in names (loops / phrases)')}
        <label className="field" title={TO_C_HELP}>
          <span>Also in C</span>
          <select value={p.to_c as string} onChange={(e) => set({ to_c: e.target.value })}>
            <option value="none">off</option>
            <option value="repitched">repitched</option>
            <option value="warped">warped</option>
            <option value="both">both</option>
          </select>
        </label>
        {check('pad_names', 'Pad names (A01_kick…) for drum kits')}
      </>
    )
  }

  return (
    <div className="flow-inspector">
      <h3 style={{ color: spec.color }}>{spec.label}</h3>
      <p className="tip">{spec.hint}</p>
      {body}
      {onDelete && <button className="wide" onClick={onDelete}>Delete node</button>}
    </div>
  )
}
