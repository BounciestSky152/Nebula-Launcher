"""Asynchronous on-disk + in-memory image cache for cover art."""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPixmap

from nebula import paths
from nebula.core import tasks
from nebula.core.http import new_session

log = logging.getLogger(__name__)

FAIL_TTL = 3 * 24 * 3600


class ImageCache:
    def __init__(self) -> None:
        self.dir = paths.images_cache()
        self.session = new_session(pool=12)
        self._mem: OrderedDict[tuple, QPixmap] = OrderedDict()
        self._mem_limit = 600
        self._pending: dict[tuple, list[Callable]] = {}
        self._lock = threading.Lock()
        self._failed: dict[str, float] = {}

    # ------------------------------------------------------------ helpers
    def file_for(self, url: str) -> Path:
        h = hashlib.sha1(url.encode()).hexdigest()
        return self.dir / h[:2] / h

    def _is_local(self, url: str) -> bool:
        return url.startswith("/") or url.startswith("file://")

    def _local_path(self, url: str) -> Path:
        return Path(url[7:] if url.startswith("file://") else url)

    def _fail_marker(self, url: str) -> Path:
        return self.file_for(url).with_suffix(".fail")

    def _recently_failed(self, url: str) -> bool:
        ts = self._failed.get(url)
        if ts is None:
            marker = self._fail_marker(url)
            try:
                ts = marker.stat().st_mtime
            except OSError:
                return False
            self._failed[url] = ts
        return time.time() - ts < FAIL_TTL

    def fetch_file(self, url: str) -> Path | None:
        """Blocking: return a local file for ``url`` (downloading if required)."""
        if not url:
            return None
        if self._is_local(url):
            p = self._local_path(url)
            return p if p.is_file() else None
        target = self.file_for(url)
        if target.is_file() and target.stat().st_size > 0:
            return target
        if self._recently_failed(url):
            return None
        try:
            r = self.session.get(url, timeout=(8, 30))
            ctype = r.headers.get("Content-Type", "")
            if r.status_code != 200 or not r.content or ("image" not in ctype and "octet" not in ctype and ctype):
                raise OSError(f"HTTP {r.status_code}")
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".part")
            tmp.write_bytes(r.content)
            os.replace(tmp, target)
            return target
        except Exception as exc:  # noqa: BLE001
            log.debug("Image download failed %s: %s", url, exc)
            self._failed[url] = time.time()
            try:
                marker = self._fail_marker(url)
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.touch()
            except OSError:
                pass
            return None

    # ---------------------------------------------------------------- API
    def get(
        self,
        urls: list[str] | str,
        size: QSize | None,
        callback: Callable[[QPixmap | None], None],
        mode: str = "cover",
    ) -> QPixmap | None:
        """Request an image; returns it immediately when cached in memory.

        ``mode`` = "cover" crops to fill ``size``; "fit" keeps aspect ratio.
        """
        if isinstance(urls, str):
            urls = [urls]
        urls = [u for u in urls if u]
        if not urls:
            callback(None)
            return None
        key = (tuple(urls), size.width() if size else 0, size.height() if size else 0, mode)
        pm = self._mem.get(key)
        if pm is not None:
            self._mem.move_to_end(key)
            return pm
        with self._lock:
            if key in self._pending:
                self._pending[key].append(callback)
                return None
            self._pending[key] = [callback]

        def work():
            for url in urls:
                f = self.fetch_file(url)
                if not f:
                    continue
                img = QImage(str(f))
                if img.isNull():
                    continue
                if size and size.width() > 0 and size.height() > 0:
                    if mode == "cover":
                        img = img.scaled(
                            size,
                            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        x = max(0, (img.width() - size.width()) // 2)
                        y = max(0, (img.height() - size.height()) // 2)
                        img = img.copy(x, y, size.width(), size.height())
                    else:
                        img = img.scaled(
                            size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                        )
                return img
            return None

        def done(img):
            pm = QPixmap.fromImage(img) if img is not None else None
            if pm is not None:
                self._mem[key] = pm
                while len(self._mem) > self._mem_limit:
                    self._mem.popitem(last=False)
            with self._lock:
                cbs = self._pending.pop(key, [])
            for cb in cbs:
                try:
                    cb(pm)
                except RuntimeError:  # receiver widget already deleted
                    pass

        def failed(_exc):
            done(None)

        tasks.run_async(work, on_done=done, on_error=failed, io=True)
        return None

    def clear(self) -> None:
        self._mem.clear()
        self._failed.clear()
        for p in self.dir.rglob("*"):
            try:
                if p.is_file():
                    p.unlink()
            except OSError:
                pass

    def disk_usage(self) -> int:
        return sum(p.stat().st_size for p in self.dir.rglob("*") if p.is_file())


_cache: ImageCache | None = None


def images() -> ImageCache:
    global _cache
    if _cache is None:
        _cache = ImageCache()
    return _cache
