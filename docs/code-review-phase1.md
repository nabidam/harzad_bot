# Code Review & Remediation Plan: Harzad Bot (Phase 1 Core UX Redesign)

**Date:** 2026-10-29  
**Branch:** `feature/phase1-core-ux`  
**Scope:** Verification of Phase 1 Core UX implementation against `HARZAD_UX_REDESIGN_SPEC.md` and codebase quality.

---

## 1. Executive Summary

The Phase 1 Core UX redesign successfully implements the architectural vision of `HARZAD_UX_REDESIGN_SPEC.md`:
- Navigation, input modes, and background tasks are cleanly separated.
- The universal link dispatcher intercepts supported media links from any state.
- Group-1 guard handles non-conversation users cleanly without double-firing.
- Background database writes for user syncing and logging use a non-blocking circuit breaker (`CircuitBreaker`).
- 388 automated tests pass.

This document details identified bugs, performance issues, and memory leaks, along with the step-by-step remediation plan to execute using autonomous subagents.

---

## 2. Issues Inventory & Remediation Status

### Issue 1: Cancel / Action Buttons Disappear After 5 Seconds (Critical / UX) — ✅ RESOLVED
- **Files:** `utils/progress.py`, `bot/handlers/youtube/flow.py`, `bot/handlers/music/spotify/download.py`, `bot/handlers/ai/chat.py`
- **Resolution:** Updated `status_ticker` to accept and pass `reply_markup` into `bot.edit_message_text`. Wrapped phase update handlers so active Cancel / Stop buttons are retained across threshold and track transitions.
- **Verification:** Unit tests added in `tests/test_21_progress.py`.

### Issue 2: Spotify Track Duration Set to `0:00` (High / Functional) — ✅ RESOLVED
- **Files:** `bot/handlers/music/spotify/download.py`, `tests/test_23_spotify_flow.py`
- **Resolution:** Removed redundant `/ 1000` division on `song.duration` (already seconds in spotdl `Song` model).
- **Verification:** Fixed test fixtures and verified real duration values in `tests/test_23_spotify_flow.py`.

### Issue 3: Unguarded `status.delete()` Aborts Finalization (High / Reliability) — ✅ RESOLVED
- **Files:** `bot/handlers/youtube/flow.py:421`, `tests/test_22_youtube_flow.py`
- **Resolution:** Wrapped `await status.delete()` in `try ... except TelegramError:` so that deleted status messages don't break task finalization, file cleanup, or reaction emojis.
- **Verification:** Unit test `test_single_video_status_delete_failure_does_not_abort_finalization` added.

### Issue 4: Event Loop Blocked on Preview Cancellation (High / Concurrency) — ✅ RESOLVED
- **Files:** `connectors/preview.py`
- **Resolution:** Replaced `with ThreadPoolExecutor() as pool:` with `await asyncio.to_thread(resolve_preview_url_sync, title, artist)`.
- **Verification:** Verified via `tests/test_19_preview.py`.

### Issue 5: AI Chat Answers Exceeding 4096 Characters Crash (Medium / Functional) — ✅ RESOLVED
- **Files:** `bot/handlers/ai/chat.py`, `tests/test_25_ai_chat_flow.py`
- **Resolution:** Added safe truncation at 3950 characters with `…\n<i>(truncated)</i>` notice to stay within Telegram's 4096-char ceiling.
- **Verification:** Added test `test_ai_long_response_is_truncated`.

### Issue 6: Heavy Audio Decoding for MP3 Duration (Medium / Performance) — ✅ RESOLVED
- **Files:** `bot/handlers/youtube/mp3.py`, `bot/handlers/youtube/download.py`
- **Resolution:** Replaced `pydub.AudioSegment.from_file()` with `mutagen` header inspection with yt-dlp metadata fallback, avoiding full PCM decoding in RAM. Broadened item exception catching to prevent playlist aborts.
- **Verification:** Verified via `tests/test_22_youtube_flow.py`.

### Issue 7: Unbounded In-Memory Collections (Medium / Memory Leak) — ✅ RESOLVED
- **Files:** `utils/tasks.py`, `utils/tokens.py`, `connectors/spotify_search.py`
- **Resolution:** Added `prune()` methods to `TaskRegistry` (evicting terminal tasks older than 1h / exceeding 200 items), `TokenStore` (evicting expired tokens on put), and `spotify_search` (evicting oldest / expired cache entries).
- **Verification:** Verified via `tests/test_18_spotify_search.py` and `tests/test_26_tasks_view.py`.

### Issue 8: Keyboard Helpers, Broken Code & Exposed Secrets (Medium / Hygiene) — ✅ RESOLVED
- **Files:** `utils/keyboards.py`, `utils/helpers.py`, `donwload_test.py`, `utils/decorators.py`
- **Resolution:**
  - Fixed `too_large_keyboard` and `large_file_keyboard` to construct valid `InlineKeyboardButton(text, copy_text=CopyTextButton(link))`.
  - Removed scratch file `donwload_test.py` containing committed credentials.
  - Fixed `next_proxy()` logic and removed debug print.
  - Replaced fragile `assert update.message is not None` in decorators with safe property checks.
- **Verification:** Added `test_too_large_and_large_file_keyboards` in `tests/test_03_keyboards.py`.

---

## 3. Subagent Execution Plan

We will assign subagents to resolve each issue sequentially, verifying with the test suite after each step:
1. **Agent 1:** Fix Cancel Button Retention in `utils/progress.py` & flows.
2. **Agent 2:** Fix Spotify Track Duration & update mock tests.
3. **Agent 3:** Guard `status.delete()` in `flow.run_media_flow`.
4. **Agent 4:** Fix `connectors/preview.py` blocking executor.
5. **Agent 5:** Safe truncation for AI Chat in `bot/handlers/ai/chat.py`.
6. **Agent 6:** Replace `AudioSegment` with `mutagen` in `youtube/mp3.py`.
7. **Agent 7:** Bound `TaskRegistry`, `TokenStore`, and `spotify_search._cache`.
8. **Agent 8:** Keyboard, helper cleanup, and credentials removal.
