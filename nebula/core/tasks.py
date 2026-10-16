"""Background execution helpers that marshal results back to the Qt main thread."""
from __future__ import annotations

import logging
import threading
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot

log = logging.getLogger(__name__)

_pool = ThreadPoolExecutor(max_workers=12, thread_name_prefix="nebula")
_io_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="nebula-img")


class _Invoker(QObject):
    call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, fn: Callable[[], Any]) -> None:
        try:
            fn()
        except Exception:  # pragma: no cover - UI callbacks should never kill the loop
            log.error("UI callback failed:\n%s", traceback.format_exc())


_invoker: _Invoker | None = None
_main_thread = threading.main_thread()


def init_main_thread() -> None:
    """Create the invoker; must be called from the GUI thread after QApplication exists."""
    global _invoker
    if _invoker is None:
        _invoker = _Invoker()


def on_main(fn: Callable[[], Any]) -> None:
    """Run ``fn`` on the GUI thread (immediately if already there)."""
    if _invoker is None or threading.current_thread() is _main_thread:
        fn()
    else:
        _invoker.call.emit(fn)


def run_async(
    fn: Callable[..., Any],
    *args: Any,
    on_done: Callable[[Any], Any] | None = None,
    on_error: Callable[[BaseException], Any] | None = None,
    io: bool = False,
    **kwargs: Any,
) -> Future:
    """Execute ``fn`` in a worker thread; callbacks are delivered on the GUI thread."""

    def wrapper():
        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            log.debug("Background task %s failed: %s", getattr(fn, "__name__", fn), exc, exc_info=True)
            if on_error:
                on_main(lambda e=exc: on_error(e))  # bind now: `exc` is cleared when the except block ends
            raise
        if on_done:
            on_main(lambda r=result: on_done(r))
        return result

    try:
        return (_io_pool if io else _pool).submit(wrapper)
    except RuntimeError:  # interpreter / app shutting down
        return Future()


def shutdown() -> None:
    _pool.shutdown(wait=False, cancel_futures=True)
    _io_pool.shutdown(wait=False, cancel_futures=True)
