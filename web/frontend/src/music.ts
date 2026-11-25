// The Mini App music library (v2 spec §12): the v1 `music` global object,
// the track sheet, and the 30s preview — as a single hook. v1 semantics are
// kept 1:1 (busy guard, page accumulation, error mapping, resend ladder,
// sheet animation) so the port is behavior-identical.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, withInit } from "./api/client";
import type { DayEntry, LibraryPage, Track } from "./api/types";
import { COPY, RESEND_LABEL, trackWord } from "./copy";
import { haptic, inTG } from "./theme";
import * as player from "./player";
import type { SheetStatus } from "./components/sheet";
import type { StateKind } from "./components/state-box";

export type View = "latest" | "daily" | "search" | "often" | "browse";
export type Sort = "recent" | "added" | "az";

interface MusicState {
  view: View;
  sort: Sort;
  query: string;
  day: string | null;
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
  page: 1,
  pages: 1,
  total: 0,
  days: null,
  allTracks: [],
  busy: false,
  entered: false,
};

function musicUrl(page: number, m: MusicState): string {
  const p = new URLSearchParams({ view: m.view, page: String(page) });
  if (m.view === "browse") p.set("sort", m.sort);
  if (m.view === "search") p.set("q", m.query);
  if (m.view === "daily" && m.day) p.set("day", m.day);
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
  onOpenTile: (t: Track) => void;
  onRetry: () => void;
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
  sheetRemoveClick: () => void;
  sheetKeep: () => void;
  sheetRemoveYes: () => void;
  previewTrack: Track | null;
  previewPlaying: boolean;
  previewToggle: () => void;
}

export function useMusic(active: boolean, toast: (msg: string) => void): MusicApi {
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
  const [sheetOpen, setSheetOpen] = useState(false);
  const sheetOpenRef = useRef(false);
  const [sheetStatus, setSheetStatus] = useState<SheetStatus | null>(null);
  const [sheetBusy, setSheetBusy] = useState(false);
  const [sheetRemoveConfirm, setSheetRemoveConfirm] = useState(false);
  const [sheetRmBusy, setSheetRmBusy] = useState(false);
  const resendPollRef = useRef<number | null>(null);

  // --- preview (30s) ---------------------------------------------------------
  const [previewTrack, setPreviewTrack] = useState<Track | null>(null);
  const [previewPlaying, setPreviewPlaying] = useState(false);

  useEffect(() => {
    sheetOpenRef.current = sheetOpen;
  }, [sheetOpen]);
  useEffect(() => {
    sheetTrackRef.current = sheetTrack;
  }, [sheetTrack]);
  useEffect(() => {
    player.onEnded(() => {
      setPreviewTrack(null);
      setPreviewPlaying(false);
    });
    return () => {
      if (resendPollRef.current) window.clearInterval(resendPollRef.current);
    };
  }, []);

  const stopPreviewAll = useCallback(() => {
    player.stopPreview();
    setPreviewTrack(null);
    setPreviewPlaying(false);
  }, []);

  const clearResendPoll = useCallback(() => {
    if (resendPollRef.current) {
      window.clearInterval(resendPollRef.current);
      resendPollRef.current = null;
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
  }, [active, loadMusic, rerender]);

  const onChip = useCallback(
    (v: View) => {
      const m = musicRef.current;
      if (m.view === v) return;
      m.view = v;
      m.day = null;
      rerender();
      loadMusic(true);
    },
    [loadMusic, rerender],
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
    (track: Track) => {
      stopPreviewAll();
      clearResendPoll();
      setSheetRemoveConfirm(false);
      setSheetStatus(null);
      setSheetBusy(false);
      setSheetRmBusy(false);
      setSheetTrack(track);
      requestAnimationFrame(() => setSheetOpen(true));
    },
    [clearResendPoll, stopPreviewAll],
  );

  const closeSheet = useCallback(() => {
    setSheetOpen(false);
    stopPreviewAll();
    clearResendPoll();
    window.setTimeout(() => {
      if (!sheetOpenRef.current) setSheetTrack(null);
    }, 300);
  }, [clearResendPoll, stopPreviewAll]);

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
    const key = encodeURIComponent(track.track_key);
    setSheetStatus({ text: COPY.loadingPreview, cls: "busy" });
    try {
      const r = await api<{ url: string }>("/api/library/track/" + key + "/preview");
      setPreviewTrack(track);
      setPreviewPlaying(true);
      player.startPreview(r.url).catch(() => {
        // v1: the sheet status surfaces the failure; the bar just disappears
        setSheetStatus({ text: COPY.previewFailed, cls: "error" });
        stopPreviewAll();
      });
    } catch (e) {
      setSheetStatus({ text: e instanceof Error ? e.message : String(e), cls: "error" });
      haptic("error");
    }
  }, [stopPreviewAll]);

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

  // --- preview toggle (playerbar) -----------------------------------------------

  const previewToggle = useCallback(() => {
    const a = player.previewAudio();
    if (!a) return;
    haptic("light");
    if (a.paused) {
      a.play().catch(() => stopPreviewAll());
      setPreviewPlaying(true);
    } else {
      a.pause();
      setPreviewPlaying(false);
    }
  }, [stopPreviewAll]);

  // --- derived render values -----------------------------------------------------

  const m = musicRef.current;
  const dailyList = m.view === "daily" && !m.day;
  const count = data && !dailyList ? renderCount(data, m) : "";
  const tracks = data && !dailyList ? m.allTracks : [];
  const loadMoreVisible = !!data && !dailyList && data.page < data.pages;

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
    sheetRemoveClick,
    sheetKeep,
    sheetRemoveYes,
    previewTrack,
    previewPlaying,
    previewToggle,
  };
}
