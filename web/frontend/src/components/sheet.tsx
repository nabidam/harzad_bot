import { useEffect, useRef } from "react";
import type { Track } from "../api/types";
import { COPY, relTime } from "../copy";
import { Cover } from "./cover";
import { Icon } from "../icons";

export interface SheetStatus {
  text: string;
  cls: "busy" | "done" | "error";
}

export interface SheetProps {
  track: Track;
  open: boolean; // .open class on backdrop + sheet (the slide animation)
  status: SheetStatus | null;
  busy: boolean; // resend in flight (disables resend/preview/remove)
  removeConfirm: boolean;
  rmBusy: boolean;
  onClose: () => void;
  onResend: () => void;
  onPreview: () => void;
  onRemoveClick: () => void;
  onKeep: () => void;
  onRemoveYes: () => void;
}

function statusIcon(cls: SheetStatus["cls"] | undefined): string | null {
  if (cls === "done") return "circle-check";
  if (cls === "error") return "alert-circle";
  if (cls === "busy") return "loader-2";
  return null;
}

/** v1 #sheet (body level), 1:1. */
export function Sheet(p: SheetProps) {
  const xRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    xRef.current?.focus({ preventScroll: true });
  }, []);

  const t = p.track;
  const stats: Array<[string, string]> = [
    [COPY.statDuration, t.duration || ""],
    [
      COPY.statRequested,
      t.request_count <= 1 ? COPY.once : t.request_count + " times",
    ],
    [COPY.statLastRequested, relTime(t.last_requested)],
    [COPY.statAdded, relTime(t.added_on)],
  ].filter((s) => s[1]) as Array<[string, string]>;

  return (
    <>
      <div
        className={"sheet-backdrop" + (p.open ? " open" : "")}
        id="sheet-backdrop"
        onClick={p.onClose}
      />
      <div
        className={"sheet" + (p.open ? " open" : "")}
        id="sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sheet-title"
      >
        <div className="grab" aria-hidden="true"></div>
        <button
          className="sheet-x"
          id="sheet-x"
          aria-label="Close"
          onClick={p.onClose}
          ref={xRef}
        >
          <Icon name="x" />
        </button>
        <div className="sheet-head">
          <div className="sheet-cover" id="sheet-coverwrap">
            <Cover track={t} sheet />
          </div>
          <div className="twrap">
            <h3 id="sheet-title">{t.title}</h3>
            <div id="sheet-artist">{t.artist || ""}</div>
            <div id="sheet-album" hidden={!t.album}>{t.album || ""}</div>
          </div>
        </div>
        <div className="stats" id="sheet-meta">
          {stats.map(([label, value]) => (
            <div className="stat" key={label}>
              <b>{label}</b>
              <span>{value}</span>
            </div>
          ))}
        </div>
        <div id="sheet-actions" hidden={p.removeConfirm}>
          <button className="btn primary" id="sheet-resend" disabled={p.busy} onClick={p.onResend}>
            <Icon name="refresh" />{" " + COPY.getAgain}
          </button>
          <div className="minirow">
            <button className="btn ghost" id="sheet-preview" disabled={p.busy} onClick={p.onPreview}>
              <Icon name="player-play" />{" " + COPY.previewBtn}
            </button>
            {t.spotify_url && (
              <a className="btn ghost" id="sheet-spotify" href={t.spotify_url} target="_blank" rel="noopener">
                <Icon name="external-link" />{" " + COPY.spotify}
              </a>
            )}
            <button className="btn ghost danger" id="sheet-remove" disabled={p.busy} onClick={p.onRemoveClick}>
              <Icon name="trash" />{" " + COPY.remove}
            </button>
          </div>
        </div>
        <div className="sheet-confirm" id="sheet-confirm" hidden={!p.removeConfirm}>
          <p id="sheet-confirm-text">{COPY.removeConfirm(t.title)}</p>
          <div className="minirow">
            <button className="btn danger-solid" id="sheet-rm-yes" disabled={p.rmBusy} onClick={p.onRemoveYes}>
              <Icon name="trash" />{" " + COPY.yesRemove}
            </button>
            <button className="btn ghost" id="sheet-rm-keep" onClick={p.onKeep}>Keep track</button>
          </div>
        </div>
        <div
          className={"sheet-status" + (p.status ? " " + p.status.cls : "")}
          id="sheet-status"
          hidden={!p.status}
        >
          {p.status && (
            <>
              {statusIcon(p.status.cls) && <Icon name={statusIcon(p.status.cls)!} />}
              <span>{p.status.text}</span>
            </>
          )}
        </div>
      </div>
    </>
  );
}
