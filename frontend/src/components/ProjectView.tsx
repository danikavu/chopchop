import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type WaveSurfer from 'wavesurfer.js'
import { api, onHistory, type UndoState } from '../api'
import type { Job, Meta, Project, Sample, Slice } from '../types'
import { fmtTime, gridPoints, isDrumTrack, label, snapTo, stopPreview, trackColor, useJob } from '../util'
import { DrumPads } from './DrumPads'
import { JobProgress, SEPARATION_STEPS } from './JobProgress'
import { KitPanel } from './KitPanel'
import { SampleBrowser } from './SampleBrowser'
import { type SeparateOptions, StemPicker } from './StemPicker'
import { TempoControl } from './TempoControl'
import { SampleEditor } from './SampleEditor'
import { SlicePanel } from './SlicePanel'
import { TrackLane } from './TrackLane'

const HEAD_W = 168
const MOD = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl+'
const MAX_ZOOM = 3000

const newId = () => Math.random().toString(16).slice(2, 12).padEnd(10, '0')
const byStart = (a: Slice, b: Slice) => a.start - b.start

interface Props {
  pid: string
  meta: Meta
  onBack: () => void
}

export function ProjectView({ pid, meta, onBack }: Props) {
  const [project, setProject] = useState<Project | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [focused, setFocusedState] = useState('mix')
  const [exportTracks, setExportTracks] = useState<string[]>([])
  const [slices, setSlicesState] = useState<Slice[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [fitZoom, setFitZoom] = useState(20)
  const [zoom, setZoom] = useState(20)
  const [time, setTime] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [scrollLeft, setScrollLeft] = useState(0)
  const [tab, setTab] = useState<'slice' | 'kit'>('slice')
  const [editing, setEditing] = useState<Sample | null>(null)
  const [selectedPad, setSelectedPad] = useState<number | null>(null)
  const [showBeats, setShowBeats] = useState(true)
  const [gridStep, setGridStep] = useState(1)
  const [snapEdits, setSnapEdits] = useState(false)
  const grid = useMemo(
    () => gridPoints(project?.tempo?.beats ?? [], gridStep, project?.tempo?.downbeat ?? 0),
    [project?.tempo, gridStep],
  )
  // Snapping for slice edits (read from lane callbacks, which are created once).
  const snapRef = useRef<number[] | null>(null)
  snapRef.current = snapEdits && grid.length > 1 ? grid : null
  const snapSlice = (s: Slice): Slice => {
    const g = snapRef.current
    if (!g) return s
    const start = snapTo(g, s.start)
    let end = snapTo(g, s.end)
    if (end <= start) end = g.find((t) => t > start) ?? s.end
    return { ...s, start, end }
  }
  const [sepJobId, setSepJobId] = useState<string | null>(null)
  const [showSeparate, setShowSeparate] = useState(false)

  const lanes = useRef(new Map<string, WaveSurfer>())
  const lanesBox = useRef<HTMLDivElement>(null)
  const focusedRef = useRef(focused)
  const slicesRef = useRef(slices)
  slicesRef.current = slices
  const timeRef = useRef(time)
  timeRef.current = time
  const lastScroll = useRef(0)
  const saveTimer = useRef<number | undefined>(undefined)
  const fileInput = useRef<HTMLInputElement>(null)

  const flash = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice((n) => (n === msg ? null : n)), 2500)
  }

  // Slice edits are saved after a short pause (a drag fires many updates); `label`
  // names the undo step, null means local-only (e.g. loading from the server).
  const pendingSave = useRef<{ slices: Slice[]; label: string } | null>(null)
  const flushSlices = useCallback(async () => {
    window.clearTimeout(saveTimer.current)
    const pending = pendingSave.current
    pendingSave.current = null
    if (pending) await api.saveSlices(pid, pending.slices, pending.label)
  }, [pid])
  const setSlices = useCallback(
    (next: Slice[], label: string | null) => {
      const sorted = [...next].sort(byStart)
      slicesRef.current = sorted
      setSlicesState(sorted)
      if (label) {
        pendingSave.current = { slices: sorted, label }
        window.clearTimeout(saveTimer.current)
        saveTimer.current = window.setTimeout(() => {
          flushSlices().catch((e) => setError(`Saving slices failed: ${e.message}`))
        }, 400)
      }
    },
    [flushSlices],
  )

  // ---------- undo / redo ----------

  const [history, setHistory] = useState<UndoState>({ undo: null, redo: null })
  useEffect(() => onHistory(setHistory), [])

  const applyProject = (p: Project) => {
    setProject(p)
    setSlices(p.slices, null)
    setSelectedId((id) => (p.slices.some((s) => s.id === id) ? id : null))
    setExportTracks((cur) => cur.filter((n) => p.tracks.some((t) => t.name === n)))
    setEditing(null)
    if (!p.tracks.some((t) => t.name === focusedRef.current)) setFocus('mix')
  }

  const undoRedo = async (redo: boolean) => {
    try {
      await flushSlices() // an edit still waiting to save is the one to undo
      if (redo) {
        const r = await api.redo(pid)
        applyProject(r.project)
        flash(`Redo: ${r.redone}`)
      } else {
        const r = await api.undo(pid)
        applyProject(r.project)
        flash(`Undo: ${r.undone}`)
      }
    } catch (e) {
      flash((e as Error).message === 'nothing to undo' || (e as Error).message === 'nothing to redo'
        ? `Nothing to ${redo ? 'redo' : 'undo'}` : `Undo failed: ${(e as Error).message}`)
    }
  }

  const autoTempo = async () => {
    try {
      const tempo = await api.detectTempo(pid)
      setProject((p) => (p ? { ...p, tempo } : p))
    } catch {
      /* leave it for the user to run by hand */
    }
  }

  const reload = useCallback(async () => {
    const p = await api.getProject(pid)
    setProject(p)
    return p
  }, [pid])

  // Initial load.
  useEffect(() => {
    reload()
      .then((p) => {
        setSlices(p.slices, null)
        void api.history(pid)
        const stems = p.tracks.filter((t) => t.kind === 'stem')
        const start = stems.find((t) => t.name.includes('drum'))?.name ?? 'mix'
        focusedRef.current = start
        setFocusedState(start)
        setExportTracks((stems.length ? stems : p.tracks).map((t) => t.name))
        if (!p.tempo && stems.some((t) => isDrumTrack(t.name))) void autoTempo()
      })
      .catch((e) => setError(e.message))
    // Resume watching a separation that is still running (e.g. after a reload).
    api.projectJobs(pid).then((jobs) => {
      const running = jobs.find((j) => j.kind === 'separate' && (j.status === 'running' || j.status === 'pending'))
      if (running) setSepJobId(running.id)
    })
    return () => {
      window.clearTimeout(saveTimer.current)
      stopPreview()
    }
  }, [pid, reload, setSlices])

  // "Fit" zoom = whole track across the lane width; follow window resizes, and keep
  // the view fitted if the user hasn't zoomed in.
  const duration = project?.duration
  const zoomRef = useRef(zoom)
  zoomRef.current = zoom
  const fitRef = useRef(fitZoom)
  useEffect(() => {
    const el = lanesBox.current
    if (!el || !duration) return
    const measure = () => {
      const fit = Math.max(1, (el.clientWidth - HEAD_W - 4) / duration)
      const wasFitted = Math.abs(zoomRef.current - fitRef.current) < 1e-6
      fitRef.current = fit
      setFitZoom(fit)
      if (wasFitted || zoomRef.current < fit) setZoom(fit)
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [duration])

  // Separation and stem tools share the progress banner.
  const sepJob = useJob(sepJobId, (j: Job) => {
    setSepJobId(null)
    const isTool = j.kind.startsWith('tool:')
    if (j.status === 'error') setError(`${isTool ? 'Stem tool' : 'Separation'} failed: ${j.error}`)
    else
      reload().then((p) => {
        const made = (j.result as { tracks: string[] }).tracks
        if (isTool) {
          setExportTracks((cur) => [...new Set([...cur, ...made])])
          flash(`Added ${made.join(', ')}`)
          return
        }
        setExportTracks(p.tracks.filter((t) => t.kind === 'stem').map((t) => t.name))
        const drums = made.find((n) => isDrumTrack(n))
        if (drums && !isDrumTrack(focusedRef.current)) setFocus(drums)
        else if (!p.tracks.some((t) => t.name === focusedRef.current)) setFocus('mix') // lane was replaced
        flash(`Separated into ${made.length} stems: ${made.join(', ')}`)
      })
  })

  const runTool = async (track: string, tool: string) => {
    setError(null)
    try {
      await flushSlices()
      setSepJobId((await api.runTool(pid, track, tool)).id)
    } catch (e) {
      setError((e as Error).message)
    }
  }
  const toolsFor = (name: string) =>
    Object.entries(meta.tools ?? {})
      .map(([id, t]) => ({ id, ...t, suggested: name.toLowerCase().includes(t.for) }))
      .sort((a, b) => Number(b.suggested) - Number(a.suggested))

  // ---------- lanes / transport ----------

  const setFocus = useCallback((name: string) => {
    const prev = focusedRef.current
    if (prev === name) return
    const old = lanes.current.get(prev)
    const wasPlaying = old?.isPlaying() ?? false
    old?.pause()
    focusedRef.current = name
    setFocusedState(name)
    const next = lanes.current.get(name)
    if (next) {
      next.setTime(timeRef.current)
      if (wasPlaying) void next.play()
    }
  }, [])

  const laneEvents = useMemo(
    () => ({
      onFocus: setFocus,
      onTime: (name: string, t: number) => {
        if (name === focusedRef.current) setTime(t)
      },
      onPlayState: (name: string, p: boolean) => {
        if (name === focusedRef.current) setPlaying(p)
      },
      onScroll: (name: string, left: number) => {
        if (Math.abs(left - lastScroll.current) < 1) return
        lastScroll.current = left
        setScrollLeft(left)
        for (const [n, ws] of lanes.current) if (n !== name) ws.setScroll(left)
      },
      onRegister: (name: string, ws: WaveSurfer | null) => {
        if (ws) {
          lanes.current.set(name, ws)
          ws.setScroll(lastScroll.current)
        } else lanes.current.delete(name)
      },
      onSliceChange: (s: Slice) => {
        const snapped = snapSlice(s)
        setSlices(slicesRef.current.map((x) => (x.id === s.id ? snapped : x)), 'Move slice')
      },
      onSliceCreate: (start: number, end: number) => {
        const s = snapSlice({ id: newId(), start, end })
        setSlices([...slicesRef.current, s], 'Draw slice')
        setSelectedId(s.id)
      },
      onSelectSlice: setSelectedId,
      onToggleExport: (name: string) =>
        setExportTracks((cur) => (cur.includes(name) ? cur.filter((n) => n !== name) : [...cur, name])),
    }),
    [setFocus, setSlices],
  )

  const focusedLane = () => lanes.current.get(focusedRef.current)

  const togglePlay = () => void focusedLane()?.playPause()

  const splitAtPlayhead = () => {
    const t = snapRef.current ? snapTo(snapRef.current, timeRef.current) : timeRef.current
    const cur = slicesRef.current
    const hit = cur.find((s) => s.start < t - 0.005 && s.end > t + 0.005)
    if (hit) {
      const right = { id: newId(), start: t, end: hit.end }
      setSlices([...cur.filter((s) => s.id !== hit.id), { ...hit, end: t }, right], 'Split slice')
      setSelectedId(right.id)
    } else {
      const next = cur.find((s) => s.start > t)
      const end = Math.min(next ? next.start : t + 0.5, project?.duration ?? t + 0.5)
      if (end - t < 0.01) return
      const s = { id: newId(), start: t, end }
      setSlices([...cur, s], 'Add slice')
      setSelectedId(s.id)
    }
  }

  const deleteSelected = () => {
    if (!selectedId) return
    const cur = slicesRef.current
    const i = cur.findIndex((s) => s.id === selectedId)
    setSlices(cur.filter((s) => s.id !== selectedId), 'Delete slice')
    setSelectedId(cur[i + 1]?.id ?? cur[i - 1]?.id ?? null)
  }

  const stepSlice = (dir: 1 | -1) => {
    const cur = slicesRef.current
    if (!cur.length) return
    const i = cur.findIndex((s) => s.id === selectedId)
    const next = cur[i < 0 ? (dir > 0 ? 0 : cur.length - 1) : Math.min(cur.length - 1, Math.max(0, i + dir))]
    setSelectedId(next.id)
    const ws = focusedLane()
    if (ws) {
      const w = ws.getWidth()
      const x = next.start * zoom
      if (x < lastScroll.current || x > lastScroll.current + w - 40) ws.setScroll(Math.max(0, x - w / 3))
      void ws.play(next.start, next.end)
    }
  }

  const saveSelectedAsSample = async () => {
    const s = slicesRef.current.find((x) => x.id === selectedId)
    if (!s) return
    try {
      const sample = await api.createSample(pid, { track: focusedRef.current, start: s.start, end: s.end })
      setProject((p) => (p ? { ...p, samples: [...p.samples, sample] } : p))
      flash(`Saved ${sample.name} (${label(sample.category)}${sample.key ? `, ${sample.key}` : ''})`)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  // Keyboard shortcuts.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (editing) return
      const el = e.target as HTMLElement
      if (el.closest('input, select, textarea, [contenteditable]')) return // native text undo there
      const mod = e.metaKey || e.ctrlKey
      if (mod && !e.altKey && (e.key === 'z' || e.key === 'Z' || e.key === 'y')) {
        e.preventDefault()
        void undoRedo(e.key === 'y' || e.shiftKey) // Ctrl+Z / ⌘Z undo · Ctrl+Shift+Z / ⌘⇧Z / Ctrl+Y redo
        return
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return
      switch (e.key) {
        case ' ':
          e.preventDefault()
          togglePlay()
          break
        case 's':
        case 'S':
          splitAtPlayhead()
          break
        case 'Delete':
        case 'Backspace':
          e.preventDefault()
          deleteSelected()
          break
        case 'Enter':
          void saveSelectedAsSample()
          break
        case 'ArrowRight':
          e.preventDefault()
          stepSlice(1)
          break
        case 'ArrowLeft':
          e.preventDefault()
          stepSlice(-1)
          break
        case 'Escape':
          setSelectedId(null)
          break
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  // ---------- pads ----------

  const setPads = (pads: (string | null)[], label = 'Edit pads') => {
    setProject((p) => (p ? { ...p, pads } : p))
    api.savePads(pid, pads, label).catch((e) => {
      setError(`Saving pads failed: ${e.message}`)
      void reload()
    })
  }

  /** MPC-style: load onto the selected pad (replacing it), else the first free pad. */
  const loadToPad = (sid: string) => {
    if (!project) return
    const pads = [...project.pads]
    const target = selectedPad ?? pads.findIndex((x) => !x)
    if (target < 0) {
      setError('All pads are full — select a pad to replace')
      return
    }
    pads[target] = sid
    setPads(pads, 'Load pad')
    // Step to the next pad so a run of samples can be loaded in a row.
    setSelectedPad(Math.min(target + 1, pads.length - 1))
  }

  // ---------- stems ----------

  const separate = async (opts: SeparateOptions) => {
    setError(null)
    try {
      await flushSlices()
      const j = await api.separate(pid, opts)
      setShowSeparate(false)
      setSepJobId(j.id)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const importStems = async (files: FileList | null) => {
    if (!files?.length) return
    setError(null)
    try {
      for (const f of Array.from(files)) await api.importStem(pid, f)
      const p = await reload()
      if (!p.tempo && p.tracks.some((t) => isDrumTrack(t.name))) void autoTempo()
      setExportTracks(p.tracks.filter((t) => t.kind === 'stem').map((t) => t.name))
      flash(`Imported ${files.length} stem${files.length > 1 ? 's' : ''}`)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const deleteTrack = async (name: string) => {
    if (!window.confirm(`Remove the “${name}” stem from this project?`)) return
    await api.deleteTrack(pid, name)
    if (focusedRef.current === name) setFocus('mix')
    setExportTracks((cur) => cur.filter((n) => n !== name))
    await reload()
  }

  if (!project) {
    return (
      <div className="center">
        {error ? <div className="error">{error}</div> : 'Loading…'}
      </div>
    )
  }

  const tracks = [...project.tracks].sort((a, b) => (a.kind === 'mix' ? -1 : b.kind === 'mix' ? 1 : 0))
  const hasStems = tracks.some((t) => t.kind === 'stem')
  const playheadX = HEAD_W + time * zoom - scrollLeft
  const zoomPos = Math.log(zoom / fitZoom) / Math.log(MAX_ZOOM / fitZoom)
  const selected = slices.find((s) => s.id === selectedId)

  return (
    <div className="project">
      <header className="topbar">
        <button className="link" onClick={onBack}>← Projects</button>
        <h1 className="title">{project.name}</h1>
        <span className="muted">{fmtTime(project.duration ?? 0)} · {project.sample_rate} Hz</span>
        <TempoControl
          pid={pid}
          tempo={project.tempo}
          tracks={tracks}
          showBeats={showBeats}
          onShowBeats={setShowBeats}
          onChange={(tempo) => setProject((p) => (p ? { ...p, tempo } : p))}
        />
        <div className="spacer" />
        {notice && <span className="notice">{notice}</span>}
        <div className="undo-btns">
          <button onClick={() => void undoRedo(false)} disabled={!history.undo}
            title={history.undo ? `Undo ${history.undo} (${MOD}Z)` : 'Nothing to undo'}>↶</button>
          <button onClick={() => void undoRedo(true)} disabled={!history.redo}
            title={history.redo ? `Redo ${history.redo} (${MOD}⇧Z)` : 'Nothing to redo'}>↷</button>
        </div>
        <input ref={fileInput} type="file" accept="audio/*" multiple hidden
          onChange={(e) => {
            void importStems(e.target.files)
            e.target.value = ''
          }} />
        {hasStems && meta.separation.available && !sepJobId && (
          <button onClick={() => setShowSeparate((v) => !v)} title="Re-separate with another model or stem choice">
            Separate…
          </button>
        )}
        <button onClick={() => fileInput.current?.click()}>Import stems…</button>
      </header>

      {(!hasStems || showSeparate) && !sepJobId && (
        <div className="banner separate-banner">
          <div className="separate-intro">
            <strong>{hasStems ? 'Separate again' : 'Separate this track into stems'}</strong>
            <div className="muted">
              Runs UVR models locally (first run downloads the model)
              {meta.device && <> · runs on <b className={`device ${meta.device.device}`}>{meta.device.name}</b></>}
            </div>
            {meta.device?.hint && <div className="warn small">{meta.device.hint}</div>}
          </div>
          {meta.separation.available ? (
            <StemPicker meta={meta} hasStems={hasStems} onStart={separate}
              onCancel={hasStems ? () => setShowSeparate(false) : undefined} />
          ) : (
            <div className="warn">Separation unavailable: {meta.separation.reason}. You can still import stems.</div>
          )}
        </div>
      )}
      {sepJobId && (
        <div className="banner">
          <JobProgress job={sepJob} steps={SEPARATION_STEPS} fallback="Starting separation…" />
        </div>
      )}

      <div className="workspace">
        <div className="main-col">
          <div className="transport">
            <button className="play" onClick={togglePlay} title="Play / pause (Space)">{playing ? '❚❚' : '▶'}</button>
            <span className="time">{fmtTime(time)}</span>
            <span className="muted">playing: <b style={{ color: trackColor(focused) }}>{label(focused)}</b></span>
            <div className="spacer" />
            <button onClick={splitAtPlayhead} title="Split / add slice at playhead (S)">Split</button>
            <button onClick={deleteSelected} disabled={!selectedId} title="Delete selected slice (Del)">Delete</button>
            {selected && <span className="muted">{fmtTime(selected.start)} – {fmtTime(selected.end)}</span>}
            <label className="zoom" title="Zoom">
              <span>zoom</span>
              <input type="range" min={0} max={1} step={0.001} value={isFinite(zoomPos) ? zoomPos : 0}
                onChange={(e) => setZoom(fitZoom * Math.pow(MAX_ZOOM / fitZoom, parseFloat(e.target.value)))} />
            </label>
          </div>

          <div className="lanes" ref={lanesBox}>
            {tracks.map((t, i) => (
              <TrackLane
                key={`${t.name}:${t.file}:${t.duration}`}
                pid={pid}
                track={t}
                color={trackColor(t.name, i)}
                zoom={zoom}
                slices={slices}
                selectedId={selectedId}
                focused={focused === t.name}
                exportChecked={exportTracks.includes(t.name)}
                timeline={i === 0}
                showScrollbar={i === tracks.length - 1}
                beats={showBeats ? project.tempo?.beats : undefined}
                downbeat={project.tempo?.downbeat ?? 0}
                {...laneEvents}
                onDelete={t.kind === 'stem' ? deleteTrack : undefined}
                tools={meta.separation.available && !sepJobId ? toolsFor(t.name) : undefined}
                onTool={runTool}
              />
            ))}
            {playheadX >= HEAD_W && <div className="playhead" style={{ left: playheadX }} />}
          </div>

          <DrumPads
            pid={pid}
            pads={project.pads}
            samples={project.samples}
            tracks={tracks}
            projectName={project.name}
            selectedPad={selectedPad}
            onSelectPad={setSelectedPad}
            onPadsChange={setPads}
            onKitBuilt={async () => {
              await reload()
              flash('Auto kit loaded onto the pads')
            }}
          />

          <SampleBrowser
            pid={pid}
            pads={project.pads}
            onLoadToPad={loadToPad}
            projectName={project.name}
            samples={project.samples}
            meta={meta}
            onChange={(s) => setProject((p) => (p ? { ...p, samples: p.samples.map((x) => (x.id === s.id ? s : x)) } : p))}
            onDelete={async (sid) => {
              await api.deleteSample(pid, sid)
              setProject((p) =>
                p
                  ? {
                      ...p,
                      samples: p.samples.filter((x) => x.id !== sid),
                      pads: p.pads.map((x) => (x === sid ? null : x)),
                    }
                  : p,
              )
            }}
            onDeleteMany={async (ids) => {
              const gone = new Set(ids)
              try {
                await api.deleteSamples(pid, ids)
              } catch (e) {
                setError((e as Error).message)
                return
              }
              setProject((p) =>
                p
                  ? {
                      ...p,
                      samples: p.samples.filter((x) => !gone.has(x.id)),
                      pads: p.pads.map((x) => (x && gone.has(x) ? null : x)),
                    }
                  : p,
              )
              flash(`Deleted ${ids.length} sample${ids.length === 1 ? '' : 's'}`)
            }}
            onEdit={setEditing}
          />
        </div>

        <aside className="side">
          <div className="tabs">
            <button className={tab === 'slice' ? 'on' : ''} onClick={() => setTab('slice')}>Slice</button>
            <button className={tab === 'kit' ? 'on' : ''} onClick={() => setTab('kit')}>Kits & bits</button>
          </div>
          {tab === 'slice' ? (
            <SlicePanel
              pid={pid}
              tracks={tracks}
              focused={focused}
              slices={slices}
              selectedId={selectedId}
              exportTracks={exportTracks}
              onFocus={setFocus}
              onSlices={(s, label) => {
                setSlices(s, label)
                setSelectedId(null)
              }}
              onToggleExport={laneEvents.onToggleExport}
              onSaveSelectedAsSample={saveSelectedAsSample}
              duration={project.duration ?? 0}
              tempo={project.tempo}
              gridStep={gridStep}
              onGridStep={setGridStep}
              grid={grid}
              snapEdits={snapEdits}
              onSnapEdits={setSnapEdits}
              onDownbeat={async (downbeat) => {
                try {
                  const tempo = await api.setTempo(pid, { downbeat })
                  setProject((p) => (p ? { ...p, tempo } : p))
                } catch (e) {
                  setError((e as Error).message)
                }
              }}
            />
          ) : (
            <KitPanel key={tracks.map((t) => t.name).join()} pid={pid} tracks={tracks} onDone={() => void reload()} />
          )}
          {error && (
            <div className="error">
              {error} <button className="link" onClick={() => setError(null)}>dismiss</button>
            </div>
          )}
        </aside>
      </div>

      {editing && (
        <SampleEditor
          pid={pid}
          sample={editing}
          track={tracks.find((t) => t.name === editing.track) ?? tracks[0]}
          onClose={() => setEditing(null)}
          onSaved={(s) => setProject((p) => (p ? { ...p, samples: p.samples.map((x) => (x.id === s.id ? s : x)) } : p))}
        />
      )}
    </div>
  )
}
