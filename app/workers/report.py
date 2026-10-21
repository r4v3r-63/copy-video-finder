"""Превью для отчёта PDF — в фоне."""

import threading

from PyQt6.QtCore import QThread, pyqtSignal

from app.export.report import fetch_thumbs
from app.storage import catalog as cat


class ReportWorker(QThread):
    """Превью для отчёта — в фоне, чтобы окно не замирало."""
    done = pyqtSignal(dict)

    def __init__(self, entries: list[cat.Entry], parent=None):
        super().__init__(parent)
        self.entries = entries
        self.cancel = threading.Event()

    def run(self):
        self.done.emit(fetch_thumbs(self.entries, cancel=self.cancel))
