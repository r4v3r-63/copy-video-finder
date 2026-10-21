"""Кадры из ролика в фоне."""

import threading
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from app.services.frames import extract_frames


class FramesWorker(QThread):
    """Кадры в фоне; key — какой ролик, чтобы поздний ответ не попал к другому."""
    done = pyqtSignal(object, list)  # ключ ролика, [(время, PNG)]

    def __init__(self, key, source: str, duration: float | None, ffmpeg: Path, parent=None):
        super().__init__(parent)
        self.key, self.source, self.duration, self.ffmpeg = key, source, duration, ffmpeg
        self.cancel = threading.Event()

    def run(self):
        self.done.emit(self.key, extract_frames(self.source, self.duration, self.ffmpeg, self.cancel))
