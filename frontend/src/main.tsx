import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { api } from './api'
import { ProjectList } from './components/ProjectList'
import { ProjectView } from './components/ProjectView'
import type { Meta } from './types'
import './styles.css'

function currentProject(): string | null {
  const m = location.hash.match(/^#\/p\/([a-f0-9]+)/)
  return m ? m[1] : null
}

function App() {
  const [pid, setPid] = useState<string | null>(currentProject)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setError(`Can't reach the Chopper server: ${e.message}`))
    const onHash = () => setPid(currentProject())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  if (error) return <div className="center error">{error}</div>
  if (!meta) return <div className="center muted">Loading…</div>
  return pid ? (
    <ProjectView key={pid} pid={pid} meta={meta} onBack={() => (location.hash = '')} />
  ) : (
    <ProjectList onOpen={(id) => (location.hash = `#/p/${id}`)} />
  )
}

createRoot(document.getElementById('root')!).render(<App />)
