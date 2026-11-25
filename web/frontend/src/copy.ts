// The app's entire user-facing copy (canonical deck, v1 spec §14).
// Pinned by tests (tests/test_08_endpoints.py, tests/test_29_miniapp_music.py)
// — keep these strings byte-identical.

export const COPY = {
  // -- download tab -----------------------------------------------------------
  greet: "Hi there",
  hero: "Paste a link to grab media, or open your saved tracks.",
  urlPlaceholder: "Paste a media link to download it.",
  download: "Download",
  failed: "This download failed. Check the link or try again.",
  expired: "This download link may have expired. Generate a new one to continue.",
  tryAgain: "Try again",
  saveToDevice: "Save to device",
  downloadViaLink: "Download via link",
  openInChat: "Open in chat",
  resultFallbackTitle: "video",
  saveFallbackName: "video.mp4",
  resultChatHint: "A copy was also sent to your chat with the bot.",
  resultAppHint: "This file is delivered by the app. Save it from here.",
  downloadCancelled: "Download cancelled.",
  modeHint:
    "Browser mode. Open the app from the Telegram menu button for chat delivery and the native save popup.",
  botLink: "https://t.me/harzad_bot",

  // -- chat --------------------------------------------------------------------
  chatWelcome:
    "Ask me a question. I answer each message independently for now, so include any context I need in each message.",
  chatUnavailablePrefix: "Chat is unavailable right now: ",
  emptyReply: "(empty)",
  chatPlaceholder: "Message Harzad…",

  // -- music ---------------------------------------------------------------------
  searchPlaceholder: "Track title or artist…",
  allDays: "All days",
  loadMore: "Load more",
  previewBtn: "30s preview",
  getAgain: "Get again",
  spotify: "Spotify",
  remove: "Remove",
  yesRemove: "Yes, remove",
  keepTrack: "Keep track",
  empty: "No music saved yet.",
  emptyDesc: "Your tracks will appear here automatically when you receive music.",
  oftenEmpty: "Often played",
  oftenEmptyDesc:
    "Your frequently requested tracks will appear here as you listen again.",
  noResultsTitle: "No matching tracks",
  unavailable: "My music is temporarily unavailable.",
  unavailableDesc: "Your library data could not be loaded. Please try again shortly.",
  noAuth: "Open the app from Telegram to see your library.",
  noAuthDesc: "Your music stays private: the library only loads inside Telegram.",
  dayEmpty: "No tracks on this day.",
  dayEmptyDesc: "This day's list is empty.",

  // -- sheet actions -----------------------------------------------------------
  sendingToChat: "Sending to your chat…",
  sent: "Sent to your chat",
  reDownloading: "Re-downloading — I'll send it to your chat when ready.",
  loadingPreview: "Loading preview…",
  previewFailed: "Preview playback failed.",
  working: "Working…",
  removed: "Track removed from My music.",
  removeConfirm: (title: string) =>
    'Remove "' + title + '" from your library? It does not delete previously sent messages.',

  // -- sheet stats ---------------------------------------------------------------
  statDuration: "Duration",
  statRequested: "Requested",
  statLastRequested: "Last requested",
  statAdded: "Added",
  once: "once",
};

/** Job states, the bot's status vocabulary (spec §12.2). */
export const STATE_LABEL: Record<string, string> = {
  starting: "Checking link…",
  extracting: "Getting media details…",
  downloading: "Downloading…",
  sending: "Sending to chat…",
  done: "Ready",
};

/** Re-download ladder states (utils/resend.py). */
export const RESEND_LABEL: Record<string, string> = {
  resolving: "Looking up the track…",
  downloading: "Downloading…",
  sending: "Sending to chat…",
};

export function fmtElapsed(s: number): string {
  if (s < 60) return s + "s";
  return Math.floor(s / 60) + "m " + String(s % 60).padStart(2, "0") + "s";
}

export function trackWord(n: number): string {
  return n === 1 ? "track" : "tracks";
}

/** No tracks matched <q> — spec-deck tail is pinned. */
export function noResultsDesc(q: string): string {
  return 'No tracks matched "' + q + '". Try another title or artist name.';
}

/** Mirrors utils.library.relative_time (the API timestamps carry the
 * server's UTC offset, so the device timezone parses the same instant). */
export function relTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return m + " min ago";
  const h = Math.floor(m / 60);
  if (h < 24) return h + " h ago";
  return Math.floor(h / 24) + " d ago";
}
