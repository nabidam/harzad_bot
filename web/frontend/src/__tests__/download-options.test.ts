// Download-options mapping (v2 spec §8): which URLs get the option row,
// how a chip selection becomes the `options` body, and the localStorage
// round-trip. download-options.ts is pure — node environment.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  buildOptions,
  DEFAULT_TARGET,
  isYoutubeUrl,
  loadTarget,
  LS_KEY,
  saveTarget,
  TARGETS,
} from "../download-options";

describe("isYoutubeUrl", () => {
  it("accepts every server-authorized host", () => {
    for (const u of [
      "https://youtube.com/watch?v=abc",
      "https://www.youtube.com/watch?v=abc",
      "https://m.youtube.com/watch?v=abc",
      "https://music.youtube.com/watch?v=abc",
      "https://youtu.be/abc",
    ]) {
      expect(isYoutubeUrl(u), u).toBe(true);
    }
  });

  it("is case-insensitive on the host", () => {
    expect(isYoutubeUrl("https://WWW.YouTube.com/watch?v=x")).toBe(true);
  });

  it("rejects lookalikes and other providers", () => {
    for (const u of [
      "https://notyoutube.com/watch?v=x",
      "https://youtube.com.evil.io/x",
      "https://soundcloud.com/x",
      "https://vimeo.com/123",
      "",
      "not a url",
    ]) {
      expect(isYoutubeUrl(u), u).toBe(false);
    }
  });
});

describe("buildOptions", () => {
  const YT = "https://youtube.com/watch?v=abc";

  it("sends {target} for a valid YouTube selection", () => {
    expect(buildOptions(YT, "video_1080")).toEqual({ target: "video_1080" });
    expect(buildOptions("https://youtu.be/abc", "audio_best")).toEqual({
      target: "audio_best",
    });
    // "default" is a real server target (the v1 behavior), sent explicitly
    expect(buildOptions(YT, "default")).toEqual({ target: "default" });
  });

  it("sends nothing for non-YouTube URLs (the server would 400)", () => {
    expect(buildOptions("https://soundcloud.com/x", "video_1080")).toBe(
      undefined,
    );
  });

  it("sends nothing for a target the client does not recognize", () => {
    expect(buildOptions(YT, "video_1440")).toBe(undefined);
    expect(buildOptions(YT, "")).toBe(undefined);
    expect(buildOptions(YT, "DEFAULT")).toBe(undefined);
  });
});

describe("localStorage round-trip", () => {
  const store = new Map<string, string>();
  const fakeStorage = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => void store.clear(),
  };

  beforeEach(() => {
    store.clear();
    (globalThis as Record<string, unknown>).localStorage = fakeStorage;
  });

  afterEach(() => {
    delete (globalThis as Record<string, unknown>).localStorage;
  });

  it("defaults when nothing is stored", () => {
    expect(loadTarget()).toBe(DEFAULT_TARGET);
  });

  it("round-trips a saved target", () => {
    saveTarget("audio_best");
    expect(loadTarget()).toBe("audio_best");
    expect(store.get(LS_KEY)).toBe("audio_best");
  });

  it("rejects a corrupted value and falls back to default", () => {
    store.set(LS_KEY, "video_1440");
    expect(loadTarget()).toBe(DEFAULT_TARGET);
    store.set(LS_KEY, "");
    expect(loadTarget()).toBe(DEFAULT_TARGET);
  });

  it("survives missing localStorage (private mode)", () => {
    delete (globalThis as Record<string, unknown>).localStorage;
    expect(loadTarget()).toBe(DEFAULT_TARGET);
    expect(() => saveTarget("video_360")).not.toThrow();
  });
});

describe("TARGETS", () => {
  it("matches the server's target set exactly (web/app.py / utils/youtube_dl.py)", () => {
    expect([...TARGETS].sort()).toEqual(
      [
        "audio_best",
        "default",
        "video_1080",
        "video_360",
        "video_480",
        "video_720",
      ].sort(),
    );
  });
});
