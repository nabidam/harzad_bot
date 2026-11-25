import { useEffect, useRef } from "react";
import { COPY } from "../copy";
import type { RepeatMode } from "../queue";
import type { PlayerApi } from "../use-player";
import { Icon } from "../icons";
import { Cover } from "./cover";

function fmtClock(sec: number): string {
  if (!isFinite(sec) || sec < 0) sec = 0;
  const s = Math.floor(sec % 60);
  const m = Math.floor(sec / 60);
  return m + ":" + String(s).padStart(2, "0");
}

function repeatIcon(mode: RepeatMode): string {
  return mode === "one" ? "repeat-1" : "repeat";
}

export function NowPlaying({ p }: { p: PlayerApi }) {
  const xRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    xRef.current?.focus({ preventScroll: true });
  }, []);

  const t = p.track;
  const seekable = !!t && p.duration > 0;
  const sub =
    p.mode === "preview"
      ? COPY.previewBtn
      : p.streamDegraded
        ? COPY.streamDegraded
        : t
          ? COPY.playerFullTrack
          : "";

  return (
    <>
      <div
        className="sheet-backdrop np-backdrop"
        id="np-backdrop"
        onClick={p.closeNowPlaying}
      />
      <div
        className="sheet np"
        id="now-playing"
        role="dialog"
        aria-modal="true"
        aria-labelledby="np-title"
      >
        <div className="grab" aria-hidden="true"></div>
        <button
          className="sheet-x"
          id="np-x"
          aria-label="Close"
          onClick={p.closeNowPlaying}
          ref={xRef}
        >
          <Icon name="x" />
        </button>

        <div className="np-coverwrap">
          {t && (
            <div className="np-cover">
              <Cover track={t} sheet />
            </div>
          )}
        </div>

        <div className="np-twrap">
          <h3 id="np-title">{t ? t.title : ""}</h3>
          <div id="np-artist">{t ? t.artist || "" : ""}</div>
          {sub && <div className="np-sub num" id="np-mode">{sub}</div>}
        </div>

        <input
          className="seek"
          id="np-seek"
          type="range"
          min={0}
          max={seekable ? Math.floor(p.duration) : 0}
          step={1}
          value={seekable ? Math.min(Math.floor(p.position), Math.floor(p.duration)) : 0}
          disabled={!seekable}
          aria-label="Seek"
          onChange={(e) => p.seekTo(Number(e.target.value))}
        />
        <div className="np-times num" aria-hidden="true">
          <span id="np-cur">{fmtClock(p.position)}</span>
          <span id="np-dur">{fmtClock(p.duration)}</span>
        </div>

        <div className="np-transport" id="np-transport">
          <button
            className="np-btn ghosty"
            id="np-shuffle"
            aria-label={COPY.shuffle}
            aria-pressed={p.shuffle}
            data-on={p.shuffle ? "true" : "false"}
            onClick={p.toggleShuffle}
          >
            <Icon name="shuffle" className={p.shuffle ? "on" : undefined} />
          </button>
          <button className="np-btn" id="np-prev" aria-label="Previous" onClick={p.prev}>
            <Icon name="player-prev" />
          </button>
          <button
            className="np-btn primary"
            id="np-toggle"
            aria-label={p.playing ? "Pause" : "Play"}
            onClick={p.toggle}
          >
            <Icon name={p.playing ? "player-pause" : "player-play"} />
          </button>
          <button className="np-btn" id="np-next" aria-label="Next" onClick={p.next}>
            <Icon name="player-next" />
          </button>
          <button
            className="np-btn ghosty"
            id="np-repeat"
            aria-label={
              p.repeat === "off" ? COPY.repeatOff :
              p.repeat === "all" ? COPY.repeatAll : COPY.repeatOne
            }
            data-repeat={p.repeat}
            onClick={p.cycleRepeat}
          >
            <Icon name={repeatIcon(p.repeat)} className={p.repeat !== "off" ? "on" : undefined} />
          </button>
        </div>

        {p.queueSize > 0 && (
          <div className="np-queue num" id="np-queue">
            {p.queueSize} {p.queueSize === 1 ? "track" : "tracks"}
            {p.shuffle ? " · " + COPY.shuffle : ""}
          </div>
        )}
      </div>
    </>
  );
}
