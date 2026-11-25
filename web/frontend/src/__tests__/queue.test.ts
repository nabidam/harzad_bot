// Queue operations (v2 spec §8): shuffle seed determinism, repeat modes,
// next/prev at boundaries. queue.ts is pure — no DOM, node environment.
import { describe, expect, it } from "vitest";
import type { Track } from "../api/types";
import {
  cycleRepeat,
  currentTrack,
  makeQueue,
  mulberry32,
  nextPos,
  prevPos,
  shuffledOrder,
  withShuffle,
} from "../queue";

function tracks(n: number): Track[] {
  return Array.from({ length: n }, (_, i) => ({
    track_key: "k" + i,
    title: "T" + i,
    artist: "A" + i,
    duration_sec: 100 + i,
    duration: "1:40",
    has_cover: false,
    has_stream: true,
    spotify_url: null,
    request_count: 1,
  }));
}

describe("makeQueue", () => {
  it("starts at the tapped track, sequential order", () => {
    const q = makeQueue(tracks(4), 2);
    expect(q.order).toEqual([0, 1, 2, 3]);
    expect(q.pos).toBe(2);
    expect(currentTrack(q)?.track_key).toBe("k2");
    expect(q.shuffle).toBe(false);
    expect(q.repeat).toBe("off");
  });

  it("clamps the start index", () => {
    expect(makeQueue(tracks(3), 9).pos).toBe(2);
    expect(makeQueue(tracks(3), -5).pos).toBe(0);
  });

  it("handles an empty context", () => {
    const q = makeQueue([], 0);
    expect(currentTrack(q)).toBeNull();
    expect(nextPos(q)).toBe(-1);
    expect(prevPos(q)).toBe(-1);
  });
});

describe("next/prev at boundaries", () => {
  it("steps sequentially", () => {
    let q = makeQueue(tracks(4), 0);
    q.pos = nextPos(q);
    expect(q.pos).toBe(1);
    q.pos = nextPos(q);
    expect(q.pos).toBe(2);
  });

  it("stops at the end with repeat off", () => {
    const q = makeQueue(tracks(4), 3);
    expect(nextPos(q)).toBe(-1);
  });

  it("wraps to the start with repeat all", () => {
    const q = makeQueue(tracks(4), 3);
    q.repeat = "all";
    expect(nextPos(q)).toBe(0);
  });

  it("prev never wraps to the end", () => {
    const q = makeQueue(tracks(4), 0);
    expect(prevPos(q)).toBe(0);
    q.pos = 2;
    expect(prevPos(q)).toBe(1);
  });
});

describe("repeat cycle", () => {
  it("off -> all -> one -> off", () => {
    expect(cycleRepeat("off")).toBe("all");
    expect(cycleRepeat("all")).toBe("one");
    expect(cycleRepeat("one")).toBe("off");
  });
});

describe("shuffledOrder / withShuffle", () => {
  it("is a valid permutation", () => {
    const o = shuffledOrder(10, 42);
    expect([...o].sort((a, b) => a - b)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  });

  it("is deterministic per seed", () => {
    expect(shuffledOrder(12, 1234)).toEqual(shuffledOrder(12, 1234));
  });

  it("differs across seeds", () => {
    expect(shuffledOrder(12, 1)).not.toEqual(shuffledOrder(12, 2));
  });

  it("mulberry32 is deterministic and in [0,1)", () => {
    const a = mulberry32(7);
    const b = mulberry32(7);
    for (let i = 0; i < 20; i++) {
      const x = a();
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x).toBeLessThan(1);
      expect(x).toBe(b());
    }
  });

  it("shuffle keeps the current track under the playhead", () => {
    const q = makeQueue(tracks(8), 3);
    const s = withShuffle(q, true, 99);
    expect(s.shuffle).toBe(true);
    expect(s.order[s.pos]).toBe(3);
    // a real permutation happened (n=8, seed 99 does not sort)
    expect(s.order).not.toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
  });

  it("shuffle off restores sequential order, same playhead", () => {
    let q = makeQueue(tracks(8), 3);
    q = withShuffle(q, true, 99);
    q = withShuffle(q, false, 1);
    expect(q.shuffle).toBe(false);
    expect(q.order).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
    expect(q.pos).toBe(3);
  });
});
