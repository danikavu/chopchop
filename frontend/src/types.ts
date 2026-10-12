export interface Track {
  name: string
  kind: 'mix' | 'stem'
  file: string
  sample_rate: number
  duration: number
  channels: number
}

export interface Slice {
  id: string
  start: number
  end: number
}

export interface Sample {
  id: string
  name: string
  file: string
  track: string
  start: number
  end: number
  duration: number
  category: string
  category_confidence?: number
  key: string | null
  key_confidence?: number
  pitch_hz?: number | null
  score?: number | null
  occurrences?: number
  origin: 'drums' | 'chops' | 'phrases' | 'manual'
  created_at: number
}

export interface Tempo {
  bpm: number | null
  confidence: number
  components: Partial<Record<'clarity' | 'stability' | 'on_grid' | 'timing' | 'octave', number>>
  range: [number, number] | null
  steady: boolean | null
  alternatives: { bpm: number; relation: string; strength: number }[]
  beats: number[]
  downbeat?: number
  swing: { percent: number | null; grid: '8th' | '16th' | null }
  track?: string
  source?: 'detected' | 'manual'
  detected_bpm?: number | null
}

export interface Project {
  id: string
  name: string
  created_at: number
  duration?: number
  sample_rate?: number
  tracks: Track[]
  slices: Slice[]
  samples: Sample[]
  pads: (string | null)[]
  tempo: Tempo | null
}

export interface ProjectSummary {
  id: string
  name: string
  created_at: number
  tracks: number
  samples: number
}

export interface Job {
  id: string
  kind: string
  project_id: string
  status: 'pending' | 'running' | 'done' | 'error' | 'cancelled'
  progress: number
  message: string
  result: unknown
  error: string | null
  created_at: number
  started_at: number | null
  finished_at: number | null
}

export interface Model {
  id: string
  label: string
  stems: string[]
  group?: string
  note?: string
  best_for?: string[]
  default?: boolean
}

export interface Meta {
  categories: string[]
  drum_categories: string[]
  keys: string[]
  notes: string[]
  separation: { available: boolean; reason: string | null }
  device: { device: 'cuda' | 'mps' | 'directml' | 'cpu'; name: string; hint: string | null } | null
  models: Model[]
  tools: Record<string, { label: string; description: string; for: string; outputs: string[] }>
}

export interface ExportResult {
  file: string
  path: string
  url: string | null  // null when written into the export folder
  folder?: boolean
  count: number
  transposed?: number  // extra copies in C (see ToC)

}

export interface Settings {
  export_dir: string | null
  default_batch_output: string
}

export interface Workflow {
  name: string
  description: string
  separate: { enabled: boolean; model: string; stems: string[] | null; rest: boolean; debleed: boolean }
  tools: { tool: string; on: string }[]
  tempo: boolean
  key: boolean
  kit: { enabled: boolean; source: string; size: number; uniqueness: number }
  bits: { enabled: boolean; stems: string[]; mode: 'chops' | 'phrases'; max: number; uniqueness: number }
  export: {
    stems: 'all' | 'kept' | 'none' | string[]
    mix: boolean
    kit: boolean
    samples: boolean
    bpm_names: boolean
    key_names: boolean
    normalize: boolean
    to_c?: ToC
  }
  keep_projects: boolean
}

export interface BatchSong {
  file: string
  song: string
  status: 'queued' | 'running' | 'done' | 'error' | 'cancelled'
  message: string
  output?: string
  project?: string
  bpm?: number
  key?: string
  kit?: number
  samples?: number
  files?: number
  warnings?: string[]
}

export interface BatchJob extends Job {
  detail: { output: string; workflow: string; songs: BatchSong[]; from_disk?: boolean } | null
}

export interface FolderListing {
  path: string
  parent: string | null
  dirs: string[]
  files: string[]
}

/** Also export keyed samples transposed to C: resampled (repitched), length kept (warped), or both. */
export type ToC = 'none' | 'repitched' | 'warped' | 'both'
