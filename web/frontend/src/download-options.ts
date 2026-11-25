// Download options (v2 spec §4.3 / §5.3): the quality chips send their
// selection as `options.target` on POST /api/jobs. The option row shows
// ONLY for YouTube URLs — same host rule as the server (web/app.py
// `_is_youtube_url`); the last-used target is remembered per browser in
// localStorage (per spec "remembered per provider in localStorage" —
// there is exactly one options-providing provider).
//
// Pure + node-testable (spec §8 "download-options mapping").
export const TARGETS = [
  "video_1080",
  "video_720",
  "video_480",
  "video_360",
  "audio_best",
  "default",
] as const;

export type Target = (typeof TARGETS)[number];

export const DEFAULT_TARGET: Target = "default";

export const LS_KEY = "harzad_dl_target";

/** The YouTube host set, byte-for-byte the server's authority rule. */
const YOUTUBE_HOSTS = new Set([
  "youtube.com",
  "www.youtube.com",
  "m.youtube.com",
  "music.youtube.com",
  "youtu.be",
]);

export function isYoutubeUrl(url: string): boolean {
  let host = "";
  try {
    host = new URL(url, "https://x").hostname.toLowerCase();
  } catch {
    return false;
  }
  return YOUTUBE_HOSTS.has(host);
}

/** Validated localStorage read: anything not in TARGETS (corruption, an
 * old build's value) falls back to the default — never sends garbage. */
export function loadTarget(): Target {
  try {
    const v = localStorage.getItem(LS_KEY);
    if (v && (TARGETS as readonly string[]).includes(v)) return v as Target;
  } catch {
    /* storage unavailable (private mode) — default */
  }
  return DEFAULT_TARGET;
}

export function saveTarget(t: Target): void {
  try {
    localStorage.setItem(LS_KEY, t);
  } catch {
    /* non-fatal */
  }
}

/** The `options` body for POST /api/jobs, or undefined (v1 request shape)
 * when there is nothing to send: non-YouTube URL, or a target the client
 * does not recognize (defensive — the server would 400 it). */
export function buildOptions(
  url: string,
  target: string,
): { target: string } | undefined {
  if (!isYoutubeUrl(url)) return undefined;
  if (!(TARGETS as readonly string[]).includes(target)) return undefined;
  return { target };
}
