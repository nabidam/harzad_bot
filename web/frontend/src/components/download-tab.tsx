import { useCallback, useEffect, useRef, useState } from "react";
import { api, jobAlive } from "../api/client";
import type { Job, User } from "../api/types";
import { COPY, STATE_LABEL, fmtElapsed } from "../copy";
import {
  buildOptions,
  isYoutubeUrl,
  loadTarget,
  saveTarget,
  TARGETS,
  type Target,
} from "../download-options";
import { Icon } from "../icons";
import { haptic, inTG, tgDownloadFile, tgOpenTelegramLink } from "../theme";

/** Chip labels per target (the order is TARGETS': 1080 → default). */
const TARGET_LABELS: Record<Target, string> = {
  video_1080: COPY.targetVideo1080,
  video_720: COPY.targetVideo720,
  video_480: COPY.targetVideo480,
  video_360: COPY.targetVideo360,
  audio_best: COPY.targetAudio,
  default: COPY.targetDefault,
};

export interface DownloadTabProps {
  user: User | null;
  authed: boolean;
  toast: (msg: string) => void;
}

export function DownloadTab({ user, authed, toast }: DownloadTabProps) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [taskLabel, setTaskLabel] = useState(STATE_LABEL.starting);
  const [elapsed, setElapsed] = useState(0);
  const [formError, setFormError] = useState<string | null>(null);
  const [result, setResult] = useState<Job | null>(null);
  // Download options (v2 §4.3): the last-used target survives reloads;
  // the chip row only shows for YouTube URLs.
  const [target, setTarget] = useState<Target>(() => loadTarget());
  const [activeTarget, setActiveTarget] = useState<Target | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // v1 startElapsed: 1s tick, visible after 5s.
  useEffect(() => {
    if (!busy) return;
    const startAt = Date.now();
    setElapsed(0);
    const t = window.setInterval(() => {
      const s = Math.floor((Date.now() - startAt) / 1000);
      if (s >= 5) setElapsed(s);
    }, 1000);
    return () => window.clearInterval(t);
  }, [busy]);

  useEffect(
    () => () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
    },
    [],
  );

  const poll = useCallback(async (id: string) => {
    let job: Job;
    try {
      job = await api<Job>("/api/jobs/" + id);
    } catch {
      return; // pruned job — v1: silent, taskcard keeps its last state
    }
    if (job.state === "done") {
      setBusy(false);
      haptic("success");
      setResult(job);
    } else if (job.state === "error") {
      setBusy(false);
      setFormError(job.error || COPY.failed);
      haptic("error");
    } else {
      setTaskLabel(STATE_LABEL[job.state] || job.state);
      pollTimer.current = setTimeout(() => poll(id), 2000);
    }
  }, []);

  const startDownload = useCallback(async () => {
    const u = url.trim();
    if (!u) return;
    setFormError(null);
    setResult(null);
    setBusy(true);
    setTaskLabel(STATE_LABEL.starting);
    // v2 §4.3: YouTube-only quality options; remembered in localStorage.
    const opts = buildOptions(u, target);
    setActiveTarget(opts ? target : null);
    try {
      const job = await api<Job>("/api/jobs", opts ? { url: u, options: opts } : { url: u });
      poll(job.id);
    } catch (e) {
      setBusy(false);
      setFormError(e instanceof Error ? e.message : String(e));
      haptic("error");
    }
  }, [url, target, poll]);

  const saveFile = useCallback(async () => {
    const job = result;
    if (!job) return;
    if (!(await jobAlive(job.id))) {
      setFormError(COPY.expired);
      haptic("error");
      return;
    }
    haptic("light");
    if (inTG && job.file_url) {
      const handed = tgDownloadFile(
        job.file_url,
        job.file_name || COPY.saveFallbackName,
        (accepted) => {
          if (!accepted) toast(COPY.downloadCancelled);
        },
      );
      if (!handed) window.open(job.file_url, "_blank");
    } else if (job.file_url) {
      window.open(job.file_url, "_blank");
    }
  }, [result, toast]);

  const openLink = useCallback(async (e: React.MouseEvent) => {
    e.preventDefault();
    const job = result;
    if (!job) return;
    if (!(await jobAlive(job.id))) {
      setFormError(COPY.expired);
      haptic("error");
      return;
    }
    if (job.file_url) window.open(job.file_url, "_blank");
  }, [result]);

  const openChat = useCallback(() => {
    haptic("light");
    if (!tgOpenTelegramLink(COPY.botLink)) window.open(COPY.botLink, "_blank");
  }, []);

  return (
    <>
      <div className="hero">
        <h1 id="greet">{user ? "Hi, " + (user.first_name || "") : COPY.greet}</h1>
        <p>{COPY.hero}</p>
      </div>

      {result && (
        <div className="card" id="result">
          <div className="result-head">
            <span className="task-ico ok" id="result-ico">
              <Icon name="circle-check" />
            </span>
            <div className="task-body">
              <div className="task-label" id="result-title">
                {result.title || COPY.resultFallbackTitle}
              </div>
              <div className="task-label result-meta num" id="result-meta">
                {[result.file_name, result.file_size_mb ? result.file_size_mb + " MB" : null]
                  .filter(Boolean)
                  .join("  ·  ")}
              </div>
            </div>
          </div>
          <button className="btn primary" id="save-btn" onClick={saveFile}>
            <Icon name="cloud-download" />{" " + COPY.saveToDevice}
          </button>
          <div className="row2">
            {result.file_url && (
              <a
                className="btn ghost"
                id="open-link"
                href={result.file_url}
                target="_blank"
                rel="noopener"
                onClick={openLink}
              >
                <Icon name="link" />{" " + COPY.downloadViaLink}
              </a>
            )}
            <button className="btn ghost" id="open-chat" onClick={openChat}>
              <Icon name="message" />{" " + COPY.openInChat}
            </button>
          </div>
          <div className="hint" id="result-hint">
            {result.file_id ? COPY.resultChatHint : COPY.resultAppHint}
          </div>
        </div>
      )}

      {busy && (
        <div className="card" id="taskcard">
          <div className="task-row">
            <span className="task-ico" id="task-ico">
              <Icon name="loader-2" className="spin" />
            </span>
            <div className="task-body">
              <div className="task-label" id="task-label">
                {taskLabel}
                {activeTarget && (
                  <span className="task-target" id="task-target">
                    {TARGET_LABELS[activeTarget]}
                  </span>
                )}
              </div>
              {elapsed >= 5 && (
                <div className="task-elapsed num" id="task-elapsed">
                  <span id="task-elapsed-ico">
                    <Icon name="clock" />
                  </span>
                  <span id="task-sec">{fmtElapsed(elapsed)}</span>&nbsp;elapsed
                </div>
              )}
            </div>
          </div>
          <div className="bar" aria-hidden="true"><span className="bar-fill"></span></div>
        </div>
      )}

      <div className="card">
        <div className="urlrow">
          <span id="url-ico">
            <Icon name="link" />
          </span>
          <input
            type="text"
            id="url"
            placeholder={COPY.urlPlaceholder}
            inputMode="url"
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            value={url}
            disabled={busy}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") startDownload();
            }}
          />
        </div>
        <div className="platforms">YouTube &middot; Instagram &middot; Pinterest &middot; TikTok &middot; Spotify</div>
        {isYoutubeUrl(url.trim()) && (
          <div className="chips" id="quality-chips" role="group" aria-label={COPY.dlQuality}>
            {TARGETS.map((t) => (
              <button
                key={t}
                type="button"
                className={"chip" + (target === t ? " active" : "")}
                data-target={t}
                aria-pressed={target === t}
                disabled={busy}
                onClick={() => {
                  setTarget(t);
                  saveTarget(t);
                  haptic("light");
                }}
              >
                {TARGET_LABELS[t]}
              </button>
            ))}
          </div>
        )}
        <button className="btn primary" id="dl-btn" disabled={busy} onClick={startDownload}>Download</button>
        {formError && (
          <div className="form-err" id="dl-err">
            <span id="dl-err-ico">
              <Icon name="alert-circle" />
            </span>
            <span id="dl-err-text">{formError}</span>
          </div>
        )}
        {formError && (
          <button className="btn ghost" id="retry-btn" style={{ marginTop: 8 }} onClick={startDownload}>Try again</button>
        )}
        <div className="hint" id="mode-hint" hidden={authed ? !!user : true}>
          {COPY.modeHint}
        </div>
      </div>
    </>
  );
}
