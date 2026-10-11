import { useEffect, useState } from 'react'
import { api } from '../api'
import { Logo } from './Brand'
import type { ProjectSummary } from '../types'

export function ProjectList({ onOpen }: { onOpen: (pid: string) => void }) {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null)
  const [uploading, setUploading] = useState(false)
  const [drag, setDrag] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')

  const load = () => api.listProjects().then(setProjects).catch((e) => setError(e.message))
  useEffect(() => {
    void load()
  }, [])

  const upload = async (file: File | undefined) => {
    if (!file) return
    setUploading(true)
    setError(null)
    try {
      const p = await api.createProject(file)
      onOpen(p.id)
    } catch (e) {
      setError((e as Error).message)
      setUploading(false)
    }
  }

  // Case-sensitive on purpose: "Song" doesn't match "song".
  const shown = (projects ?? []).filter((p) => p.name.includes(query))

  return (
    <>
    <header className="topbar">
      <b className="title">Projects</b>
      <button className="link" onClick={() => (location.hash = '#/batch')}>Batch</button>
      <div className="spacer" />
      <Logo />
    </header>
    <div className="home">

      <button className="batch-link" onClick={() => (location.hash = '#/batch')}>
        <strong>Batch a whole folder →</strong>
        <span className="muted">e.g. an album: a drum kit per song, or all vocal + bass stems</span>
      </button>

      <label
        className={`drop ${drag ? 'over' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setDrag(true)
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDrag(false)
          void upload(e.dataTransfer.files[0])
        }}
      >
        <input type="file" accept="audio/*" hidden onChange={(e) => void upload(e.target.files?.[0])} />
        {uploading ? 'Importing…' : (
          <>
            <strong>Drop a track here</strong>
            <span className="muted">or click to choose · WAV, FLAC, AIFF, MP3, OGG</span>
          </>
        )}
      </label>
      {error && <div className="error">{error}</div>}

      {projects && projects.length > 0 && (
        <div className="project-list">
          <div className="row project-list-head">
            <h2>Projects</h2>
            <div className="spacer" />
            <input className="project-search" type="search" placeholder="Search projects (case-sensitive)"
              value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          {shown.length === 0 && <div className="empty">No project name contains “{query}”.</div>}
          {shown.map((p) => (
            <div key={p.id} className="project-row" onClick={() => onOpen(p.id)}>
              <div>
                <div className="project-name">{p.name}</div>
                <div className="muted">
                  {p.tracks} track{p.tracks === 1 ? '' : 's'} · {p.samples} samples ·{' '}
                  {new Date(p.created_at * 1000).toLocaleString()}
                </div>
              </div>
              <button
                className="icon-btn"
                title="Delete project"
                onClick={async (e) => {
                  e.stopPropagation()
                  if (!window.confirm(`Delete “${p.name}” and all its stems and samples?`)) return
                  await api.deleteProject(p.id)
                  void load()
                }}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
    </>
  )
}
