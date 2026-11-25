import { useEffect, useRef, useState } from "react";
import type { Playlist, Track } from "../api/types";
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
  onPlay: () => void; // full track (visible only when canPlay)
  onPreview: () => void;
  onRemoveClick: () => void;
  onKeep: () => void;
  onRemoveYes: () => void;
  canPlay: boolean; // the row still carries a Telegram file_id
  // tags + playlists (v2 §5.3): the track's tag row and the picker
  playlists: Playlist[];
  onTagAssign: (name: string) => void;
  onTagUnlink: (name: string) => void;
  onPlAdd: (plId: number) => void;
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

  // --- tags + playlists (v2 §5.3) --------------------------------------------
  const [tagInput, setTagInput] = useState("");
  const [tagArm, setTagArm] = useState<string | null>(null);
  const [plPickOpen, setPlPickOpen] = useState(false);
  const armTimer = useRef<number | null>(null);
  const t = p.track;

  // a new track opens: the tag input, the armed × and the picker reset
  useEffect(() => {
    setTagInput("");
    setTagArm(null);
    setPlPickOpen(false);
  }, [t.track_key]);
  useEffect(() => {
    return () => {
      if (armTimer.current) window.clearTimeout(armTimer.current);
    };
  }, []);

  const armTag = (n: string) => {
    setTagArm(n);
    if (armTimer.current) window.clearTimeout(armTimer.current);
    armTimer.current = window.setTimeout(() => setTagArm(null), 2600);
  };
  const tagCount = t.tags?.length ?? 0;
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

        {/* tags (v2 §5.3): the track's chip row + inline add. The × is
            two-tap (arm, then confirm) — no confirm dialogs in a sheet. */}
        <div className="sheet-tags" id="sheet-tags" hidden={p.removeConfirm}>
          <div className="section-label">{COPY.tagsLabel}</div>
          <div className="sheet-tagrow">
            {(t.tags ?? []).map((n) => (
              <span
                key={n}
                className={"mini-tag" + (tagArm === n ? " arm" : "")}
                data-tag={n}
              >
                {n}
                <button
                  type="button"
                  aria-label={COPY.deleteTagQ(n)}
                  onClick={() =>
                    tagArm === n ? p.onTagUnlink(n) : armTag(n)
                  }
                >
                  <Icon name="x" />
                </button>
              </span>
            ))}
            {tagCount === 0 && (
              <span className="mini-tag empty">{COPY.noTags}</span>
            )}
            <form
              className="tagadd"
              id="sheet-tag-add"
              onSubmit={(e) => {
                e.preventDefault();
                const v = tagInput.trim();
                if (!v) return;
                setTagInput("");
                p.onTagAssign(v);
              }}
            >
              <input
                type="text"
                value={tagInput}
                placeholder={COPY.tagPlaceholder}
                maxLength={40}
                autoComplete="off"
                aria-label={COPY.tagAdd}
                onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") setTagInput("");
                }}
              />
              <button type="submit" className="go" aria-label={COPY.tagAdd}>
                <span>
                  <Icon name="plus" />
                </span>
              </button>
            </form>
          </div>
        </div>

        {/* add to playlist (v2 §5.3) */}
        {p.playlists.length > 0 && (
          <div className="sheet-pl" id="sheet-playlists" hidden={p.removeConfirm}>
            <button
              type="button"
              className="section-link"
              id="sheet-pl-toggle"
              aria-expanded={plPickOpen}
              onClick={() => setPlPickOpen(!plPickOpen)}
            >
              {COPY.addToPlaylist}
              <Icon
                name="chevron-right"
                className={plPickOpen ? "flip" : undefined}
              />
            </button>
            {plPickOpen && (
              <div className="sheet-pl-list">
                {p.playlists.map((pl) => (
                  <button
                    type="button"
                    className="plpick"
                    key={pl.id}
                    data-pl={pl.id}
                    onClick={() => {
                      p.onPlAdd(pl.id);
                      setPlPickOpen(false);
                    }}
                  >
                    <span className="plpick-name">{pl.name}</span>
                    <span className="num">{pl.count}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        <div id="sheet-actions" hidden={p.removeConfirm}>
          {p.canPlay ? (
            <button className="btn primary" id="sheet-play" onClick={p.onPlay}>
              <Icon name="player-play" />{" " + COPY.play}
            </button>
          ) : (
            <>
              <button className="btn primary" id="sheet-resend" disabled={p.busy} onClick={p.onResend}>
                <Icon name="refresh" />{" " + COPY.getAgain}
              </button>
              <div className="hint sheet-hint" id="sheet-stream-hint">
                {COPY.streamUnavailableHint}
              </div>
            </>
          )}
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
