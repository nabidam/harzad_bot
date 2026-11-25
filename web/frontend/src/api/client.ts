// The ONLY fetch site in the app (v2 spec §5). Every API call goes through
// api(); initData is injected here and nowhere else — the 2026-11-24 401
// regression (library calls sent without initData) is structurally
// impossible. GETs carry initData in the query string, everything else
// (POST/PATCH/DELETE) in the JSON body — exactly as web/app.py's _auth()
// accepts (it tries the body first, then the query).
import { activeInitData } from "../theme";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export type ApiMethod = "POST" | "PATCH" | "DELETE";

/** Append initData to a GET url (covers, library pages, resend status). */
export function withInit(url: string): string {
  const init = activeInitData();
  if (!init) return url;
  return url + (url.includes("?") ? "&" : "?") + "initData=" + encodeURIComponent(init);
}

export async function api<T = unknown>(
  path: string,
  body?: Record<string, unknown>,
  method: ApiMethod = "POST",
): Promise<T> {
  const init = activeInitData();
  const isGet = body === undefined;
  const res = await fetch(
    // GETs: the caller applied withInit() to the path (pinned contract,
    // see client.test.ts) — never applied twice here.
    path,
    isGet
      ? { method: "GET" }
      : {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...body, initData: init ?? "" }),
        },
  );
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
