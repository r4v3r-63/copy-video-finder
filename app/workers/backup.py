"""Резервная копия «Данных» в фоне."""

import sqlite3
import threading
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from app.storage import backup


class BackupWorker(QThread):
    """Копирование в фоне с отменой между порциями файла и страницами SQLite."""
    progress = pyqtSignal(int, int)

    def __init__(self, source: Path, dest: Path, parent=None):
        super().__init__(parent)
        self.source, self.dest = source, dest
        self.cancel = threading.Event()
        self.error = ""
        self.cancelled = False

    def run(self):
        try:
            backup.create_backup(self.source, self.dest, self.cancel, self.progress.emit)
        except backup.Cancelled:
            self.cancelled = True
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.error = f"Не удалось создать резервную копию: {exc}"
