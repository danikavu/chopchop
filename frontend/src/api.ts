import type { ExportResult, Job, Meta, Project, ProjectSummary, Sample, Slice, Track } from './types'

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method }
  if (body instanceof FormData) {
    init.body = body
  } else if (body !== undefined) {
    init.body = JSON.stringify(body)
    init.headers = { 'Content-Type': 'application/json' }
  }
  const res = await fetch(url, init)
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

  separate: (pid: string, model: string) => req<Job>('POST', `${P(pid)}/separate`, { model }),
  job: (jid: string) => req<Job>('GET', `/api/jobs/${jid}`),
  projectJobs: (pid: string) => req<Job[]>('GET', `${P(pid)}/jobs`),

  detectSlices: (
    pid: string,
    body: { track: string; sensitivity: number; min_gap: number; max_len: number | null; gate_db: number },
  ) => req<{ slices: Slice[] }>('POST', `${P(pid)}/slices/detect`, body),
  saveSlices: (pid: string, slices: Slice[]) => req<{ slices: Slice[] }>('PUT', `${P(pid)}/slices`, { slices }),
  exportSlices: (
    pid: string,
    body: { tracks: string[]; slice_ids?: string[]; fade_in_ms: number; fade_out_ms: number; normalize: boolean },
  ) => req<ExportResult>('POST', `${P(pid)}/slices/export`, body),

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
  savePads: (pid: string, pads: (string | null)[]) => req<{ pads: (string | null)[] }>('PUT', `${P(pid)}/pads`, { pads }),
  exportKit: (pid: string, body: { sample_ids?: string[]; kit_name: string; normalize: boolean; from_pads?: boolean }) =>
    req<ExportResult>('POST', `${P(pid)}/samples/export`, body),
}
