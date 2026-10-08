import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { FolderListing } from '../types'

interface Props {
  title: string
  start?: string
  onPick: (path: string) => void
  onClose: () => void
}

/** Browse folders on this computer (Chopper runs locally) and pick one. */
export function FolderPicker({ title, start, onPick, onClose }: Props) {
  const [listing, setListing] = useState<FolderListing | null>(null)
  const [typed, setTyped] = useState(start ?? '')
  const [error, setError] = useState<string | null>(null)

  // Only the latest request may update the view: a slow earlier listing (e.g. the
  // home folder on open) must not overwrite the folder the user navigated to since.
  const latest = useRef(0)
  const open = async (path?: string) => {
    const n = ++latest.current
    setError(null)
    try {
      const l = await api.listFolder(path)
      if (n !== latest.current) return
      setListing(l)
      setTyped(l.path)
    } catch (e) {
      if (n === latest.current) setError((e as Error).message)
    }
  }
  useEffect(() => {
    void open(start || undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const join = (dir: string) => `${listing!.path.replace(/[\\/]+$/, '')}${listing!.path.includes('\\') ? '\\' : '/'}${dir}`

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal picker" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>{title}</h3>
        </div>
        <div className="row">
          <button onClick={() => listing?.parent && open(listing.parent)} disabled={!listing?.parent} title="Up one folder">↑</button>
          <input className="grow path-input" value={typed} onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && open(typed)} />
          <button onClick={() => open(typed)}>Go</button>
        </div>
        {error && <div className="error">{error}</div>}
        {listing && (
          <div className="picker-list">
            {listing.dirs.map((d) => (
              <button key={d} className="picker-item" onDoubleClick={() => open(join(d))} onClick={() => open(join(d))}>
                📁 {d}
              </button>
            ))}
            {listing.files.map((f) => (
              <div key={f} className="picker-item file">♪ {f}</div>
            ))}
            {!listing.dirs.length && !listing.files.length && <div className="muted picker-item">Empty folder</div>}
          </div>
        )}
        <div className="row">
          <span className="muted">{listing ? `${listing.files.length} audio file${listing.files.length === 1 ? '' : 's'} here` : ''}</span>
          <div className="spacer" />
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={!listing} onClick={() => listing && onPick(listing.path)}>Use this folder</button>
        </div>
      </div>
    </div>
  )
}
