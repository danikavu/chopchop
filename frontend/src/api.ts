import type {
  BatchJob,
  ExportResult,
  FolderListing,
  Job,
  Meta,
  Project,
  ProjectSummary,
  Sample,
  Settings,
  Slice,
  Tempo,
  ToC,
  Track,
  Workflow,
} from './types'

export interface UndoState {
  undo: string | null
  redo: string | null
}
type HistoryListener = (h: UndoState) => void
const historyListeners = new Set<HistoryListener>()

/** Every project response carries what Undo / Redo would do next (see the backend middleware). */
export function onHistory(fn: HistoryListener): () => void {
  historyListeners.add(fn)
  return () => historyListeners.delete(fn)
}

function readHistory(res: Response) {
  const undo = res.headers.get('X-Chopper-Undo')
  const redo = res.headers.get('X-Chopper-Redo')
  if (undo === null || redo === null) return
  const h = { undo: decodeURIComponent(undo) || null, redo: decodeURIComponent(redo) || null }
  historyListeners.forEach((fn) => fn(h))
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method }
  if (body instanceof FormData) {
    init.body = body
  } else if (body !== undefined) {
    init.body = JSON.stringify(body)
    init.headers = { 'Content-Type': 'application/json' }
  }
  const res = await fetch(url, init)
  readHistory(res)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const data = await res.json()
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } catch {
      /* not json */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

const P = (pid: string) => `/api/projects/${pid}`
const enc = encodeURIComponent

const scanQuery = (scan: string[]) => {
  const q = scan.filter(Boolean).map((p) => `scan=${encodeURIComponent(p)}`).join('&')
  return q ? `?${q}` : ''
}

export const api = {
  meta: () => req<Meta>('GET', '/api/meta'),

  listProjects: () => req<ProjectSummary[]>('GET', '/api/projects'),
  getProject: (pid: string) => req<Project>('GET', P(pid)),
  renameProject: (pid: string, name: string) => req<Project>('PATCH', P(pid), { name }),
  deleteProject: (pid: string) => req('DELETE', P(pid)),
  createProject: (file: File, name = '') => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('name', name)
    return req<Project>('POST', '/api/projects', fd)
  },

  importStem: (pid: string, file: File, name = '') => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('name', name)
    return req<Track>('POST', `${P(pid)}/tracks`, fd)
  },
  deleteTrack: (pid: string, name: string) => req('DELETE', `${P(pid)}/tracks/${enc(name)}`),
  trackAudioUrl: (pid: string, name: string) => `${P(pid)}/tracks/${enc(name)}/audio`,
  trackWindowUrl: (pid: string, name: string, start: number, end: number) =>
    `${P(pid)}/tracks/${enc(name)}/window?start=${start}&end=${end}`,
  trackPeaks: (pid: string, name: string, n = 8000) =>
    req<{ duration: number; peaks: number[] }>('GET', `${P(pid)}/tracks/${enc(name)}/peaks?n=${n}`),

  separate: (pid: string, body: { model: string; stems: string[] | null; rest: boolean; replace: boolean; debleed: boolean }) =>
    req<Job>('POST', `${P(pid)}/separate`, body),
  runTool: (pid: string, track: string, tool: string) =>
    req<Job>('POST', `${P(pid)}/tracks/${enc(track)}/tool`, { tool }),
  job: (jid: string) => req<Job>('GET', `/api/jobs/${jid}`),
  cancelJob: (jid: string) => req('POST', `/api/jobs/${jid}/cancel`),

  listFolder: (path?: string) => req<FolderListing>('GET', `/api/fs${path ? `?path=${enc(path)}` : ''}`),
  workflows: () => req<{ presets: Workflow[]; saved: Workflow[]; default_output: string }>('GET', '/api/workflows'),
  saveWorkflow: (wf: Workflow) => req<{ saved: Workflow[] }>('PUT', '/api/workflows', wf),
  deleteWorkflow: (name: string) => req<{ saved: Workflow[] }>('DELETE', `/api/workflows/${enc(name)}`),
  startBatch: (body: { input_dir: string; files?: string[]; recursive: boolean; output_dir?: string; workflow: Workflow }) =>
    req<BatchJob>('POST', '/api/batches', body),
  // scan: extra folders to look in for earlier runs (chopper-batch.json)
  batches: (scan: string[] = []) => req<BatchJob[]>('GET', `/api/batches${scanQuery(scan)}`),
  removeBatch: (jid: string, scan: string[] = []) => req('DELETE', `/api/batches/${enc(jid)}${scanQuery(scan)}`),
  clearBatches: (scan: string[] = []) => req<{ removed: number }>('DELETE', `/api/batches${scanQuery(scan)}`),
  settings: () => req<Settings>('GET', '/api/settings'),
  setExportDir: (export_dir: string | null) => req<Settings>('PUT', '/api/settings', { export_dir }),
  reveal: (path: string) => req('POST', '/api/reveal', { path }),
  projectJobs: (pid: string) => req<Job[]>('GET', `${P(pid)}/jobs`),

  detectSlices: (
    pid: string,
    body: { track: string; sensitivity: number; min_gap: number; max_len: number | null; gate_db: number },
  ) => req<{ slices: Slice[] }>('POST', `${P(pid)}/slices/detect`, body),
  saveSlices: (pid: string, slices: Slice[], label?: string) =>
    req<{ slices: Slice[] }>('PUT', `${P(pid)}/slices`, { slices, label }),
  exportSlices: (
    pid: string,
    body: {
      tracks: string[]
      slice_ids?: string[]
      fade_in_ms: number
      fade_out_ms: number
      normalize: boolean
      key_names: boolean
      bpm_names: boolean
    },
  ) => req<Job>('POST', `${P(pid)}/slices/export`, body),

  buildKit: (
    pid: string,
    body: {
      track: string
      mode: string
      sensitivity: number
      similarity: number
      max_per_category: number
      kit_size?: number
      load_pads?: boolean
      tracks?: string[]
    },
  ) => req<Job>('POST', `${P(pid)}/kits`, body),

  createSample: (pid: string, body: { track: string; start: number; end: number; category?: string }) =>
    req<Sample>('POST', `${P(pid)}/samples`, body),
  updateSample: (pid: string, sid: string, body: Partial<Pick<Sample, 'name' | 'category' | 'start' | 'end'>> & { key?: string }) =>
    req<Sample>('PATCH', `${P(pid)}/samples/${sid}`, body),
  analyzeSample: (pid: string, sid: string, keepCategory = false) =>
    req<Sample>('POST', `${P(pid)}/samples/${sid}/analyze?keep_category=${keepCategory}`),
  deleteSample: (pid: string, sid: string) => req('DELETE', `${P(pid)}/samples/${sid}`),
  deleteSamples: (pid: string, ids: string[]) =>
    req<{ deleted: number }>('POST', `${P(pid)}/samples/delete`, { ids }),
  sampleAudioUrl: (pid: string, s: Sample) => `${P(pid)}/samples/${s.id}/audio?v=${s.start}-${s.end}`,
  samplePeaks: (pid: string, s: Sample, n = 160) =>
    req<{ duration: number; peaks: number[] }>('GET', `${P(pid)}/samples/${s.id}/peaks?n=${n}&v=${s.start}-${s.end}`),
  detectTempo: (pid: string, track?: string) => req<Tempo>('POST', `${P(pid)}/tempo`, { track }),
  setTempo: (pid: string, body: { bpm?: number; downbeat?: number; shift?: number; anchor?: number }) => req<Tempo>('PUT', `${P(pid)}/tempo`, body),
  savePads: (pid: string, pads: (string | null)[], label?: string) =>
    req<{ pads: (string | null)[] }>('PUT', `${P(pid)}/pads`, { pads, label }),
  history: (pid: string) => req<UndoState>('GET', `${P(pid)}/history`),
  undo: (pid: string) => req<{ undone: string; project: Project }>('POST', `${P(pid)}/undo`),
  redo: (pid: string) => req<{ redone: string; project: Project }>('POST', `${P(pid)}/redo`),
  exportKit: (pid: string, body: { sample_ids?: string[]; kit_name: string; normalize: boolean; from_pads?: boolean; to_c?: ToC }) =>
    req<ExportResult>('POST', `${P(pid)}/samples/export`, body),
}
