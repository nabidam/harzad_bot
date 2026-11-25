import { useEffect, useState } from "react";
import { coverSrc } from "../api/client";
import type { Track } from "../api/types";
import { COPY } from "../copy";
import { Icon } from "../icons";

export interface PlayerBarProps {
  track: Track | null;
  mode: "stream" | "preview" | null;
  playing: boolean;
  onToggle: () => void;
  onOpen: () => void;
}

/** v1 playerbar, extended (v2 §5.2): the sub line tells stream vs
 * 30s preview apart; the body opens the Now Playing sheet. */
export function PlayerBar({ track, mode, playing, onToggle, onOpen }: PlayerBarProps) {
  const [imgFailed, setImgFailed] = useState(false);
  const src = track ? coverSrc(track) : null;
  useEffect(() => {
    setImgFailed(false);
  }, [track]);
  const sub = track
    ? mode === "preview"
      ? COPY.previewBtn
      : COPY.playerFullTrack
    : "";
  return (
    <div className="playerbar" id="playerbar" hidden={!track}>
      <div
        className="pb-main"
        role="button"
        tabIndex={0}
        aria-label="Open now playing"
        onClick={onOpen}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onOpen();
          }
        }}
      >
        <div className="pb-cover" id="pb-cover">
          {src && !imgFailed ? (
            <img alt="" src={src} onError={() => setImgFailed(true)} />
          ) : (
            <Icon name="music" />
          )}
        </div>
        <div className="pb-body">
          <div className="pb-title" id="player-title">{track ? track.title : ""}</div>
          <div className="pb-sub" id="player-mode">{sub}</div>
        </div>
      </div>
      <button
        className="pb-btn"
        id="player-toggle"
        aria-label={playing ? "Pause" : "Play"}
        onClick={onToggle}
      >
        <Icon name={playing ? "player-pause" : "player-play"} />
      </button>
    </div>
  );
}
