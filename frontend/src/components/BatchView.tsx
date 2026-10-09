import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { BatchJob, FolderListing, Meta, Workflow } from '../types'
import { Logo } from './Brand'
import { FolderPicker } from './FolderPicker'
import { JobProgress } from './JobProgress'
import { WorkflowEditor } from './WorkflowEditor'

interface Props {
  meta: Meta
  onOpenProject: (pid: string) => void
}

const clone = (wf: Workflow): Workflow => JSON.parse(JSON.stringify(wf))

/** Run a workflow over a folder of tracks (e.g. an album → a drum kit per song). */
export function BatchView({ meta, onOpenProject }: Props) {
  const [presets, setPresets] = useState<Workflow[]>([])
  const [saved, setSaved] = useState<Workflow[]>([])
  const [defaultOut, setDefaultOut] = useState('')
  const [wf, setWf] = useState<Workflow | null>(null)
  const [base, setBase] = useState('') // name of the preset / saved workflow it started from
  const [input, setInput] = useState('')
  const [listing, setListing] = useState<FolderListing | null>(null)
  const [excluded, setExcluded] = useState<string[]>([])
  const [recursive, setRecursive] = useState(false)
  const [output, setOutput] = useState('')
  const [picker, setPicker] = useState<'input' | 'output' | null>(null)
  const [batches, setBatches] = useState<BatchJob[]>([])
  const [error, setError] = useState<string | null>(null)
  const [saveName, setSaveName] = useState('')

  useEffect(() => {
    api.workflows().then((r) => {
      setPresets(r.presets)
      setSaved(r.saved)
      setDefaultOut(r.default_output)
      setWf(clone(r.presets[0]))
      setBase(r.presets[0].name)
    }).catch((e) => setError(e.message))
  }, [])

  const refresh = useCallback(() => api.batches().then(setBatches).catch(() => undefined), [])
  useEffect(() => {
    void refresh()
    const running = batches.some((b) => b.status === 'running' || b.status === 'pending')
    const t = window.setInterval(refresh, running ? 800 : 4000)
    return () => window.clearInterval(t)
  }, [refresh, batches.some((b) => b.status === 'running' || b.status === 'pending')]) // eslint-disable-line react-hooks/exhaustive-deps

  const loadFolder = async (path: string) => {
    setInput(path)
    setExcluded([])
    setError(null)
    try {
      setListing(await api.listFolder(path))
    } catch (e) {
      setListing(null)
      setError((e as Error).message)
    }
  }

  const pickWorkflow = (name: string) => {
    const found = [...saved, ...presets].find((w) => w.name === name)
    if (found) {
      setWf(clone(found))
      setBase(found.name)
    }
  }

  const start = async () => {
    if (!wf) return
    setError(null)
    try {
      const files = listing && !recursive ? listing.files.filter((f) => !excluded.includes(f)) : undefined
      await api.startBatch({ input_dir: input, files, recursive, output_dir: output || undefined, workflow: wf })
      void refresh()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const saveWorkflow = async () => {
    if (!wf || !saveName.trim()) return
    try {
      const r = await api.saveWorkflow({ ...wf, name: saveName.trim() })
      setSaved(r.saved)
      setBase(saveName.trim())
      setWf({ ...wf, name: saveName.trim() })
      setSaveName('')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const count = listing ? listing.files.length - excluded.length : 0
  const isSaved = saved.some((w) => w.name === base)

  return (
    <div className="batch">
      <header className="topbar">
        <button className="link" onClick={() => (location.hash = '')}>← Projects</button>
        <h1 className="title">Batch</h1>
        <span className="muted">Run a workflow over a whole folder (e.g. an album → a drum kit per song)</span>
        <div className="spacer" />
        <Logo />
      </header>

      <div className="batch-body">
        <div className="batch-setup">
          <section className="card">
            <h2>1 · Input folder</h2>
            <div className="row">
              <input className="grow path-input" placeholder="/path/to/album" value={input}
                onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && loadFolder(input)} />
              <button onClick={() => setPicker('input')}>Browse…</button>
            </div>
            <label className="check">
              <input type="checkbox" checked={recursive} onChange={(e) => setRecursive(e.target.checked)} /> Include subfolders
            </label>
            {listing && (
              <div className="song-list">
                {listing.files.length === 0 && <div className="muted">No audio files in this folder.</div>}
                {listing.files.map((f) => (
                  <label key={f} className="check">
                    <input type="checkbox" checked={!excluded.includes(f)}
                      onChange={() => setExcluded((x) => (x.includes(f) ? x.filter((y) => y !== f) : [...x, f]))} />
                    {f}
                  </label>
                ))}
                {recursive && <div className="muted small">Subfolders are included when the batch starts.</div>}
              </div>
            )}
          </section>

          <section className="card">
            <h2>2 · Workflow</h2>
            <div className="row">
              <select value={base} onChange={(e) => pickWorkflow(e.target.value)}>
                <optgroup label="Presets">
                  {presets.map((w) => <option key={w.name} value={w.name}>{w.name}</option>)}
                </optgroup>
                {saved.length > 0 && (
                  <optgroup label="Saved">
                    {saved.map((w) => <option key={w.name} value={w.name}>{w.name}</option>)}
                  </optgroup>
                )}
              </select>
              {isSaved && (
                <button className="icon-btn" title="Delete this saved workflow" onClick={async () => {
                  const r = await api.deleteWorkflow(base)
                  setSaved(r.saved)
                  pickWorkflow(presets[0].name)
                }}>×</button>
              )}
            </div>
            {wf?.description && <p className="tip">{wf.description}</p>}
            {wf && <WorkflowEditor meta={meta} wf={wf} onChange={setWf} />}
            <div className="row">
              <input placeholder="Save as… (name)" value={saveName} onChange={(e) => setSaveName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && saveWorkflow()} />
              <button onClick={saveWorkflow} disabled={!saveName.trim()}>Save workflow</button>
            </div>
          </section>

          <section className="card">
            <h2>3 · Output folder</h2>
            <div className="row">
              <input className="grow path-input" placeholder={defaultOut} value={output} onChange={(e) => setOutput(e.target.value)} />
              <button onClick={() => setPicker('output')}>Browse…</button>
            </div>
            <p className="tip">
              Each run gets its own folder inside, one subfolder per song (<code>stems/</code>, <code>kit/</code>,{' '}
              <code>samples/</code>) and a <code>chopper-batch.json</code> summary.
              {!output && ' Empty = ChopChop’s data folder.'}
            </p>
          </section>

          {!meta.separation.available && (wf?.separate.enabled || (wf?.tools.length ?? 0) > 0) && (
            <div className="warn">Separation unavailable: {meta.separation.reason}</div>
          )}
          {error && <div className="error">{error}</div>}
          <button className="primary wide run-batch" onClick={start} disabled={!wf || !input || (!!listing && !recursive && count === 0)}>
            Run “{wf?.name}” on {listing && !recursive ? `${count} track${count === 1 ? '' : 's'}` : 'the folder'}
          </button>
        </div>

        <div className="batch-runs">
          <h2>Runs</h2>
          {batches.length === 0 && <div className="empty">No batches yet.</div>}
          {batches.map((b) => <BatchCard key={b.id} b={b} onOpenProject={onOpenProject} />)}
        </div>
      </div>

      {picker && (
        <FolderPicker
          title={picker === 'input' ? 'Choose the folder with your tracks' : 'Choose where to save the results'}
          start={picker === 'input' ? input : output || defaultOut}
          onClose={() => setPicker(null)}
          onPick={(path) => {
            if (picker === 'input') void loadFolder(path)
            else setOutput(path)
            setPicker(null)
          }}
        />
      )}
    </div>
  )
}

function BatchCard({ b, onOpenProject }: { b: BatchJob; onOpenProject: (pid: string) => void }) {
  const running = b.status === 'running' || b.status === 'pending'
  const songs = b.detail?.songs ?? []
  return (
    <div className={`card batch-card ${b.status}`}>
      <div className="row">
        <b>{b.detail?.workflow}</b>
        <span className={`status ${b.status}`}>{b.status}</span>
        <div className="spacer" />
        {running && <button onClick={() => api.cancelJob(b.id)}>Cancel</button>}
        {b.detail?.output && <button onClick={() => api.reveal(b.detail!.output)} title={b.detail.output}>Show folder</button>}
      </div>
      {running && <JobProgress job={b} />}
      {!running && b.error && <div className="error">{b.error}</div>}
      <div className="path small muted" title={b.detail?.output}>{b.detail?.output}</div>
      <table className="songs">
        <tbody>
          {songs.map((s) => (
            <tr key={s.file} className={s.status}>
              <td className="song-name" title={s.file}>{s.song}</td>
              <td><span className={`status ${s.status}`}>{s.status}</span></td>
              <td className="song-msg">
                {s.status === 'done'
                  ? [s.bpm && `${s.bpm} BPM`, s.key, s.kit && `${s.kit} pads`, s.samples && `${s.samples} bits`, s.files != null && `${s.files} files`]
                      .filter(Boolean).join(' · ')
                  : s.message}
                {s.warnings?.length ? <div className="warn small">{s.warnings.join(' · ')}</div> : null}
              </td>
              <td className="song-actions">
                {s.project && <button className="link" onClick={() => onOpenProject(s.project!)}>open</button>}
                {s.output && <button className="link" onClick={() => api.reveal(s.output!)}>folder</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
