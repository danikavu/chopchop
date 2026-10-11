import { useEffect, useRef } from 'react'
import logo from '../assets/chopchop-logo.png'

/** The ChopChop wordmark for navbars (top right). */
export function Logo() {
  return (
    <a className="logo" href="#" title="ChopChop — home">
      <img src={logo} alt="ChopChop" />
    </a>
  )
}

// ---------- the sliceable logo background ----------

type Pt = [number, number]

interface Sticker {
  x: number // centre, screen px (current, incl. bob / sway: what's drawn and sliced)
  y: number
  w: number // width px (height follows the logo's aspect ratio)
  rot: number // radians (current)
  alpha: number
  born: number // ms, for the fade-in
  // Floating: a slow drift of the base position, plus a gentle bob and sway on top.
  bx: number
  by: number
  baseRot: number
  vx: number // px/s
  vy: number
  bob: number // px
  sway: number // radians
  freq: number // Hz of the bob / sway
  phase: number
}

interface Piece {
  poly: Pt[] // in the logo's own (unrotated, centred) coordinates
  w: number
  x: number
  y: number
  rot: number
  vx: number // px/s
  vy: number
  vr: number // rad/s
  alpha: number
  born: number
}

const RATIO = 67 / 432 // logo height / width
const SPAWN_MS = 1400 // fade-in of a new logo
const PIECE_MS = 1700 // how long pieces fall before they're gone
const GRAVITY = 1400 // px/s²
const SLICE_SPEED = 1.1 // px/ms: anything slower is just moving the mouse
const RESPAWN_MS: [number, number] = [2500, 6500]

const rnd = (a: number, b: number) => a + Math.random() * (b - a)

/** Rotate p by -rot around c: screen → logo-local. */
function toLocal(p: Pt, s: { x: number; y: number; rot: number }): Pt {
  const dx = p[0] - s.x
  const dy = p[1] - s.y
  const c = Math.cos(-s.rot)
  const n = Math.sin(-s.rot)
  return [dx * c - dy * n, dx * n + dy * c]
}

/** Does segment a→b pass through the rect [-hw,hw]×[-hh,hh]? (Liang–Barsky) */
function crosses(a: Pt, b: Pt, hw: number, hh: number): boolean {
  let t0 = 0
  let t1 = 1
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  for (const [p, q] of [[-dx, a[0] + hw], [dx, hw - a[0]], [-dy, a[1] + hh], [dy, hh - a[1]]] as Pt[]) {
    if (p === 0) {
      if (q < 0) return false
    } else {
      const t = q / p
      if (p < 0) t0 = Math.max(t0, t)
      else t1 = Math.min(t1, t)
      if (t0 > t1) return false
    }
  }
  return true
}

/** Clip polygon to the side of the line (through o, direction d) where side() >= 0. */
function clipHalf(poly: Pt[], o: Pt, d: Pt, sign: 1 | -1): Pt[] {
  const side = (p: Pt) => sign * ((p[0] - o[0]) * d[1] - (p[1] - o[1]) * d[0])
  const out: Pt[] = []
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i]
    const b = poly[(i + 1) % poly.length]
    const sa = side(a)
    const sb = side(b)
    if (sa >= 0) out.push(a)
    if ((sa >= 0) !== (sb >= 0)) {
      const t = sa / (sa - sb)
      out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t])
    }
  }
  return out
}

function area(poly: Pt[]): number {
  let s = 0
  for (let i = 0; i < poly.length; i++) {
    const [x1, y1] = poly[i]
    const [x2, y2] = poly[(i + 1) % poly.length]
    s += x1 * y2 - x2 * y1
  }
  return Math.abs(s) / 2
}

/**
 * Big ChopChop logos behind the app. Swipe the mouse quickly across one and it's cut
 * in two along the swipe; the pieces fall, tumble and fade, and a new logo grows back
 * in the emptiest spot a few seconds later. One canvas, idle unless something is
 * moving; never catches clicks. Static (and unsliceable) with reduced motion.
 */
export function StickerBackground() {
  const canvas = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const cv = canvas.current!
    const ctx = cv.getContext('2d')!
    const img = new Image()
    img.src = logo
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const stickers: Sticker[] = []
    const pieces: Piece[] = []
    const trail: { x: number; y: number; t: number }[] = []
    const samples: { x: number; y: number; t: number }[] = []
    const timers: number[] = []
    let raf = 0
    let W = 0
    let H = 0

    const target = () => Math.max(7, Math.min(18, Math.round((W * H) / 110000)))

    /** Share of a logo at p that would be hidden behind solid UI (cards, lanes, panels). */
    const covered = (p: Pt, w: number) => {
      const h = w * RATIO
      const probes: Pt[] = [p, [p[0] - w * 0.35, p[1]], [p[0] + w * 0.35, p[1]], [p[0], p[1] - h * 0.3], [p[0], p[1] + h * 0.3]]
      let hidden = 0
      for (const [x, y] of probes) {
        let el = document.elementFromPoint(Math.max(0, Math.min(W - 1, x)), Math.max(0, Math.min(H - 1, y)))
        while (el && el !== document.body) {
          const bg = getComputedStyle(el).backgroundColor
          if (bg && bg !== 'transparent' && !bg.endsWith(', 0)')) {
            hidden++
            break
          }
          el = el.parentElement
        }
      }
      return hidden / probes.length
    }

    /** Best-candidate placement: of many random spots, the one that's most visible and
     * farthest from the other logos. */
    const spawn = (now: number, fadeIn: boolean) => {
      const w = rnd(260, 720)
      let best: Pt = [rnd(0, W), rnd(0, H)]
      let bestScore = -Infinity
      for (let k = 0; k < 24; k++) {
        const p: Pt = [rnd(w * 0.15, W - w * 0.15), rnd(50, H - 30)]
        const spacing = stickers.length
          ? Math.min(...stickers.map((s) => Math.hypot(s.x - p[0], (s.y - p[1]) * 2.4) - (s.w + w) * 0.45))
          : 0
        const score = spacing - covered(p, w) * 900
        if (score > bestScore) {
          bestScore = score
          best = p
        }
      }
      const rot = rnd(-0.75, 0.75)
      const heading = rnd(0, Math.PI * 2)
      const speed = still ? 0 : rnd(6, 20) // px/s: a slow float
      stickers.push({
        x: best[0], y: best[1], w, rot, alpha: rnd(0.045, 0.085), born: fadeIn ? now : -1e9,
        bx: best[0], by: best[1], baseRot: rot,
        vx: Math.cos(heading) * speed, vy: Math.sin(heading) * speed * 0.6,
        bob: still ? 0 : rnd(6, 16), sway: still ? 0 : rnd(0.02, 0.07),
        freq: rnd(0.05, 0.12), phase: rnd(0, Math.PI * 2),
      })
    }

    const resize = () => {
      const dpr = window.devicePixelRatio || 1
      W = window.innerWidth
      H = window.innerHeight
      cv.width = W * dpr
      cv.height = H * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      for (let i = stickers.length - 1; i >= 0; i--) {
        if (stickers[i].x > W + 100 || stickers[i].y > H + 60) stickers.splice(i, 1)
      }
      if (filled) while (stickers.length < target()) spawn(performance.now(), false)
      draw(performance.now())
    }
    // First fill once the page has rendered, so placement can see what's on it.
    let filled = false
    const fill = () => {
      filled = true
      const now = performance.now()
      while (stickers.length < target()) spawn(now, true)
      wake()
    }

    const drawLogo = (x: number, y: number, w: number, rot: number, alpha: number, poly?: Pt[]) => {
      const h = w * RATIO
      ctx.save()
      ctx.globalAlpha = alpha
      ctx.translate(x, y)
      ctx.rotate(rot)
      if (poly) {
        ctx.beginPath()
        poly.forEach(([px, py], i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)))
        ctx.closePath()
        ctx.clip()
      }
      ctx.drawImage(img, -w / 2, -h / 2, w, h)
      ctx.restore()
    }

    const draw = (now: number) => {
      ctx.clearRect(0, 0, W, H)
      if (!img.complete) return
      for (const s of stickers) {
        const k = Math.min(1, (now - s.born) / SPAWN_MS)
        const ease = 1 - Math.pow(1 - k, 3)
        drawLogo(s.x, s.y, s.w * (0.92 + 0.08 * ease), s.rot, s.alpha * ease)
      }
      for (const p of pieces) {
        drawLogo(p.x, p.y, p.w, p.rot, p.alpha * Math.max(0, 1 - (now - p.born) / PIECE_MS), p.poly)
      }
      // A faint blade trail along the swipe.
      for (let i = 1; i < trail.length; i++) {
        const a = Math.max(0, 1 - (now - trail[i].t) / 220)
        if (!a) continue
        ctx.strokeStyle = `rgba(255,255,255,${0.35 * a})`
        ctx.lineWidth = 2 * a
        ctx.beginPath()
        ctx.moveTo(trail[i - 1].x, trail[i - 1].y)
        ctx.lineTo(trail[i].x, trail[i].y)
        ctx.stroke()
      }
    }

    let last = performance.now()
    const FRAME_MS = 1000 / 30 // the float is slow: 30 fps is plenty and halves the work
    const float = (now: number, dt: number) => {
      for (const s of stickers) {
        s.bx += s.vx * dt
        s.by += s.vy * dt
        // Wrap around once fully off one edge: re-enter from the opposite side.
        const mx = s.w / 2 + 20
        const my = (s.w * RATIO) / 2 + 40
        if (s.bx < -mx) s.bx = W + mx
        else if (s.bx > W + mx) s.bx = -mx
        if (s.by < -my) s.by = H + my
        else if (s.by > H + my) s.by = -my
        const t = (now / 1000) * Math.PI * 2 * s.freq + s.phase
        s.x = s.bx
        s.y = s.by + Math.sin(t) * s.bob
        s.rot = s.baseRot + Math.sin(t * 0.7 + 1.3) * s.sway
      }
    }
    const tick = (now: number) => {
      if (!still && now - last < FRAME_MS && !pieces.length && !trail.length) {
        raf = requestAnimationFrame(tick)
        return
      }
      const dt = Math.min(0.05, (now - last) / 1000)
      last = now
      if (!still) float(now, dt)
      for (let i = pieces.length - 1; i >= 0; i--) {
        const p = pieces[i]
        p.vy += GRAVITY * dt
        p.x += p.vx * dt
        p.y += p.vy * dt
        p.rot += p.vr * dt
        if (now - p.born > PIECE_MS || p.y - p.w > H) pieces.splice(i, 1)
      }
      while (trail.length && now - trail[0].t > 220) trail.shift()
      draw(now)
      // Floating logos keep the loop running; with reduced motion it idles when nothing moves.
      const busy = !still || pieces.length || trail.length || stickers.some((s) => now - s.born < SPAWN_MS)
      raf = busy ? requestAnimationFrame(tick) : 0
    }
    const wake = () => {
      if (!raf) {
        last = performance.now()
        raf = requestAnimationFrame(tick)
      }
    }

    const cut = (s: Sticker, at: Pt, dir: Pt, speed: number, now: number) => {
      const hw = s.w / 2
      const hh = (s.w * RATIO) / 2
      const rect: Pt[] = [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]]
      const o = toLocal(at, s)
      const d0 = toLocal([s.x + dir[0], s.y + dir[1]], s)
      const halves = [clipHalf(rect, o, d0, 1), clipHalf(rect, o, d0, -1)]
      const full = area(rect)
      if (halves.some((h) => h.length < 3 || area(h) < full * 0.08)) return false // just a corner
      // Push each half off the cut, plus a share of the swipe's momentum.
      const len = Math.hypot(dir[0], dir[1]) || 1
      const push = Math.min(speed, 2.5) * 120 // px/s: a lightning flick still falls visibly
      const nx = -dir[1] / len
      const ny = dir[0] / len
      halves.forEach((poly, i) => {
        const away = i === 0 ? 1 : -1
        pieces.push({
          poly, w: s.w, x: s.x, y: s.y, rot: s.rot, alpha: s.alpha, born: now,
          vx: (dir[0] / len) * push + nx * away * rnd(60, 140),
          vy: (dir[1] / len) * push + ny * away * rnd(60, 140) - rnd(80, 220),
          vr: away * rnd(0.6, 2.2),
        })
      })
      timers.push(window.setTimeout(() => {
        spawn(performance.now(), true)
        wake()
      }, rnd(...RESPAWN_MS)))
      return true
    }

    const onMove = (e: PointerEvent) => {
      const now = performance.now()
      const p = { x: e.clientX, y: e.clientY, t: now }
      samples.push(p)
      while (samples.length > 2 && now - samples[0].t > 60) samples.shift()
      const prev = samples.length > 1 ? samples[samples.length - 2] : null
      if (!prev) return
      const dt = Math.max(1, now - prev.t)
      const speed = Math.hypot(p.x - prev.x, p.y - prev.y) / dt
      if (speed < SLICE_SPEED) return
      trail.push(p)
      // Direction smoothed over the last ~60 ms; segment = this mouse step.
      const first = samples[0]
      const dir: Pt = [p.x - first.x, p.y - first.y]
      for (let i = stickers.length - 1; i >= 0; i--) {
        const s = stickers[i]
        const a = toLocal([prev.x, prev.y], s)
        const b = toLocal([p.x, p.y], s)
        if (!crosses(a, b, s.w / 2, (s.w * RATIO) / 2)) continue
        if (cut(s, [p.x, p.y], dir, speed, now)) stickers.splice(i, 1)
      }
      wake()
    }

    resize()
    timers.push(window.setTimeout(fill, 500))
    img.onload = () => draw(performance.now())
    window.addEventListener('resize', resize)
    if (!still) window.addEventListener('pointermove', onMove, { passive: true })
    return () => {
      cancelAnimationFrame(raf)
      timers.forEach((t) => window.clearTimeout(t))
      window.removeEventListener('resize', resize)
      window.removeEventListener('pointermove', onMove)
    }
  }, [])

  return <canvas ref={canvas} className="sticker-canvas" aria-hidden="true" />
}
