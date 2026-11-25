// The player queue (v2 spec §5.2): the current context (library view /
// playlist) becomes the queue; prev/next in order; shuffle = seeded
// permutation; repeat modes off/all/one.
//
// Pure logic, no DOM — vitest runs in a node environment, so this is the
// surface the queue tests pin (spec §8).
import type { Track } from "./api/types";

export type RepeatMode = "off" | "all" | "one";

export interface Queue {
  tracks: Track[];
  /** Playback order: indices into `tracks` (sequential when shuffle off). */
  order: number[];
  /** Position inside `order` (not into `tracks`). */
  pos: number;
  shuffle: boolean;
  repeat: RepeatMode;
}

/** Deterministic PRNG (mulberry32) — shuffle orders are seeded, so tests
 * pin exact permutations and a given seed always replays the same order. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Seeded Fisher–Yates permutation of [0..n-1]. */
export function shuffledOrder(n: number, seed: number): number[] {
  const out = Array.from({ length: n }, (_, i) => i);
  const rnd = mulberry32(seed);
  for (let i = n - 1; i > 0; i--) {
    const j = Math.floor(rnd() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/** The context becomes the queue; `start` is the tapped track's index. */
export function makeQueue(tracks: Track[], start: number): Queue {
  const pos = Math.max(0, Math.min(start, tracks.length - 1));
  return {
    tracks,
    order: Array.from({ length: tracks.length }, (_, i) => i),
    pos,
    shuffle: false,
    repeat: "off",
  };
}

export function currentTrack(q: Queue): Track | null {
  if (!q.tracks.length) return null;
  return q.tracks[q.order[q.pos]] ?? null;
}

/** Next playback position; -1 when playback should STOP (end of the
 * order with repeat off). Repeat "one" never calls this — the audio
 * element's loop attribute replays the track natively. */
export function nextPos(q: Queue): number {
  if (!q.tracks.length) return -1;
  if (q.pos < q.order.length - 1) return q.pos + 1;
  return q.repeat === "all" ? 0 : -1;
}

/** Previous playback position (no wrap — standard player behavior). */
export function prevPos(q: Queue): number {
  if (!q.tracks.length) return -1;
  return Math.max(0, q.pos - 1);
}

/** Shuffle on: a seeded permutation that keeps the CURRENT track at its
 * current position (the user's tap point does not jump). Shuffle off:
 * back to sequential, same guarantee. */
export function withShuffle(q: Queue, on: boolean, seed: number): Queue {
  if (!q.tracks.length) return { ...q, shuffle: on };
  const cur = q.order[q.pos];
  const order = on ? shuffledOrder(q.tracks.length, seed) :
    Array.from({ length: q.tracks.length }, (_, i) => i);
  return { ...q, shuffle: on, order, pos: order.indexOf(cur) };
}

/** off -> all -> one -> off. */
export function cycleRepeat(repeat: RepeatMode): RepeatMode {
  return repeat === "off" ? "all" : repeat === "all" ? "one" : "off";
}
