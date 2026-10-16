"""Downloads page: the install/update queue."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QSpinBox, QVBoxLayout, QWidget

from nebula.core.utils import eta_text, human_size, human_speed
from nebula.ui import theme
from nebula.ui.pages.base import Page, scroll_page
from nebula.ui.widgets.common import RemoteImage, Sparkline, button, card, clear_layout, icon_button, label

VERBS = {"install": "Installing", "update": "Updating", "repair": "Verifying", "uninstall": "Uninstalling",
         "dlc": "Installing DLC", "runner": "Downloading"}


class JobRow(QWidget):
    def __init__(self, page, job, big: bool = False):
        super().__init__()
        self.page = page
        self.job = job
        s = page.s
        c = card()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(c)
        lay = QHBoxLayout(c)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(14)
        entry = s.library.entry_for_uid(job.uid)
        size = QSize(78, 110) if big else QSize(44, 62)
        img = RemoteImage(size, radius=5)
        img.set_source(entry.cover_urls() if entry else [], job.title)
        lay.addWidget(img)
        col = QVBoxLayout()
        col.setSpacing(4)
        t = label(job.title)
        t.setStyleSheet("font-weight:600; font-size: 12pt;" if big else "font-weight:600;")
        col.addWidget(t)
        self.stage = label("", "Subtle")
        col.addWidget(self.stage)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        col.addWidget(self.bar)
        self.sub = label("", "Faint")
        col.addWidget(self.sub)
        lay.addLayout(col, 1)
        self.spark = None
        if big:
            self.spark = Sparkline()
            self.spark.setFixedWidth(220)
            lay.addWidget(self.spark)
        btns = QHBoxLayout()
        btns.setSpacing(2)
        if job.active:
            if job.state == "queued":
                up = icon_button("up", "Move up")
                up.clicked.connect(lambda: s.jobs.move(job.id, -1))
                down = icon_button("down", "Move down")
                down.clicked.connect(lambda: s.jobs.move(job.id, 1))
                btns.addWidget(up)
                btns.addWidget(down)
            if job.can_pause:
                self.pause = icon_button("pause", "Pause")
                self.pause.clicked.connect(self._toggle)
                btns.addWidget(self.pause)
            cancel = icon_button("x", "Cancel")
            cancel.clicked.connect(lambda: s.jobs.cancel(job.id))
            btns.addWidget(cancel)
        else:
            if job.uid and s.library.entry_for_uid(job.uid):
                go = icon_button("forward", "Open game")
                go.clicked.connect(lambda: page.win.navigate("game", s.library.entry_for_uid(job.uid).key))
                btns.addWidget(go)
        lay.addLayout(btns)
        self.update_progress()

    def _toggle(self) -> None:
        jobs = self.page.s.jobs
        (jobs.resume if self.job.state == "paused" else jobs.pause)(self.job.id)

    def update_progress(self) -> None:
        j = self.job
        verb = VERBS.get(j.kind, j.kind.title())
        if j.state == "done":
            self.stage.setText(f"{verb.replace('ing', 'ed').replace('Installed DLC', 'DLC installed')} • "
                               f"{datetime.fromtimestamp(j.finished).strftime('%H:%M')}")
            self.stage.setObjectName("Success")
        elif j.state == "failed":
            self.stage.setText(f"Failed: {j.error}")
            self.stage.setStyleSheet(f"color:{theme.DANGER};")
        elif j.state == "canceled":
            self.stage.setText("Canceled")
        else:
            self.stage.setText(f"{verb} — {j.stage}" if j.state != "queued" else "Queued")
        self.bar.setVisible(j.active)
        self.bar.setValue(int(j.progress * 1000))
        parts = []
        if j.total:
            parts.append(f"{human_size(j.done)} / {human_size(j.total)}  ({j.progress * 100:.1f}%)")
        if j.state == "running" and j.speed > 1024:
            parts.append(human_speed(j.speed))
            parts.append(f"ETA {eta_text(j.eta)}")
        if j.state == "paused":
            parts.append("Paused")
        self.sub.setText("   •   ".join(parts))
        self.sub.setVisible(bool(parts))
        if hasattr(self, "pause"):
            from nebula.ui import icons

            self.pause.setIcon(icons.icon("play" if j.state == "paused" else "pause"))
            self.pause.setToolTip("Resume" if j.state == "paused" else "Pause")
        if self.spark is not None:
            self.spark.set_values(j.speed_history)


class DownloadsPage(Page):
    name = "Downloads"

    def __init__(self, services, ctl, window):
        super().__init__(services, ctl, window)
        lay = scroll_page(self)
        head = QHBoxLayout()
        head.addWidget(label("Downloads", "PageTitle"))
        head.addStretch(1)
        head.addWidget(label("Speed limit", "Faint"))
        self.limit = QSpinBox()
        self.limit.setRange(0, 1_000_000)
        self.limit.setSingleStep(512)
        self.limit.setSuffix(" KB/s")
        self.limit.setSpecialValueText("Unlimited")
        self.limit.setValue(int(services.settings.get("downloads.speed_limit_kbps") or 0))
        self.limit.valueChanged.connect(lambda v: services.settings.set("downloads.speed_limit_kbps", v))
        head.addWidget(self.limit)
        self.pause_all = button("Pause all", "pause")
        self.pause_all.clicked.connect(self._pause_all)
        head.addWidget(self.pause_all)
        lay.addLayout(head)
        self.summary = label("", "Subtle")
        lay.addWidget(self.summary)
        lay.addWidget(label("Active", "SectionTitle"))
        self.active = QVBoxLayout()
        self.active.setSpacing(8)
        lay.addLayout(self.active)
        self.queue_title = label("Up next", "SectionTitle")
        lay.addWidget(self.queue_title)
        self.queue = QVBoxLayout()
        self.queue.setSpacing(6)
        lay.addLayout(self.queue)
        hist = QHBoxLayout()
        self.hist_title = label("Completed", "SectionTitle")
        hist.addWidget(self.hist_title)
        hist.addStretch(1)
        self.clear_btn = button("Clear")
        self.clear_btn.clicked.connect(services.jobs.clear_finished)
        hist.addWidget(self.clear_btn)
        lay.addLayout(hist)
        self.history = QVBoxLayout()
        self.history.setSpacing(6)
        lay.addLayout(self.history)
        lay.addStretch(1)
        self.rows: dict[str, JobRow] = {}
        services.bus.jobs_changed.connect(self.rebuild)
        services.bus.job_progress.connect(self._progress)

    def on_show(self, arg=None) -> None:
        self.rebuild()

    def _pause_all(self) -> None:
        jobs = self.s.jobs
        if any(j.state == "paused" for j in jobs.active_jobs()):
            jobs.resume_all()
        else:
            jobs.pause_all()
        self.rebuild()

    def rebuild(self) -> None:
        if not self.isVisible():
            return
        for lay in (self.active, self.queue, self.history):
            clear_layout(lay)
        self.rows.clear()
        jobs = self.s.jobs
        running = [j for j in jobs.jobs if j.state in ("running", "paused")]
        queued = [j for j in jobs.jobs if j.state == "queued"]
        done = jobs.finished_jobs()[:30]
        for j in running:
            row = JobRow(self, j, big=True)
            self.rows[j.id] = row
            self.active.addWidget(row)
        if not running:
            self.active.addWidget(label("Nothing is downloading right now.", "Faint"))
        for j in queued:
            row = JobRow(self, j)
            self.rows[j.id] = row
            self.queue.addWidget(row)
        self.queue_title.setVisible(bool(queued))
        for j in done:
            self.history.addWidget(JobRow(self, j))
        self.hist_title.setVisible(bool(done))
        self.clear_btn.setVisible(bool(done))
        paused = any(j.state == "paused" for j in jobs.active_jobs())
        self.pause_all.setText("Resume all" if paused else "Pause all")
        self.pause_all.setVisible(bool(running or queued))
        self._summary()

    def _summary(self) -> None:
        jobs = self.s.jobs.active_jobs()
        speed = sum(j.speed for j in jobs if j.state == "running")
        remaining = sum(max(0, j.total - j.done) for j in jobs)
        if jobs:
            self.summary.setText(f"{len(jobs)} item{'s' if len(jobs) != 1 else ''} • "
                                 f"{human_size(remaining)} remaining" + (f" • {human_speed(speed)}" if speed else ""))
        else:
            self.summary.setText("All downloads finished.")

    def _progress(self, job_id: str) -> None:
        row = self.rows.get(job_id)
        if row is not None and self.isVisible():
            row.update_progress()
            self._summary()

