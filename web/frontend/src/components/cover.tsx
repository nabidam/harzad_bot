import { useState } from "react";
import { coverSrc } from "../api/client";
import type { Track } from "../api/types";

/** v1 coverNode, 1:1 — img or letter tile; a dead cover URL degrades to
 * the letter tile (v1 replaced the node; here a failed flag does it). */
export function Cover({ track, sheet }: { track: Track; sheet?: boolean }) {
  const [failed, setFailed] = useState(false);
  const src = !failed ? coverSrc(track) : null;
  if (src) {
    return (
      <img
        className="cover"
        alt=""
        loading={sheet ? undefined : "lazy"}
        src={src}
        onError={() => setFailed(true)}
      />
    );
  }
  const letter = ((track.artist || track.title || "?").trim()[0] || "?").toUpperCase();
  return <div className="letter">{letter}</div>;
}
