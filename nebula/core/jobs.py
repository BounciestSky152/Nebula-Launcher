"""Download / installation queue with pause, resume, cancel and a global speed limit."""
from __future__ import annotations

import itertools
import logging
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer

from nebula.core import tasks

log = logging.getLogger(__name__)

QUEUED, RUNNING, PAUSED, DONE, FAILED, CANCELED = "queued", "running", "paused", "done", "failed", "canceled"
FINISHED = (DONE, FAILED, CANCELED)


class Canceled(Exception):
    pass


class RateLimiter:
    """Token bucket shared by every running download."""

    def __init__(self, limit_fn: Callable[[], int]):
        self._limit_fn = limit_fn  # bytes per second, 0 = unlimited
        self._lock = threading.Lock()
        self._tokens = 0.0
        self._last = time.monotonic()

    def consume(self, n: int) -> None:
        limit = self._limit_fn()
        if limit <= 0:
            return
        with self._lock:
            now = time.monotonic()
            # refill (burst capped at one second worth of data), then take n tokens;
            # a negative balance is debt that the caller pays off by sleeping.
            self._tokens = min(float(limit), self._tokens + (now - self._last) * limit)
            self._last = now
            self._tokens -= n
            debt = -self._tokens
        if debt > 0:
            time.sleep(debt / limit)


@dataclass
class Job:
    id: str
    title: str
    kind: str  # install | update | repair | uninstall | dlc | runner | move
    uid: str = ""
    func: Callable[["JobContext"], Any] | None = None
    can_pause: bool = True
    verb: str = ""  # queue wording while it runs, e.g. "Rolling back to 1.0" ("" = derived from kind)
    verb_done: str = ""  # wording once it finished, e.g. "Rolled back to 1.0"
    state: str = QUEUED
    stage: str = "Queued"
    done: int = 0
    total: int = 0
    written: int = 0
    speed: float = 0.0
    created: float = field(default_factory=time.time)
    started: float = 0.0
    finished: float = 0.0
    error: str = ""
    result: Any = None
    speed_history: deque = field(default_factory=lambda: deque(maxlen=120))
    on_success: Callable[[Any], Any] | None = None
    on_failure: Callable[[str], Any] | None = None
    _cancel: threading.Event = field(default_factory=threading.Event)
    _pause: threading.Event = field(default_factory=threading.Event)
    _net: int = 0
    _last_net: int = 0
    _last_tick: float = 0.0

    @property
    def progress(self) -> float:
        if self.total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.done / self.total))

    @property
    def eta(self) -> float:
        if self.speed <= 0 or self.total <= 0:
            return float("inf")
        return max(0.0, (self.total - self.done) / self.speed)

    @property
    def active(self) -> bool:
        return self.state in (QUEUED, RUNNING, PAUSED)


class JobContext:
    """Handed to job functions so they can report progress and honour pause/cancel."""

    def __init__(self, job: Job, limiter: RateLimiter, workers: Callable[[], int]):
        self.job = job
        self._limiter = limiter
        self._workers = workers
        self._lock = threading.Lock()

    @property
    def workers(self) -> int:
        return max(1, int(self._workers()))

    @property
    def cancelled(self) -> bool:
        return self.job._cancel.is_set()

    def check(self) -> None:
        if self.job._cancel.is_set():
            raise Canceled()
        while self.job._pause.is_set():
            if self.job._cancel.is_set():
                raise Canceled()
            time.sleep(0.2)

    def stage(self, text: str) -> None:
        self.job.stage = text

    def set_total(self, total: int) -> None:
        self.job.total = int(total)

    def set_done(self, done: int) -> None:
        with self._lock:
            self.job.done = int(done)

    def add(self, n: int, network: bool = True) -> None:
        """Account ``n`` bytes of progress (throttled by the global limit when network)."""
        if network and n > 0:
            self._limiter.consume(n)
        with self._lock:
            self.job.done += n
            if network:
                self.job._net += n

    def report_external(self, done: int, total: int | None = None) -> None:
        """Progress reported by an external tool (not subject to Nebula's speed limit)."""
        with self._lock:
            if total is not None:
                self.job.total = int(total)
            delta = int(done) - self.job.done
            self.job.done = int(done)
            if delta > 0:
                self.job._net += delta

    def add_network(self, n: int) -> None:
        """Account network bytes without moving the progress bar (e.g. compressed chunks)."""
        if n > 0:
            self._limiter.consume(n)
            with self._lock:
                self.job._net += n


class JobManager(QObject):
    def __init__(self, settings, bus):
        super().__init__()
        self.settings = settings
        self.bus = bus
        self.jobs: list[Job] = []
        self._ids = itertools.count(1)
        self._lock = threading.RLock()
        self.limiter = RateLimiter(lambda: int(self.settings.get("downloads.speed_limit_kbps") or 0) * 1024)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # --------------------------------------------------------------- API
    def submit(
        self,
        title: str,
        kind: str,
        func: Callable[[JobContext], Any],
        uid: str = "",
        can_pause: bool = True,
        on_success: Callable[[Any], Any] | None = None,
        on_failure: Callable[[str], Any] | None = None,
        front: bool = False,
        verb: str = "",
        verb_done: str = "",
    ) -> Job:
        job = Job(id=str(next(self._ids)), title=title, kind=kind, uid=uid, func=func, can_pause=can_pause,
                  verb=verb, verb_done=verb_done, on_success=on_success, on_failure=on_failure)
        with self._lock:
            if front:
                idx = next((i for i, j in enumerate(self.jobs) if j.state == QUEUED), len(self.jobs))
                self.jobs.insert(idx, job)
            else:
                self.jobs.append(job)
        self.bus.jobs_changed.emit()
        self._schedule()
        return job

    def get(self, job_id: str) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)

    def active_for(self, uid: str) -> Job | None:
        return next((j for j in self.jobs if j.uid == uid and j.active), None)

    def active_jobs(self) -> list[Job]:
        return [j for j in self.jobs if j.active]

    def finished_jobs(self) -> list[Job]:
        return sorted([j for j in self.jobs if not j.active], key=lambda j: j.finished, reverse=True)

    def pause(self, job_id: str) -> None:
        job = self.get(job_id)
        if job and job.can_pause and job.state in (RUNNING, QUEUED):
            job._pause.set()
            job.state = PAUSED
            job.stage = "Paused"
            self.bus.jobs_changed.emit()
            self._schedule()

    def resume(self, job_id: str) -> None:
        job = self.get(job_id)
        if job and job.state == PAUSED:
            job._pause.clear()
            job.state = RUNNING if job.started else QUEUED
            job.stage = "Resuming" if job.started else "Queued"
            self.bus.jobs_changed.emit()
            self._schedule()

    def pause_all(self) -> None:
        for j in self.active_jobs():
            self.pause(j.id)

    def resume_all(self) -> None:
        for j in self.active_jobs():
            self.resume(j.id)

    def cancel(self, job_id: str) -> None:
        job = self.get(job_id)
        if not job or not job.active:
            return
        job._cancel.set()
        job._pause.clear()
        if not job.started:
            job.state = CANCELED
            job.stage = "Canceled"
            job.finished = time.time()
        self.bus.jobs_changed.emit()
        self._schedule()

    def move(self, job_id: str, delta: int) -> None:
        with self._lock:
            queued = [j for j in self.jobs if j.state == QUEUED]
            job = self.get(job_id)
            if not job or job not in queued:
                return
            i = queued.index(job)
            k = max(0, min(len(queued) - 1, i + delta))
            if i == k:
                return
            other = queued[k]
            a, b = self.jobs.index(job), self.jobs.index(other)
            self.jobs[a], self.jobs[b] = self.jobs[b], self.jobs[a]
        self.bus.jobs_changed.emit()

    def clear_finished(self) -> None:
        with self._lock:
            self.jobs = [j for j in self.jobs if j.active]
        self.bus.jobs_changed.emit()

    def shutdown(self) -> None:
        for j in self.active_jobs():
            j._cancel.set()
            j._pause.clear()

    # ---------------------------------------------------------- internals
    def _schedule(self) -> None:
        limit = max(1, int(self.settings.get("downloads.concurrent_jobs") or 1))
        with self._lock:
            running = [j for j in self.jobs if j.state == RUNNING]
            for job in self.jobs:
                if len(running) >= limit:
                    break
                if job.state == QUEUED and not job._cancel.is_set():
                    job.state = RUNNING
                    job.started = time.time()
                    job.stage = "Starting"
                    running.append(job)
                    threading.Thread(target=self._run, args=(job,), daemon=True, name=f"job-{job.id}").start()

    def _run(self, job: Job) -> None:
        ctx = JobContext(job, self.limiter, lambda: self.settings.get("downloads.workers") or 8)
        try:
            ctx.check()
            job.result = job.func(ctx) if job.func else None
            job.state = DONE
            job.stage = "Completed"
            job.done = job.total if job.total else job.done
        except Canceled:
            job.state = CANCELED
            job.stage = "Canceled"
        except Exception as exc:  # noqa: BLE001
            log.error("Job %s failed:\n%s", job.title, traceback.format_exc())
            job.state = FAILED
            job.error = str(exc) or exc.__class__.__name__
            job.stage = "Failed"
        job.finished = time.time()
        job.speed = 0.0

        def finish():
            if job.state == DONE and job.on_success:
                job.on_success(job.result)
            elif job.state == FAILED and job.on_failure:
                job.on_failure(job.error)
            self.bus.jobs_changed.emit()
            self._schedule()

        tasks.on_main(finish)

    def _tick(self) -> None:
        now = time.monotonic()
        for job in self.jobs:
            if job.state not in (RUNNING, PAUSED):
                continue
            dt = now - job._last_tick if job._last_tick else 0.5
            job._last_tick = now
            delta = job._net - job._last_net
            job._last_net = job._net
            inst = delta / dt if dt > 0 else 0.0
            job.speed = inst if job.speed == 0 else job.speed * 0.6 + inst * 0.4
            if job.state == PAUSED:
                job.speed = 0.0
            job.speed_history.append(job.speed)
            self.bus.job_progress.emit(job.id)
