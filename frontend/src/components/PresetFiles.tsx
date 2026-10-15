import { useRef } from 'react'
import { api } from '../api'

export type ImportResult = Awaited<ReturnType<typeof api.importPresets>>

/** Save a preset as <name>.chopchop.json (the same JSON `chopchop workflow NAME` prints). */
export function exportPreset(preset: { name: string }) {
  const blob = new Blob([JSON.stringify(preset, null, 1)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${preset.name.replace(/[\\/:*?"<>|]+/g, '_').trim() || 'workflow'}.chopchop.json`
  a.click()
  window.setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}

/** One line about what an import did, e.g. "Imported “Kit” (renamed “Kit (imported)”)". */
export function importSummary(r: ImportResult): string {
  const names = [...r.flows, ...r.workflows].map((n) => `“${n}”`).join(', ')
  const renamed = Object.keys(r.renamed).length ? ' (renamed: a preset with that name already exists)' : ''
  return `Imported ${names}${renamed}`
}

/** "Import…" button: reads a .chopchop.json file and imports every preset in it. */
export function ImportPresetButton({ onImported, onError }: {
  onImported: (r: ImportResult) => void
  onError: (msg: string) => void
}) {
  const input = useRef<HTMLInputElement>(null)
  const read = async (file: File | undefined) => {
    if (!file) return
    try {
      let data: unknown
      try {
        data = JSON.parse(await file.text())
      } catch {
        throw new Error(`${file.name} isn't a workflow preset file (not JSON)`)
      }
      onImported(await api.importPresets(data))
    } catch (e) {
      onError((e as Error).message)
    }
  }
  return (
    <>
      <input ref={input} type="file" accept=".json,application/json" hidden
        onChange={(e) => {
          void read(e.target.files?.[0])
          e.target.value = ''
        }} />
      <button onClick={() => input.current?.click()} title="Import workflow presets from a .chopchop.json file">Import…</button>
    </>
  )
}
