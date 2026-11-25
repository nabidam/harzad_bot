// The ONE audio element of the app (v2 spec §5.1: single HTMLAudioElement
// singleton, module scope, outlives re-renders). It plays both sources:
//   - "stream": the full track via the server's Range proxy
//     (/api/library/track/{key}/stream?initData=...) — the <audio> element
//     seeks transparently (the browser's Range GETs pass through the proxy)
//   - "preview": the 30s catalog MP3 (the v1 source, kept for rows without
//     a file_id and as the honest fallback when Telegram purged one)
//
// React mirrors this via subscribePlayer(); all state changes go through
// the module so the track sheet, the mini player and the Now Playing
// sheet can never disagree.
import { haptic } from "./theme";

export type SourceKind = "stream" | "preview";

export type PlayerEvent =
  | { type: "time"; time: number; duration: number }
  | { type: "playing"; playing: boolean }
  | { type: "ended" }
  | { type: "error"; source: SourceKind; stale: boolean };

type Listener = (e: PlayerEvent) => void;
const listeners = new Set<Listener>();

let audio: HTMLAudioElement | null = null;
let source: SourceKind | null = null;
let ticking = false;
// A play token: every playSource() bumps it. An `error` event for a src
// that has since been replaced (queue hop) must not trigger the preview
// fallback for the NEW track — the listener marks such events `stale`.
let token = 0;
let srcToken = 0;

function el(): HTMLAudioElement {
  if (!audio) {
    audio = new Audio();
    audio.preload = "auto";
    audio.addEventListener("timeupdate", onTime);
    audio.addEventListener("play", () => emit({ type: "playing", playing: true }));
    audio.addEventListener("playing", () => emit({ type: "playing", playing: true }));
    audio.addEventListener("pause", () =>
      emit({ type: "playing", playing: false }),
    );
    audio.addEventListener("ended", () => emit({ type: "ended" }));
    audio.addEventListener("error", () => {
      if (source) emit({ type: "error", source, stale: srcToken !== token });
    });
  }
  return audio;
}

// timeupdate fires ~4x/s; batch to one emit per frame (spec §5.1).
function onTime(): void {
  if (ticking || !audio) return;
  ticking = true;
  requestAnimationFrame(() => {
    ticking = false;
    if (audio) emit({ type: "time", time: audio.currentTime, duration: audio.duration || 0 });
  });
}

function emit(e: PlayerEvent): void {
  listeners.forEach((l) => l(e));
}

export function subscribePlayer(cb: Listener): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

/** Start playing `url` fresh. `source` labels it for the error fallback
 * (the stream 404 "no-source" must degrade to the preview, not dead-end). */
export function playSource(url: string, sourceKind: SourceKind): void {
  const a = el();
  source = sourceKind;
  token += 1;
  srcToken = token;
  a.loop = false; // the queue layer (repeat "one") re-arms this
  a.src = url;
  a.currentTime = 0;
  a.play().catch(() => {
    /* autoplay refused (no gesture) or a 404: the error event / the
       caller's catch surfaces the honest state */
  });
}

export function pause(): void {
  if (audio) audio.pause();
}

export function resume(): Promise<void> {
  return audio ? audio.play().catch(() => undefined) : Promise.resolve();
}

export function seekTo(sec: number): void {
  if (audio && isFinite(sec)) audio.currentTime = Math.max(0, sec);
}

export function stop(): void {
  if (audio) {
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
  }
  source = null;
  clearMediaSession();
}

export function isPlaying(): boolean {
  return !!audio && !audio.paused;
}

export function currentTime(): number {
  return audio ? audio.currentTime : 0;
}

export function currentDuration(): number {
  return audio && isFinite(audio.duration) ? audio.duration : 0;
}

export function currentSource(): SourceKind | null {
  return source;
}

/** Repeat "one" rides the native loop (no queue hop). */
export function setLoop(on: boolean): void {
  if (audio) audio.loop = on;
}

// --- Media Session (lock-screen / media keys where the webview allows) ----
//
// Only the METADATA lives here; the action handlers are wired once by the
// hook (use-player.ts) and must survive track changes — so setting new
// metadata never touches them.

export function setMediaMetadata(
  meta: { title: string; artist: string; cover: string | null } | null,
): void {
  if (typeof navigator === "undefined" || !("mediaSession" in navigator)) return;
  try {
    const ms = navigator.mediaSession;
    if (meta) {
      ms.metadata = new MediaMetadata({
        title: meta.title,
        artist: meta.artist,
        album: "Harzad",
        artwork: meta.cover ? [{ src: meta.cover, sizes: "512x512" }] : [],
      });
    } else {
      ms.metadata = null;
    }
    ms.playbackState = isPlaying() ? "playing" : "paused";
  } catch {
    /* the webview may expose a partial API — playback must never break */
  }
}

export function setMediaPlaying(playing: boolean): void {
  if (typeof navigator === "undefined" || !("mediaSession" in navigator)) return;
  try {
    (navigator.mediaSession as { playbackState: string }).playbackState =
      playing ? "playing" : "paused";
  } catch {
    /* see above */
  }
}

export function clearMediaSession(): void {
  setMediaMetadata(null);
}

/** A haptic tick on transport changes (v1 parity on interactive stops). */
export function hapticPlay(): void {
  haptic("light");
}
