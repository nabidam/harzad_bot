# coding: utf-8
"""Detached worker threads for long blocking calls (redesign §4.4).

Two incident-driven contracts (2026-10-28 21:39 Spotify stall):

1. Detach, never join. A worker doing a long network call cannot be
   killed; a ThreadPoolExecutor context manager (shutdown(wait=True))
   would block the event-loop thread in __exit__ until the worker
   returns — a cancel would freeze the whole bot for the remainder of
   the call. The pool is therefore shut down with wait=False right
   after the submit: on cancel/timeout the worker simply outlives the
   wait and its late result is discarded (the copy never claims the
   backend stopped).

2. Finished work is never thrown away at the guard line. A process
   stop (SIGSTOP, cgroup freezer, host suspend) makes the local guard
   timer expire while the worker has already finished; on resume the
   timer beats the delivery of the result. `grace_done` gives the
   worker a short grace to land before the timeout is declared — the
   same rule as a same-tick race, extended across the freeze boundary
   (a finished resolve/search wins; the work is done).
"""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

# The grace: long enough for a worker that finished while the process
# was stopped (a resume race of microseconds) to register, short enough
# that a genuinely dead backend still times out on time (0.1 s total).
GRACE_STEPS = 10
GRACE_STEP_SECONDS = 0.01


async def grace_done(done_check: Callable[[], bool]) -> bool:
    """Poll `done_check` for a short grace. True when it went done
    during the grace (see module docstring, contract 2)."""
    deadline = time.monotonic() + GRACE_STEPS * GRACE_STEP_SECONDS
    while not done_check():
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(GRACE_STEP_SECONDS)
    return True


class DetachedWorker:
    """Run a blocking fn in a dedicated worker thread, wrapped for the
    running loop.

    `fut` is the loop-side future (await it). `done()` reflects the
    worker's own state — checkable from the loop without depending on
    the delivery of the result (the freeze-resume race). `result()`
    raises the worker's exception, like concurrent.futures.Future.
    """

    def __init__(self, fn, *args):
        loop = asyncio.get_running_loop()
        pool = ThreadPoolExecutor()
        self._worker = pool.submit(fn, *args)
        pool.shutdown(wait=False)  # never join on the loop thread
        self.fut = asyncio.wrap_future(self._worker, loop=loop)

    def done(self) -> bool:
        return self._worker.done()

    def result(self):
        return self._worker.result()

    async def post_timeout_done(self) -> bool:
        """The grace for this worker (see `grace_done`)."""
        return await grace_done(self.done)
