// The player hook (v2 spec §5.2): queue/shuffle/repeat over the module
// audio singleton (player.ts) + the stream/preview source ladder.
//
// Source ladder per track (the honest states the spec requires):
//   has_stream  -> stream the full track via /stream
//                  on playback error (Telegram purged the file -> the
//                  proxy's 404 "no-source") -> fall back to the 30s
//                  preview, flagged as "preview" in the UI
//   no file_id  -> straight to the 30s preview
//   preview 404 -> stop with the honest "Preview playback failed."
import { useCallback, useEffect, useRef, useState } from "react";
import { api, withInit } from "./api/client";
import type { Track } from "./api/types";
import { COPY } from "./copy";
import {
  cycleRepeat,
  makeQueue,
  nextPos,
  prevPos,
  withShuffle,
  type Queue,
  type RepeatMode,
} from "./queue";
import * as player from "./player";

export function streamUrl(track: Track): string {
  return withInit(
    "/api/library/track/" + encodeURIComponent(track.track_key) + "/stream",
  );
}

export interface PlayerApi {
  track: Track | null;
  mode: "stream" | "preview" | null;
  playing: boolean;
  position: number;
  duration: number;
  shuffle: boolean;
  repeat: RepeatMode;
  queueSize: number;
  streamDegraded: boolean; // current track: full source went, preview plays
  nowPlayingOpen: boolean;
  playFromTracks: (tracks: Track[], index: number) => void;
  playPreview: (track: Track) => void;
  toggle: () => void;
  seekTo: (sec: number) => void;
  next: () => void;
  prev: () => void;
  toggleShuffle: () => void;
  cycleRepeat: () => void;
  openNowPlaying: () => void;
  closeNowPlaying: () => void;
}

export function usePlayer(toast: (msg: string) => void): PlayerApi {
  const [track, setTrack] = useState<Track | null>(null);
  const [mode, setMode] = useState<"stream" | "preview" | null>(null);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [shuffle, setShuffle] = useState(false);
  const [repeat, setRepeat] = useState<RepeatMode>("off");
  const [streamDegraded, setStreamDegraded] = useState(false);
  const [nowPlayingOpen, setNowPlayingOpen] = useState(false);

  const queueRef = useRef<Queue | null>(null);
  const trackRef = useRef<Track | null>(null);
  const modeRef = useRef<"stream" | "preview" | null>(null);
  const busyRef = useRef(false); // a source fetch is in flight
  const toastRef = useRef(toast);
  toastRef.current = toast;

  useEffect(() => {
    trackRef.current = track;
  }, [track]);
  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);

  const syncFromQueue = useCallback(() => {
    const q = queueRef.current;
    setShuffle(!!q?.shuffle);
    setRepeat(q?.repeat ?? "off");
    player.setLoop(!!q && q.repeat === "one");
  }, []);

  const applyMedia = useCallback(() => {
    const t = trackRef.current;
    const m = modeRef.current;
    if (!t || !m) {
      player.clearMediaSession();
      return;
    }
    // metadata only — the action handlers are wired once by the effect
    // below and must survive track changes
    player.setMediaMetadata({
      title: t.title,
      artist: t.artist,
      cover: t.has_cover
        ? withInit("/api/library/cover/" + encodeURIComponent(t.track_key))
        : null,
    });
  }, []);

  /** The source ladder for one track. Returns the resulting mode, or
   * null when nothing could play (already toasted). */
  const startTrack = useCallback(
    async (t: Track): Promise<"stream" | "preview" | null> => {
    if (t.has_stream) {
      setStreamDegraded(false);
      player.playSource(streamUrl(t), "stream");
      setMode("stream");
      return "stream";
    }
    const m = await fetchPreview(t);
    if (m === "gone") return null;
    setMode("preview");
    return "preview";
  }, []);

  async function fetchPreview(t: Track): Promise<"ok" | "gone"> {
    busyRef.current = true;
    try {
      // POST (v1 1:1): the route is add_post, initData rides the JSON body
      const r = await api<{ url: string }>(
        "/api/library/track/" + encodeURIComponent(t.track_key) + "/preview",
        {},
      );
      player.playSource(r.url, "preview");
      return "ok";
    } catch {
      return "gone";
    } finally {
      busyRef.current = false;
    }
  }

  const beginAt = useCallback(
    async (index: number) => {
      const q = queueRef.current;
      if (!q) return;
      const t = q.tracks[q.order[index]];
      if (!t) return;
      trackRef.current = t;
      setTrack(t);
      setStreamDegraded(false);
      setPosition(0);
      const m = await startTrack(t);
      if (m === null) {
        toastRef.current(COPY.previewFailed);
        stopAll();
      }
      applyMedia();
    },
    [applyMedia, startTrack],
  );

  const stopAll = useCallback(() => {
    player.stop();
    queueRef.current = null;
    trackRef.current = null;
    modeRef.current = null;
    setTrack(null);
    setMode(null);
    setPlaying(false);
    setPosition(0);
    setDuration(0);
    setStreamDegraded(false);
    setNowPlayingOpen(false);
  }, []);

  const advance = useCallback(
    async (dir: 1 | -1) => {
      const q = queueRef.current;
      if (!q) return;
      const np = dir === 1 ? nextPos(q) : prevPos(q);
      if (np < 0) {
        stopAll();
        return;
      }
      q.pos = np;
      await beginAt(np);
    },
    [beginAt, stopAll],
  );

  // --- audio element events ----------------------------------------------------

  useEffect(() => {
    const unsub = player.subscribePlayer((e) => {
      switch (e.type) {
        case "time":
          setPosition(e.time);
          setDuration(e.duration);
          break;
        case "playing":
          setPlaying(e.playing);
          player.setMediaPlaying(e.playing);
          break;
        case "ended": {
          const q = queueRef.current;
          if (!q) break;
          if (q.repeat === "one") break; // native loop: no event
          const np = nextPos(q);
          if (np < 0) stopAll();
          else {
            q.pos = np;
            void beginAt(np);
          }
          break;
        }
        case "error": {
          if (e.stale) break; // a newer src replaced it — not our failure
          const t = trackRef.current;
          const m = modeRef.current;
          if (!t || !m) break;
          if (m === "stream") {
            // the proxy answered no-source (Telegram purged the file) or
            // the network dropped: degrade to the 30s preview, honestly
            void (async () => {
              const r = await fetchPreview(t);
              // a queue hop landed while we fetched: don't clobber it
              if (trackRef.current !== t) return;
              if (r === "gone") {
                toastRef.current(COPY.previewFailed);
                stopAll();
              } else {
                setMode("preview");
                setStreamDegraded(true);
                applyMedia();
              }
            })();
          } else {
            toastRef.current(COPY.previewFailed);
            stopAll();
          }
          break;
        }
      }
    });
    return unsub;
  }, [applyMedia, beginAt, stopAll]);

  // Media Session action handlers — once, reading the latest queue via refs.
  useEffect(() => {
    if (typeof navigator === "undefined" || !("mediaSession" in navigator)) return;
    const ms = navigator.mediaSession;
    const wire = (action: string, fn: (() => void) | null) => {
      try {
        ms.setActionHandler(action as never, fn as never);
      } catch {
        /* partial API in some webviews */
      }
    };
    wire("play", () => void player.resume());
    wire("pause", () => player.pause());
    wire("previoustrack", () => void advance(-1));
    wire("nexttrack", () => void advance(1));
    wire("seekbackward", () => player.seekTo(player.currentTime() - 15));
    wire("seekforward", () => player.seekTo(player.currentTime() + 15));
    return () => {
      wire("play", null);
      wire("pause", null);
      wire("previoustrack", null);
      wire("nexttrack", null);
      wire("seekbackward", null);
      wire("seekforward", null);
    };
  }, [advance]);

  // --- public surface ------------------------------------------------------------

  const playFromTracks = useCallback(
    (tracks: Track[], index: number) => {
      if (!tracks.length) return;
      queueRef.current = makeQueue(tracks, index);
      syncFromQueue();
      void beginAt(queueRef.current.pos);
    },
    [beginAt, syncFromQueue],
  );

  const playPreview = useCallback(
    (t: Track) => {
      queueRef.current = null; // preview-only: no queue context
      trackRef.current = t;
      setTrack(t);
      setMode("preview");
      setStreamDegraded(false);
      setPosition(0);
      void fetchPreview(t).then((r) => {
        if (r === "gone") {
          toastRef.current(COPY.previewFailed);
          stopAll();
        }
        applyMedia();
      });
    },
    [applyMedia, stopAll],
  );

  const toggle = useCallback(() => {
    if (!trackRef.current) return;
    if (player.isPlaying()) player.pause();
    else void player.resume();
  }, []);

  const next = useCallback(() => void advance(1), [advance]);
  const prev = useCallback(() => void advance(-1), [advance]);

  const toggleShuffle = useCallback(() => {
    const q = queueRef.current;
    if (!q) return;
    queueRef.current = withShuffle(q, !q.shuffle, Date.now() & 0x7fffffff);
    syncFromQueue();
  }, [syncFromQueue]);

  const cycleRepeatMode = useCallback(() => {
    const q = queueRef.current;
    if (!q) return;
    queueRef.current = { ...q, repeat: cycleRepeat(q.repeat) };
    syncFromQueue();
  }, [syncFromQueue]);

  const seekTo = useCallback((sec: number) => {
    player.seekTo(sec);
    setPosition(sec);
  }, []);

  return {
    track,
    mode,
    playing,
    position,
    duration,
    shuffle,
    repeat,
    queueSize: queueRef.current?.tracks.length ?? 0,
    streamDegraded,
    nowPlayingOpen,
    playFromTracks,
    playPreview,
    toggle,
    seekTo,
    next,
    prev,
    toggleShuffle,
    cycleRepeat: cycleRepeatMode,
    openNowPlaying: () => {
      if (trackRef.current) setNowPlayingOpen(true);
    },
    closeNowPlaying: () => setNowPlayingOpen(false),
  };
}
