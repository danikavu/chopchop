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
  status: 'pending' | 'running' | 'done' | 'error'
  progress: number
  message: string
  result: unknown
  error: string | null
}

export interface Model {
  id: string
  label: string
  stems: string[]
  default?: boolean
}

export interface Meta {
  categories: string[]
  drum_categories: string[]
  keys: string[]
  notes: string[]
  separation: { available: boolean; reason: string | null }
  models: Model[]
}

export interface ExportResult {
  file: string
  path: string
  url: string
  count: number
}
