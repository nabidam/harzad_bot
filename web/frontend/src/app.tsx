import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api/client";
import type { AuthResult, User } from "./api/types";
import { COPY } from "./copy";
import { Icon } from "./icons";
import { LOGO } from "./logo";
import { useMusic } from "./music";
import type { UiMsg } from "./components/chat-tab";
import { ChatTab } from "./components/chat-tab";
import { DownloadTab } from "./components/download-tab";
import { MusicTab } from "./components/music-tab";
import { PlayerBar } from "./components/player-bar";
import { Sheet } from "./components/sheet";
import { haptic } from "./theme";

type Tab = "download" | "music" | "chat";

// The thinking bubble is replaced in place by the reply / error text (v1 did
// this on the DOM node); it never enters the API history.
function replaceLastThinking(m: UiMsg[], replacement: UiMsg): UiMsg[] {
  let i = -1;
  for (let k = m.length - 1; k >= 0; k--) {
    if (m[k].thinking && !m[k].inHistory) {
      i = k;
      break;
    }
  }
  if (i < 0) return [...m, replacement];
  const c = m.slice();
  c[i] = replacement;
  return c;
}

export default function App() {
  const [tab, setTabState] = useState<Tab>("download");
  const [user, setUser] = useState<User | null>(null);
  const [authed, setAuthed] = useState(false);
  const [messages, setMessages] = useState<UiMsg[]>([]);
  const [sending, setSending] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [toastMsg, setToastMsg] = useState("");
  const [toastShow, setToastShow] = useState(false);
  const mainRef = useRef<HTMLElement | null>(null);
  const toastTimer = useRef<number | null>(null);

  const showToast = useCallback((msg: string, ms = 2600) => {
    setToastMsg(msg);
    setToastShow(true);
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToastShow(false), ms);
  }, []);

  const music = useMusic(tab === "music", showToast);
  const musicLive = useRef(music);
  useEffect(() => {
    musicLive.current = music;
  });

  // ---- auth boot (v1 boot()) -------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    api<AuthResult>("/api/auth", {})
      .then((d) => {
        if (!cancelled) setUser(d.user || null);
      })
      .catch(() => {
        if (!cancelled) setUser(null);
      })
      .finally(() => {
        if (!cancelled) setAuthed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // ---- tab switching (v1 setTab) ----------------------------------------------
  const setTab = useCallback(
    (t: Tab) => {
      setTabState(t);
      if (t === "chat" && messages.length === 0) {
        setMessages([{ role: "bot", content: COPY.chatWelcome, inHistory: true }]);
      }
    },
    [messages],
  );

  useEffect(() => {
    document.body.classList.toggle("chat-on", tab === "chat");
    if (mainRef.current) mainRef.current.scrollTop = 0;
  }, [tab]);

  // v1 scrollChat: pin to the bottom on every new message (chat tab).
  useEffect(() => {
    if (tab === "chat" && mainRef.current) {
      mainRef.current.scrollTop = mainRef.current.scrollHeight;
    }
  }, [tab, messages]);

  // ---- chat (v1 sendChat) ---------------------------------------------------------
  const sendChat = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text) return;
      const history = [
        ...messages.filter((m) => m.inHistory).map((m) => ({ role: m.role, content: m.content })),
        { role: "user" as const, content: text },
      ];
      setMessages((m) => [
        ...m,
        { role: "user", content: text, inHistory: true },
        { role: "bot", content: "", inHistory: false, thinking: true },
      ]);
      setChatInput("");
      setSending(true);
      try {
        const data = await api<{ reply?: string }>("/api/chat", { messages: history });
        const reply = data.reply || COPY.emptyReply;
        setMessages((m) => replaceLastThinking(m, { role: "bot", content: reply, inHistory: true }));
        haptic("light");
      } catch (e) {
        setMessages((m) =>
          replaceLastThinking(m, {
            role: "bot",
            content: COPY.chatUnavailablePrefix + (e instanceof Error ? e.message : String(e)),
            inHistory: false,
          }),
        );
        haptic("error");
      } finally {
        setSending(false);
      }
    },
    [messages],
  );

  // ---- sheet Escape-to-close (v1 document keydown) ---------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && musicLive.current.sheet) musicLive.current.sheetClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // ---- topbar -----------------------------------------------------------------------
  const avatar = !authed ? (
    <span className="av-txt"></span>
  ) : user ? (
    <span className="av-txt">{((user.first_name || "")[0] || "?").toUpperCase()}</span>
  ) : (
    <Icon name="cloud-off" />
  );
  const avatarTitle = !authed
    ? "Not signed in"
    : user
      ? [user.first_name, user.last_name].filter(Boolean).join(" ")
      : "Browser mode";

  return (
    <>
      <div className="app">
        <header className="topbar">
          <div className="brand">
            <img id="brand-logo" alt="" width={27} height={27} src={LOGO} />
            <b>Harzad</b>
          </div>
          <div className="avatar" id="user-avatar" title={avatarTitle}>{avatar}</div>
        </header>

        <main id="main" ref={mainRef}>
          <section id="tab-download" className={tab === "download" ? "tab active" : "tab"}>
            <DownloadTab user={user} authed={authed} toast={showToast} />
          </section>
          <section id="tab-music" className={tab === "music" ? "tab active" : "tab"}>
            <MusicTab m={music} searchInput={searchInput} onSearchInput={setSearchInput} />
          </section>
          <section id="tab-chat" className={tab === "chat" ? "tab active" : "tab"}>
            <ChatTab messages={messages} />
          </section>
        </main>

        <div className="composer" id="composer" hidden={tab !== "chat"}>
          <form
            id="chat-form"
            style={{ display: "flex", alignItems: "center", gap: 9, flex: 1 }}
            onSubmit={(e) => {
              e.preventDefault();
              sendChat(chatInput);
            }}
          >
            <input
              type="text"
              id="chat-input"
              placeholder={COPY.chatPlaceholder}
              autoComplete="off"
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
            />
            <button className="send" type="submit" aria-label="Send" id="send-btn" disabled={sending}>
              <Icon name="send" />
            </button>
          </form>
        </div>

        <PlayerBar
          track={music.previewTrack}
          playing={music.previewPlaying}
          onToggle={music.previewToggle}
        />

        <nav className="tabbar" aria-label="Tabs">
          <button data-tab="download" className={tab === "download" ? "active" : undefined} aria-label="Download" onClick={() => setTab("download")}>
            <span className="nav-ico" data-ico="cloud-download"><Icon name="cloud-download" /></span>Download
          </button>
          <button data-tab="music" className={tab === "music" ? "active" : undefined} aria-label="My music" onClick={() => setTab("music")}>
            <span className="nav-ico" data-ico="music"><Icon name="music" /></span>My music</button>
          <button data-tab="chat" className={tab === "chat" ? "active" : undefined} aria-label="Chat" onClick={() => setTab("chat")}>
            <span className="nav-ico" data-ico="message"><Icon name="message" /></span>Chat
          </button>
        </nav>
      </div>

      {music.sheet && (
        <Sheet
          track={music.sheet.track}
          open={music.sheet.open}
          status={music.sheet.status}
          busy={music.sheet.busy}
          removeConfirm={music.sheet.removeConfirm}
          rmBusy={music.sheet.rmBusy}
          onClose={music.sheetClose}
          onResend={music.sheetResend}
          onPreview={music.sheetPreview}
          onRemoveClick={music.sheetRemoveClick}
          onKeep={music.sheetKeep}
          onRemoveYes={music.sheetRemoveYes}
        />
      )}

      <div
        className={"toast" + (toastShow ? " show" : "")}
        id="toast"
        role="status"
        aria-live="polite"
      >{toastMsg}</div>
    </>
  );
}
