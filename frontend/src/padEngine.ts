/** Low-latency, polyphonic one-shot playback for the drum pads (Web Audio). */

let ctx: AudioContext | null = null
const buffers = new Map<string, Promise<AudioBuffer>>()
const chokeGroups = new Map<string, Set<AudioBufferSourceNode>>()

function audioContext(): AudioContext {
  ctx ??= new AudioContext({ latencyHint: 'interactive' })
  return ctx
}

function load(url: string): Promise<AudioBuffer> {
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
 * mute group, a closed hat cuts off a ringing open hat.
 */
export async function trigger(url: string, choke?: string, gain = 1) {
  const ac = audioContext()
  if (ac.state === 'suspended') await ac.resume()
  const buffer = await load(url)
  if (choke) {
    const group = chokeGroups.get(choke) ?? new Set()
    for (const s of group) {
      try {
        s.stop()
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
    group.add(src)
    src.onended = () => group.delete(src)
  }
  src.start()
}

export function chokeGroupFor(category: string): string | undefined {
  return category === 'hihat' || category === 'open_hat' ? 'hats' : undefined
}
