import { useMemo } from 'react'
import logo from '../assets/chopchop-logo.png'

/** The ChopChop wordmark for navbars (top right). */
export function Logo() {
  return (
    <a className="logo" href="#" title="ChopChop — home">
      <img src={logo} alt="ChopChop" />
    </a>
  )
}

/** Small deterministic PRNG so the sticker layout is the same on every render and page. */
function mulberry32(seed: number) {
  return () => {
    seed |= 0
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const COLS = 6
const ROWS = 6

/**
 * Logo "stickers" flying through depth behind the app. Each one sits on a jittered grid
 * cell (spread all over, no clumps) and loops forever along the z axis: about two thirds
 * come in from the distance and fade as they pass the viewer, the rest drift away into
 * the distance. Sizes span ~40-460 px (skewed towards small, a few big), rotation covers
 * the full circle with a slow spin during the flight, and every sticker has its own speed
 * and starting point, so the scene never visibly repeats. Pure CSS transforms / opacity;
 * static when the system asks for reduced motion. Never catches clicks.
 */
export function StickerBackground() {
  const stickers = useMemo(() => {
    const rand = mulberry32(0xc40c)
    const out = []
    for (let r = 0; r < ROWS; r++) {
      for (let c = 0; c < COLS; c++) {
        const incoming = rand() < 0.65
        const near = 450 // px towards the viewer (perspective is 1000 px: up to ~1.8x)
        const far = -(1800 + rand() * 1600)
        const duration = 26 + rand() * 34 // s
        out.push({
          left: ((c + 0.05 + rand() * 0.9) / COLS) * 100,
          top: ((r + 0.05 + rand() * 0.9) / ROWS) * 100,
          width: 40 + Math.pow(rand(), 2.2) * 420, // mostly small, a few large
          rotate: rand() * 360 - 180,
          spin: (rand() - 0.5) * 120, // degrees over one flight
          z0: incoming ? far : near * rand(),
          z1: incoming ? near : far,
          opacity: 0.03 + rand() * 0.045,
          duration,
          delay: -rand() * duration, // start mid-flight, so the screen is full at once
        })
      }
    }
    return out
  }, [])

  return (
    <div className="stickers" aria-hidden="true">
      {stickers.map((s, i) => (
        <img
          key={i}
          src={logo}
          alt=""
          style={
            {
              left: `${s.left}%`,
              top: `${s.top}%`,
              width: s.width,
              '--op': s.opacity,
              '--r0': `${s.rotate}deg`,
              '--r1': `${s.rotate + s.spin}deg`,
              '--z0': `${s.z0}px`,
              '--z1': `${s.z1}px`,
              animationDuration: `${s.duration}s`,
              animationDelay: `${s.delay}s`,
            } as React.CSSProperties
          }
        />
      ))}
    </div>
  )
}
