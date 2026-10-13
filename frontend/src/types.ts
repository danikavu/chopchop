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
  /** Chop only this track; unset = every track (lined up). */
  track?: string
}

/** A part of the track to separate (seconds). */
export interface Part {
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
  hidden?: boolean  // hidden from the model pickers (Models page)
}

export type ModelArch = 'mdx' | 'mdxc' | 'vr' | 'demucs'
export type Scores = Record<string, Record<string, number>>

/** Everything the Models page shows for one model. */
export interface ModelInfo extends Model {
  arch: ModelArch | null  // null: two-stage (two models in a row)
  arch_label: string
  source: 'built-in' | 'catalog' | 'files'
  builtin?: boolean
  tool?: string  // a stem tool's model
  stages?: string[]
  aliases?: Record<string, string>
  catalog_name?: string | null
  scores: Scores
  settings: Record<string, unknown> | null
  changed: Record<string, unknown>
  defaults: Record<string, unknown> | null
  gpu: { batch: number; fp16: boolean; label: string } | null
  perf: { runs: number; realtime: number | null; last_at: string; last_device: string; last_seconds: number; fallbacks: number } | null
  files: { name: string; present: boolean; size: number | null }[]
  downloaded: boolean
  size: number
  added_at?: string
  config?: string
  mdx_params?: Record<string, unknown>
}

export interface CatalogModel {
  arch: ModelArch
  name: string
  filename: string
  stems: string[]
  target_stem: string | null
  scores: Scores
  files: string[]
  added: boolean
  downloaded: boolean
}

export interface AddModel {
  source: 'catalog' | 'files'
  filename?: string
  arch?: 'mdxc' | 'mdx'
  model_path?: string
  config_path?: string
  mdx_params?: Record<string, unknown>
  label: string
  group?: string
  stems?: string[]
  note?: string
  best_for?: string[]
  aliases?: Record<string, string>
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
  silent?: number  // slices left out because they were silent on a track

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

// ---------- node workflows ----------

export type FlowNodeType =
  | 'input' | 'separate' | 'tool' | 'slices' | 'kit' | 'bits' | 'save_stems' | 'save_slices' | 'save_samples'

/** What a connection carries. */
export type PortKind = 'audio' | 'slices' | 'samples'

export interface FlowNode {
  id: string
  type: FlowNodeType
  params: Record<string, unknown>
  x: number
  y: number
}

export interface FlowEdge {
  source: string
  port: string
  target: string
}

export interface Flow {
  kind?: 'flow'
  name: string
  description: string
  nodes: FlowNode[]
  edges: FlowEdge[]
  keep_projects: boolean
}
