"""Harzad Mini App backend.

An aiohttp server embedded in the bot's asyncio loop (PTB already runs one).
It serves the single-page app (static/index.html) and a small JSON API from a
single origin — Bot API 10.2 locks WebApp methods to the registered Mini App
domain, so SPA and API must share an origin.

Auth: /api/auth, /api/jobs and /api/chat take the Telegram WebApp initData
blob and verify it against the bot token (HMAC-SHA256 with the "WebAppData"
key, per the Web Apps reference) before use. A missing initData is treated
as anonymous browser/preview mode (jobs run without chat delivery); a
present-but-invalid initData is rejected. Job file downloads are addressed
by an unguessable uuid job id instead (the downloadFile popup does a plain
GET).
"""
import asyncio
import hashlib
import hmac
import io
import json
import math
import os
import time
import uuid
from datetime import date
from logging import getLogger
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests
import validators
import aiohttp
from aiohttp import web
from PIL import Image
from telegram.constants import ChatAction, ParseMode

import utils.library as mlib
import utils.resend as resend
from configurations.settings import DOWNLOAD_VIDEO_PATH, MINIAPP_URL, TOKEN
from connectors.preview import resolve_preview_url
from utils.constants.messages import BOT_ID, MEDIA_CAPTION
from utils.helpers import escape_html, prepare_cover
from utils.storage import cleanup_media
from utils.youtube_dl import TARGETS, opts_for_target

logger = getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
INIT_DATA_TTL = 24 * 3600  # seconds; initData older than this is stale
CHUNK_SIZE = 1 << 20  # 1 MB


class JobError(Exception):
    """A job-level failure that should be shown to the user as-is."""


class Unauthorized(Exception):
    """initData missing, invalid or expired."""


# ---------------------------------------------------------------------------
# initData verification
# ---------------------------------------------------------------------------

def verify_init_data(init_data: str) -> dict | None:
    """Verify a Telegram WebApp initData blob.

    Returns the embedded user dict when valid, None otherwise.
    """
    if not init_data:
        return None
    params = {k: v[0] for k, v in parse_qs(init_data, keep_blank_values=True).items()}
    received_hash = params.pop("hash", None)
    if not received_hash:
        logger.warning("initData rejected: no hash param params=%s raw[:400]=%r",
                       sorted(params.keys()), init_data[:400])
        return None
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        # If a real client's blob fails we must see it: Telegram has silently
        # changed the blob format before (the undocumented `signature` param).
        # Log the param names + truncated raw blob so the scheme can be
        # re-derived from one log line.
        logger.warning(
            "initData hash mismatch: params=%s expected=%s received=%s check_string=%r raw[:400]=%r",
            sorted(params.keys()), expected, received_hash, check_string[:400], init_data[:400],
        )
        return None
    try:
        user = json.loads(params.get("user", ""))
    except (ValueError, TypeError):
        logger.warning("initData rejected: user field not a JSON object raw=%r", init_data)
        return None
    # Freshness: current clients carry the timestamp in the top-level
    # auth_date param only (the user object no longer embeds it); fall back
    # to the embedded one for older clients.
    stamp = params.get("auth_date") or user.get("auth_date") or 0
    if time.time() - int(stamp) > INIT_DATA_TTL:
        logger.warning("initData rejected: stale auth_date=%s raw=%r", stamp, init_data)
        return None
    logger.info("initData verified: user_id=%s", user.get("id"))
    return user


async def _auth(request: web.Request) -> dict | None:
    """Extract and verify initData from a JSON body or query string.

    Returns the user dict, or None when the client sent no initData at all
    (browser / preview mode). A present-but-invalid initData still raises:
    a failed hash check must never be accepted silently.
    """
    init_data = ""
    try:
        init_data = (await request.json()).get("initData", "")
    except Exception:
        init_data = request.rel_url.query.get("initData", "")
    if not init_data:
        return None
    user = verify_init_data(init_data)
    if user is None:
        raise Unauthorized()
    return user


# ---------------------------------------------------------------------------
# download jobs
# ---------------------------------------------------------------------------

def _ytdlp_run(url: str, job: dict | None = None):
    """Extract + download one video, mirroring the chat handler's pipeline.

    Runs in a worker thread (blocking network + disk). When `job` is
    passed, the thread flips it to the honest "downloading" state once
    the metadata is resolved (the state machine the SPA polls:
    extracting -> downloading -> sending -> done). The yt-dlp options
    come from the job's `options.target` (spec §4.3; None = the v1
    default 720p mp4).
    """
    from yt_dlp import YoutubeDL, utils as ytdl_utils

    target = (job or {}).get("options", {}).get("target")
    opts = opts_for_target(target)
    with YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except ytdl_utils.DownloadError:
            raise JobError("Could not fetch that link. Check it and try again.")
        if info.get("_type") == "playlist":
            raise JobError("Playlists aren't supported in the app yet — paste a single video link.")
        if job is not None:
            job["title"] = info.get("title") or "video"
            job["state"] = "downloading"
        ydl.download([url])
        filename = ydl.prepare_filename(info)
    if not os.path.exists(filename):
        raise JobError("Download finished but the file is missing.")
    return info, filename


async def run_job(app: web.Application, job: dict) -> None:
    """Drive one download job through its states until done or error."""
    bot = app["bot"]
    try:
        job["state"] = "extracting"
        info, filename = await asyncio.to_thread(_ytdlp_run, job["url"], job)
        job["title"] = info.get("title") or "video"
        job["file_name"] = os.path.basename(filename)
        job["file_path"] = filename
        job["file_size_mb"] = round(os.path.getsize(filename) / (1024 * 1024), 2)
        base = (app["base_url"] or MINIAPP_URL).rstrip("/")
        job["file_url"] = f"{base}/api/jobs/{job['id']}/file"

        # Also deliver a copy to the chat (bot upload cap: 50 MB).
        if job["user_id"] and job["file_size_mb"] < 50:
            job["state"] = "sending"
            # distinct InputFile per field: each needs its own multipart part
            cover_file, thumb_file = await asyncio.gather(
                asyncio.to_thread(prepare_cover, filename),
                asyncio.to_thread(prepare_cover, filename),
            )
            with open(filename, "rb") as f:
                msg = await bot.send_video(
                    chat_id=job["user_id"],
                    video=f,
                    duration=info.get("duration"),
                    thumbnail=thumb_file if thumb_file is not None else info.get("thumbnail"),
                    cover=cover_file,
                    caption=MEDIA_CAPTION.format(caption=escape_html(job["title"]), bot_id=BOT_ID),
                    parse_mode=ParseMode.HTML,
                    protect_content=False,
                    supports_streaming=True,
                )
            job["file_id"] = msg.video.file_id if msg.video else None
        elif job["user_id"] is None:
            logger.info("Job %s: browser (anonymous) job — no chat delivery", job["id"])
        else:
            logger.info("Job %s: file is %.1f MB, over the 50 MB bot upload cap — app-only delivery", job["id"], job["file_size_mb"])
        job["state"] = "done"
        logger.info("Job %s done: %s (%.1f MB)", job["id"], job["file_name"], job["file_size_mb"])
    except JobError as e:
        job["state"], job["error"] = "error", str(e)
    except Exception:
        logger.exception("Job %s failed", job["id"])
        # canonical Mini App failure copy (spec §12.3), aligned with the
        # bot's service-failure tone
        job["state"], job["error"] = "error", "This download failed. Check the link or try again."


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------

async def handle_health(request: web.Request):
    return web.json_response({"ok": True})


async def handle_index(request: web.Request):
    return web.FileResponse(STATIC_DIR / "index.html")


async def handle_auth(request: web.Request):
    try:
        user = await _auth(request)
    except Unauthorized:
        return web.json_response({"error": "This app can only be opened from Telegram."}, status=401)
    if user is None:
        return web.json_response({"user": None, "anonymous": True})
    return web.json_response({"user": {k: user.get(k) for k in ("id", "first_name", "last_name", "username")}})


def _is_youtube_url(url: str) -> bool:
    """The providers that take download options (spec §4.3): YouTube
    only. Host match on the URL — the SPA decides whether to SHOW the
    option row from the same rule; this is the server-side authority."""
    host = (urlsplit(url).hostname or "").lower()
    return host in (
        "youtube.com", "www.youtube.com", "m.youtube.com",
        "music.youtube.com", "youtu.be",
    )


async def handle_create_job(request: web.Request):
    try:
        user = await _auth(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    user_id = user["id"] if user else None
    body = await request.json()
    url = (body.get("url") or "").strip()
    if not validators.url(url):
        return web.json_response({"error": "That doesn't look like a valid link."}, status=400)

    # Optional download options (spec §4.3): a semantic `target` label,
    # mapped server-side to yt-dlp formats — the client never sends a
    # raw format string. YouTube only; unknown target -> 400.
    options = body.get("options")
    if options is not None:
        if not isinstance(options, dict):
            return web.json_response({"error": "Bad options."}, status=400)
        if set(options) - {"target"}:
            return web.json_response({"error": "Bad options."}, status=400)
        target = options.get("target")
        if target is not None:
            if not isinstance(target, str) or target not in TARGETS:
                return web.json_response({"error": "Unknown download option."}, status=400)
            if not _is_youtube_url(url):
                return web.json_response(
                    {"error": "Download options are only available for YouTube links."},
                    status=400)
        options = {"target": target} if target is not None else {}
    else:
        options = {}

    jobs = request.app["jobs"]
    active = [j for j in jobs.values()
              if j["user_id"] == user_id and j["state"] not in ("done", "error")]
    if active:
        return web.json_response({"error": "Already downloading one — wait for it to finish."}, status=409)

    job = {
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "url": url,
        "options": options,
        "state": "starting",
        "title": None,
        "file_name": None,
        "file_path": None,
        "file_size_mb": None,
        "file_url": None,
        "file_id": None,
        "error": None,
        "created": time.time(),
    }
    jobs[job["id"]] = job
    # bound the job table: drop finished jobs older than a day
    _prune_jobs(jobs)
    asyncio.get_running_loop().create_task(run_job(request.app, job))
    return web.json_response({"id": job["id"]})


def _prune_jobs(jobs: dict) -> None:
    stale = [jid for jid, j in jobs.items()
             if j["state"] in ("done", "error") and time.time() - j["created"] > INIT_DATA_TTL]
    for jid in stale:
        jobs.pop(jid, None)


async def handle_job_status(request: web.Request):
    # No initData here: the job id is a uuid4 capability.
    job = request.app["jobs"].get(request.match_info["job_id"])
    if job is None:
        return web.json_response({"error": "Unknown job."}, status=404)
    return web.json_response({k: job[k] for k in
                              ("state", "title", "file_name", "file_size_mb", "file_url", "file_id", "error", "options")})


_JOB_MIME = {
    ".mp4": "video/mp4", ".webm": "video/webm", ".m4v": "video/mp4",
    ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
    ".opus": "audio/ogg", ".wav": "audio/wav", ".flac": "audio/flac",
}


def _job_content_type(filename: str | None) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    return _JOB_MIME.get(ext, "application/octet-stream")


async def handle_job_file(request: web.Request):
    """Stream the downloaded file, with Range support.

    The downloadFile popup (Bot API 8.0+) downloads from this URL, so the
    response carries the two headers the Web Apps reference asks for:
    Content-Disposition: attachment and Access-Control-Allow-Origin for
    https://web.telegram.org (web platform fetches).
    """
    job = request.app["jobs"].get(request.match_info["job_id"])
    if job is None or job["state"] != "done":
        return web.Response(status=404)
    # yt-dlp's outtmpl string-concatenates DOWNLOAD_VIDEO_PATH (no trailing
    # slash), so the real path is the one prepared_filename() returned.
    path = job.get("file_path") or ""
    if not path or not os.path.isfile(path):
        return web.Response(status=404)

    size = os.path.getsize(path)
    start, end, status = 0, size - 1, 200
    range_header = request.headers.get("Range", "")
    if range_header.startswith("bytes="):
        spec = range_header[6:].split("-", 1)
        try:
            if spec[0] == "":
                # suffix range: the last N bytes
                start = max(size - int(spec[1]), 0)
                end = size - 1
            else:
                start = int(spec[0])
                end = min(int(spec[1]), size - 1) if len(spec) > 1 and spec[1] else size - 1
        except (ValueError, IndexError):
            start, end, status = 0, size - 1, 200
        if start >= size or start > end:
            return web.Response(status=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206

    resp = web.StreamResponse(
        status=status,
        headers={
            "Content-Type": _job_content_type(job.get("file_name")),
            "Content-Disposition": f'attachment; filename="{job["file_name"]}"',
            "Access-Control-Allow-Origin": "https://web.telegram.org",
        },
    )
    resp.headers["Content-Length"] = str(end - start + 1)
    if status == 206:
        resp.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    else:
        resp.headers["Accept-Ranges"] = "bytes"
    await resp.prepare(request)
    with open(path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = f.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            await resp.write(chunk)
            remaining -= len(chunk)
    await resp.write_eof()
    return resp


async def handle_chat(request: web.Request):
    try:
        await _auth(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    body = await request.json()
    messages = body.get("messages") or []
    # The SPA labels assistant turns "bot"; LangChain tuples take its own role names.
    _ROLE = {"bot": "assistant", "ai": "assistant", "assistant": "assistant",
             "user": "human", "human": "human", "system": "system"}
    clean = [(_ROLE.get(m.get("role", "user"), "human"), str(m.get("content", "")))
             for m in messages[-10:] if isinstance(m, dict) and m.get("content")]
    if not clean:
        return web.json_response({"error": "No message."}, status=400)
    # imported here: the module pulls in langchain at import time
    from bot.handlers.ai.chat import SYSTEM_PROMPT, llm

    response = await llm.ainvoke([("system", SYSTEM_PROMPT), *clean])
    return web.json_response({"reply": response.content})


# ---------------------------------------------------------------------------
# music library (plan §7 / §12.14)
# ---------------------------------------------------------------------------
# The Mini App's visual library: the same facade reads as chat (one full
# per-user read, Python-side filter/sort/page) plus the per-track actions
# "Get again" (the plan §3 ladder, shared core utils.resend), the 30s
# preview (in-app <audio>, no chat message) and remove. Auth is the
# initData blob — MANDATORY here (unlike /api/jobs, which has a browser
# preview mode): the library is personal data, so anonymous = 401.

LIB_VIEWS = ("latest", "search", "often", "browse", "daily")
LIB_SORTS = ("recent", "added", "az")
LIB_DEFAULT_PAGE_SIZE = 30
LIB_MAX_PAGE_SIZE = 100

# Cover cache (plan §7): the remote cover_url is proxied + cached as a
# <=480 px JPEG — the grid never hotlinks full-size CDN images, and a
# dead CDN degrades to the letter tile in the SPA instead of a broken img.
COVER_DIR = Path(__file__).resolve().parent.parent / "assets" / "covers"
COVER_MAX_EDGE = 480
COVER_MAX_BYTES = 190_000
COVER_MAX_FILES = 500
COVER_FETCH_TIMEOUT = 15.0


async def _auth_required(request: web.Request) -> dict:
    """initData is mandatory on the library routes; a missing or invalid
    blob raises Unauthorized (401)."""
    user = await _auth(request)
    if user is None:
        raise Unauthorized()
    return user


def _lib_unavailable():
    """S1 for the app (the chat deck's LIB_UNAVAILABLE tone): a DB
    failure must never look like the empty library (design §10.5)."""
    return web.json_response(
        {"error": "My music is temporarily unavailable. Try again shortly."},
        status=503,
    )


def _iso_ts(dt) -> str | None:
    """Naive server-local timestamp -> ISO with the server's UTC offset,
    so the SPA parses the same instant on a device in any timezone."""
    if dt is None:
        return None
    return dt.astimezone().isoformat()


def _track_json(track, tags=None) -> dict:
    """One LibraryTrack as SPA JSON (plan §12.14). `has_cover` gates the
    tile's img — the cover itself rides the authenticated proxy route.
    `has_stream` (v2 spec §5.2) gates the player: full-track playback is
    offered only when the row still carries a Telegram file_id; the raw
    id stays server-side (the stream URL embeds the bot token). `tags`
    (v2 §4.2) is the track's chip row — batched by the caller."""
    return {
        "track_key": track.track_key,
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "duration_sec": track.duration_sec,
        "duration": mlib.duration_fmt(track.duration_sec),
        "has_cover": bool(track.cover_url),
        "has_stream": bool(track.file_id),
        "tags": list(tags or ()),
        "spotify_url": track.spotify_url,
        "request_count": track.request_count,
        "added_on": _iso_ts(track.added_on),
        "last_requested": _iso_ts(track.last_requested),
    }


async def handle_library(request: web.Request):
    """GET /api/library?view=&sort=&q=&day=&page=&page_size= — one page
    of one library view: latest / search / often / browse / daily.

    `view=daily` always carries the day list (label + count, newest
    first); with `day=YYYY-MM-DD` it also carries that day's tracks.
    Owner isolation is intrinsic — the facade reads are per-user."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response(
            {"error": "This app can only be opened from Telegram."}, status=401)
    q = request.rel_url.query
    view = q.get("view", "latest")
    if view not in LIB_VIEWS:
        return web.json_response({"error": "Unknown view."}, status=400)
    sort = q.get("sort")
    if view == "browse":
        sort = sort or "recent"
        if sort not in LIB_SORTS:
            return web.json_response({"error": "Unknown sort."}, status=400)
    else:
        sort = None
    try:
        page = max(1, int(q.get("page", "1") or 1))
        page_size = int(q.get("page_size", str(LIB_DEFAULT_PAGE_SIZE)))
    except ValueError:
        return web.json_response({"error": "Bad page."}, status=400)
    page_size = min(max(page_size, 1), LIB_MAX_PAGE_SIZE)

    tg_id = user["id"]
    days_json = None
    day_iso = None
    query = ""

    # `?tag=` (WebApp v2 §4.2): restrict ANY view to one tag's tracks.
    # Empty set = the tag matches nothing (an empty page, not an error);
    # None = DB failure (the honest unavailable state).
    tag_param = (q.get("tag") or "").strip()
    tag_keys = None
    if tag_param:
        tag_keys = await mlib.tagged_track_keys(tg_id, tag_param)
        if tag_keys is None:
            return _lib_unavailable()

    if view == "daily":
        days = await mlib.daily_days(tg_id)
        if days is None:
            return _lib_unavailable()
        days_json = [
            {"day": d.isoformat(), "label": mlib.day_label(d), "count": c}
            for d, c in days
        ]
        day_param = (q.get("day") or "").strip()
        if day_param:
            try:
                day = date.fromisoformat(day_param)
            except ValueError:
                return web.json_response({"error": "Bad day."}, status=400)
            data = await mlib.daily_page(tg_id, day, page, page_size, tag_keys)
            if data is None:
                return _lib_unavailable()
            tracks, total = data
            day_iso = day.isoformat()
        else:  # the day list only — no track page yet
            tracks, total = [], 0
    elif view == "search":
        # the RAW query rides the response (the chat snapshot's display
        # contract, plan §12.13); matching normalizes internally
        query = q.get("q") or ""
        if len(mlib.normalize_query(query)) < mlib.LIB_SEARCH_MIN_CHARS:
            return web.json_response(
                {"error": "Please enter at least 2 characters."}, status=400)
        rows = await mlib.search_library(tg_id, query)
        if rows is None:
            return _lib_unavailable()
        # rows is the FULL matched set (the facade's frozen contract);
        # the page slices from it, like the chat's search snapshot
        if tag_keys is not None:
            rows = [t for t in rows if t.track_key in tag_keys]
        total = len(rows)
        pages = math.ceil(total / page_size) if total else 1
        if page > pages:
            page = 1  # the facade's out-of-range contract
        start = (page - 1) * page_size
        tracks = rows[start:start + page_size]
    else:  # latest / often / browse — the facade pages (and clamps)
        data = await mlib.library_page(
            tg_id, view, sort=sort, page=page, page_size=page_size,
            tag_keys=tag_keys)
        if data is None:
            return _lib_unavailable()
        tracks, total = data

    pages = math.ceil(total / page_size) if total else 1
    if page > pages:
        page = 1  # echo the page the rows actually came from (the facade
        # re-clamps out-of-range pages to 1 — the rows may have moved)
    tags_map = await mlib.track_tags_map(
        tg_id, [t.track_key for t in tracks])
    return web.json_response({
        "view": view,
        "sort": sort,
        "query": query,
        "tag": tag_param or None,
        "day": day_iso,
        "days": days_json,
        "page": page,
        "pages": pages,
        "total": total,
        "tracks": [_track_json(t, tags_map.get(t.track_key)) for t in tracks],
    })


def _cover_cache_path(tg_id: int, track_key: str, cover_url: str) -> Path:
    digest = hashlib.sha1(
        f"{tg_id}:{track_key}:{cover_url}".encode()).hexdigest()[:20]
    return COVER_DIR / f"{digest}.jpg"


def _fetch_cover_image(url: str) -> bytes | None:
    """Fetch one remote cover, re-encode as a <=480 px JPEG (the
    prepare_cover pipeline, plan §7). Sync — runs in a worker thread."""
    try:
        resp = requests.get(url, timeout=COVER_FETCH_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        logger.warning("cover fetch failed for %s", url[:80], exc_info=True)
        return None
    try:
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        img.thumbnail((COVER_MAX_EDGE, COVER_MAX_EDGE), Image.LANCZOS)
        data = None
        for quality in (85, 70, 55):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            data = buf.getvalue()
            if len(data) <= COVER_MAX_BYTES:
                break
        return data
    except Exception:
        logger.warning("cover encode failed for %s", url[:80], exc_info=True)
        return None


def _evict_covers() -> None:
    """Bound the cover cache: oldest-first deletion past COVER_MAX_FILES."""
    try:
        files = sorted(COVER_DIR.glob("*.jpg"),
                       key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    for path in files[:-COVER_MAX_FILES + 100]:
        try:
            path.unlink()
        except OSError:
            pass


async def handle_library_cover(request: web.Request):
    """GET /api/library/cover/<track_key>?initData=... — the tile's
    cover, proxied + cached (plan §7). The fetched URL is the DB row's
    OWN cover_url (fresh read) — never a client-supplied URL."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    track = await mlib.get_library_track(
        user["id"], request.match_info["track_key"])
    if track is None or not track.cover_url:
        return web.json_response({"error": "Unknown track."}, status=404)
    headers = {"Cache-Control": "private, max-age=3600"}
    path = _cover_cache_path(user["id"], track.track_key, track.cover_url)
    if path.is_file():
        return web.FileResponse(path, headers=headers)
    data = await asyncio.to_thread(_fetch_cover_image, track.cover_url)
    if data is None:
        return web.json_response(
            {"error": "Cover image is unavailable right now."}, status=502)
    try:
        COVER_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError:
        logger.warning("could not write the cover cache", exc_info=True)
    else:
        _evict_covers()
    return web.Response(body=data, content_type="image/jpeg",
                        headers=headers)


# --- "Get again": the plan §3 ladder from the app (plan §12.14) --------------

def _resend_key(tg_id: int, track_key: str) -> str:
    return f"{tg_id}:{track_key}"


def _prune_resends(resends: dict) -> None:
    now = time.time()
    for key in [k for k, s in resends.items()
                if s["state"] in ("sent", "failed")
                and now - s["created"] > 3600]:
        resends.pop(key, None)


async def _redownload_spotify(app: web.Application, tg_id: int, track,
                              status: dict) -> None:
    """The app's ladder step 3 (plan §12.14): re-download the Spotify
    track (the SAME spotdl primitives the chat flow drives) and deliver
    it to the owner's chat with send_audio, then refresh the row's cloud
    handles + request count (the next re-send takes the fast file_id
    path). `status` is the caller's in-memory entry — the SPA polls it
    at .../resend/status. No chat status card and no tasks-registry
    entry: the app owns the progress display for app-initiated work."""
    from bot.handlers.music.spotify import download as spotify_download

    bot = app["bot"]
    path = None
    try:
        status["state"] = "resolving"
        songs = await asyncio.to_thread(
            spotify_download.search_term, app["spotdl"], track.spotify_url)
        if not songs:
            status.update(state="failed",
                          error="That link no longer resolves to a track.")
            return
        status["state"] = "downloading"
        result_song, path = await spotify_download.await_download_from_spotify(
            app["spotdl"], songs[0])
        if path is None:
            status.update(state="failed",
                          error="This download failed. Check the link or try again.")
            return
        status["state"] = "sending"
        await bot.send_chat_action(chat_id=tg_id,
                                   action=ChatAction.UPLOAD_VOICE)
        with open(str(path), "rb") as f:
            message = await bot.send_audio(
                chat_id=tg_id,
                audio=f,
                thumbnail=getattr(result_song, "cover_url", None)
                or track.cover_url,
                title=track.title or None,
                performer=track.artist or None,
                duration=track.duration_sec or None,
                protect_content=False,
            )
        file_ref = None
        try:
            audio = getattr(message, "audio", None)
            file_id = getattr(audio, "file_id", None) if audio else None
            message_id = getattr(message, "message_id", None)
            if isinstance(file_id, str) and file_id and message_id is not None:
                file_ref = (file_id, tg_id, message_id)
        except Exception:
            logger.debug("could not read file refs from the re-send "
                         "response", exc_info=True)
        status["state"] = "sent"
        mlib.record_file_ref(
            tg_id, track.track_key,
            dict(title=track.title, artist=track.artist, album=track.album,
                 duration_sec=track.duration_sec, cover_url=track.cover_url,
                 spotify_url=track.spotify_url, source=track.source),
            file_ref,
        )
    except Exception:
        logger.exception("library re-download failed for %r",
                         track.track_key)
        status.update(state="failed",
                      error="This download failed. Check the link or try again.")
    finally:
        # the staging file never outlives the send (plan 1.5 contract)
        if path is not None:
            cleanup_media(str(path))


async def handle_library_resend(request: web.Request):
    """POST /api/library/track/<track_key>/resend — "Get again": the
    plan §3 ladder against a FRESH row (plan §7/§12.14). Steps 1-2 run
    the shared core (file_id send, copyMessage); step 3 is this app's
    background spotdl re-download (poll .../resend/status). The chat is
    the delivery destination on every path."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    tg_id = user["id"]
    track_key = request.match_info["track_key"]
    track = await mlib.get_library_track(tg_id, track_key)
    if track is None:
        return web.json_response({"error": "Unknown track."}, status=404)

    resends = request.app["resends"]
    _prune_resends(resends)
    key = _resend_key(tg_id, track_key)
    existing = resends.get(key)
    if existing is not None and existing["state"] in (
            "resolving", "downloading", "sending"):
        return web.json_response(
            {"error": "Already working on this track."}, status=409)

    result = await resend.redeliver(request.app["bot"], chat_id=tg_id,
                                    track=track)
    state = result["state"]
    if state == resend.FLOOD:
        return web.json_response(
            {"error": (
                "Telegram is limiting messages. "
                f"Wait {result['retry_after']} seconds, then try again.")},
            status=429)
    if state in (resend.SENT_FILE_ID, resend.SENT_COPIED):
        mlib.record_request(tg_id, track_key)
        return web.json_response({"state": "sent", "via": state})
    if state == resend.EXHAUSTED or request.app["spotdl"] is None:
        # nothing left to try (no cloud copy, no link — or the spotdl
        # engine is not up): the honest failure, never a silent skip
        return web.json_response(
            {"error": ("We couldn't retrieve this track. It may no longer "
                       "be available from its source.")},
            status=409)
    entry = {"state": "resolving", "error": None, "created": time.time()}
    resends[key] = entry
    asyncio.get_running_loop().create_task(
        _redownload_spotify(request.app, tg_id, track, entry))
    return web.json_response({"state": "queued"})


async def handle_library_resend_status(request: web.Request):
    """GET /api/library/track/<track_key>/resend/status — the outcome of
    a queued re-download for THIS owner+track (404: nothing in flight
    and nothing finished within the prune window)."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    entry = request.app["resends"].get(
        _resend_key(user["id"], request.match_info["track_key"]))
    if entry is None:
        return web.json_response(
            {"error": "No re-send in progress."}, status=404)
    return web.json_response({"state": entry["state"], "error": entry["error"]})


async def handle_library_preview(request: web.Request):
    """POST /api/library/track/<track_key>/preview — the 30s clip URL
    (iTunes/Deezer, the same resolve as the chat's preview action) for
    the SPA's in-app <audio> player (plan §12.14). No chat message: the
    app plays it locally."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    track = await mlib.get_library_track(
        user["id"], request.match_info["track_key"])
    if track is None:
        return web.json_response({"error": "Unknown track."}, status=404)
    url = await resolve_preview_url(track.title, track.artist)
    if not url:
        return web.json_response(
            {"error": "No 30s preview found for this track."}, status=404)
    return web.json_response({"url": url})


async def handle_library_remove(request: web.Request):
    """POST /api/library/track/<track_key>/remove — delete one row. The
    SPA carries the confirm (M05b's contract): previously delivered
    messages are untouched."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    track_key = request.match_info["track_key"]
    track = await mlib.get_library_track(user["id"], track_key)
    if track is None:
        return web.json_response(
            {"error": "This track is no longer in your library."}, status=404)
    ok = await mlib.remove_track(user["id"], track_key)
    if not ok:
        return web.json_response(
            {"error": "That didn't work — please try again."}, status=503)
    return web.json_response({"ok": True})


# --- full playback: the Range proxy to the Telegram file (spec §3/§4.2) --------
# The token embeds in the Telegram file URL, so the client never sees it:
# every byte of full playback crosses this proxy. Range pass-through is
# what lets <audio> seek + play without buffering the whole file.
# Verified against the live API 2026-11-25: the file endpoint answers
# 200 + Accept-Ranges and 206 + Content-Range for byte ranges.

STREAM_FILE_PATH_TTL = 3600  # a getFile result is guaranteed ~1h (Bot API)
STREAM_MAX_CACHED_PATHS = 500
STREAM_UPSTREAM_TIMEOUT = aiohttp.ClientTimeout(
    total=None, connect=15, sock_connect=15, sock_read=120)


def _stream_session(app: web.Application) -> aiohttp.ClientSession:
    """One shared upstream session per app (lazy — create_app has no
    event loop yet; tests' loop provides one on first use)."""
    session = app.get("stream_session")
    if session is None or session.closed:
        session = aiohttp.ClientSession()
        app["stream_session"] = session
    return session


async def _close_stream_session(app: web.Application) -> None:
    session = app.get("stream_session")
    if session is not None and not session.closed:
        await session.close()


async def _telegram_file_url(app: web.Application, file_id: str) -> str | None:
    """file_id -> the full https://api.telegram.org/file/bot…/… URL,
    cached for ~1h. None when Telegram no longer knows the file (the
    handler then answers the same "no-source" as a row without file_id
    — the SPA falls back to the 30s preview)."""
    cache = app["stream_file_paths"]
    now = time.time()
    entry = cache.get(file_id)
    if entry is not None and now - entry[1] < STREAM_FILE_PATH_TTL:
        return entry[0]
    try:
        file_obj = await app["bot"].get_file(file_id)
    except Exception:
        logger.warning("stream getFile failed for a library file_id",
                       exc_info=True)
        return None
    url = str(file_obj.file_path or "")
    if not url.startswith(("http://", "https://")):
        # PTB 22.x returns the full URL; an absolute path (a local Bot
        # API server) or a bare path falls back to the public endpoint
        url = f"https://api.telegram.org/file/bot{TOKEN}/{url.lstrip('/')}"
    if len(cache) > STREAM_MAX_CACHED_PATHS:
        for key in [k for k, v in cache.items()
                    if now - v[1] >= STREAM_FILE_PATH_TTL]:
            cache.pop(key, None)
    cache[file_id] = (url, now)
    return url


_STREAM_MIME = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".ogg": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav",
    ".flac": "audio/flac", ".mp4": "video/mp4", ".webm": "video/webm",
}


def _stream_content_type(url: str) -> str | None:
    ext = os.path.splitext(urlsplit(url).path)[1].lower()
    return _STREAM_MIME.get(ext)


async def handle_library_stream(request: web.Request):
    """GET /api/library/track/{track_key}/stream?initData=... — full
    playback, proxied from Telegram with Range pass-through (spec §3).

    Auth is the initData blob, like every library route. 404
    {"error": "no-source"} covers BOTH a row without a file_id and a
    file_id Telegram no longer serves — the SPA treats them identically
    (30s preview + the honest "full playback unavailable" state)."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    track_key = request.match_info["track_key"]
    track = await mlib.get_library_track(user["id"], track_key)
    if track is None:
        return web.json_response({"error": "Unknown track."}, status=404)
    if not track.file_id:
        return web.json_response({"error": "no-source"}, status=404)
    file_url = await _telegram_file_url(request.app, track.file_id)
    if file_url is None:
        return web.json_response({"error": "no-source"}, status=404)

    upstream_headers: dict[str, str] = {}
    range_header = request.headers.get("Range")
    if range_header:
        upstream_headers["Range"] = range_header
    session = _stream_session(request.app)
    try:
        upstream = await session.get(
            file_url, headers=upstream_headers,
            timeout=STREAM_UPSTREAM_TIMEOUT)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        logger.warning("stream upstream fetch failed", exc_info=True)
        return web.json_response(
            {"error": "The source file is unavailable right now."}, status=502)
    try:
        if upstream.status in (404, 410):
            # Telegram purged the file since getFile (or the file_id
            # went stale): same contract as no file_id
            return web.json_response({"error": "no-source"}, status=404)
        if upstream.status == 416:
            return web.Response(
                status=416,
                headers={"Content-Range":
                         upstream.headers.get("Content-Range", "")})
        if upstream.status not in (200, 206):
            logger.warning("stream upstream answered %s", upstream.status)
            return web.json_response(
                {"error": "The source file is unavailable right now."},
                status=502)
        content_type = (_stream_content_type(file_url)
                        or upstream.headers.get("Content-Type")
                        or "audio/mpeg")
        resp = web.StreamResponse(
            status=upstream.status,
            headers={
                "Content-Type": content_type,
                "Accept-Ranges": "bytes",
                "Cache-Control": "private, max-age=3600",
            },
        )
        content_length = upstream.headers.get("Content-Length")
        if content_length:
            resp.headers["Content-Length"] = content_length
        content_range = upstream.headers.get("Content-Range")
        if content_range:
            resp.headers["Content-Range"] = content_range
        await resp.prepare(request)
        async for chunk in upstream.content.iter_chunked(CHUNK_SIZE):
            await resp.write(chunk)
        await resp.write_eof()
    finally:
        upstream.release()
    return resp


# --- playlists (WebApp v2 spec §4.2) -------------------------------------------
# Same error contract as the rest of the library: 401 anonymous, 503 the
# honest "temporarily unavailable", 404 unknown id, 409 a name collision
# (case-insensitive), 400 bad input.

async def _json_body(request: web.Request) -> dict | None:
    """The POST/PATCH body as a dict; None when it is not one (the
    handler answers 400 — a malformed body must never 500)."""
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _playlist_json(playlist, count: int) -> dict:
    return {
        "id": playlist.id,
        "name": playlist.name,
        "count": count,
        "created_on": _iso_ts(playlist.created_on),
        "updated_on": _iso_ts(playlist.updated_on),
    }


async def handle_library_playlists(request: web.Request):
    """GET /api/library/playlists — every playlist with its track
    count, newest first."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    rows = await mlib.playlist_list(user["id"])
    if rows is None:
        return _lib_unavailable()
    return web.json_response({"playlists": rows})


async def handle_library_playlist_create(request: web.Request):
    """POST /api/library/playlists — {name}; 409 on a case-insensitive
    duplicate, 201 with the playlist on success."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    body = await _json_body(request)
    name = (body.get("name") or "").strip() if body else ""
    if not name:
        return web.json_response({"error": "Name required."}, status=400)
    if len(name) > 120:
        return web.json_response({"error": "Name too long."}, status=400)
    result = await mlib.playlist_create(user["id"], name)
    if result is None:
        return _lib_unavailable()
    if result == "duplicate":
        return web.json_response(
            {"error": "A playlist with this name already exists."}, status=409)
    return web.json_response(result, status=201)


async def handle_library_playlist_rename(request: web.Request):
    """PATCH /api/library/playlists/{id} — {name}; 409 on a
    case-insensitive collision, 404 for an unknown id."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    body = await _json_body(request)
    name = (body.get("name") or "").strip() if body else ""
    if not name:
        return web.json_response({"error": "Name required."}, status=400)
    if len(name) > 120:
        return web.json_response({"error": "Name too long."}, status=400)
    try:
        playlist_id = int(request.match_info["id"])
    except ValueError:
        return web.json_response({"error": "Unknown playlist."}, status=404)
    result = await mlib.playlist_rename(user["id"], playlist_id, name)
    if result is None:
        return _lib_unavailable()
    if result == "missing":
        return web.json_response({"error": "Unknown playlist."}, status=404)
    if result == "duplicate":
        return web.json_response(
            {"error": "A playlist with this name already exists."}, status=409)
    return web.json_response(result)


async def handle_library_playlist_delete(request: web.Request):
    """DELETE /api/library/playlists/{id} — deletes the playlist only
    (the tracks stay in the library)."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    try:
        playlist_id = int(request.match_info["id"])
    except ValueError:
        return web.json_response({"error": "Unknown playlist."}, status=404)
    ok = await mlib.playlist_delete(user["id"], playlist_id)
    if not ok:
        return web.json_response({"error": "Unknown playlist."}, status=404)
    return web.json_response({"ok": True})


async def handle_library_playlist_tracks(request: web.Request):
    """GET /api/library/playlists/{id}/tracks?page= — the playlist's
    tracks in playback order, paged, library row shape."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    q = request.rel_url.query
    try:
        playlist_id = int(request.match_info["id"])
        page = max(1, int(q.get("page", "1") or 1))
        page_size = int(q.get("page_size", str(LIB_DEFAULT_PAGE_SIZE)))
    except ValueError:
        return web.json_response({"error": "Bad page."}, status=400)
    page_size = min(max(page_size, 1), LIB_MAX_PAGE_SIZE)
    data = await mlib.playlist_tracks_page(
        user["id"], playlist_id, page, page_size)
    if data is None:
        return _lib_unavailable()
    if data == "missing":
        return web.json_response({"error": "Unknown playlist."}, status=404)
    tracks, total = data
    pages = math.ceil(total / page_size) if total else 1
    if page > pages:
        page = 1
    tags_map = await mlib.track_tags_map(
        user["id"], [t.track_key for t in tracks])
    return web.json_response({
        "playlist_id": playlist_id,
        "page": page,
        "pages": pages,
        "total": total,
        "tracks": [_track_json(t, tags_map.get(t.track_key)) for t in tracks],
    })


async def handle_library_playlist_tracks_add(request: web.Request):
    """POST /api/library/playlists/{id}/tracks — {track_keys: []};
    idempotent (a member added twice counts once). The response splits
    `added` from `unknown` (keys with no library row)."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    body = await _json_body(request)
    keys = body.get("track_keys") if body else None
    if not isinstance(keys, list) or not all(
            isinstance(k, str) and k.strip() for k in keys):
        return web.json_response({"error": "Bad track_keys."}, status=400)
    try:
        playlist_id = int(request.match_info["id"])
    except ValueError:
        return web.json_response({"error": "Unknown playlist."}, status=404)
    result = await mlib.playlist_add_tracks(
        user["id"], playlist_id, [k.strip() for k in keys])
    if result is None:
        return _lib_unavailable()
    if result == "missing":
        return web.json_response({"error": "Unknown playlist."}, status=404)
    added, unknown = result
    return web.json_response({"added": added, "unknown": unknown})


async def handle_library_playlist_track_remove(request: web.Request):
    """DELETE /api/library/playlists/{id}/tracks/{key} — remove one
    membership (the track stays in the library)."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    try:
        playlist_id = int(request.match_info["id"])
    except ValueError:
        return web.json_response({"error": "Unknown playlist."}, status=404)
    ok = await mlib.playlist_remove_track(
        user["id"], playlist_id, request.match_info["key"])
    if not ok:
        return web.json_response(
            {"error": "This track is not in that playlist."}, status=404)
    return web.json_response({"ok": True})


# --- tags (WebApp v2 spec §4.2) -------------------------------------------------

async def handle_library_tags(request: web.Request):
    """GET /api/library/tags — every tag with its link count, A→Z."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    rows = await mlib.tag_list(user["id"])
    if rows is None:
        return _lib_unavailable()
    return web.json_response({"tags": rows})


async def handle_library_tag_create(request: web.Request):
    """POST /api/library/tags — {name}; idempotent by design: a
    case-variant of an existing name returns THAT tag (200, exists)."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    body = await _json_body(request)
    name = (body.get("name") or "").strip() if body else ""
    if not name:
        return web.json_response({"error": "Name required."}, status=400)
    if len(name) > mlib.TAG_NAME_MAX:
        return web.json_response({"error": "Name too long."}, status=400)
    result = await mlib.tag_create(user["id"], name)
    if result is None:
        return _lib_unavailable()
    status = 200 if result.pop("exists", False) else 201
    return web.json_response(result, status=status)


async def handle_library_tag_delete(request: web.Request):
    """DELETE /api/library/tags/{id} — deletes the tag; its links to
    tracks cascade."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    try:
        tag_id = int(request.match_info["id"])
    except ValueError:
        return web.json_response({"error": "Unknown tag."}, status=404)
    ok = await mlib.tag_delete(user["id"], tag_id)
    if not ok:
        return web.json_response({"error": "Unknown tag."}, status=404)
    return web.json_response({"ok": True})


async def handle_library_track_tags(request: web.Request):
    """POST /api/library/track/{key}/tags — {names: []}: merge the
    names onto the track (existing ones matched case-insensitively,
    new ones created). Responds with the track's FULL resulting tag
    list, so the sheet never needs a second read."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    body = await _json_body(request)
    names = mlib.clean_tag_names(body.get("names")) if body else None
    if names is None:
        return web.json_response({"error": "Bad names."}, status=400)
    track_key = request.match_info["track_key"]
    result = await mlib.track_set_tags(user["id"], track_key, names)
    if result is None:
        return _lib_unavailable()
    if result == "missing":
        return web.json_response(
            {"error": "This track is no longer in your library."}, status=404)
    return web.json_response({"tags": result})


async def handle_library_track_tag_delete(request: web.Request):
    """DELETE /api/library/track/{key}/tags/{name} — unlink one tag
    (case-insensitive); idempotent. Responds with the FULL resulting
    tag list, the same contract as the merge endpoint, so the sheet
    never needs a second read."""
    try:
        user = await _auth_required(request)
    except Unauthorized:
        return web.json_response({"error": "Unauthorized."}, status=401)
    name = request.match_info["name"]
    result = await mlib.track_remove_tag(
        user["id"], request.match_info["track_key"], name)
    if result is None:
        return _lib_unavailable()
    if result == "missing":
        return web.json_response(
            {"error": "This track is no longer in your library."}, status=404)
    return web.json_response({"ok": True, "tags": result})


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------

def create_app(bot, base_url: str, spotdl=None) -> web.Application:
    app = web.Application(client_max_size=2 ** 20)  # 1 MB request bodies
    app["bot"] = bot
    app["jobs"] = {}
    app["resends"] = {}
    app["spotdl"] = spotdl  # the app's ladder step 3 (None in tests)
    app["base_url"] = base_url
    app["stream_file_paths"] = {}  # file_id -> (url, fetched_at), ~1h TTL
    app.on_shutdown.append(_close_stream_session)
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/health", handle_health)
    app.router.add_post("/api/auth", handle_auth)
    app.router.add_post("/api/jobs", handle_create_job)
    app.router.add_get("/api/jobs/{job_id}", handle_job_status)
    app.router.add_get("/api/jobs/{job_id}/file", handle_job_file)
    app.router.add_post("/api/chat", handle_chat)
    app.router.add_get("/api/library", handle_library)
    app.router.add_get("/api/library/cover/{track_key}", handle_library_cover)
    app.router.add_get("/api/library/track/{track_key}/stream",
                       handle_library_stream)
    app.router.add_get("/api/library/playlists", handle_library_playlists)
    app.router.add_post("/api/library/playlists",
                        handle_library_playlist_create)
    app.router.add_patch("/api/library/playlists/{id}",
                         handle_library_playlist_rename)
    app.router.add_delete("/api/library/playlists/{id}",
                          handle_library_playlist_delete)
    app.router.add_get("/api/library/playlists/{id}/tracks",
                       handle_library_playlist_tracks)
    app.router.add_post("/api/library/playlists/{id}/tracks",
                        handle_library_playlist_tracks_add)
    app.router.add_delete("/api/library/playlists/{id}/tracks/{key}",
                          handle_library_playlist_track_remove)
    app.router.add_get("/api/library/tags", handle_library_tags)
    app.router.add_post("/api/library/tags", handle_library_tag_create)
    app.router.add_delete("/api/library/tags/{id}", handle_library_tag_delete)
    app.router.add_post("/api/library/track/{track_key}/tags",
                        handle_library_track_tags)
    app.router.add_delete("/api/library/track/{track_key}/tags/{name}",
                          handle_library_track_tag_delete)
    app.router.add_post("/api/library/track/{track_key}/resend",
                        handle_library_resend)
    app.router.add_get("/api/library/track/{track_key}/resend/status",
                       handle_library_resend_status)
    app.router.add_post("/api/library/track/{track_key}/preview",
                        handle_library_preview)
    app.router.add_post("/api/library/track/{track_key}/remove",
                        handle_library_remove)
    return app


async def start_server(bot, host: str, port: int, base_url: str,
                       spotdl=None) -> web.AppRunner:
    """Start the web server inside the running asyncio loop.

    Returns the AppRunner so the caller could close it later; the runner
    lives as long as the application does.
    """
    app = create_app(bot, base_url, spotdl=spotdl)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Mini App web server listening on http://%s:%s", host, port)
    return runner
