interface Props {
  label: string
  value: number
  min: number
  max: number
  step: number
  onChange: (v: number) => void
  format?: (v: number) => string
  hint?: string
}

export function Slider({ label, value, min, max, step, onChange, format, hint }: Props) {
  return (
    <label className="slider" title={hint}>
      <span className="slider-top">
        <span>{label}</span>
        <span className="slider-val">{format ? format(value) : value}</span>
      </span>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))} />
    </label>
  )
}
