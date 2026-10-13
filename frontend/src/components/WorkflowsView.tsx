import {
  addEdge, Background, Controls, MiniMap, ReactFlow, ReactFlowProvider, useEdgesState, useNodesState, useReactFlow,
  useUpdateNodeInternals, type Connection, type Edge, type Node,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { NODE_TYPES, outputsOf, PALETTE } from '../flowCatalog'
import type { Flow, FlowNodeType, Job, Meta, ProjectSummary } from '../types'
import { useJob } from '../util'
import { Logo } from './Brand'
import { type CardData, FlowCard, FlowInspector } from './FlowInspector'
import { JobProgress } from './JobProgress'

const nodeTypes = { card: FlowCard }
const newId = () => Math.random().toString(16).slice(2, 10)
const BLANK: Flow = {
  name: 'My workflow', description: '', keep_projects: true, edges: [],
  nodes: [{ id: 'in', type: 'input', params: {}, x: 0, y: 120 }],
}

type RFNode = Node<CardData>

function toCanvas(flow: Flow, meta: Meta): { nodes: RFNode[]; edges: Edge[] } {
  return {
    nodes: flow.nodes.map((n) => ({
      id: n.id, type: 'card', position: { x: n.x, y: n.y }, deletable: n.type !== 'input',
      data: { type: n.type, params: { ...NODE_TYPES[n.type].defaults, ...n.params }, meta },
    })),
    edges: flow.edges.map((e) => edge(e.source, e.port, e.target)),
  }
}

const edge = (source: string, port: string, target: string): Edge => ({
  id: `${source}:${port}->${target}`, source, sourceHandle: port, target, targetHandle: 'in',
})

/** Workflows page: build a graph of steps, save it, run it on a project or (via Batch) a folder. */
export function WorkflowsView(props: { meta: Meta; onOpenProject: (pid: string) => void }) {
  return (
    <ReactFlowProvider>
      <Workflows {...props} />
    </ReactFlowProvider>
  )
}

function Workflows({ meta, onOpenProject }: { meta: Meta; onOpenProject: (pid: string) => void }) {
  const [presets, setPresets] = useState<Flow[]>([])
  const [saved, setSaved] = useState<Flow[]>([])
  const [name, setName] = useState(BLANK.name)
  const [description, setDescription] = useState('')
  const [keepProjects, setKeepProjects] = useState(true)
  const [nodes, setNodes, onNodesChange] = useNodesState<RFNode>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [target, setTarget] = useState('')
  const [jobId, setJobId] = useState<string | null>(null)
  const [result, setResult] = useState<{ output: string; files: number; samples: number; warnings: string[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const rf = useReactFlow()
  const updateInternals = useUpdateNodeInternals()
  const canvas = useRef<HTMLDivElement>(null)

  const load = useCallback((flow: Flow) => {
    const c = toCanvas(flow, meta)
    setNodes(c.nodes)
    setEdges(c.edges)
    setName(flow.name)
    setDescription(flow.description)
    setKeepProjects(flow.keep_projects ?? true)
    setSelected(null)
    window.setTimeout(() => rf.fitView({ padding: 0.08, maxZoom: 1 }), 50)
  }, [meta, rf, setEdges, setNodes])

  useEffect(() => {
    api.flows().then((r) => {
      setPresets(r.presets)
      setSaved(r.saved)
      load(r.saved[0] ?? r.presets[0] ?? BLANK)
    }).catch((e) => setError(e.message))
    api.listProjects().then((ps) => {
      setProjects(ps)
      const m = location.hash.match(/[?&]project=([a-f0-9]+)/)
      setTarget(m?.[1] ?? ps[0]?.id ?? '')
    }).catch(() => undefined)
  }, [load])

  const current = (): Flow => ({
    kind: 'flow', name: name.trim() || 'My workflow', description, keep_projects: keepProjects,
    nodes: rf.getNodes().map((n) => {
      const d = n.data as CardData
      return { id: n.id, type: d.type, params: d.params, x: Math.round(n.position.x), y: Math.round(n.position.y) }
    }),
    edges: rf.getEdges().map((e) => ({ source: e.source, port: e.sourceHandle ?? '', target: e.target })),
  })

  // Validate as you edit (the server knows the models and the rules).
  const shape = JSON.stringify([nodes.map((n) => [n.id, (n.data as CardData).params]), edges.map((e) => e.id)])
  useEffect(() => {
    const t = window.setTimeout(() => {
      if (!rf.getNodes().length) return
      api.checkFlow(current()).then(() => setProblem(null)).catch((e) => setProblem(e.message))
    }, 300)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shape])

  // ---------- editing ----------

  const kindOut = (nodeId: string, port: string) => {
    const n = rf.getNode(nodeId)
    if (!n) return null
    const d = n.data as CardData
    return outputsOf(d.type, d.params, meta).find(([p]) => p === port)?.[1] ?? null
  }
  const reaches = (from: string, to: string): boolean => {
    const seen = new Set<string>()
    const walk = (id: string): boolean => {
      if (id === to) return true
      if (seen.has(id)) return false
      seen.add(id)
      return rf.getEdges().some((e) => e.source === id && walk(e.target))
    }
    return walk(from)
  }
  const isValidConnection = (c: Connection | Edge) => {
    if (!c.source || !c.target || c.source === c.target) return false
    const kind = kindOut(c.source, c.sourceHandle ?? '')
    const t = rf.getNode(c.target)
    if (!kind || !t || NODE_TYPES[(t.data as CardData).type].input !== kind) return false
    return !reaches(c.target, c.source) // no loops
  }
  const onConnect = (c: Connection) =>
    setEdges((es) => addEdge(edge(c.source, c.sourceHandle ?? '', c.target), es))

  const addNode = (type: FlowNodeType) => {
    const box = canvas.current?.getBoundingClientRect()
    const at = box ? rf.screenToFlowPosition({ x: box.left + box.width / 2, y: box.top + box.height / 2 }) : { x: 0, y: 0 }
    const id = `${type}-${newId()}`
    setNodes((ns) => [...ns.map((n) => ({ ...n, selected: false })), {
      id, type: 'card', selected: true, position: { x: at.x - 90 + (ns.length % 4) * 20, y: at.y - 40 + (ns.length % 4) * 20 },
      data: { type, params: { ...NODE_TYPES[type].defaults }, meta },
    }])
    setSelected(id)
  }

  const setParams = (id: string, params: Record<string, unknown>) => {
    setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, data: { ...(n.data as CardData), params } } : n)))
    // Outputs can change (another model, other stems): drop connections from ports that went away.
    const d = rf.getNode(id)?.data as CardData
    const ports = new Set(outputsOf(d.type, params, meta).map(([p]) => p))
    setEdges((es) => es.filter((e) => e.source !== id || ports.has(e.sourceHandle ?? '')))
    window.setTimeout(() => updateInternals(id), 0)
  }

  const deleteNode = (id: string) => {
    setNodes((ns) => ns.filter((n) => n.id !== id))
    setEdges((es) => es.filter((e) => e.source !== id && e.target !== id))
    setSelected(null)
  }

  // ---------- save / open ----------

  const flash = (m: string) => {
    setNotice(m)
    window.setTimeout(() => setNotice((n) => (n === m ? null : n)), 2500)
  }
  const save = async () => {
    setError(null)
    try {
      const r = await api.saveFlow(current())
      setSaved(r.saved)
      flash(`Saved “${name}”`)
      return true
    } catch (e) {
      setError((e as Error).message)
      return false
    }
  }
  const remove = async () => {
    if (!saved.some((f) => f.name === name) || !window.confirm(`Delete the workflow “${name}”?`)) return
    const r = await api.deleteFlow(name)
    setSaved(r.saved)
    load(r.saved[0] ?? presets[0] ?? BLANK)
  }
  const open = (value: string) => {
    if (value === '__new') return load(BLANK)
    const f = [...saved, ...presets].find((x) => x.name === value)
    if (f) load(f)
  }

  // ---------- run ----------

  const job = useJob(jobId, (j: Job) => {
    setJobId(null)
    if (j.status === 'error') setError(`Workflow failed: ${j.error}`)
    else setResult(j.result as typeof result)
  })
  const runOnProject = async () => {
    setError(null)
    setResult(null)
    try {
      setJobId((await api.runFlow(target, current())).id)
    } catch (e) {
      setError((e as Error).message)
    }
  }
  const runAsBatch = async () => {
    if (await save()) location.hash = `#/batch?flow=${encodeURIComponent(name.trim())}`
  }

  const sel = nodes.find((n) => n.id === selected)
  const selData = sel?.data as CardData | undefined
  const isSaved = saved.some((f) => f.name === name)
  const busy = jobId !== null
  const nodeCount = useMemo(() => nodes.length, [nodes])

  return (
    <div className="flows">
      <header className="topbar">
        <button className="link" onClick={() => (location.hash = '')}>← Projects</button>
        <b className="title">Workflows</b>
        <button className="link" onClick={() => (location.hash = '#/batch')}>Batch</button>
        <span className="flows-sep" />
      <select value={isSaved || presets.some((p) => p.name === name) ? name : ''} onChange={(e) => open(e.target.value)}>
        {!isSaved && !presets.some((p) => p.name === name) && <option value="">(unsaved) {name}</option>}
        {saved.length > 0 && <optgroup label="Saved">{saved.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}</optgroup>}
        <optgroup label="Examples">{presets.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}</optgroup>
        <option value="__new">+ New workflow</option>
      </select>
      <input className="flow-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Workflow name" />
      <button className="primary" onClick={() => void save()}>Save</button>
      {isSaved && <button onClick={() => void remove()}>Delete</button>}
      <span className={`flow-status ${problem ? 'bad' : 'ok'}`} title={problem ?? undefined}>
        {problem ? `⚠ ${problem}` : `✓ ready · ${nodeCount} nodes`}
      </span>
        <div className="spacer" />
        {notice && <span className="notice">{notice}</span>}
        <Logo />
      </header>

      <div className="flows-bar flows-run">
        <b>Run</b>
        <select value={target} onChange={(e) => setTarget(e.target.value)} title="Project to run on (its mix is the Input track)">
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <button className="primary" disabled={busy || !target || !!problem} onClick={() => void runOnProject()}>
          {busy ? 'Running…' : 'Run on this project'}
        </button>
        <button disabled={!!problem} onClick={() => void runAsBatch()}
          title="Saves the workflow, then opens Batch with it selected">
          Run on a folder (batch)…
        </button>
        <label className="check" title="Batches: keep each song as a project to open and tweak later">
          <input type="checkbox" checked={keepProjects} onChange={(e) => setKeepProjects(e.target.checked)} />
          keep each song as a project
        </label>
        <div className="flows-run-out">
          {busy && <JobProgress job={job} fallback="Starting…" />}
          {!busy && result && (
            <div className="result run-result">
              <span>{result.files} files{result.samples ? ` · ${result.samples} bits` : ''} saved to</span>
              <span className="path" title={result.output}>{`\u200e${result.output}\u200e`}</span>
              <button className="link" onClick={() => void api.reveal(result.output)}>Show folder</button>
              <button className="link" onClick={() => onOpenProject(target)}>Open project</button>
              {result.warnings?.length > 0 && <span className="warn small" title={result.warnings.join('\n')}> · ⚠ {result.warnings.length}</span>}
            </div>
          )}
          {!busy && error && <div className="error">{error}</div>}
        </div>
      </div>

      <div className="flows-body">
        <aside className="flow-palette">
          <div className="field-label">Add a node</div>
          {PALETTE.map((t) => (
            <button key={t} className="flow-add" style={{ '--c': NODE_TYPES[t].color } as React.CSSProperties}
              onClick={() => addNode(t)} title={NODE_TYPES[t].hint}>
              + {NODE_TYPES[t].label}
            </button>
          ))}
          <p className="tip">
            Drag from an output (right) to an input (left). Colours: <b style={{ color: '#f59e0b' }}>audio</b>,{' '}
            <b style={{ color: '#22d3ee' }}>slices</b>, <b style={{ color: '#a78bfa' }}>samples</b>. An input takes any
            number of connections. Select and press <kbd>Del</kbd> to remove.
          </p>
          <label className="field-label">Description</label>
          <textarea className="flow-desc" value={description} onChange={(e) => setDescription(e.target.value)}
            placeholder="What it makes" rows={4} />
        </aside>

        <div className="flow-canvas" ref={canvas}>
          <ReactFlow
            nodes={nodes} edges={edges} nodeTypes={nodeTypes} colorMode="dark"
            onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect}
            isValidConnection={isValidConnection}
            onSelectionChange={({ nodes: s }) => setSelected(s[0]?.id ?? null)}
            deleteKeyCode={['Backspace', 'Delete']} fitView fitViewOptions={{ padding: 0.08, maxZoom: 1 }} proOptions={{ hideAttribution: true }}
            defaultEdgeOptions={{ animated: busy }}
          >
            <Background gap={24} size={1} />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable nodeColor={(n) => NODE_TYPES[(n.data as CardData).type].color} />
          </ReactFlow>
        </div>

        <aside className="flow-side">
          {sel && selData ? (
            <FlowInspector type={selData.type} params={selData.params} meta={meta}
              onChange={(p) => setParams(sel.id, p)}
              onDelete={selData.type === 'input' ? undefined : () => deleteNode(sel.id)} />
          ) : (
            <div className="flow-inspector"><p className="tip">Select a node to see all its options.</p></div>
          )}

        </aside>
      </div>
    </div>
  )
}
