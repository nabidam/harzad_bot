import { useEffect, useState } from "react";
import { coverSrc } from "../api/client";
import type { Track } from "../api/types";
import { Icon } from "../icons";

export interface PlayerBarProps {
  track: Track | null;
  playing: boolean;
  onToggle: () => void;
}

/** v1 playerbar, 1:1 (the cover img has no class in v1 either). */
export function PlayerBar({ track, playing, onToggle }: PlayerBarProps) {
  const [imgFailed, setImgFailed] = useState(false);
  const src = track ? coverSrc(track) : null;
  useEffect(() => {
    setImgFailed(false);
  }, [track]);
  return (
    <div className="playerbar" id="playerbar" hidden={!track}>
      <div className="pb-cover" id="pb-cover">
        {src && !imgFailed ? (
          <img alt="" src={src} onError={() => setImgFailed(true)} />
        ) : (
          <Icon name="music" />
        )}
      </div>
      <div className="pb-body">
        <div className="pb-title" id="player-title">{track ? track.title : ""}</div>
        <div className="pb-sub">30s preview</div>
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
