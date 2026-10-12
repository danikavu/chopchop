import type { Meta, ToC, Workflow } from '../types'
import { label } from '../util'
import { canDebleed, DEBLEED } from './StemPicker'
import { TO_C_HELP } from './ToCSelect'

interface Props {
  meta: Meta
  wf: Workflow
  onChange: (wf: Workflow) => void
}

const REST = (keep: string[]) => (keep.length === 1 && keep[0] === 'vocals' ? 'instrumental' : keep.length === 1 ? `no_${keep[0]}` : 'rest')

/** Tracks a song will have after separation and the stem tools (for the pickers below). */
export function producedTracks(meta: Meta, wf: Workflow): string[] {
  const out = ['mix']
  if (wf.separate.enabled) {
    const all = meta.models.find((m) => m.id === wf.separate.model)?.stems ?? []
    const keep = all.filter((s) => !wf.separate.stems || wf.separate.stems.includes(s))
    out.push(...keep)
    if (wf.separate.rest && keep.length < all.length) out.push(REST(keep))
  }
  for (const t of wf.tools) for (const o of meta.tools[t.tool]?.outputs ?? []) out.push(`${t.on}_${o}`)
  return [...new Set(out)]
}

function Section({ title, on, onToggle, children }: { title: string; on?: boolean; onToggle?: (v: boolean) => void; children?: React.ReactNode }) {
  return (
    <section className={`wf-step ${on === false ? 'off' : ''}`}>
      <h3>
        {onToggle && <input type="checkbox" checked={!!on} onChange={(e) => onToggle(e.target.checked)} />}
        {title}
      </h3>
      {on !== false && children}
    </section>
  )
}

/** Edit every step of a batch workflow. */
export function WorkflowEditor({ meta, wf, onChange }: Props) {
  const set = <K extends keyof Workflow>(k: K, v: Partial<Workflow[K]> | Workflow[K]) =>
    onChange({ ...wf, [k]: typeof v === 'object' && !Array.isArray(v) ? { ...(wf[k] as object), ...v } : v })
  const model = meta.models.find((m) => m.id === wf.separate.model)
  const modelStems = model?.stems ?? []
  const keep = modelStems.filter((s) => !wf.separate.stems || wf.separate.stems.includes(s))
  const tracks = producedTracks(meta, wf)
  const groups = [...new Set(meta.models.map((m) => m.group ?? 'Models'))]
  const toggleIn = (list: string[], x: string) => (list.includes(x) ? list.filter((y) => y !== x) : [...list, x])
  const exportCustom = Array.isArray(wf.export.stems)

  return (
    <div className="wf-editor">
      <Section title="Separate" on={wf.separate.enabled} onToggle={(v) => set('separate', { enabled: v })}>
        <label className="field">
          <span>Model</span>
          <select value={wf.separate.model} onChange={(e) => set('separate', { model: e.target.value, stems: null })}>
            {groups.map((g) => (
              <optgroup key={g} label={g}>
                {meta.models.filter((m) => (m.group ?? 'Models') === g).map((m) => (
                  <option key={m.id} value={m.id}>{m.label}{m.note ? ` — ${m.note}` : ''}</option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
        <div className="chips">
          {modelStems.map((s) => (
            <label key={s} className={`chip ${keep.includes(s) ? 'on' : ''}`}>
              <input type="checkbox" checked={keep.includes(s)} onChange={() => {
                const next = toggleIn(keep, s)
                set('separate', { stems: next.length === modelStems.length ? null : next })
              }} />
              {label(s)}
            </label>
          ))}
        </div>
        {canDebleed(modelStems) && (
          <label className="check" title="Moves the faint drums the models leave in other / guitar / piano into drums, so sound bits from those stems aren't drum hits.">
            <input type="checkbox" checked={wf.separate.debleed ?? true} onChange={(e) => set('separate', { debleed: e.target.checked })} />
            Remove drum bleed from {modelStems.filter((s) => DEBLEED.includes(s)).join(' / ')}
          </label>
        )}
        {keep.length < modelStems.length && (
          <label className="check">
            <input type="checkbox" checked={wf.separate.rest} onChange={(e) => set('separate', { rest: e.target.checked })} />
            Mix the other stems into “{REST(keep)}”
          </label>
        )}
      </Section>

      <Section title="Stem tools">
        {wf.tools.map((t, i) => (
          <div key={i} className="row wf-tool">
            <select value={t.tool} onChange={(e) => set('tools', wf.tools.map((x, j) => (j === i ? { ...x, tool: e.target.value } : x)))}>
              {Object.entries(meta.tools).map(([id, tool]) => <option key={id} value={id}>{tool.label}</option>)}
            </select>
            <span className="muted">on</span>
            <select value={t.on} onChange={(e) => set('tools', wf.tools.map((x, j) => (j === i ? { ...x, on: e.target.value } : x)))}>
              {[...new Set([t.on, ...tracks])].map((n) => <option key={n} value={n}>{label(n)}</option>)}
            </select>
            <button className="icon-btn" title="Remove" onClick={() => set('tools', wf.tools.filter((_, j) => j !== i))}>×</button>
          </div>
        ))}
        <button onClick={() => set('tools', [...wf.tools, { tool: 'drumsep', on: tracks.includes('drums') ? 'drums' : tracks[1] ?? 'mix' }])}>
          + Add tool
        </button>
      </Section>

      <Section title="Analyse">
        <label className="check"><input type="checkbox" checked={wf.tempo} onChange={(e) => set('tempo', e.target.checked)} /> Tempo (BPM)</label>
        <label className="check"><input type="checkbox" checked={wf.key} onChange={(e) => set('key', e.target.checked)} /> Song key</label>
      </Section>

      <Section title="Drum kit" on={wf.kit.enabled} onToggle={(v) => set('kit', { enabled: v })}>
        <label className="field">
          <span>From</span>
          <select value={wf.kit.source} onChange={(e) => set('kit', { source: e.target.value })}>
            <option value="parts">split drum parts (falls back to the drum stem)</option>
            <option value="drums">the drum stem</option>
            {tracks.filter((n) => n !== 'drums').map((n) => <option key={n} value={n}>{label(n)}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Kit size</span>
          <select value={wf.kit.size} onChange={(e) => set('kit', { size: Number(e.target.value) })}>
            {[8, 12, 16, 32, 64].map((n) => <option key={n} value={n}>{n} pads</option>)}
          </select>
        </label>
        <Uniqueness value={wf.kit.uniqueness} onChange={(v) => set('kit', { uniqueness: v })} />
      </Section>

      <Section title="Sound bits" on={wf.bits.enabled} onToggle={(v) => set('bits', { enabled: v })}>
        <div className="chips">
          {tracks.filter((n) => n !== 'mix').map((n) => (
            <label key={n} className={`chip ${wf.bits.stems.includes(n) ? 'on' : ''}`}>
              <input type="checkbox" checked={wf.bits.stems.includes(n)} onChange={() => set('bits', { stems: toggleIn(wf.bits.stems, n) })} />
              {label(n)}
            </label>
          ))}
        </div>
        <div className="seg">
          <button className={wf.bits.mode === 'chops' ? 'on' : ''} onClick={() => set('bits', { mode: 'chops' })}>Chops</button>
          <button className={wf.bits.mode === 'phrases' ? 'on' : ''} onClick={() => set('bits', { mode: 'phrases' })}>Phrases</button>
        </div>
        <label className="field">
          <span>Max per stem</span>
          <input type="number" min={1} max={200} value={wf.bits.max} onChange={(e) => set('bits', { max: Number(e.target.value) || 1 })} />
        </label>
      </Section>

      <Section title="Export">
        <label className="field">
          <span>Stems</span>
          <select value={exportCustom ? 'custom' : (wf.export.stems as string)}
            onChange={(e) => set('export', { stems: e.target.value === 'custom' ? tracks.filter((n) => n !== 'mix') : (e.target.value as 'all') })}>
            <option value="all">all (incl. tool outputs)</option>
            <option value="kept">separated stems only</option>
            <option value="custom">choose…</option>
            <option value="none">none</option>
          </select>
        </label>
        {exportCustom && (
          <div className="chips">
            {tracks.filter((n) => n !== 'mix').map((n) => (
              <label key={n} className={`chip ${(wf.export.stems as string[]).includes(n) ? 'on' : ''}`}>
                <input type="checkbox" checked={(wf.export.stems as string[]).includes(n)}
                  onChange={() => set('export', { stems: toggleIn(wf.export.stems as string[], n) })} />
                {label(n)}
              </label>
            ))}
          </div>
        )}
        <label className="check"><input type="checkbox" checked={wf.export.mix} onChange={(e) => set('export', { mix: e.target.checked })} /> Original mix</label>
        <label className="check"><input type="checkbox" checked={wf.export.kit} disabled={!wf.kit.enabled} onChange={(e) => set('export', { kit: e.target.checked })} /> Drum kit (pad-named: A01_kick_01…)</label>
        <label className="check"><input type="checkbox" checked={wf.export.samples} disabled={!wf.bits.enabled} onChange={(e) => set('export', { samples: e.target.checked })} /> Sound bits (by category)</label>
        <label className="check"><input type="checkbox" checked={wf.export.bpm_names} onChange={(e) => set('export', { bpm_names: e.target.checked })} /> BPM in names</label>
        <label className="check"><input type="checkbox" checked={wf.export.key_names} onChange={(e) => set('export', { key_names: e.target.checked })} /> Key in names</label>
        <label className="check"><input type="checkbox" checked={wf.export.normalize} onChange={(e) => set('export', { normalize: e.target.checked })} /> Normalize</label>
        <label className="check" title={TO_C_HELP}>
          Kit / bits also in C
          <select value={wf.export.to_c ?? 'none'} disabled={!(wf.export.kit && wf.kit.enabled) && !(wf.export.samples && wf.bits.enabled)}
            onChange={(e) => set('export', { to_c: e.target.value as ToC })}>
            <option value="none">off</option>
            <option value="repitched">repitched</option>
            <option value="warped">warped</option>
            <option value="both">both</option>
          </select>
        </label>
        <label className="check"><input type="checkbox" checked={wf.keep_projects} onChange={(e) => set('keep_projects', e.target.checked)} /> Keep each song as a ChopChop project (to tweak later)</label>
      </Section>
    </div>
  )
}

function Uniqueness({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <label className="slider" title="Two hits more similar than this count as the same sound">
      <span className="slider-top">
        <span>Uniqueness</span>
        <span className="slider-val">{value < 0.87 ? 'strict' : value > 0.94 ? 'loose' : 'balanced'}</span>
      </span>
      <input type="range" min={0.8} max={0.98} step={0.005} value={0.8 + 0.98 - value}
        onChange={(e) => onChange(0.8 + 0.98 - parseFloat(e.target.value))} />
    </label>
  )
}
