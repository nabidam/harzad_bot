import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { Track } from "../api/types";
import { COPY } from "../copy";
import { haptic } from "../theme";
import { Cover } from "./cover";
import { Icon } from "../icons";
import { StateBox } from "./state-box";

export interface MusicTabProps {
  m: import("../music").MusicApi;
  searchInput: string;
  onSearchInput: (v: string) => void;
}

const LONG_PRESS_MS = 420;
const DRAG_CANCEL_PX = 24;

function Tile({
  track,
  index,
  onOpen,
  selMode,
  selected,
  onEnterSel,
  onToggleSel,
}: {
  track: Track;
  index: number;
  onOpen: (t: Track, index: number) => void;
  selMode: boolean;
  selected: boolean;
  onEnterSel: (key: string) => void;
  onToggleSel: (key: string) => void;
}) {
  const pressTimer = useRef<number | null>(null);
  const pressStart = useRef<{ x: number; y: number } | null>(null);
  const firedRef = useRef(false);

  const cancelPress = () => {
    if (pressTimer.current !== null) {
      window.clearTimeout(pressTimer.current);
      pressTimer.current = null;
    }
    pressStart.current = null;
  };
  useEffect(() => cancelPress, []);

  return (
    <div
      className={"tile" + (selected ? " sel" : "")}
      style={{ "--i": Math.min(index, 12) } as CSSProperties}
      onPointerDown={(e) => {
        if (selMode) return; // in selection mode a tap toggles, never re-presses
        if (e.pointerType === "mouse" && e.button !== 0) return;
        pressStart.current = { x: e.clientX, y: e.clientY };
        firedRef.current = false;
        pressTimer.current = window.setTimeout(() => {
          pressTimer.current = null;
          firedRef.current = true; // the following click must not open the sheet
          onEnterSel(track.track_key);
        }, LONG_PRESS_MS);
      }}
      onPointerMove={(e) => {
        const s = pressStart.current;
        if (!s) return;
        // a drag is a scroll, not a press
        if (Math.abs(e.clientX - s.x) + Math.abs(e.clientY - s.y) > DRAG_CANCEL_PX)
          cancelPress();
      }}
      onPointerUp={cancelPress}
      onPointerLeave={cancelPress}
      onPointerCancel={cancelPress}
      onContextMenu={(e) => e.preventDefault()}
      onClick={() => {
        if (firedRef.current) {
          firedRef.current = false;
          return;
        }
        if (selMode) {
          haptic("light");
          onToggleSel(track.track_key);
          return;
        }
        haptic("light");
        onOpen(track, index);
      }}
    >
      {selMode && (
        <span className="tsel" aria-hidden="true">
          {selected && <Icon name="circle-check" />}
        </span>
      )}
      <div className="coverwrap">
        <Cover track={track} />
        {track.duration ? <span className="dur num">{track.duration}</span> : null}
      </div>
      <div className="tt">{track.title}</div>
      <div className="ta">{track.artist || ""}</div>
    </div>
  );
}

/** The selection action bar (v2 §5.2): batch ops on the grid selection.
 * Library context -> "Add to playlist…"; open playlist -> "Remove". Sits
 * above the tab bar, and above the player bar when it is visible. */
function SelBar({ m }: { m: import("../music").MusicApi }) {
  return (
    <div
      className={"selbar" + (m.selPlayerVisible ? " above-pb" : "")}
      id="sel-bar"
      role="toolbar"
      aria-label={COPY.selCount(m.selKeys.length)}
    >
      {m.selPlPick && (
        <div className="selbar-pick" id="sel-picker">
          {m.plList.length === 0 ? (
            <span className="selbar-none">{COPY.playlistsEmpty}</span>
          ) : (
            m.plList.map((pl) => (
              <button
                key={pl.id}
                type="button"
                className="plpick"
                data-pl={pl.id}
                onClick={() => void m.onSelAddToPlaylist(pl.id)}
              >
                <span className="plpick-name">{pl.name}</span>
                <span className="num">{pl.count}</span>
              </button>
            ))
          )}
        </div>
      )}
      <div className="selbar-row">
        <span className="selcount num">{COPY.selCount(m.selKeys.length)}</span>
        {m.plOpen ? (
          <button
            type="button"
            className="btn danger-solid"
            id="sel-remove"
            onClick={() => void m.onSelRemoveFromPlaylist()}
          >
            <Icon name="trash" /> {COPY.remove}
          </button>
        ) : (
          <button
            type="button"
            className="btn primary"
            id="sel-add"
            onClick={m.onSelOpenPicker}
          >
            <Icon name="plus" /> {COPY.addToPlaylist}
          </button>
        )}
        <button
          type="button"
          className="btn ghost"
          id="sel-cancel"
          onClick={m.onSelCancel}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

/** The tag filter row (v2 §5.3): A→Z chips with link counts, tap to
 * filter, × to delete (two-tap arm), + for inline create. */
function TagRow({ m }: { m: import("../music").MusicApi }) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [armed, setArmed] = useState<number | null>(null);
  const armedTimer = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (armedTimer.current) window.clearTimeout(armedTimer.current);
    };
  }, []);

  const arm = (id: number) => {
    setArmed(id);
    if (armedTimer.current) window.clearTimeout(armedTimer.current);
    armedTimer.current = window.setTimeout(() => setArmed(null), 2600);
  };

  return (
    <div
      className="chips tagrow"
      id="tag-chips"
      role="group"
      aria-label={COPY.tagsLabel}
    >
      {m.tags.map((t) => (
        <span
          key={t.id}
          className={"tagchip" + (m.activeTag === t.name ? " active" : "")}
          data-tag={t.name}
        >
          <button
            type="button"
            className="tagchip-f"
            aria-pressed={m.activeTag === t.name}
            onClick={() => m.onTagFilter(t.name)}
          >
            {t.name}
            <span className="tagcount num">{t.count}</span>
          </button>
          <button
            type="button"
            className={"tagchip-x" + (armed === t.id ? " arm" : "")}
            aria-label={COPY.deleteTagQ(t.name)}
            onClick={() => {
              if (armed === t.id) {
                setArmed(null);
                void m.onTagDelete(t.id, t.name);
              } else {
                arm(t.id);
              }
            }}
          >
            <Icon name="x" />
          </button>
        </span>
      ))}
      {adding ? (
        <form
          className="tagadd"
          id="tag-add-form"
          onSubmit={(e) => {
            e.preventDefault();
            const n = name.trim();
            if (!n) return;
            setAdding(false);
            setName("");
            void m.onTagAdd(n);
          }}
        >
          <input
            autoFocus
            type="text"
            value={name}
            placeholder={COPY.tagPlaceholder}
            maxLength={40}
            autoComplete="off"
            aria-label={COPY.tagAdd}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setAdding(false);
                setName("");
              }
            }}
          />
          <button type="submit" className="go" aria-label={COPY.tagAdd}>
            <span>
              <Icon name="plus" />
            </span>
          </button>
        </form>
      ) : (
        <button
          type="button"
          className="chip"
          id="tag-add"
          onClick={() => setAdding(true)}
        >
          {COPY.tagAdd}
        </button>
      )}
    </div>
  );
}

/** The Playlists view (v2 §5.3): the list, then the open playlist's
 * paged grid. Add/remove from the track sheet; no drag-reorder (spec). */
function PlaylistsView({ m }: { m: import("../music").MusicApi }) {
  const [createName, setCreateName] = useState("");
  const [renameName, setRenameName] = useState("");

  const pl = m.plOpen;

  if (pl) {
    const first = m.plTracks[0];
    return (
      <div id="playlist-detail">
        <button
          className="chip back"
          id="pl-back"
          onClick={m.onPlBack}
        >
          <Icon name="chevron-left" />{" "}
          {COPY.playlistsView}
        </button>
        <div className="plhead">
          <div className="plhead-cover">
            {first ? <Cover track={first} /> : <Icon name="list" />}
          </div>
          <div className="plhead-body">
            {m.plRenameOpen ? (
              <form
                className="plrename"
                id="pl-rename-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  const n = renameName.trim();
                  if (!n) return;
                  setRenameName("");
                  void m.onPlRename(n);
                }}
              >
                <input
                  autoFocus
                  type="text"
                  value={renameName}
                  placeholder={COPY.namePlaceholder}
                  maxLength={120}
                  aria-label={COPY.renamePlaylist}
                  onChange={(e) => setRenameName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") m.onPlCloseRename();
                  }}
                />
                <button type="submit" className="btn primary">
                  {COPY.renamePlaylist}
                </button>
                <button
                  type="button"
                  className="btn ghost"
                  onClick={m.onPlCloseRename}
                >
                  Cancel
                </button>
              </form>
            ) : (
              <div className="plhead-name" id="pl-name">
                {pl.name}
              </div>
            )}
            {!m.plRenameOpen && (
              <div className="minirow plhead-actions">
                <button
                  className="btn ghost"
                  id="pl-rename"
                  onClick={() => {
                    setRenameName(pl.name);
                    m.onPlOpenRename();
                  }}
                >
                  {COPY.renamePlaylist}
                </button>
                <button
                  className="btn ghost danger"
                  id="pl-delete"
                  onClick={m.onPlDeleteClick}
                >
                  <Icon name="trash" /> {COPY.deletePlaylist}
                </button>
              </div>
            )}
          </div>
        </div>

        {m.plDelConfirm && (
          <div className="confirmrow" id="pl-confirm">
            <span>{COPY.deletePlaylistQ(pl.name)}</span>
            <div className="minirow">
              <button
                className="btn danger-solid"
                id="pl-delete-yes"
                onClick={() => void m.onPlDeleteYes()}
              >
                Yes
              </button>
              <button
                className="btn ghost"
                id="pl-delete-keep"
                onClick={m.onPlDeleteKeep}
              >
                Keep
              </button>
            </div>
          </div>
        )}
      </div>
    );
  }

  // the list
  return (
    <div id="playlist-list">
      <>
        {m.plCreateOpen ? (
            <form
              className="plcreate"
              id="pl-create-form"
              onSubmit={(e) => {
                e.preventDefault();
                const n = createName.trim();
                if (!n) return;
                setCreateName("");
                void m.onPlCreate(n);
              }}
            >
              <input
                autoFocus
                type="text"
                value={createName}
                placeholder={COPY.namePlaceholder}
                maxLength={120}
                aria-label={COPY.newPlaylist}
                onChange={(e) => setCreateName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") m.onPlCloseCreate();
                }}
              />
              <button type="submit" className="btn primary">
                Create
              </button>
              <button
                type="button"
                className="btn ghost"
                onClick={m.onPlCloseCreate}
              >
                Cancel
              </button>
            </form>
          ) : (
            <button
              className="btn primary"
              id="pl-new"
              onClick={m.onPlOpenCreate}
            >
              <Icon name="plus" /> {COPY.newPlaylist}
            </button>
          )}
          {m.plList.length > 0 && !m.plCreateOpen && (
            <div className="pllist">
              {m.plList.map((p) => (
                <button
                  className="dayrow plrow"
                  type="button"
                  key={p.id}
                  data-pl={p.id}
                  onClick={() => m.onOpenPlaylist(p.id)}
                >
                  <span className="dico">
                    <Icon name="list" />
                  </span>
                  <div>
                    <div className="dl">{p.name}</div>
                    <div className="ds num">
                      {p.count} {p.count === 1 ? "track" : "tracks"}
                    </div>
                  </div>
                  <span className="chev">
                    <Icon name="chevron-right" />
                  </span>
                </button>
              ))}
            </div>
          )}
        </>
    </div>
  );
}

/** v1 #tab-download sibling: the music section (v1 markup 1:1). */
export function MusicTab({ m, searchInput, onSearchInput }: MusicTabProps) {
  const chip = (on: boolean) => "chip" + (on ? " active" : "");
  const plView = m.view === "playlists";
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
            <button className={chip(plView)} aria-pressed={plView} data-view="playlists">{COPY.playlistsView}</button>
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
          {m.tags.length > 0 && !plView && (
            <TagRow m={m} />
          )}
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

      {plView && <PlaylistsView m={m} />}

      {plView && !m.plOpen && m.plStateBox && (
        <StateBox kind={m.plStateBox.kind} extra={m.plStateBox.extra} onRetry={m.onPlRetry} />
      )}

      {!plView && (
        <>
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
                  <Tile
                    key={t.track_key}
                    track={t}
                    index={i}
                    onOpen={m.onOpenTile}
                    selMode={m.selMode}
                    selected={m.selKeys.includes(t.track_key)}
                    onEnterSel={m.onSelEnter}
                    onToggleSel={m.onSelToggle}
                  />
                ))}
          </div>

          <button className="btn ghost" id="load-more" hidden={!m.loadMoreVisible} onClick={m.onLoadMore}>
            <Icon name="download" />{" " + COPY.loadMore}
          </button>

          {m.stateBox && (
            <StateBox kind={m.stateBox.kind} extra={m.stateBox.extra} onRetry={m.onRetry} />
          )}
        </>
      )}

      {plView && m.plOpen && (
        <>
          <div id="track-grid" className="grid" key={"pl" + m.gridNonce}>
            {m.plSkeleton
              ? Array.from({ length: 6 }, (_, i) => (
                  <div className="skel" key={i}>
                    <div className="sk coverwrap"></div>
                    <div className="sk sk-l1"></div>
                    <div className="sk sk-l2"></div>
                  </div>
                ))
              : m.plTracks.map((t, i) => (
                  <Tile
                    key={t.track_key}
                    track={t}
                    index={i}
                    onOpen={m.onOpenTile}
                    selMode={m.selMode}
                    selected={m.selKeys.includes(t.track_key)}
                    onEnterSel={m.onSelEnter}
                    onToggleSel={m.onSelToggle}
                  />
                ))}
          </div>

          <button
            className="btn ghost"
            id="pl-load-more"
            hidden={!m.plLoadMoreVisible}
            onClick={m.onPlLoadMore}
          >
            <Icon name="download" />{" " + COPY.loadMore}
          </button>

          {m.plStateBox && (
            <StateBox kind={m.plStateBox.kind} extra={m.plStateBox.extra} onRetry={m.onPlRetry} />
          )}
        </>
      )}

      {/* grid selection action bar (v2 §5.2): selMode is only reachable
          from a tile long-press, and context switches clear it */}
      {m.selMode && <SelBar m={m} />}
    </>
  );
}
