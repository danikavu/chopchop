import { lazy, Suspense, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { api } from './api'
import { BatchView } from './components/BatchView'
import { StickerBackground } from './components/Brand'
import { ProjectList } from './components/ProjectList'
import { ModelsView } from './components/ModelsView'
import { ProjectView } from './components/ProjectView'
import type { Meta } from './types'
import './styles.css'

// The node editor (React Flow) loads only when the Workflows page opens.
const WorkflowsView = lazy(() => import('./components/WorkflowsView').then((m) => ({ default: m.WorkflowsView })))

function currentProject(): string | null {
  const m = location.hash.match(/^#\/p\/([a-f0-9]+)/)
  return m ? m[1] : null
}

function onBatchPage() {
  return location.hash.startsWith('#/batch')
}

function onModelsPage() {
  return location.hash.startsWith('#/models')
}

function onWorkflowsPage() {
  return location.hash.startsWith('#/workflows')
}

function App() {
  const [pid, setPid] = useState<string | null>(currentProject)
  const [batch, setBatch] = useState(onBatchPage)
  const [flows, setFlows] = useState(onWorkflowsPage)
  const [modelsPage, setModelsPage] = useState(onModelsPage)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setError(`Can't reach the ChopChop server: ${e.message}`))
    const onHash = () => {
      setPid(currentProject())
      setBatch(onBatchPage())
      setFlows(onWorkflowsPage())
      setModelsPage(onModelsPage())
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  let page
  if (error) page = <div className="center error">{error}</div>
  else if (!meta) page = <div className="center muted">Loading…</div>
  else if (modelsPage) page = <ModelsView meta={meta} onMetaChange={() => void api.meta().then(setMeta)} />
  else if (flows) page = (
    <Suspense fallback={<div className="center muted">Loading…</div>}>
      <WorkflowsView meta={meta} onOpenProject={(id) => (location.hash = `#/p/${id}`)} />
    </Suspense>
  )
  else if (batch) page = <BatchView meta={meta} onOpenProject={(id) => (location.hash = `#/p/${id}`)} />
  else if (pid) page = <ProjectView key={pid} pid={pid} meta={meta} onBack={() => (location.hash = '')} />
  else page = <ProjectList onOpen={(id) => (location.hash = `#/p/${id}`)} />
  return (
    <>
      <StickerBackground />
      <div className="film-grain" aria-hidden />
      <div className="app">{page}</div>
    </>
  )
}

createRoot(document.getElementById('root')!).render(<App />)
