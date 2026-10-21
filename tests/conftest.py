import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

# isolate every test session from the real user configuration
os.environ["SUPERNOVA_HOME"] = tempfile.mkdtemp(prefix="nebula-test-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["SUPERNOVA_NO_UPDATE"] = "1"  # never self-update while testing
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    from nebula.core import tasks

    app = QApplication.instance() or QApplication([])
    tasks.init_main_thread()
    return app


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A fresh SUPERNOVA_HOME per test."""
    monkeypatch.setenv("SUPERNOVA_HOME", str(tmp_path / "nebula"))
    return tmp_path


@pytest.fixture()
def services(qapp, home):
    from nebula.services import Services

    s = Services()
    s.library.load()
    yield s
    s.launcher.timer.stop()
    s.friends.timer.stop()
    s.jobs._timer.stop()  # noqa: SLF001
    # Keep QObjects alive for the whole session: if Python's GC collected them on a worker
    # thread, Qt would destroy them off the GUI thread (in the app, Services lives forever).
    _KEEP_ALIVE.append(s)


_KEEP_ALIVE: list = []


def make_ctx():
    from nebula.core.jobs import Job, JobContext, RateLimiter

    job = Job(id="t", title="test", kind="install")
    return JobContext(job, RateLimiter(lambda: 0), lambda: 4)


class FakeServer:
    """Tiny threaded HTTP server; ``routes`` maps (method, path) -> handler(req) -> (status, headers, body)."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.hits: list[str] = []
        self.handlers = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _dispatch(self, method):
                outer.hits.append(f"{method} {self.path}")
                for (m, prefix), fn in outer.handlers.items():
                    if m == method and self.path.startswith(prefix):
                        status, headers, body = fn(self)
                        break
                else:
                    if method == "GET" and self.path in outer.files:
                        status, headers, body = 200, {}, outer.files[self.path]
                    else:
                        status, headers, body = 404, {}, b"not found"
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                self._dispatch("GET")

            def do_PUT(self):  # noqa: N802
                self._dispatch("PUT")

            def do_POST(self):  # noqa: N802
                self._dispatch("POST")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture()
def server():
    s = FakeServer()
    yield s
    s.close()
