"""Проверка и скачивание обновления в фоне."""

import logging
import threading

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from app.services import updater

log = logging.getLogger(__name__)


class CheckWorker(QThread):
    found = pyqtSignal(object)  # updater.Release
    nothing = pyqtSignal()
    failed = pyqtSignal(str)

    def run(self):
        try:
            release = updater.check_latest(requests.Session())
        except Exception as exc:  # noqa: BLE001 — без сети программа работает как обычно
            log.info("Проверка обновлений не удалась: %s", exc)
            self.failed.emit("Не удалось связаться с GitHub. Проверьте подключение к интернету.")
            return
        if release:
            self.found.emit(release)
        else:
            self.nothing.emit()


class DownloadWorker(QThread):
    progress = pyqtSignal(int, int)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, release, parent=None):
        super().__init__(parent)
        self.release = release
        self.cancel = threading.Event()

    def run(self):
        try:
            path = updater.download(self.release, requests.Session(),
                                    lambda d, t: self.progress.emit(d, t), self.cancel)
            self.done.emit(str(path))
        except Exception as exc:  # noqa: BLE001
            if self.cancel.is_set():
                self.failed.emit("")
            else:
                log.exception("Не удалось скачать обновление")
                self.failed.emit(str(exc) or "Не удалось скачать обновление.")
