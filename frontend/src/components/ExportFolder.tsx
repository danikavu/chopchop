import { useEffect, useState } from 'react'
import { api } from '../api'
import { FolderPicker } from './FolderPicker'
import type { ExportResult } from '../types'

// One export folder for the whole app, saved on the server (settings.json) so it
// survives restarts. Shared here so every panel shows the same value.
let current: string | null | undefined
const listeners = new Set<(v: string | null) => void>()

function publish(v: string | null) {
  current = v
  listeners.forEach((l) => l(v))
}

export function useExportDir(): [string | null | undefined, (v: string | null) => Promise<void>] {
  const [value, setValue] = useState(current)
  useEffect(() => {
    listeners.add(setValue)
    // Re-read on every mount: it may have been changed by another tab or a batch run.
    api.settings().then((s) => publish(s.export_dir)).catch(() => undefined)
    return () => {
      listeners.delete(setValue)
    }
  }, [])
  const save = async (v: string | null) => publish((await api.setExportDir(v)).export_dir)
  return [value, save]
}

/** Where exports go: the saved folder, or a zip download when none is set. */
export function ExportFolder() {
  const [dir, save] = useExportDir()
  const [picking, setPicking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = async (v: string | null) => {
    setError(null)
    try {
      await save(v)
    } catch (e) {
      setError((e as Error).message)
    }
  }
  return (
    <div className="export-folder">
      <span className="muted">Export to</span>
      <span className="path" title={dir ?? undefined}>{dir || 'zip download'}</span>
      <div className="row">
        <button onClick={() => setPicking(true)}>{dir ? 'Change…' : 'Choose folder…'}</button>
        {dir && <button onClick={() => void set(null)} title="Export as a zip download instead">Reset</button>}
      </div>
      {error && <div className="error">{error}</div>}
      {picking && (
        <FolderPicker
          title="Choose where exports are saved"
          start={dir ?? undefined}
          onClose={() => setPicking(false)}
          onPick={(path) => {
            setPicking(false)
            void set(path)
          }}
        />
      )}
    </div>
  )
}

/** "12 files → <folder> [Show folder]" or "→ download zip". */
export function ExportResultLine({ result, noun }: { result: ExportResult; noun: string }) {
  if (result.folder || !result.url) {
    return (
      <div className="result">
        {result.count} {noun} saved to <span className="path" title={result.path}>{result.path}</span>{' '}
        <button className="link" onClick={() => void api.reveal(result.path)}>Show folder</button>
      </div>
    )
  }
  return (
    <div className="result">
      {result.count} {noun} → <a href={result.url}>download zip</a>
      <div className="path" title={result.path}>{result.path}</div>
    </div>
  )
}
