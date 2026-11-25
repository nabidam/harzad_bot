// The Mini App music library (v2 spec §12): the v1 `music` global object,
// the track sheet, and the 30s preview — as a single hook. v1 semantics are
// kept 1:1 (busy guard, page accumulation, error mapping, resend ladder,
// sheet animation) so the port is behavior-identical.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, withInit } from "./api/client";
import type {
  DayEntry,
  LibraryPage,
  Playlist,
  Tag,
  Track,
} from "./api/types";
import { COPY, RESEND_LABEL, trackWord } from "./copy";
import { haptic, inTG } from "./theme";
import type { PlayerApi } from "./use-player";
import type { SheetStatus } from "./components/sheet";
import type { StateKind } from "./components/state-box";

export type View =
  | "latest"
  | "daily"
  | "search"
  | "often"
  | "browse"
  | "playlists"; // client-side view (no server library call)
export type Sort = "recent" | "added" | "az";

interface MusicState {
  view: View;
  sort: Sort;
  query: string;
  day: string | null;
  activeTag: string | null; // v2 §4.2 ?tag= filter, applies to any view
  page: number;
  pages: number;
  total: number;
  days: DayEntry[] | null;
  allTracks: Track[];
  busy: boolean;
  entered: boolean;
}

const INITIAL: MusicState = {
  view: "latest",
  sort: "recent",
  query: "",
  day: null,
  activeTag: null,
  page: 1,
  pages: 1,
  total: 0,
  days: null,
  allTracks: [],
  busy: false,
  entered: false,
};

/** Playlist sub-state (v2 §5.3): the list, the open playlist's paged
 * tracks. Ref-backed like the music state (loaders read it synchronously). */
interface PlState {
  list: Playlist[];
  listBusy: boolean;
  listErr: boolean;
  openId: number | null;
  openName: string;
  page: number;
  pages: number;
  total: number;
  tracks: Track[];
  busy: boolean;
  err: boolean;
}

const PL_INITIAL: PlState = {
  list: [],
  listBusy: false,
  listErr: false,
  openId: null,
  openName: "",
  page: 1,
  pages: 1,
  total: 0,
  tracks: [],
  busy: false,
  err: false,
};

function musicUrl(page: number, m: MusicState): string {
  const p = new URLSearchParams({ view: m.view, page: String(page) });
  if (m.view === "browse") p.set("sort", m.sort);
  if (m.view === "search") p.set("q", m.query);
  if (m.view === "daily" && m.day) p.set("day", m.day);
  if (m.activeTag) p.set("tag", m.activeTag);
  return withInit("/api/library?" + p.toString());
}

// v1 renderCount, 1:1.
function renderCount(data: LibraryPage, m: MusicState): string {
  let text = "";
  if (m.view === "latest") text = data.total + " " + trackWord(data.total) + " saved";
  else if (m.view === "search") text = "Found " + data.total + " " + trackWord(data.total) + ".";
  else if (m.view === "often") text = "Often played · " + data.total + " " + trackWord(data.total);
  else if (m.view === "browse") text = data.total + " " + trackWord(data.total);
  else {
    const label = (data.days ?? m.days ?? []).find((d) => d.day === data.day)?.label;
    text = (label || (data.day || "")) + " · " + data.total + " " + trackWord(data.total);
  }
  if (data.pages > 1) text += "   Page " + data.page + " of " + data.pages;
  return text;
}

export interface MusicApi {
  entered: boolean;
  noAuth: boolean; // !inTG after first entry — chipbar hidden
  view: View;
  sort: Sort;
  days: DayEntry[] | null;
  day: string | null;
  count: string;
  tracks: Track[];
  skeleton: boolean;
  stateBox: { kind: StateKind; extra?: string } | null;
  searchErr: string;
  loadMoreVisible: boolean;
  gridNonce: number;
  onChip: (v: View) => void;
  onSort: (s: Sort) => void;
  onSearch: (q: string) => void;
  onOpenDay: (d: string) => void;
  onDayBack: () => void;
  onLoadMore: () => void;
  onOpenTile: (t: Track, index: number) => void;
  onRetry: () => void;
  // tags (v2 §5.3)
  tags: Tag[];
  activeTag: string | null;
  onTagFilter: (name: string) => void;
  onTagAdd: (name: string) => Promise<boolean>;
  onTagDelete: (id: number, name: string) => Promise<void>;
  // playlists (v2 §5.3)
  plList: Playlist[];
  plListErr: boolean;
  plOpen: { id: number; name: string } | null;
  plTracks: Track[];
  plLoadMoreVisible: boolean;
  plSkeleton: boolean;
  plStateBox: { kind: StateKind; extra?: string } | null;
  plCreateOpen: boolean;
  plRenameOpen: boolean;
  plDelConfirm: boolean;
  onPlBack: () => void;
  onPlCreate: (name: string) => Promise<void>;
  onPlRename: (name: string) => Promise<void>;
  onPlDeleteClick: () => void;
  onPlDeleteYes: () => Promise<void>;
  onPlDeleteKeep: () => void;
  onPlOpenCreate: () => void;
  onPlCloseCreate: () => void;
  onPlOpenRename: () => void;
  onPlCloseRename: () => void;
  onOpenPlaylist: (id: number) => void;
  onPlLoadMore: () => void;
  onPlRetry: () => void;
  sheet: {
    track: Track;
    open: boolean;
    status: SheetStatus | null;
    busy: boolean;
    removeConfirm: boolean;
    rmBusy: boolean;
  } | null;
  sheetClose: () => void;
  sheetResend: () => void;
  sheetPreview: () => void;
  sheetPlay: () => void; // full-track playback, queue = current context
  sheetRemoveClick: () => void;
  sheetKeep: () => void;
  sheetRemoveYes: () => void;
  sheetTagAssign: (name: string) => void;
  sheetTagUnlink: (name: string) => void;
  sheetPlAdd: (plId: number) => void;
}

export function useMusic(
  active: boolean,
  toast: (msg: string) => void,
  player: PlayerApi,
): MusicApi {
  // v1's mutable `music` object. The ref is the source of truth (loadMusic
  // must read the value set synchronously by the handler); state mirrors it
  // for rendering.
  const musicRef = useRef<MusicState>(INITIAL);
  const [, bump] = useState(0);
  const rerender = useCallback(() => bump((n) => n + 1), []);

  const [data, setData] = useState<LibraryPage | null>(null);
  const [skeleton, setSkeleton] = useState(false);
  const [stateBox, setStateBox] = useState<{ kind: StateKind; extra?: string } | null>(null);
  const [searchErr, setSearchErr] = useState("");
  const [gridNonce, setGridNonce] = useState(0);

  // --- sheet ---------------------------------------------------------------
  const [sheetTrack, setSheetTrack] = useState<Track | null>(null);
  const sheetTrackRef = useRef<Track | null>(null);
  const sheetIndexRef = useRef(0); // queue position of the sheet's track
  const [sheetOpen, setSheetOpen] = useState(false);
  const sheetOpenRef = useRef(false);
  const [sheetStatus, setSheetStatus] = useState<SheetStatus | null>(null);
  const [sheetBusy, setSheetBusy] = useState(false);
  const [sheetRemoveConfirm, setSheetRemoveConfirm] = useState(false);
  const [sheetRmBusy, setSheetRmBusy] = useState(false);
  const resendPollRef = useRef<number | null>(null);
  const playerRef = useRef(player);
  useEffect(() => {
    playerRef.current = player;
  }, [player]);

  useEffect(() => {
    sheetOpenRef.current = sheetOpen;
  }, [sheetOpen]);
  useEffect(() => {
    sheetTrackRef.current = sheetTrack;
  }, [sheetTrack]);
  useEffect(() => {
    return () => {
      if (resendPollRef.current) window.clearInterval(resendPollRef.current);
    };
  }, []);

  const clearResendPoll = useCallback(() => {
    if (resendPollRef.current) {
      window.clearInterval(resendPollRef.current);
      resendPollRef.current = null;
    }
  }, []);

  // --- tags (v2 §5.3) ----------------------------------------------------------

  const [tags, setTags] = useState<Tag[]>([]);

  const loadTags = useCallback(async () => {
    try {
      const r = await api<{ tags: Tag[] }>(withInit("/api/library/tags"));
      setTags(r.tags);
    } catch {
      // tags are an aid: a failure hides the row, it never breaks the grid
      setTags([]);
    }
  }, []);

  // --- loading ---------------------------------------------------------------

  const loadMusic = useCallback(
    async (reset: boolean) => {
      const m = musicRef.current;
      if (m.busy) return;
      m.busy = true;
      if (reset) {
        m.page = 1;
        m.allTracks = [];
      }
      try {
        const page = reset ? 1 : m.page + 1;
        const res = await api<LibraryPage>(musicUrl(page, m));
        m.page = res.page;
        m.pages = res.pages;
        m.total = res.total;
        if (res.days) m.days = res.days;
        setSearchErr("");
        setStateBox(null);
        setSkeleton(false);
        setData(res);
        // Daily, no day picked yet: the day list IS the view.
        if (m.view === "daily" && !m.day) {
          m.allTracks = [];
          if (!res.days || res.days.length === 0) setStateBox({ kind: "empty" });
          return;
        }
        if (res.total === 0) {
          m.allTracks = [];
          if (m.view === "often") setStateBox({ kind: "often-empty" });
          else if (m.view === "search") setStateBox({ kind: "no-results", extra: res.query });
          else if (m.view === "daily") setStateBox({ kind: "day-empty" });
          else setStateBox({ kind: "empty" });
          return;
        }
        m.allTracks = reset ? res.tracks.slice() : m.allTracks.concat(res.tracks);
        // v1 re-built the whole grid (every tile re-staggered) — remount via
        // the grid's key does the same.
        setGridNonce((n) => n + 1);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        const mm = musicRef.current;
        setData(null);
        setStateBox(null);
        setSkeleton(false);
        if (mm.view === "search" && /2 characters/.test(msg)) {
          setSearchErr(msg);
        } else if (/temporarily unavailable/.test(msg)) {
          setStateBox({ kind: "unavailable" });
        } else if (/Telegram|Unauthorized/.test(msg)) {
          setStateBox({ kind: "no-auth" });
        } else {
          setStateBox({ kind: "unavailable" });
        }
      } finally {
        musicRef.current.busy = false;
      }
    },
    [],
  );

  // v1 enterMusic: once, on first tab entry.
  useEffect(() => {
    if (!active) return;
    const m = musicRef.current;
    if (m.entered) return;
    m.entered = true;
    if (!inTG) {
      // the library endpoints require initData — no preview mode (personal data)
      setStateBox({ kind: "no-auth" });
      rerender();
      return;
    }
    setSkeleton(true);
    loadMusic(true);
    loadTags();
  }, [active, loadMusic, loadTags, rerender]);

  const onTagFilter = useCallback(
    (name: string) => {
      const m = musicRef.current;
      m.activeTag = m.activeTag === name ? null : name;
      if (m.view === "playlists") m.view = "latest"; // the filter applies to
      // the library views; a tap from the Playlists view starts in Latest
      haptic("light");
      rerender();
      loadMusic(true);
    },
    [loadMusic, rerender],
  );

  const onTagAdd = useCallback(
    async (name: string) => {
      const n = name.trim();
      if (!n) return false;
      try {
        const r = await api<{ id: number; name: string; exists?: boolean }>(
          "/api/library/tags",
          { name: n },
        );
        toast(r.exists ? COPY.tagExists : COPY.tagCreated);
        haptic("success");
        await loadTags();
        return true;
      } catch (e) {
        toast(e instanceof Error ? e.message : String(e));
        haptic("error");
        return false;
      }
    },
    [loadTags, toast],
  );

  const onTagDelete = useCallback(
    async (id: number, name: string) => {
      try {
        await api("/api/library/tags/" + id, {}, "DELETE");
        toast(COPY.tagDeleted);
        haptic("light");
        const m = musicRef.current;
        if (m.activeTag && m.activeTag.toLowerCase() === name.toLowerCase()) {
          m.activeTag = null;
        }
        await loadTags();
        loadMusic(true); // a filtered page may have changed
      } catch (e) {
        toast(e instanceof Error ? e.message : String(e));
        haptic("error");
      }
    },
    [loadMusic, loadTags, toast],
  );

  // --- playlists (v2 §5.3) -------------------------------------------------------

  const plRef = useRef<PlState>(PL_INITIAL);
  const [plCreateOpen, setPlCreateOpen] = useState(false);
  const [plRenameOpen, setPlRenameOpen] = useState(false);
  const [plDelConfirm, setPlDelConfirm] = useState(false);

  const loadPlaylists = useCallback(async () => {
    const pl = plRef.current;
    pl.listBusy = true;
    rerender();
    try {
      const r = await api<{ playlists: Playlist[] }>(
        withInit("/api/library/playlists"),
      );
      pl.list = r.playlists;
      pl.listErr = false;
    } catch {
      pl.listErr = true;
    } finally {
      pl.listBusy = false;
      rerender();
    }
  }, [rerender]);

  const loadPlTracks = useCallback(
    async (reset: boolean) => {
      const pl = plRef.current;
      if (pl.openId === null || pl.busy) return;
      pl.busy = true;
      if (reset) {
        pl.page = 1;
        pl.tracks = [];
      }
      rerender();
      try {
        const page = reset ? 1 : pl.page + 1;
        const r = await api<{
          page: number;
          pages: number;
          total: number;
          tracks: Track[];
        }>(
          withInit(
            "/api/library/playlists/" + pl.openId + "/tracks?page=" + page,
          ),
        );
        pl.page = r.page;
        pl.pages = r.pages;
        pl.total = r.total;
        pl.err = false;
        pl.tracks = reset ? r.tracks.slice() : pl.tracks.concat(r.tracks);
        setGridNonce((n) => n + 1);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        if (/Unknown playlist/.test(msg)) {
          // deleted under us — back to the list (the honest state)
          pl.openId = null;
          pl.openName = "";
          pl.tracks = [];
          void loadPlaylists(); // the list itself changed
        } else {
          pl.err = true;
        }
      } finally {
        pl.busy = false;
        rerender();
      }
    },
    [rerender],
  );

  const openPlaylist = useCallback(
    (id: number) => {
      const pl = plRef.current;
      const p = pl.list.find((x) => x.id === id);
      if (!p) return;
      haptic("light");
      pl.openId = id;
      pl.openName = p.name;
      pl.err = false;
      setPlCreateOpen(false);
      setPlRenameOpen(false);
      setPlDelConfirm(false);
      rerender();
      loadPlTracks(true);
    },
    [loadPlTracks, rerender],
  );

  const plBack = useCallback(() => {
    haptic("light");
    const pl = plRef.current;
    pl.openId = null;
    pl.openName = "";
    pl.tracks = [];
    pl.err = false;
    setPlCreateOpen(false);
    setPlRenameOpen(false);
    setPlDelConfirm(false);
    rerender();
    loadPlaylists(); // counts may have changed
  }, [loadPlaylists, rerender]);

  const plCreate = useCallback(
    async (name: string) => {
      const n = name.trim();
      if (!n) return;
      try {
        const r = await api<Playlist>("/api/library/playlists", { name: n });
        toast(COPY.playlistCreated);
        haptic("success");
        setPlCreateOpen(false);
        const pl = plRef.current;
        pl.list = [r, ...pl.list];
        pl.openId = r.id;
        pl.openName = r.name;
        rerender();
        loadPlTracks(true);
      } catch (e) {
        toast(e instanceof Error ? e.message : String(e));
        haptic("error");
      }
    },
    [loadPlTracks, rerender, toast],
  );

  const plRename = useCallback(
    async (name: string) => {
      const pl = plRef.current;
      if (pl.openId === null) return;
      const n = name.trim();
      if (!n) return;
      try {
        const r = await api<Playlist>(
          "/api/library/playlists/" + pl.openId,
          { name: n },
          "PATCH",
        );
        toast(COPY.playlistRenamed);
        haptic("success");
        pl.openName = r.name;
        pl.list = pl.list.map((x) =>
          x.id === r.id ? { ...x, name: r.name } : x,
        );
        setPlRenameOpen(false);
        rerender();
      } catch (e) {
        toast(e instanceof Error ? e.message : String(e));
        haptic("error");
      }
    },
    [rerender, toast],
  );

  const plDeleteClick = useCallback(() => {
    haptic("light");
    setPlDelConfirm(true);
  }, []);

  const plDeleteKeep = useCallback(() => setPlDelConfirm(false), []);

  const plDeleteYes = useCallback(async () => {
    const pl = plRef.current;
    if (pl.openId === null) return;
    const id = pl.openId;
    try {
      await api("/api/library/playlists/" + id, {}, "DELETE");
      toast(COPY.playlistDeleted);
      haptic("light");
      pl.openId = null;
      pl.openName = "";
      pl.tracks = [];
      setPlDelConfirm(false);
      rerender();
      loadPlaylists();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e));
      haptic("error");
      setPlDelConfirm(false);
    }
  }, [loadPlaylists, rerender, toast]);

  const onChip = useCallback(
    (v: View) => {
      const m = musicRef.current;
      if (m.view === v) return;
      m.view = v;
      m.day = null;
      if (v === "playlists") {
        const pl = plRef.current;
        pl.openId = null;
        pl.tracks = [];
        setPlCreateOpen(false);
        setPlRenameOpen(false);
        setPlDelConfirm(false);
        rerender();
        loadPlaylists();
        return;
      }
      plRef.current.openId = null;
      plRef.current.tracks = [];
      rerender();
      loadMusic(true);
    },
    [loadMusic, loadPlaylists, rerender],
  );

  const onSort = useCallback(
    (s: Sort) => {
      const m = musicRef.current;
      if (m.sort === s) return;
      m.sort = s;
      rerender();
      loadMusic(true);
    },
    [loadMusic, rerender],
  );

  const onSearch = useCallback(
    (q: string) => {
      musicRef.current.query = q.trim();
      loadMusic(true);
    },
    [loadMusic],
  );

  const onOpenDay = useCallback(
    (d: string) => {
      haptic("light");
      musicRef.current.day = d;
      rerender();
      loadMusic(true);
    },
    [loadMusic, rerender],
  );

  const onDayBack = useCallback(() => {
    haptic("light");
    musicRef.current.day = null;
    rerender();
    loadMusic(true);
  }, [loadMusic, rerender]);

  const onLoadMore = useCallback(() => loadMusic(false), [loadMusic]);
  const onRetry = useCallback(() => loadMusic(true), [loadMusic]);

  // --- sheet ------------------------------------------------------------------

  const openSheet = useCallback(
    (track: Track, index: number) => {
      // v2: opening the sheet never stops the player (the v1 preview died
      // with the sheet; full playback persists across it)
      clearResendPoll();
      setSheetRemoveConfirm(false);
      setSheetStatus(null);
      setSheetBusy(false);
      setSheetRmBusy(false);
      sheetIndexRef.current = index;
      setSheetTrack(track);
      // the sheet's "Add to playlist" picker needs the list even if the
      // user never opened the Playlists view — load it lazily once
      if (plRef.current.list.length === 0 && !plRef.current.listBusy) {
        void loadPlaylists();
      }
      requestAnimationFrame(() => setSheetOpen(true));
    },
    [clearResendPoll, loadPlaylists],
  );

  const closeSheet = useCallback(() => {
    setSheetOpen(false);
    clearResendPoll();
    window.setTimeout(() => {
      if (!sheetOpenRef.current) setSheetTrack(null);
    }, 300);
  }, [clearResendPoll]);

  const startResendPoll = useCallback(
    (key: string) => {
      clearResendPoll();
      resendPollRef.current = window.setInterval(async () => {
        try {
          const s = await api<{ state: string; error?: string | null }>(
            withInit("/api/library/track/" + key + "/resend/status"),
          );
          if (s.state === "sent") {
            clearResendPoll();
            setSheetStatus({ text: COPY.sent, cls: "done" });
            haptic("success");
            loadMusic(true); // grid order may have shifted
          } else if (s.state === "failed") {
            clearResendPoll();
            setSheetStatus({ text: s.error || COPY.failed, cls: "error" });
            haptic("error");
          } else {
            setSheetStatus({ text: RESEND_LABEL[s.state] || COPY.working, cls: "busy" });
          }
        } catch {
          /* pruned mid-poll — keep the last status (v1: silent) */
        }
      }, 2500);
    },
    [clearResendPoll, loadMusic],
  );

  const sheetResend = useCallback(async () => {
    const track = sheetTrackRef.current;
    if (!track) return;
    const key = encodeURIComponent(track.track_key);
    setSheetBusy(true);
    setSheetStatus({ text: COPY.sendingToChat, cls: "busy" });
    try {
      const r = await api<{ state: string }>("/api/library/track/" + key + "/resend");
      if (r.state === "sent") {
        setSheetStatus({ text: COPY.sent, cls: "done" });
        haptic("success");
        track.request_count += 1;
        track.last_requested = new Date().toISOString();
        // the ladder restored a fresh Telegram file_id: full playback is
        // available again (the sheet's Play button appears)
        track.has_stream = true;
        rerender();
      } else if (r.state === "queued") {
        setSheetStatus({ text: COPY.reDownloading, cls: "busy" });
        startResendPoll(key);
      }
    } catch (e) {
      setSheetStatus({ text: e instanceof Error ? e.message : String(e), cls: "error" });
      haptic("error");
    } finally {
      setSheetBusy(false);
    }
  }, [rerender, startResendPoll]);

  const sheetPreview = useCallback(async () => {
    const track = sheetTrackRef.current;
    if (!track) return;
    // v2: the player hook owns the 30s source (the sheet no longer tracks
    // preview state itself — the mini player + Now Playing mirror it)
    setSheetStatus({ text: COPY.loadingPreview, cls: "busy" });
    window.setTimeout(() => {
      // the status clears on the next action; the bar shows the real state
      setSheetStatus(null);
    }, 0);
    playerRef.current.playPreview(track);
  }, []);

  const sheetPlay = useCallback(() => {
    const track = sheetTrackRef.current;
    if (!track) return;
    haptic("light");
    // the queue is the CURRENT context (the loaded grid — or the open
    // playlist's tracks), the sheet's track at its tapped position (spec §5.2)
    const ctx =
      plRef.current.openId !== null
        ? plRef.current.tracks
        : musicRef.current.allTracks;
    playerRef.current.playFromTracks(ctx, sheetIndexRef.current);
  }, []);

  // --- sheet: tags + playlists (v2 §5.3) ------------------------------------------

  const sheetTagAssign = useCallback(
    (name: string) => {
      const track = sheetTrackRef.current;
      const n = name.trim();
      if (!track || !n) return;
      api<{ tags: string[] }>(
        "/api/library/track/" + encodeURIComponent(track.track_key) + "/tags",
        { names: [n] },
      ).then((r) => {
        // the response carries the full resulting list — keep the grid
        // object (same reference) in sync, no second read
        track.tags = r.tags;
        toast(COPY.tagAdded);
        haptic("success");
        rerender();
        loadTags(); // the row's counts changed
      }).catch((e) => {
        toast(e instanceof Error ? e.message : String(e));
        haptic("error");
      });
    },
    [loadTags, rerender, toast],
  );

  const sheetTagUnlink = useCallback(
    (name: string) => {
      const track = sheetTrackRef.current;
      if (!track || !name) return;
      api<{ ok: boolean; tags: string[] }>(
        "/api/library/track/" +
          encodeURIComponent(track.track_key) +
          "/tags/" +
          encodeURIComponent(name),
        {},
        "DELETE",
      ).then((r) => {
        track.tags = r.tags;
        toast(COPY.tagUnlinked);
        haptic("light");
        rerender();
        loadTags();
      }).catch((e) => {
        toast(e instanceof Error ? e.message : String(e));
        haptic("error");
      });
    },
    [loadTags, rerender, toast],
  );

  const sheetPlAdd = useCallback(
    (plId: number) => {
      const track = sheetTrackRef.current;
      if (!track) return;
      const row = plRef.current.list.find((x) => x.id === plId);
      const name = row ? row.name : plRef.current.openName;
      api<{ added: number; unknown: string[] }>(
        "/api/library/playlists/" + plId + "/tracks",
        { track_keys: [track.track_key] },
      ).then((r) => {
        if (r.unknown.length > 0) {
          // the row vanished mid-tap — refresh the context
          loadMusic(true);
          return;
        }
        toast(
          r.added > 0 ? COPY.addedToPlaylist(name) : COPY.alreadyInPlaylist,
        );
        haptic(r.added > 0 ? "success" : "light");
        if (r.added > 0) {
          plRef.current.list = plRef.current.list.map((x) =>
            x.id === plId ? { ...x, count: x.count + 1 } : x,
          );
        }
        rerender();
      }).catch((e) => {
        toast(e instanceof Error ? e.message : String(e));
        haptic("error");
      });
    },
    [loadMusic, rerender, toast],
  );

  const sheetRemoveClick = useCallback(() => setSheetRemoveConfirm(true), []);
  const sheetKeep = useCallback(() => setSheetRemoveConfirm(false), []);

  const sheetRemoveYes = useCallback(async () => {
    const track = sheetTrackRef.current;
    if (!track) return;
    const key = encodeURIComponent(track.track_key);
    setSheetRmBusy(true);
    try {
      await api("/api/library/track/" + key + "/remove");
      toast(COPY.removed);
      closeSheet();
      loadMusic(true);
    } catch (e) {
      setSheetStatus({ text: e instanceof Error ? e.message : String(e), cls: "error" });
      haptic("error");
    } finally {
      setSheetRmBusy(false);
    }
  }, [closeSheet, loadMusic, toast]);

  // --- derived render values -----------------------------------------------------

  const m = musicRef.current;
  const dailyList = m.view === "daily" && !m.day;
  const pl = plRef.current;
  const plView = m.view === "playlists";
  const plOpen = plView && pl.openId !== null;
  const libList = !dailyList && !plView;
  const count = data && libList ? renderCount(data, m) :
    plView && !plOpen
      ? pl.list.length + (pl.list.length === 1 ? " playlist" : " playlists")
      : plView
        ? pl.total + " " + trackWord(pl.total)
        : "";
  const tracks = data && libList ? m.allTracks : plOpen ? pl.tracks : [];
  const loadMoreVisible =
    plView
      ? plOpen && pl.page < pl.pages && !pl.busy
      : !!data && libList && data.page < data.pages;
  const plSkeleton = plOpen && pl.busy && pl.tracks.length === 0;
  const plStateBox: { kind: StateKind; extra?: string } | null =
    plView && !plOpen
      ? pl.listErr
        ? { kind: "unavailable" }
        : pl.list.length === 0 && !pl.listBusy
          ? { kind: "playlists-empty" }
          : null
      : plOpen
        ? pl.err
          ? { kind: "unavailable" }
          : pl.total === 0
            ? { kind: "playlist-empty" }
            : null
        : null;

  return {
    entered: m.entered,
    noAuth: m.entered && !inTG,
    view: m.view,
    sort: m.sort,
    days: m.days,
    day: m.day,
    count,
    tracks,
    skeleton,
    stateBox,
    searchErr,
    loadMoreVisible,
    gridNonce,
    onChip,
    onSort,
    onSearch,
    onOpenDay,
    onDayBack,
    onLoadMore,
    onOpenTile: openSheet,
    onRetry,
    tags,
    activeTag: m.activeTag,
    onTagFilter,
    onTagAdd,
    onTagDelete,
    plList: pl.list,
    plListErr: pl.listErr,
    plOpen: plOpen ? { id: pl.openId as number, name: pl.openName } : null,
    plTracks: pl.tracks,
    plLoadMoreVisible: plView && loadMoreVisible,
    plStateBox,
    plSkeleton,
    plCreateOpen,
    plRenameOpen,
    plDelConfirm,
    onPlBack: plBack,
    onPlCreate: plCreate,
    onPlRename: plRename,
    onPlDeleteClick: plDeleteClick,
    onPlDeleteYes: plDeleteYes,
    onPlDeleteKeep: plDeleteKeep,
    onPlOpenCreate: () => setPlCreateOpen(true),
    onPlCloseCreate: () => setPlCreateOpen(false),
    onPlOpenRename: () => {
      setPlCreateOpen(false);
      setPlRenameOpen(true);
    },
    onPlCloseRename: () => setPlRenameOpen(false),
    onOpenPlaylist: openPlaylist,
    onPlLoadMore: () => void loadPlTracks(false),
    onPlRetry: () =>
      plOpen ? void loadPlTracks(true) : void loadPlaylists(),
    sheet: sheetTrack
      ? {
          track: sheetTrack,
          open: sheetOpen,
          status: sheetStatus,
          busy: sheetBusy,
          removeConfirm: sheetRemoveConfirm,
          rmBusy: sheetRmBusy,
        }
      : null,
    sheetClose: closeSheet,
    sheetResend,
    sheetPreview,
    sheetPlay,
    sheetRemoveClick,
    sheetKeep,
    sheetRemoveYes,
    sheetTagAssign,
    sheetTagUnlink,
    sheetPlAdd,
  };
}
