import type { CSSProperties } from "react";
import type { Track } from "../api/types";
import { COPY } from "../copy";
import { Cover } from "./cover";
import { Icon } from "../icons";
import { StateBox } from "./state-box";

export interface MusicTabProps {
  m: import("../music").MusicApi;
  searchInput: string;
  onSearchInput: (v: string) => void;
}

function Tile({
  track,
  index,
  onOpen,
}: {
  track: Track;
  index: number;
  onOpen: (t: Track) => void;
}) {
  return (
    <div
      className="tile"
      style={{ "--i": Math.min(index, 12) } as CSSProperties}
      onClick={() => {
        import("../theme").then((t) => t.haptic("light"));
        onOpen(track);
      }}
    >
      <div className="coverwrap">
        <Cover track={track} />
        {track.duration ? <span className="dur num">{track.duration}</span> : null}
      </div>
      <div className="tt">{track.title}</div>
      <div className="ta">{track.artist || ""}</div>
    </div>
  );
}

/** v1 #tab-download sibling: the music section (v1 markup 1:1). */
export function MusicTab({ m, searchInput, onSearchInput }: MusicTabProps) {
  const chip = (on: boolean) => "chip" + (on ? " active" : "");
  return (
    <>
      <div className="music-head">
        <h2>My music</h2>
        <div className="music-count num" id="music-count">{m.count}</div>
      </div>

      {!m.noAuth && (
        <div className="chipbar">
          <div className="chips" id="music-chips" role="group" aria-label="Library views">
            <button className={chip(m.view === "latest")} aria-pressed={m.view === "latest"} data-view="latest">Latest</button>
            <button className={chip(m.view === "daily")} aria-pressed={m.view === "daily"} data-view="daily">Daily</button>
            <button className={chip(m.view === "search")} aria-pressed={m.view === "search"} data-view="search">Search</button>
            <button className={chip(m.view === "often")} aria-pressed={m.view === "often"} data-view="often">Often</button>
            <button className={chip(m.view === "browse")} aria-pressed={m.view === "browse"} data-view="browse">Browse</button>
          </div>
          <div className="chips" id="music-sorts" role="group" aria-label="Browse order" hidden={m.view !== "browse"}>
            <button className={chip(m.sort === "recent")} aria-pressed={m.sort === "recent"} data-sort="recent">Recently requested</button>
            <button className={chip(m.sort === "added")} aria-pressed={m.sort === "added"} data-sort="added">Recently added</button>
            <button className={chip(m.sort === "az")} aria-pressed={m.sort === "az"} data-sort="az">Title A-Z</button>
          </div>
          <form
            className="searchrow"
            id="search-box"
            hidden={m.view !== "search"}
            onSubmit={(e) => {
              e.preventDefault();
              m.onSearch(searchInput);
            }}
          >
            <div className="urlrow">
              <span id="search-ico">
                <Icon name="search" />
              </span>
              <input
                type="text"
                id="search-input"
                placeholder={COPY.searchPlaceholder}
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                value={searchInput}
                onChange={(e) => onSearchInput(e.target.value)}
              />
            </div>
            <button className="go" type="submit" aria-label="Search">
              <span id="go-ico">
                <Icon name="search" />
              </span>
            </button>
          </form>
        </div>
      )}

      <div className="field-err" id="search-err" hidden={!m.searchErr}>{m.searchErr}</div>
      <button
        className="chip back"
        id="day-back"
        hidden={!(m.view === "daily" && m.day)}
        onClick={m.onDayBack}
      >
        <Icon name="chevron-left" />{" " + COPY.allDays}
      </button>

      <div id="day-list">
        {m.view === "daily" && !m.day && (m.days || []).map((d) => (
          <button className="dayrow" type="button" key={d.day} onClick={() => m.onOpenDay(d.day)}>
            <span className="dico">
              <Icon name="calendar" />
            </span>
            <div>
              <div className="dl">{d.label}</div>
              <div className="ds num">{d.count} {d.count === 1 ? "track" : "tracks"}</div>
            </div>
            <span className="chev">
              <Icon name="chevron-right" />
            </span>
          </button>
        ))}
      </div>

      <div id="track-grid" className="grid" key={m.gridNonce}>
        {m.skeleton
          ? Array.from({ length: 6 }, (_, i) => (
              <div className="skel" key={i}>
                <div className="sk coverwrap"></div>
                <div className="sk sk-l1"></div>
                <div className="sk sk-l2"></div>
              </div>
            ))
          : m.tracks.map((t, i) => (
              <Tile key={t.track_key} track={t} index={i} onOpen={m.onOpenTile} />
            ))}
      </div>

      <button className="btn ghost" id="load-more" hidden={!m.loadMoreVisible} onClick={m.onLoadMore}>
        <Icon name="download" />{" " + COPY.loadMore}
      </button>

      {m.stateBox && (
        <StateBox kind={m.stateBox.kind} extra={m.stateBox.extra} onRetry={m.onRetry} />
      )}
    </>
  );
}
