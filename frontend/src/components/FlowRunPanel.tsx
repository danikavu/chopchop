import { useEffect, useState } from 'react'
import { api } from '../api'
import { NODE_TYPES } from '../flowCatalog'
import type { Flow, Job } from '../types'
import { useJob } from '../util'
import { JobProgress } from './JobProgress'

interface Props {
  pid: string
  onDone: () => void
  onClose: () => void
}

/** Run a node workflow on this project: its mix is the Input track. */
export function FlowRunPanel({ pid, onDone, onClose }: Props) {
  const [flows, setFlows] = useState<Flow[]>([])
  const [name, setName] = useState('')
  const [jobId, setJobId] = useState<string | null>(null)
  const [result, setResult] = useState<{ output: string; files: number; samples: number; warnings: string[] } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.flows().then((r) => {
      const all = [...r.saved, ...r.presets]
      setFlows(all)
      setName(all[0]?.name ?? '')
    }).catch((e) => setError(e.message))
  }, [])

  const job = useJob(jobId, (j: Job) => {
    setJobId(null)
    if (j.status === 'error') setError(`Workflow failed: ${j.error}`)
    else {
      setResult(j.result as typeof result)
      onDone()
    }
  })

  const flow = flows.find((f) => f.name === name)
  const run = async () => {
    if (!flow) return
    setError(null)
    setResult(null)
    try {
      setJobId((await api.runFlow(pid, flow)).id)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="banner flow-run-banner">
      <div className="separate-intro">
        <strong>Run a workflow</strong>
        <div className="muted">On this project: its mix is the Input track. New stems, slices and samples stay here.</div>
      </div>
      <div className="stem-picker">
        <div className="row">
          <select value={name} onChange={(e) => setName(e.target.value)} disabled={jobId !== null}>
            {flows.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}
          </select>
          <button className="link" onClick={() => (location.hash = `#/workflows?project=${pid}`)}>Edit workflows →</button>
          <div className="spacer" />
          <button onClick={onClose} disabled={jobId !== null}>Close</button>
          <button className="primary" onClick={() => void run()} disabled={!flow || jobId !== null}>
            {jobId ? 'Running…' : 'Run'}
          </button>
        </div>
        {flow && (
          <div className="flow-steps">
            {flow.description && <span className="muted small">{flow.description} </span>}
            {flow.nodes.filter((n) => n.type !== 'input').map((n) => <span key={n.id} className="chip">{NODE_TYPES[n.type].label}</span>)}
          </div>
        )}
        {jobId && <JobProgress job={job} fallback="Starting…" />}
        {result && (
          <div className="result">
            {result.files} files{result.samples ? ` · ${result.samples} bits` : ''} saved to{' '}
            <span className="path" title={result.output}>{`\u200E${result.output}\u200E`}</span>{' '}
            <button className="link" onClick={() => void api.reveal(result.output)}>Show folder</button>
            {result.warnings?.length > 0 && <div className="warn small">{result.warnings.join(' · ')}</div>}
          </div>
        )}
        {error && <div className="error">{error}</div>}
      </div>
    </div>
  )
}
