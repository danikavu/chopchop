import { useEffect, useState } from 'react'
import { api } from '../api'
import type { ProjectSummary } from '../types'

export function ProjectList({ onOpen }: { onOpen: (pid: string) => void }) {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null)
  const [uploading, setUploading] = useState(false)
  const [drag, setDrag] = useState(false)
  const [error, setError] = useState<string | null>(null)

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

  return (
    <div className="home">
      <div className="brand">
        <h1>Chopper</h1>
        <p className="muted">Slice tracks and stems, build drum kits, cut sound bits.</p>
      </div>

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
          <h2>Projects</h2>
          {projects.map((p) => (
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
  )
}
