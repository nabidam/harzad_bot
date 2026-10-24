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
import json
import os
import time
import uuid
from logging import getLogger
from pathlib import Path
from urllib.parse import parse_qs

import validators
from aiohttp import web
from telegram.constants import ParseMode

from configurations.settings import DOWNLOAD_VIDEO_PATH, MINIAPP_URL, TOKEN
from utils.constants.messages import BOT_ID, MEDIA_CAPTION
from utils.helpers import escape_html, prepare_cover
from utils.youtube_dl import YDL_OPTS

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

def _ytdlp_run(url: str):
    """Extract + download one video, mirroring the chat handler's pipeline.

    Runs in a worker thread (blocking network + disk).
    """
    from yt_dlp import YoutubeDL, utils as ytdl_utils

    with YoutubeDL(YDL_OPTS) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except ytdl_utils.DownloadError:
            raise JobError("Could not fetch that link. Check it and try again.")
        if info.get("_type") == "playlist":
            raise JobError("Playlists aren't supported in the app yet — paste a single video link.")
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
        info, filename = await asyncio.to_thread(_ytdlp_run, job["url"])
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
                    protect_content=True,
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
        job["state"], job["error"] = "error", "Download failed. Try again."


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

    jobs = request.app["jobs"]
    active = [j for j in jobs.values()
              if j["user_id"] == user_id and j["state"] not in ("done", "error")]
    if active:
        return web.json_response({"error": "Already downloading one — wait for it to finish."}, status=409)

    job = {
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "url": url,
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
                              ("state", "title", "file_name", "file_size_mb", "file_url", "file_id", "error")})


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
            "Content-Type": "video/mp4",
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
# server
# ---------------------------------------------------------------------------

def create_app(bot, base_url: str) -> web.Application:
    app = web.Application(client_max_size=2 ** 20)  # 1 MB request bodies
    app["bot"] = bot
    app["jobs"] = {}
    app["base_url"] = base_url
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/health", handle_health)
    app.router.add_post("/api/auth", handle_auth)
    app.router.add_post("/api/jobs", handle_create_job)
    app.router.add_get("/api/jobs/{job_id}", handle_job_status)
    app.router.add_get("/api/jobs/{job_id}/file", handle_job_file)
    app.router.add_post("/api/chat", handle_chat)
    return app


async def start_server(bot, host: str, port: int, base_url: str) -> web.AppRunner:
    """Start the web server inside the running asyncio loop.

    Returns the AppRunner so the caller could close it later; the runner
    lives as long as the application does.
    """
    app = create_app(bot, base_url)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Mini App web server listening on http://%s:%s", host, port)
    return runner
