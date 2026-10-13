import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { Job, Meta, ModelInfo, Scores } from '../types'
import { label, useJob } from '../util'
import { AddModelDialog } from './AddModelDialog'
import { Logo } from './Brand'
import { JobProgress } from './JobProgress'

const mb = (bytes: number) =>
  bytes >= 1e9 ? `${(bytes / 1e9).toFixed(2)} GB` : bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} KB`

interface Props {
  meta: Meta
  /** Model list changed (added, hidden, renamed): the pickers elsewhere re-read it. */
  onMetaChange: () => void
}

/** Every model: what it makes, whether it's downloaded, its quality, speed and settings. */
export function ModelsView({ meta, onMetaChange }: Props) {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.models>> | null>(null)
  const [selected, setSelected] = useState<string | null>(() => location.hash.match(/[?&]model=([^&]+)/)?.[1] ?? null)
  const [query, setQuery] = useState('')
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => api.models().then((d) => {
    setData(d)
    setSelected((s) => (s && d.models.some((m) => m.id === decodeURIComponent(s)) ? decodeURIComponent(s) : d.models[0]?.id ?? null))
  }).catch((e) => setError(e.message)), [])
  useEffect(() => {
    void load()
  }, [load])
  // A link to one model (…#/models?model=x) selects it, also while already here.
  useEffect(() => {
    const onHash = () => {
      const m = location.hash.match(/[?&]model=([^&]+)/)
      if (m) setSelected(decodeURIComponent(m[1]))
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const changed = () => {
    void load()
    onMetaChange()
  }

  const needle = query.trim().toLowerCase()
  const shown = (data?.models ?? []).filter((m) =>
    !needle || [m.label, m.id, m.group, ...m.stems].some((x) => x?.toLowerCase().includes(needle)))
  const groups = [...new Set(shown.map((m) => m.group ?? 'Models'))]
  const model = data?.models.find((m) => m.id === selected)
  const total = (data?.models ?? []).filter((m) => !m.stages).reduce((n, m) => n + m.size, 0)

  return (
    <div className="models-page">
      <header className="topbar">
        <button className="link" onClick={() => (location.hash = '')}>← Projects</button>
        <b className="title">Models</b>
        <button className="link" onClick={() => (location.hash = '#/workflows')}>Workflows</button>
        <button className="link" onClick={() => (location.hash = '#/batch')}>Batch</button>
        <span className="muted">
          {data ? `${data.models.filter((m) => m.downloaded).length} of ${data.models.length} downloaded · ${mb(total)} on disk` : ''}
        </span>
        <div className="spacer" />
        {data && <button onClick={() => void api.reveal(data.folder)} title={data.folder}>Show models folder</button>}
        <button className="primary" onClick={() => setAdding(true)}>+ Add model</button>
        <Logo />
      </header>

      <div className="models-body">
        <aside className="models-list">
          <input type="search" placeholder="Search name, file or stem" value={query} onChange={(e) => setQuery(e.target.value)} />
          {groups.map((g) => (
            <div key={g}>
              <div className="field-label">{g}</div>
              {shown.filter((m) => (m.group ?? 'Models') === g).map((m) => (
                <button key={m.id} className={`model-row ${m.id === selected ? 'on' : ''} ${m.hidden ? 'hidden-model' : ''}`}
                  onClick={() => setSelected(m.id)}>
                  <span className="model-row-name">{m.label}</span>
                  <span className={`model-dl ${m.downloaded ? 'yes' : ''}`} title={m.downloaded ? 'Downloaded' : 'Downloads on first use'}>
                    {m.downloaded ? `✓ ${mb(m.size)}` : '↓'}
                  </span>
                  <span className="model-row-meta">{m.stems.map(label).join(' · ')}{m.hidden ? ' · hidden' : ''}</span>
                </button>
              ))}
            </div>
          ))}
          {!shown.length && <div className="empty">Nothing matches “{query}”.</div>}
        </aside>

        <main className="model-detail">
          {error && <div className="error">{error}</div>}
          {model && data && (
            <ModelDetail key={model.id} model={model} all={data.models} help={data.help} meta={meta}
              onChanged={changed} onSelect={setSelected} />
          )}
        </main>
      </div>

      {adding && data && (
        <AddModelDialog meta={meta} mdxDefaults={data.mdx_defaults} onClose={() => setAdding(false)}
          onAdded={(m) => {
            setAdding(false)
            setSelected(m.id)
            changed()
          }} />
      )}
    </div>
  )
}

function ModelDetail({ model: m, all, help, onChanged, onSelect }: {
  model: ModelInfo
  all: ModelInfo[]
  help: Record<string, string>
  meta: Meta
  onChanged: () => void
  onSelect: (id: string) => void
}) {
  const [values, setValues] = useState<Record<string, unknown>>(m.settings ?? {})
  const [hiddenNow, setHiddenNow] = useState(!!m.hidden) // ticks at once; the server catches up
  const [edit, setEdit] = useState({
    label: m.label, group: m.group ?? '', stems: m.stems.join(', '), note: m.note ?? '',
    best_for: (m.best_for ?? []).join(', '),
    aliases: Object.entries(m.aliases ?? {}).map(([k, v]) => `${k}=${v}`).join(', '),
  })
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const job = useJob(jobId, (j: Job) => {
    setJobId(null)
    if (j.status === 'error') setError(`Download failed: ${j.error}`)
    else onChanged()
  })
  const added = m.source !== 'built-in'
  const dirty = JSON.stringify(values) !== JSON.stringify(m.settings ?? {})

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setError(null)
    try {
      await fn()
      if (ok) setNotice(ok)
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    }
  }
  const list = (s: string) => s.split(',').map((x) => x.trim()).filter(Boolean)
  const saveSetup = () => act(() => api.patchModel(m.id, {
    label: edit.label, group: edit.group, stems: list(edit.stems), note: edit.note, best_for: list(edit.best_for),
    aliases: Object.fromEntries(list(edit.aliases).map((p) => p.split('=').map((x) => x.trim())).filter((p) => p.length === 2 && p[0] && p[1])),
  }), 'Saved')

  return (
    <div className="model-card">
      <div className="row model-head">
        <div>
          <h2>{m.label}</h2>
          <div className="muted small"><code>{m.id}</code>{m.catalog_name ? ` · ${m.catalog_name}` : ''}</div>
        </div>
        <div className="spacer" />
        <span className={`badge src-${m.source}`}>{m.source === 'files' ? 'your files' : m.source}</span>
        {m.arch && <span className="badge">{m.arch_label}</span>}
        {m.tool && <span className="badge">stem tool</span>}
      </div>
      {notice && <div className="notice">{notice}</div>}
      {error && <div className="error">{error}</div>}

      <section>
        <h3>What it makes</h3>
        <div className="chips">
          {m.stems.map((s) => <span key={s} className={`chip on ${(m.best_for ?? []).includes(s) ? 'best' : ''}`}>{label(s)}{(m.best_for ?? []).includes(s) ? ' ★' : ''}</span>)}
        </div>
        {m.note && <p className="tip">{m.note}</p>}
        {m.stages && (
          <p className="tip">
            Two models in a row:{' '}
            {m.stages.map((s, i) => (
              <span key={s}>{i > 0 && ' → '}<button className="link" onClick={() => onSelect(s)}>{all.find((x) => x.id === s)?.label ?? s}</button></span>
            ))}. Their settings and files are the two models’ own.
          </p>
        )}
        {m.aliases && Object.keys(m.aliases).length > 0 && (
          <p className="tip">Renames the model’s outputs: {Object.entries(m.aliases).map(([k, v]) => `${k} → ${v}`).join(', ')}</p>
        )}
        {!m.tool && !m.stages && (
          <label className="check" title="Hidden models stay usable in saved workflows but aren't offered in the pickers">
            <input type="checkbox" checked={hiddenNow} onChange={(e) => {
              const hide = e.target.checked
              setHiddenNow(hide)
              void act(() => api.patchModel(m.id, { hidden: hide }))
            }} />
            Hide from the model pickers
          </label>
        )}
      </section>

      {added && (
        <section>
          <h3>Setup</h3>
          <div className="model-form">
            <label>Name <input value={edit.label} onChange={(e) => setEdit({ ...edit, label: e.target.value })} /></label>
            <label>Group <input value={edit.group} onChange={(e) => setEdit({ ...edit, group: e.target.value })} /></label>
            <label title="What it outputs, as ChopChop names them (vocals, drums, bass, other, instrumental…)">
              Stems <input value={edit.stems} onChange={(e) => setEdit({ ...edit, stems: e.target.value })} />
            </label>
            <label title="Stems it's the best choice for: the stem picker suggests it">
              Best for <input value={edit.best_for} onChange={(e) => setEdit({ ...edit, best_for: e.target.value })} placeholder="e.g. vocals" />
            </label>
            <label title="When the model names a stem differently, e.g. other=instrumental">
              Renames <input value={edit.aliases} onChange={(e) => setEdit({ ...edit, aliases: e.target.value })} placeholder="model name=stem, …" />
            </label>
            <label>Note <input value={edit.note} onChange={(e) => setEdit({ ...edit, note: e.target.value })} placeholder="shown in the pickers" /></label>
          </div>
          <div className="row">
            <button className="primary" onClick={() => void saveSetup()}>Save setup</button>
            <div className="spacer" />
            <button onClick={() => {
              if (!window.confirm(`Remove “${m.label}” from ChopChop? Workflows that use it will stop working.`)) return
              const files = m.downloaded && window.confirm('Also delete its downloaded files?')
              void act(() => api.removeModel(m.id, files))
            }}>Remove model</button>
          </div>
        </section>
      )}

      {Object.keys(m.scores).length > 0 && (
        <section>
          <h3>Quality <span className="muted small">(audio-separator’s benchmark on MUSDB; higher is better)</span></h3>
          <ScoreTable scores={m.scores} />
        </section>
      )}

      <section>
        <h3>Files</h3>
        <table className="model-files">
          <tbody>
            {m.files.map((f) => (
              <tr key={f.name}>
                <td><code>{f.name}</code></td>
                <td className={f.present ? 'ok' : 'muted'}>{f.present ? mb(f.size ?? 0) : 'not downloaded'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="row">
          <span className="muted">{m.downloaded ? `${mb(m.size)} on disk` : m.source === 'files' ? 'Files missing: add the model again' : 'Downloads automatically on first use'}</span>
          <div className="spacer" />
          {!m.downloaded && m.source !== 'files' && (
            <button className="primary" disabled={jobId !== null}
              onClick={() => void act(async () => setJobId((await api.downloadModel(m.id)).id))}>Download now</button>
          )}
          {m.files.some((f) => f.present) && !m.stages && (
            <button disabled={jobId !== null} onClick={() => {
              if (window.confirm(`Delete ${m.label}'s files (${mb(m.size)})? ${m.source === 'files' ? 'They can’t be downloaded again.' : 'They download again when needed.'}`))
                void act(async () => {
                  const r = await api.deleteModelFiles(m.id)
                  setNotice(r.removed.length ? `Deleted ${r.removed.length} file${r.removed.length > 1 ? 's' : ''}` : 'Nothing to delete (files shared with other models)')
                })
            }}>Delete files</button>
          )}
        </div>
        {jobId && <JobProgress job={job} fallback="Starting download…" />}
      </section>

      <section>
        <h3>On this computer</h3>
        {m.perf ? (
          <dl className="model-facts">
            <dt>Runs</dt><dd>{m.perf.runs}</dd>
            {m.perf.realtime && <><dt>Speed</dt><dd>{m.perf.realtime}× realtime (a 3-min song in ~{Math.max(1, Math.round(180 / m.perf.realtime))} s)</dd></>}
            <dt>Last run</dt><dd>{m.perf.last_at} on {m.perf.last_device} · {m.perf.last_seconds} s</dd>
            {m.perf.fallbacks > 0 && <><dt>CPU fallbacks</dt><dd className="warn">{m.perf.fallbacks} (the GPU failed; see perf.log)</dd></>}
          </dl>
        ) : <p className="tip">Not run yet: speed shows here after the first separation.</p>}
        {m.gpu && (
          <p className="tip">GPU: batch {m.gpu.batch} · {m.gpu.fp16 ? 'half precision (fp16)' : 'full precision'} — tuned per machine by <code>chopchop benchmark</code>.</p>
        )}
      </section>

      {m.settings && m.defaults && (
        <section>
          <h3>Settings <span className="muted small">({m.arch_label})</span></h3>
          <div className="model-settings">
            {Object.entries(m.defaults).map(([k, def]) => (
              <Setting key={k} name={k} value={values[k]} def={def} arch={m.arch!} hint={help[k]}
                onChange={(v) => setValues({ ...values, [k]: v })} />
            ))}
          </div>
          <div className="row">
            <button className="primary" disabled={!dirty} onClick={() => void act(() => api.patchModel(m.id, { settings: values }), 'Settings saved')}>
              Save settings
            </button>
            <button disabled={!Object.keys(m.changed).length && !dirty}
              onClick={() => {
                setValues(m.defaults!)
                void act(() => api.patchModel(m.id, { settings: m.defaults! }), 'Back to the defaults')
              }}>Reset to defaults</button>
            {Object.keys(m.changed).length > 0 && <span className="muted small">changed: {Object.keys(m.changed).join(', ')}</span>}
          </div>
        </section>
      )}
    </div>
  )
}

function Setting({ name, value, def, arch, hint, onChange }: {
  name: string
  value: unknown
  def: unknown
  arch: string
  hint?: string
  onChange: (v: unknown) => void
}) {
  const text = name.replace(/_/g, ' ')
  if (typeof def === 'boolean') {
    return (
      <label className="check" title={hint}>
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} /> {text}
      </label>
    )
  }
  const nullable = arch === 'mdxc' && name === 'overlap'
  const word = arch === 'demucs' && name === 'segment_size'
  return (
    <label className="field" title={hint}>
      <span>{text}</span>
      <input type={word ? 'text' : 'number'} step="any" value={value === null || value === undefined ? '' : String(value)}
        placeholder={nullable ? "model's own" : String(def)}
        onChange={(e) => {
          const raw = e.target.value.trim()
          if (raw === '') onChange(nullable ? null : def)
          else if (word && !/^\d+$/.test(raw)) onChange('Default')
          else onChange(Number(raw))
        }} />
      <small className="muted">{hint}</small>
    </label>
  )
}

export function ScoreTable({ scores }: { scores: Scores }) {
  const metrics = useMemo(() => [...new Set(Object.values(scores).flatMap((s) => Object.keys(s)))], [scores])
  return (
    <table className="model-scores">
      <thead><tr><th>Stem</th>{metrics.map((k) => <th key={k} title={METRIC[k]}>{k}</th>)}</tr></thead>
      <tbody>
        {Object.entries(scores).map(([stem, s]) => (
          <tr key={stem}><td>{label(stem)}</td>{metrics.map((k) => <td key={k}>{s[k] !== undefined ? `${s[k].toFixed(1)} dB` : '—'}</td>)}</tr>
        ))}
      </tbody>
    </table>
  )
}

const METRIC: Record<string, string> = {
  SDR: 'overall quality (the usual headline number)',
  SIR: 'how little of the other stems bleeds in',
  SAR: 'how few artifacts it adds',
  ISR: 'how much of the stem itself is kept',
}
