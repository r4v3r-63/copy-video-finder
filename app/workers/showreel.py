"""Сборка шоурила в фоне."""

import threading
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from app.export.showreel import Cancelled, ShowreelError, credits_text, make_showreel
from app.storage import catalog as cat


class ShowreelWorker(QThread):
    """Сборка шоурила в фоне: progress — проценты, done — текст ошибки (пустой — готово)."""
    progress = pyqtSignal(int)
    done = pyqtSignal(str, list)

    def __init__(self, title: str, entries: list[cat.Entry], dest: Path, ffmpeg: Path, ffprobe: Path | None,
                 seconds: float, fade: float, parent=None):
        super().__init__(parent)
        self.title, self.entries, self.dest = title, entries, Path(dest)
        self.ffmpeg, self.ffprobe, self.seconds, self.fade = ffmpeg, ffprobe, seconds, fade
        self.cancel = threading.Event()

    def run(self):
        try:
            used = make_showreel(self.entries, self.dest, self.ffmpeg, self.ffprobe, self.seconds, self.fade,
                                 lambda share: self.progress.emit(int(share * 100)), self.cancel)
            self.dest.with_suffix(".txt").write_text(credits_text(self.title, used), encoding="utf-8")
        except Cancelled:
            self.done.emit("Сборка отменена.", [])
        except (ShowreelError, OSError) as exc:
            self.done.emit(str(exc), [])
        else:
            self.done.emit("", used)
