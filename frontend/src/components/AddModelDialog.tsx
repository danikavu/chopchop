import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { AddModel, CatalogModel, Meta, ModelInfo } from '../types'
import { label } from '../util'
import { FolderPicker } from './FolderPicker'
import { ScoreTable } from './ModelsView'

const ARCH: Record<string, string> = { mdx: 'MDX-Net', mdxc: 'MDX23C / Roformer', vr: 'VR', demucs: 'Demucs' }
const list = (s: string) => s.split(',').map((x) => x.trim()).filter(Boolean)
const best = (c: CatalogModel) => Math.max(0, ...Object.values(c.scores).map((s) => s.SDR ?? 0))

/** "Model: MDX23C-InstVoc HQ" -> "MDX23C-InstVoc HQ" */
const shortName = (name: string) => name.replace(/^[^:]*:\s*/, '')

interface Props {
  meta: Meta
  mdxDefaults: Record<string, unknown>
  onClose: () => void
  onAdded: (m: ModelInfo) => void
}

export function AddModelDialog({ mdxDefaults, onClose, onAdded }: Props) {
  const [tab, setTab] = useState<'catalog' | 'files'>('catalog')
  const [catalog, setCatalog] = useState<CatalogModel[] | null>(null)
  const [query, setQuery] = useState('')
  const [arch, setArch] = useState<string | null>(null)
  const [pick, setPick] = useState<CatalogModel | null>(null)
  const [form, setForm] = useState({ label: '', group: 'Added', stems: '', note: '', best_for: '', aliases: '' })
  // Your files
  const [fileArch, setFileArch] = useState<'mdxc' | 'mdx'>('mdxc')
  const [modelPath, setModelPath] = useState('')
  const [configPath, setConfigPath] = useState('')
  const [mdx, setMdx] = useState<Record<string, unknown>>(mdxDefaults)
  const [browse, setBrowse] = useState<'model' | 'config' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const loadCatalog = (refresh = false) => api.modelCatalog(refresh).then(setCatalog).catch((e) => setError(e.message))
  useEffect(() => {
    void loadCatalog()
  }, [])

  const needle = query.trim().toLowerCase()
  const rows = useMemo(() => (catalog ?? [])
    .filter((c) => (!arch || c.arch === arch) && (!needle || `${c.name} ${c.filename} ${c.stems.join(' ')}`.toLowerCase().includes(needle)))
    .sort((a, b) => Number(a.added) - Number(b.added) || best(b) - best(a)), [catalog, arch, needle])

  const choose = (c: CatalogModel) => {
    setPick(c)
    setForm({ ...form, label: shortName(c.name), stems: c.stems.join(', '),
      best_for: Object.entries(c.scores).filter(([, s]) => (s.SDR ?? 0) >= 10).map(([k]) => k).join(', ') })
  }

  // Pre-fill from the files: the YAML lists the stems; the file name is a fair name.
  const inspect = async (path: string, kind: 'model' | 'config') => {
    if (!path.trim()) return
    try {
      const info = await api.inspectModelFile(path)
      if (kind === 'model') {
        if (!form.label) setForm((f) => ({ ...f, label: info.name.replace(/\.[^.]+$/, '') }))
        if (info.kind === 'mdx' || info.kind === 'mdxc') setFileArch(info.kind)
      } else if (info.stems?.length) {
        setForm((f) => ({ ...f, stems: info.stems!.join(', ') }))
      }
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const submit = async () => {
    setBusy(true)
    setError(null)
    const common = {
      label: form.label, group: form.group, note: form.note, best_for: list(form.best_for),
      stems: list(form.stems).length ? list(form.stems) : undefined,
      aliases: Object.fromEntries(list(form.aliases).map((p) => p.split('=').map((x) => x.trim())).filter((p) => p.length === 2)),
    }
    const body: AddModel = tab === 'catalog'
      ? { source: 'catalog', filename: pick?.filename, ...common }
      : { source: 'files', arch: fileArch, model_path: modelPath, config_path: fileArch === 'mdxc' ? configPath : undefined,
          mdx_params: fileArch === 'mdx' ? mdx : undefined, ...common }
    try {
      onAdded(await api.addModel(body))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const setupForm = (
    <div className="model-form">
      <label>Name <input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} /></label>
      <label>Group <input value={form.group} onChange={(e) => setForm({ ...form, group: e.target.value })} /></label>
      <label title="What it outputs, as ChopChop names them">
        Stems <input value={form.stems} onChange={(e) => setForm({ ...form, stems: e.target.value })} placeholder="vocals, instrumental" />
      </label>
      <label title="Stems it's the best choice for: the stem picker suggests it">
        Best for <input value={form.best_for} onChange={(e) => setForm({ ...form, best_for: e.target.value })} placeholder="e.g. vocals" />
      </label>
      <label title="When the model names a stem differently, e.g. other=instrumental">
        Renames <input value={form.aliases} onChange={(e) => setForm({ ...form, aliases: e.target.value })} placeholder="other=instrumental" />
      </label>
      <label>Note <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="shown in the pickers" /></label>
    </div>
  )

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal add-model" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>Add a model</h3>
          <div className="seg">
            <button className={tab === 'catalog' ? 'on' : ''} onClick={() => setTab('catalog')}>From the catalog</button>
            <button className={tab === 'files' ? 'on' : ''} onClick={() => setTab('files')}>Your own files</button>
          </div>
        </div>

        {tab === 'catalog' ? (
          <>
            <p className="tip">
              Every UVR model audio-separator can download ({catalog?.length ?? '…'}). It downloads the first time it runs.
            </p>
            <div className="row">
              <input className="grow" type="search" placeholder="Search name, file or stem" value={query} onChange={(e) => setQuery(e.target.value)} />
              <button onClick={() => void loadCatalog(true)} title="Fetch the latest list">Refresh</button>
            </div>
            <div className="chips">
              <button className={`chip ${arch === null ? 'on' : ''}`} onClick={() => setArch(null)}>all</button>
              {Object.entries(ARCH).map(([k, v]) => (
                <button key={k} className={`chip ${arch === k ? 'on' : ''}`} onClick={() => setArch(arch === k ? null : k)}>
                  {v} <span className="count">{(catalog ?? []).filter((c) => c.arch === k).length}</span>
                </button>
              ))}
            </div>
            <div className="catalog-list">
              {rows.map((c) => (
                <button key={c.filename} className={`catalog-row ${pick?.filename === c.filename ? 'on' : ''}`}
                  disabled={c.added} onClick={() => choose(c)}>
                  <span className="catalog-name">{shortName(c.name)}</span>
                  <span className="muted small">{ARCH[c.arch]} · {c.stems.map(label).join(' · ') || '?'}</span>
                  <span className="catalog-badges">
                    {best(c) > 0 && <span className="badge" title="best SDR">{best(c).toFixed(1)} dB</span>}
                    {c.downloaded && <span className="badge ok">downloaded</span>}
                    {c.added && <span className="badge">in ChopChop</span>}
                  </span>
                </button>
              ))}
              {catalog && !rows.length && <div className="empty">No model matches.</div>}
            </div>
            {pick && (
              <div className="catalog-pick">
                <div className="muted small"><code>{pick.filename}</code> · files: {pick.files.join(', ')}</div>
                {Object.keys(pick.scores).length > 0 && <ScoreTable scores={pick.scores} />}
                {setupForm}
              </div>
            )}
          </>
        ) : (
          <>
            <p className="tip">
              A model audio-separator doesn’t list, e.g. a new Roformer from Hugging Face. The files are copied into
              ChopChop’s models folder.
            </p>
            <div className="seg">
              <button className={fileArch === 'mdxc' ? 'on' : ''} onClick={() => setFileArch('mdxc')}>MDX23C / Roformer (.ckpt + .yaml)</button>
              <button className={fileArch === 'mdx' ? 'on' : ''} onClick={() => setFileArch('mdx')}>MDX-Net (.onnx)</button>
            </div>
            <label className="field-label">Model file</label>
            <div className="row">
              <input className="grow path-input" value={modelPath} onChange={(e) => setModelPath(e.target.value)}
                onBlur={() => void inspect(modelPath, 'model')} placeholder={fileArch === 'mdxc' ? '/path/to/model.ckpt' : '/path/to/model.onnx'} />
              <button onClick={() => setBrowse('model')}>Browse…</button>
            </div>
            {fileArch === 'mdxc' ? (
              <>
                <label className="field-label">Config (.yaml) — the stems are read from it</label>
                <div className="row">
                  <input className="grow path-input" value={configPath} onChange={(e) => setConfigPath(e.target.value)}
                    onBlur={() => void inspect(configPath, 'config')} placeholder="/path/to/config.yaml" />
                  <button onClick={() => setBrowse('config')}>Browse…</button>
                </div>
              </>
            ) : (
              <div className="model-form">
                <p className="tip">MDX-Net models need the parameters they were trained with (from the model’s page).</p>
                {[
                  ['primary_stem', 'Primary stem', 'text'], ['compensate', 'Compensate', 'number'],
                  ['mdx_dim_f_set', 'dim_f', 'number'], ['mdx_dim_t_set', 'dim_t', 'number'], ['mdx_n_fft_scale_set', 'n_fft', 'number'],
                ].map(([k, text, type]) => (
                  <label key={k}>{text}
                    <input type={type} step="any" value={String(mdx[k] ?? '')}
                      onChange={(e) => setMdx({ ...mdx, [k]: type === 'number' ? Number(e.target.value) : e.target.value })} />
                  </label>
                ))}
              </div>
            )}
            {setupForm}
          </>
        )}

        {error && <div className="error">{error}</div>}
        <div className="row">
          <div className="spacer" />
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy || !form.label.trim() || (tab === 'catalog' ? !pick : !modelPath.trim())}
            onClick={() => void submit()}>
            {busy ? 'Adding…' : 'Add model'}
          </button>
        </div>

        {browse && (
          <FolderPicker title={browse === 'model' ? 'Choose the model file' : 'Choose its .yaml config'}
            start={(browse === 'model' ? modelPath : configPath).replace(/[^/\\]*$/, '') || undefined}
            files={browse === 'model' ? (fileArch === 'mdxc' ? ['.ckpt', '.pth', '.pt', '.safetensors'] : ['.onnx']) : ['.yaml', '.yml']}
            onClose={() => setBrowse(null)}
            onPick={(p) => {
              if (browse === 'model') {
                setModelPath(p)
                void inspect(p, 'model')
              } else {
                setConfigPath(p)
                void inspect(p, 'config')
              }
              setBrowse(null)
            }} />
        )}
      </div>
    </div>
  )
}
