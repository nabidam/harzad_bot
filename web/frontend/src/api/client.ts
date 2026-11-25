// The ONLY fetch site in the app (v2 spec §5). Every API call goes through
// api(); initData is injected here and nowhere else — the 2026-11-24 401
// regression (library calls sent without initData) is structurally
// impossible. GETs carry initData in the query string, POSTs in the JSON
// body, exactly as web/app.py's _auth() accepts.
import { activeInitData } from "../theme";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

/** Append initData to a GET url (covers, library pages, resend status). */
export function withInit(url: string): string {
  const init = activeInitData();
  if (!init) return url;
  return url + (url.includes("?") ? "&" : "?") + "initData=" + encodeURIComponent(init);
}

export async function api<T = unknown>(
  path: string,
  body?: Record<string, unknown>,
): Promise<T> {
  const isPost = body !== undefined;
  const init = activeInitData();
  const res = await fetch(path, {
    method: isPost ? "POST" : "GET",
    headers: isPost ? { "Content-Type": "application/json" } : {},
    body: isPost
      ? JSON.stringify({ ...body, initData: init ?? "" })
      : undefined,
  });
  let data: { error?: string } = {};
  try {
    data = (await res.json()) as { error?: string };
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) throw new ApiError(data.error || "HTTP " + res.status, res.status);
  return data as T;
}

// Jobs are pruned after a day; a file link from a pruned job 404s.
// Check liveness before offering the save, and say so honestly.
export async function jobAlive(id: string): Promise<boolean> {
  try {
    await api("/api/jobs/" + id);
    return true;
  } catch {
    return false;
  }
}

/** Cover proxy URL — the DB row's own cover (never client-supplied). */
export function coverSrc(track: {
  track_key: string;
  has_cover: boolean;
}): string | null {
  if (!track.has_cover) return null;
  return withInit("/api/library/cover/" + encodeURIComponent(track.track_key));
}
