/** Low-latency, polyphonic one-shot playback for the drum pads (Web Audio). */

let ctx: AudioContext | null = null
const buffers = new Map<string, Promise<AudioBuffer>>()
interface Voice {
  src: AudioBufferSourceNode
  gain: GainNode
}
const chokeGroups = new Map<string, Set<Voice>>()
const CHOKE_FADE_S = 0.005 // a choked sound fades this fast instead of clicking off

export function audioContext(): AudioContext {
  ctx ??= new AudioContext({ latencyHint: 'interactive' })
  return ctx
}

export function load(url: string): Promise<AudioBuffer> {
  let p = buffers.get(url)
  if (!p) {
    p = fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`failed to load ${url}`)
        return r.arrayBuffer()
      })
      .then((data) => audioContext().decodeAudioData(data))
    p.catch(() => buffers.delete(url))
    buffers.set(url, p)
  }
  return p
}

/** Decode ahead of time so the first hit is instant. */
export function preload(urls: string[]) {
  for (const u of urls) void load(u).catch(() => undefined)
}

/**
 * Trigger a sample. Hits overlap freely, except within a choke group: like an MPC
 * mute group, a new hit cuts off whatever of its group is still ringing (a closed hat
 * cuts an open hat; with "choke all", every pad cuts every other).
 */
export async function trigger(url: string, choke?: string, gain = 1) {
  const ac = audioContext()
  if (ac.state === 'suspended') await ac.resume()
  const buffer = await load(url)
  if (choke) {
    const group = chokeGroups.get(choke) ?? new Set()
    const now = ac.currentTime
    for (const v of group) {
      v.gain.gain.cancelScheduledValues(now)
      v.gain.gain.setValueAtTime(v.gain.gain.value, now)
      v.gain.gain.linearRampToValueAtTime(0, now + CHOKE_FADE_S)
      try {
        v.src.stop(now + CHOKE_FADE_S)
      } catch {
        /* already ended */
      }
    }
    group.clear()
    chokeGroups.set(choke, group)
  }
  const src = ac.createBufferSource()
  src.buffer = buffer
  const g = ac.createGain()
  g.gain.value = gain
  src.connect(g).connect(ac.destination)
  if (choke) {
    const group = chokeGroups.get(choke)!
    const voice = { src, gain: g }
    group.add(voice)
    src.onended = () => group.delete(voice)
  }
  src.start()
}

export function chokeGroupFor(category: string): string | undefined {
  return category === 'hihat' || category === 'open_hat' ? 'hats' : undefined
}
