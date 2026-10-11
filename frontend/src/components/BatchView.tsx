import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { BatchJob, FolderListing, Meta, Workflow } from '../types'
import { Logo } from './Brand'
import { useExportDir } from './ExportFolder'
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
  // The saved export folder (shared with project exports) is the starting point.
  const [exportDir, saveExportDir] = useExportDir()
  const [touched, setTouched] = useState(false)
  useEffect(() => {
    if (!touched && exportDir !== undefined) setOutput(exportDir ?? '')
  }, [exportDir, touched])
  const [picker, setPicker] = useState<'input' | 'output' | null>(null)
  const [batches, setBatches] = useState<BatchJob[]>([])
  const [error, setError] = useState<string | null>(null)
  const [saveName, setSaveName] = useState('')

  useEffect(() => {
    api.workflows().then((r) => {
      setPresets(r.presets)
      setSaved(r.saved)
      api.settings().then((s) => setDefaultOut(s.default_batch_output)).catch(() => setDefaultOut(r.default_output))
      setWf(clone(r.presets[0]))
      setBase(r.presets[0].name)
    }).catch((e) => setError(e.message))
  }, [])

  // Earlier runs are also read from disk: in the input folder (e.g. a past run's folder)
  // and the output folder, as well as the saved export / default batch folders.
  const scan = [listing?.path ?? '', (output || defaultOut).trim()]
  const scanKey = scan.join('\n')
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const refresh = useCallback(() => api.batches(scan).then(setBatches).catch(() => undefined), [scanKey])
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
      if (output.trim() && output.trim() !== exportDir) await saveExportDir(output.trim())
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

  // Running batches and the latest run stay in view; older runs sit behind a dropdown.
  const isRunning = (b: BatchJob) => b.status === 'running' || b.status === 'pending'
  const latest = batches[0]
  const current = batches.filter((b) => isRunning(b) || b === latest)
  const previous = batches.filter((b) => !current.includes(b))
  const finished = batches.filter((b) => !isRunning(b))
  const [showPrevious, setShowPrevious] = useState(false)

  const remove = async (b: BatchJob) => {
    const where = b.detail?.output ? `\n\nIts files stay in:\n${b.detail.output}` : ''
    if (!window.confirm(`Remove this run (“${b.detail?.workflow}”) from the list?${where}`)) return
    try {
      await api.removeBatch(b.id, scan)
      void refresh()
    } catch (e) {
      setError((e as Error).message)
    }
  }
  const clearAll = async () => {
    if (!window.confirm(`Remove all ${finished.length} finished runs from the list? Their files stay on disk.`)) return
    try {
      await api.clearBatches(scan)
      void refresh()
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
                {listing.files.length > 1 && (
                  <label className="check song-list-all">
                    <input type="checkbox" checked={excluded.length === 0}
                      ref={(el) => {
                        if (el) el.indeterminate = excluded.length > 0 && count > 0
                      }}
                      onChange={() => setExcluded(excluded.length === 0 ? [...listing.files] : [])} />
                    {count === listing.files.length ? 'All tracks' : `${count} of ${listing.files.length} tracks`}
                    <span className="spacer" />
                    {count > 0 && <button className="link" onClick={(e) => {
                      e.preventDefault()
                      setExcluded([...listing.files])
                    }}>Deselect all</button>}
                  </label>
                )}
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
              <input className="grow path-input" placeholder={defaultOut} value={output}
                onChange={(e) => {
                  setTouched(true)
                  setOutput(e.target.value)
                }} />
              <button onClick={() => setPicker('output')}>Browse…</button>
            </div>
            <p className="tip">
              Each run gets its own folder inside, one subfolder per song (<code>stems/</code>, <code>kit/</code>,{' '}
              <code>samples/</code>) and a <code>chopper-batch.json</code> summary.
              {' '}The folder is remembered and also used for slice / kit exports from a project.
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
          <div className="row runs-head">
            <h2>Runs</h2>
            <div className="spacer" />
            {finished.length > 0 && (
              <button onClick={clearAll} title="Remove every finished run from this list (their files stay on disk)">
                Clear all
              </button>
            )}
          </div>
          {batches.length === 0 && <div className="empty">No batches yet.</div>}
          {current.map((b) => <BatchCard key={b.id} b={b} onOpenProject={onOpenProject} onRemove={remove} />)}
          {previous.length > 0 && (
            <>
              <button className={`runs-toggle ${showPrevious ? 'open' : ''}`} onClick={() => setShowPrevious((v) => !v)}>
                {showPrevious ? '▾' : '▸'} Previous runs ({previous.length})
              </button>
              {showPrevious && previous.map((b) => <BatchCard key={b.id} b={b} onOpenProject={onOpenProject} onRemove={remove} />)}
            </>
          )}
        </div>
      </div>

      {picker && (
        <FolderPicker
          title={picker === 'input' ? 'Choose the folder with your tracks' : 'Choose where to save the results'}
          start={picker === 'input' ? input : output || defaultOut}
          onClose={() => setPicker(null)}
          onPick={(path) => {
            if (picker === 'input') void loadFolder(path)
            else {
              setTouched(true)
              setOutput(path)
              void saveExportDir(path).catch((e) => setError(e.message))
            }
            setPicker(null)
          }}
        />
      )}
    </div>
  )
}

function BatchCard({ b, onOpenProject, onRemove }: {
  b: BatchJob
  onOpenProject: (pid: string) => void
  onRemove: (b: BatchJob) => void
}) {
  const running = b.status === 'running' || b.status === 'pending'
  const songs = b.detail?.songs ?? []
  return (
    <div className={`card batch-card ${b.status}`}>
      <div className="row">
        <b>{b.detail?.workflow}</b>
        <span className={`status ${b.status}`}>{b.status}</span>
        {b.detail?.from_disk && (
          <span className="muted small" title="Read from this run's chopper-batch.json">
            {new Date(b.created_at * 1000).toLocaleString()}
          </span>
        )}
        <div className="spacer" />
        {running && <button onClick={() => api.cancelJob(b.id)}>Cancel</button>}
        {b.detail?.output && <button onClick={() => api.reveal(b.detail!.output)} title={b.detail.output}>Show folder</button>}
        {!running && (
          <button onClick={() => onRemove(b)} title="Remove this run from the list (its files stay on disk)">Remove</button>
        )}
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
