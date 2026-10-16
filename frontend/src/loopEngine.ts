/** Sample-accurate looping of one chop.
 *
 * Looping on the lane's audio element can't be exact: the end of the chop is only noticed
 * on the next time update (a screen frame or more), so the start of the next chop is heard
 * before the jump back. Here the chop alone is decoded and looped by Web Audio's own loop
 * points: gapless, and nothing past its end. */
import { audioContext, load } from './padEngine'

const FADE_S = 0.002 // a 2 ms fade at the seam so the loop doesn't click
const faded = new WeakSet<AudioBuffer>()

interface Loop {
  source: AudioBufferSourceNode
  gain: GainNode
  startedAt: number // context time the loop began
  length: number // seconds
  from: number // the chop's start in the track
  token: number
}

let current: Loop | null = null
let token = 0

function fade(buf: AudioBuffer) {
  if (faded.has(buf)) return
  faded.add(buf)
  const n = Math.min(Math.floor(buf.sampleRate * FADE_S), Math.floor(buf.length / 4))
  for (let c = 0; c < buf.numberOfChannels; c++) {
    const d = buf.getChannelData(c)
    for (let i = 0; i < n; i++) {
      const g = i / n
      d[i] *= g
      d[d.length - 1 - i] *= g
    }
  }
}

const SWITCH_FADE_S = 0.005

/** Loop `url` (the chop's audio, which starts at `from` seconds in the track). Resolves
 * once it plays; a newer start / stop meanwhile wins. onEnd: it stopped by itself.
 *
 * keepPlace: the same chop from another track (switching lanes) — the old loop keeps
 * playing until the new audio is ready, which then takes over at the same point in the
 * chop, with a short crossfade, instead of from the top. */
export async function startLoop(url: string, from: number, onEnd: () => void, keepPlace = false): Promise<boolean> {
  const old = keepPlace && current && Math.abs(current.from - from) < 1e-6 ? current : null
  if (!old) stopLoop()
  const mine = ++token
  const ctx = audioContext()
  if (ctx.state === 'suspended') await ctx.resume()
  const buf = await load(url)
  if (mine !== token) return false
  fade(buf)
  const source = ctx.createBufferSource()
  source.buffer = buf
  source.loop = true
  const gain = ctx.createGain()
  source.connect(gain).connect(ctx.destination)
  source.onended = () => {
    if (current?.source === source) {
      current = null
      onEnd()
    }
  }
  const now = ctx.currentTime
  let offset = 0
  if (old && current === old) {
    offset = ((now - old.startedAt) % old.length) % buf.duration
    gain.gain.setValueAtTime(0, now)
    gain.gain.linearRampToValueAtTime(1, now + SWITCH_FADE_S)
    old.source.onended = null
    old.gain.gain.setValueAtTime(old.gain.gain.value, now)
    old.gain.gain.linearRampToValueAtTime(0, now + SWITCH_FADE_S)
    old.source.stop(now + SWITCH_FADE_S)
  } else if (old) {
    stopLoop() // the old one ended meanwhile
  }
  source.start(now, offset)
  current = { source, gain, startedAt: now - offset, length: buf.duration, from, token: mine }
  return true
}

export function stopLoop() {
  token++
  const c = current
  current = null
  if (c) {
    c.source.onended = null
    try {
      c.source.stop()
    } catch {
      /* already stopped */
    }
  }
}

/** Loop off while it plays: finish this pass, then stop. */
export function finishLoop() {
  if (current) current.source.loop = false
}

export const looping = () => current !== null

/** Where the loop is, in track seconds (for the playhead). */
export function loopTime(): number | null {
  if (!current) return null
  const t = audioContext().currentTime - current.startedAt
  return current.from + (current.source.loop ? t % current.length : Math.min(t, current.length))
}
