/** Node types of a workflow: defaults, ports and a one-line summary (mirrors backend flows.py). */
import type { FlowNodeType, Meta, PortKind } from './types'
import { label } from './util'

export interface NodeSpec {
  label: string
  /** What the node takes in; null = nothing (the Input track). */
  input: PortKind | null
  color: string
  hint: string
  defaults: Record<string, unknown>
}

export const NODE_TYPES: Record<FlowNodeType, NodeSpec> = {
  input: { label: 'Input track', input: null, color: '#94a3b8', hint: 'The song: a project’s mix, or each file of a batch.', defaults: {} },
  separate: {
    label: 'Separate', input: 'audio', color: '#f59e0b', hint: 'Split into stems with a UVR model; one output per stem.',
    defaults: { model: 'UVR-MDX-NET-Inst_HQ_4.onnx', stems: null, rest: false, debleed: true },
  },
  tool: {
    label: 'Stem tool', input: 'audio', color: '#fb923c', hint: 'Split drums into parts, remove reverb, or lead / backing vocals.',
    defaults: { tool: 'drumsep' },
  },
  slices: {
    label: 'Slices', input: 'audio', color: '#22d3ee', hint: 'Chop each incoming track on its hits or on the beat grid.',
    defaults: { mode: 'detect', sensitivity: 0.5, min_gap: 0.08, max_len: null, gate_db: -40, grid_beats: 4 },
  },
  kit: {
    label: 'Drum kit', input: 'audio', color: '#f472b6', hint: 'The best unique hits (kick, snare, hats…), one kit across all inputs.',
    defaults: { sensitivity: 0.5, uniqueness: 0.93, max_per_type: 8, kit_size: 16, load_pads: true },
  },
  bits: {
    label: 'Sound bits', input: 'audio', color: '#a78bfa', hint: 'One-shots or phrases from every incoming stem, with key detection.',
    defaults: { mode: 'chops', sensitivity: 0.5, uniqueness: 0.9, max: 16 },
  },
  save_stems: {
    label: 'Save stems', input: 'audio', color: '#34d399', hint: 'Write the incoming tracks as WAV files.',
    defaults: { folder: 'stems', normalize: false, bpm_names: true, key_names: true },
  },
  save_slices: {
    label: 'Save slices', input: 'slices', color: '#34d399', hint: 'Write every chop, one folder per track.',
    defaults: { folder: 'slices', fade_in_ms: 2, fade_out_ms: 8, normalize: false, bpm_names: true, key_names: false, skip_silent: true },
  },
  save_samples: {
    label: 'Save samples', input: 'samples', color: '#34d399', hint: 'Write the kit / bits, optionally also transposed to C.',
    defaults: { folder: 'samples', normalize: false, bpm_names: true, to_c: 'none', pad_names: false },
  },
}

export const PALETTE: FlowNodeType[] = ['separate', 'tool', 'slices', 'kit', 'bits', 'save_stems', 'save_slices', 'save_samples']

const KIND_COLOR: Record<PortKind, string> = { audio: '#f59e0b', slices: '#22d3ee', samples: '#a78bfa' }
export const portColor = (k: PortKind) => KIND_COLOR[k]

/** Mirrors separation.rest_name. */
export function restName(keep: string[]) {
  if (keep.length === 1 && keep[0] === 'vocals') return 'instrumental'
  if (keep.length === 1) return `no_${keep[0]}`
  return 'rest'
}

/** Output ports: [port, kind]. */
export function outputsOf(type: FlowNodeType, p: Record<string, unknown>, meta: Meta): [string, PortKind][] {
  switch (type) {
    case 'input':
      return [['mix', 'audio']]
    case 'separate': {
      const m = meta.models.find((x) => x.id === p.model)
      if (!m) return []
      const want = p.stems as string[] | null
      const keep = m.stems.filter((s) => !want || want.includes(s))
      const rest = p.rest && keep.length < m.stems.length ? [restName(keep)] : []
      return [...keep, ...rest].map((s) => [s, 'audio'])
    }
    case 'tool':
      return (meta.tools[p.tool as string]?.outputs ?? []).map((s) => [s, 'audio'])
    case 'slices':
      return [['slices', 'slices']]
    case 'kit':
    case 'bits':
      return [['samples', 'samples']]
    default:
      return []
  }
}

const pct = (v: unknown) => `${Math.round((v as number) * 100)}%`
const GRID: Record<number, string> = { 0.25: '1/16', 0.5: '1/8', 1: '1 beat', 2: '2 beats', 4: '1 bar', 8: '2 bars', 16: '4 bars' }
export const gridLabel = (beats: number) => GRID[beats] ?? `${beats} beats`

/** Short description of the node's settings, shown on the card. */
export function summary(type: FlowNodeType, p: Record<string, unknown>, meta: Meta): string {
  switch (type) {
    case 'input':
      return 'the mix'
    case 'separate': {
      const m = meta.models.find((x) => x.id === p.model)
      return m ? m.label : `unknown model ${p.model}`
    }
    case 'tool':
      return meta.tools[p.tool as string]?.label ?? String(p.tool)
    case 'slices':
      return p.mode === 'grid' ? `beat grid · ${gridLabel(p.grid_beats as number)}` : `detect hits · sens ${pct(p.sensitivity)}`
    case 'kit':
      return `${p.kit_size ? `${p.kit_size} pads` : 'every distinct hit'} · unique ${pct(p.uniqueness)}`
    case 'bits':
      return `${p.mode} · up to ${p.max} per stem`
    case 'save_stems':
    case 'save_slices':
      return `→ ${p.folder}/`
    case 'save_samples':
      return `→ ${p.folder}/${p.to_c && p.to_c !== 'none' ? ` · + in C (${p.to_c})` : ''}${p.pad_names ? ' · pad names' : ''}`
  }
}

export const portLabel = (port: string) => label(port)
