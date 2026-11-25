// Runs inside Telegram's WebView and in a plain browser (preview mode).
// Outside Telegram the SDK script may be missing entirely, so every tg
// access is guarded (v1 1:1).

export interface TgWebApp {
  initData: string;
  themeParams?: { bg_color?: string; [k: string]: unknown };
  HapticFeedback?: { impactOccurred(kind: string): void };
  ready?: () => void;
  expand?: () => void;
  onEvent?: (event: string, cb: () => void) => void;
  setHeaderColor?: (color: string) => void;
  setBackgroundColor?: (color: string) => void;
  downloadFile?: (
    params: { url: string; file_name: string },
    cb: (accepted: boolean) => void,
  ) => void;
  openTelegramLink?: (url: string) => void;
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TgWebApp };
  }
}

export const tg: TgWebApp | null =
  typeof window !== "undefined" && window.Telegram
    ? window.Telegram.WebApp ?? null
    : null;

/** True inside Telegram with a non-empty initData (auth available). */
export const inTG = !!(tg && tg.initData && tg.initData.length);

// Test seam: the client unit tests run in node, where the SDK is absent.
let testInitData: string | null = null;
export function setTestInitData(v: string | null): void {
  testInitData = v;
}

/** The initData to attach to API calls, or null when there is none. */
export function activeInitData(): string | null {
  if (testInitData !== null) return testInitData;
  return inTG && tg ? tg.initData : null;
}

// ---- theme ------------------------------------------------------------------
// Brand palette in both modes; detect the user's surface only (see header).

export function detectDark(): boolean {
  if (
    inTG &&
    tg &&
    tg.themeParams &&
    typeof tg.themeParams.bg_color === "string"
  ) {
    const m = tg.themeParams.bg_color.match(/#?([0-9a-f]{6})/i);
    if (m) {
      const n = parseInt(m[1], 16);
      const lum =
        (0.2126 * (n >> 16 & 255) +
          0.7152 * (n >> 8 & 255) +
          0.0722 * (n & 255)) /
        255;
      return lum < 0.5;
    }
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function applyTheme(): void {
  const dark = detectDark();
  document.documentElement.classList.toggle("light", !dark);
  const bg = getComputedStyle(document.documentElement)
    .getPropertyValue("--bg")
    .trim();
  const meta = document.getElementById("meta-theme");
  if (meta) meta.setAttribute("content", bg);
  if (tg && tg.setHeaderColor) {
    try {
      tg.setHeaderColor(bg);
      tg.setBackgroundColor?.(bg);
    } catch {
      /* SDK quirk */
    }
  }
}

export function bootTg(): void {
  if (tg) {
    try {
      tg.ready?.();
      tg.expand?.();
    } catch {
      /* SDK quirk */
    }
  }
  applyTheme();
  tg?.onEvent?.("themeChanged", applyTheme);
  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", () => {
      if (!inTG) applyTheme();
    });
}

export function haptic(kind: string): void {
  try {
    if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred(kind);
  } catch {
    /* not in Telegram */
  }
}

/** Native save popup. Returns true when it was handed to Telegram. */
export function tgDownloadFile(
  url: string,
  fileName: string,
  onResult: (accepted: boolean) => void,
): boolean {
  if (tg && typeof tg.downloadFile === "function") {
    try {
      tg.downloadFile({ url, file_name: fileName }, onResult);
      return true;
    } catch {
      return false;
    }
  }
  return false;
}

/** Open an https://t.me/... link through the SDK when possible. */
export function tgOpenTelegramLink(url: string): boolean {
  if (tg && typeof tg.openTelegramLink === "function") {
    try {
      tg.openTelegramLink(url);
      return true;
    } catch {
      return false;
    }
  }
  return false;
}
