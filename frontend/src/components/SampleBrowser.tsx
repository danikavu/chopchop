import { useMemo, useState } from 'react'
import { api } from '../api'
import type { ExportResult, Meta, Sample } from '../types'
import { categoryColor, keySuggestions, label, playPreview } from '../util'
import { SAMPLE_MIME } from './DrumPads'
import { ExportResultLine, useExportDir } from './ExportFolder'
import { MiniWave } from './MiniWave'
import { ToCSelect, useToC } from './ToCSelect'

interface Props {
  pid: string
  pads: (string | null)[]
  onLoadToPad: (sid: string) => void
  projectName: string
  samples: Sample[]
  meta: Meta
  onChange: (s: Sample) => void
  onDelete: (sid: string) => void
  onDeleteMany: (ids: string[]) => Promise<void>
  onEdit: (s: Sample) => void
}

export function SampleBrowser({ pid, pads, onLoadToPad, projectName, samples, meta, onChange, onDelete, onDeleteMany, onEdit }: Props) {
  const padLabels = useMemo(() => {
    const m = new Map<string, string[]>()
    pads.forEach((sid, i) => {
      if (sid) m.set(sid, [...(m.get(sid) ?? []), `${'ABCD'[Math.floor(i / 16)]}${String((i % 16) + 1).padStart(2, '0')}`])
    })
    return m
  }, [pads])
  const [filter, setFilter] = useState<string | null>(null)
  const [trackFilter, setTrackFilter] = useState<string | null>(null)
  const [kitName, setKitName] = useState(projectName)
  const [normalize, setNormalize] = useState(false)
  const [result, setResult] = useState<ExportResult | null>(null)
  const [exportDir] = useExportDir()
  const [toC] = useToC()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const categories = useMemo(() => {
    const present = new Set(samples.map((s) => s.category))
    const ordered = meta.categories.filter((c) => present.has(c))
    return [...ordered, ...[...present].filter((c) => !meta.categories.includes(c)).sort()]
  }, [samples, meta.categories])
  const trackNames = useMemo(() => [...new Set(samples.map((s) => s.track))], [samples])
  const shown = samples.filter((s) => (!filter || s.category === filter) && (!trackFilter || s.track === trackFilter))
  const groups = categories
    .map((c) => ({ cat: c, items: shown.filter((s) => s.category === c) }))
    .filter((g) => g.items.length)
  const keyOptions = useMemo(() => keySuggestions(meta.notes, meta.keys), [meta])

  const update = async (s: Sample, body: Parameters<typeof api.updateSample>[2]) => {
    setError(null)
    try {
      onChange(await api.updateSample(pid, s.id, body))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const exportKit = async () => {
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      setResult(await api.exportKit(pid, { sample_ids: shown.map((s) => s.id), kit_name: kitName, normalize, to_c: toC }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="browser">
      <div className="browser-bar">
        <h2>Samples <span className="count">{samples.length}</span></h2>
        <div className="chips">
          <button className={`chip ${filter === null ? 'on' : ''}`} onClick={() => setFilter(null)}>all</button>
          {categories.map((c) => (
            <button key={c} className={`chip ${filter === c ? 'on' : ''}`} style={{ '--c': categoryColor(c) } as React.CSSProperties}
              onClick={() => setFilter(filter === c ? null : c)}>
              <i className="dot" />
              {label(c)} <span className="count">{samples.filter((s) => s.category === c).length}</span>
            </button>
          ))}
        </div>
        {trackNames.length > 1 && (
          <select value={trackFilter ?? ''} onChange={(e) => setTrackFilter(e.target.value || null)}>
            <option value="">all tracks</option>
            {trackNames.map((t) => <option key={t} value={t}>from {label(t)}</option>)}
          </select>
        )}
        <div className="spacer" />
        <button
          disabled={busy || !shown.length}
          title="Delete every sample currently shown (respects the category / track filter)"
          onClick={async () => {
            const what = shown.length === samples.length ? 'ALL samples' : `the ${shown.length} samples shown`
            if (!window.confirm(`Delete ${what}? This also clears them from the drum pads.`)) return
            await onDeleteMany(shown.map((s) => s.id))
            setFilter(null)
            setTrackFilter(null)
          }}
        >
          {shown.length === samples.length ? 'Delete all' : `Delete shown (${shown.length})`}
        </button>
        <input className="kit-name" value={kitName} onChange={(e) => setKitName(e.target.value)} placeholder="kit name" />
        <label className="check">
          <input type="checkbox" checked={normalize} onChange={(e) => setNormalize(e.target.checked)} /> normalize
        </label>
        <ToCSelect samples={shown} />
        <button className="primary" disabled={busy || !shown.length} onClick={exportKit}
          title={exportDir ? `Saves into ${exportDir}` : 'Downloads a zip (set an export folder in the Slices panel)'}>
          {busy ? 'Exporting…' : `Export kit (${shown.length})`}
        </button>
      </div>
      {result && <ExportResultLine result={result} noun="samples" />}
      {error && <div className="error">{error}</div>}

      {!samples.length && (
        <div className="empty">
          No samples yet. Build a drum kit or extract sound bits from the panel on the right, or select a slice and
          press <kbd>Enter</kbd>.
        </div>
      )}

      <datalist id="key-options">
        {keyOptions.map((k) => <option key={k} value={k} />)}
      </datalist>
      <datalist id="category-options">
        {meta.categories.map((c) => <option key={c} value={c} />)}
      </datalist>

      {groups.map((g) => (
        <div key={g.cat} className="group">
          <h4 style={{ color: categoryColor(g.cat) }}>{label(g.cat)}</h4>
          <div className="cards">
            {g.items.map((s) => (
              <SampleCard key={`${s.id}:${s.name}:${s.key}`} pid={pid} s={s} meta={meta}
                onPlay={() => playPreview(api.sampleAudioUrl(pid, s))}
                onUpdate={(b) => update(s, b)}
                onReanalyze={async () => onChange(await api.analyzeSample(pid, s.id, true))}
                onEdit={() => onEdit(s)}
                onLoadToPad={() => onLoadToPad(s.id)}
                padLabels={padLabels.get(s.id)}
                onDelete={() => onDelete(s.id)} />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

interface CardProps {
  pid: string
  s: Sample
  meta: Meta
  onPlay: () => void
  onUpdate: (b: Parameters<typeof api.updateSample>[2]) => void
  onReanalyze: () => void
  onEdit: () => void
  onLoadToPad: () => void
  padLabels?: string[]
  onDelete: () => void
}

function SampleCard({ pid, s, meta, onPlay, onUpdate, onReanalyze, onEdit, onLoadToPad, padLabels, onDelete }: CardProps) {
  const color = categoryColor(s.category)
  const [name, setName] = useState(s.name)
  const [key, setKey] = useState(s.key ?? '')
  const categoryChoices = meta.categories.includes(s.category) ? meta.categories : [s.category, ...meta.categories]

  return (
    <div className="card" style={{ '--c': color } as React.CSSProperties} draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(SAMPLE_MIME, s.id)
        e.dataTransfer.effectAllowed = 'copy'
      }}
      title="Drag onto a drum pad">
      <button className="card-wave" onClick={onPlay} title="Play">
        <MiniWave pid={pid} sample={s} color={color} />
        <span className="card-dur">{s.duration.toFixed(2)}s</span>
        {padLabels && <span className="card-pads">{padLabels.join(' ')}</span>}
      </button>
      <input className="card-name" value={name} onChange={(e) => setName(e.target.value)}
        onBlur={() => name.trim() && name !== s.name && onUpdate({ name })}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()} />
      <div className="card-row">
        <select value={s.category} onChange={(e) => onUpdate({ category: e.target.value })} title="Category">
          {categoryChoices.map((c) => <option key={c} value={c}>{label(c)}</option>)}
        </select>
        <input className="card-key" list="key-options" value={key} placeholder="key"
          title={s.key_confidence != null && s.key ? `detected, confidence ${Math.round((s.key_confidence ?? 0) * 100)}%` : 'Key or note'}
          onChange={(e) => setKey(e.target.value)}
          onBlur={() => key !== (s.key ?? '') && onUpdate({ key })}
          onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()} />
      </div>
      <div className="card-row small">
        <span className="muted" title={`from ${s.track} @ ${s.start.toFixed(3)}s`}>
          {label(s.track)}{s.score != null ? ` · q${Math.round(s.score * 100)}` : ''}
          {s.occurrences && s.occurrences > 1 ? ` · ×${s.occurrences}` : ''}
        </span>
        <span className="card-actions">
          <button className="icon-btn" title="Load onto the selected (or next free) drum pad" onClick={onLoadToPad}>⊞</button>
          <button className="icon-btn" title="Trim" onClick={onEdit}>✂</button>
          <button className="icon-btn" title="Re-detect key" onClick={onReanalyze}>♪</button>
          <button className="icon-btn" title="Delete" onClick={onDelete}>×</button>
        </span>
      </div>
    </div>
  )
}
