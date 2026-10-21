"""Фоновые операции раздела загрузки по ссылке, без изменения основного каталога."""

import threading

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from app import paths
from app.services.downloader import Cancelled, DownloadError
from app.services.stock_download import run_stock_job
from app.services.stock_listing import collect_listing
from app.sources.base import SourceError


class ListingWorker(QThread):
    page_found = pyqtSignal(list, int, int)
    problem = pyqtSignal(str)

    def __init__(self, link, adapter, parent=None):
        super().__init__(parent)
        self.link, self.adapter = link, adapter
        self.cancel = threading.Event()

    def stop(self):
        self.cancel.set()
        self.adapter.source.stop.set()
        self.wait()

    def run(self):
        try:
            collect_listing(self.link, self.adapter, self.cancel, self.page_found.emit)
        except SourceError as exc:
            self.problem.emit(exc.message)
        except (OSError, ValueError, TypeError, KeyError):
            self.problem.emit("Не удалось прочитать ответ источника. Попробуйте позже.")


class StockDownloadWorker(QThread):
    progress = pyqtSignal(int, object, object)
    done = pyqtSignal(int, str, str)
    failed = pyqtSignal(int, str)

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.cancel = threading.Event()
        self.paused = threading.Event()

    def pause(self):
        self.paused.set()
        for job in self.jobs:
            job.paused.set()

    def resume(self):
        for job in self.jobs:
            job.paused.clear()
        self.paused.clear()

    def stop(self):
        self.cancel.set()
        for job in self.jobs:
            job.cancel.set()
        self.resume()
        self.wait()

    def run(self):
        ffmpeg, ffprobe = paths.ffmpeg_tool("ffmpeg"), paths.ffmpeg_tool("ffprobe")
        with requests.Session() as session:
            for index, job in enumerate(self.jobs):
                while self.paused.is_set() and not self.cancel.wait(0.1):
                    pass
                if self.cancel.is_set():
                    self.failed.emit(index, "Скачивание отменено.")
                    continue
                try:
                    report = lambda done, total, i=index: self.progress.emit(i, done, total)  # noqa: E731
                    file, note = run_stock_job(job, session, report, ffmpeg, ffprobe)
                    self.done.emit(index, str(file), note)
                except Cancelled:
                    self.failed.emit(index, "Скачивание отменено.")
                except (DownloadError, OSError) as exc:
                    message = (str(exc) if isinstance(exc, DownloadError)
                               else "Нет места или нет прав на запись в папку.")
                    self.failed.emit(index, message)
                except Exception:  # noqa: BLE001 — отказ одного ролика не останавливает очередь
                    self.failed.emit(index, "Не удалось скачать ролик. Проверьте доступность и повторите.")
