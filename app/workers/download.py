"""Очередь скачивания в фоне: по одному файлу, не блокируя интерфейс."""

import logging
import queue

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from app import paths
from app.services.downloader import Cancelled, DownloadError, DownloadJob, run_job

log = logging.getLogger(__name__)


class DownloadQueue(QThread):
    """Очередь скачивания: по одному файлу, не блокируя интерфейс."""

    progress = pyqtSignal(int, int, int)  # id задания, скачано байт, всего (-1, -1 — конвертация)
    done = pyqtSignal(int, str, str)  # id, путь к файлу, примечание
    failed = pyqtSignal(int, str)  # id, сообщение

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs: queue.Queue[DownloadJob | None] = queue.Queue()
        self._next_id = 1
        self.jobs: dict[int, DownloadJob] = {}
        self.session = requests.Session()

    def add(self, job: DownloadJob) -> int:
        job.id = self._next_id
        self._next_id += 1
        self.jobs[job.id] = job
        self._jobs.put(job)
        if not self.isRunning():
            self.start()
        return job.id

    def stop(self):
        for job in self.jobs.values():
            job.cancel.set()
        self._jobs.put(None)
        self.wait()  # отмена прерывает и скачивание, и FFmpeg

    def run(self):
        ffmpeg, ffprobe = paths.ffmpeg_tool("ffmpeg"), paths.ffmpeg_tool("ffprobe")
        while True:
            job = self._jobs.get()
            if job is None:
                return
            if job.cancel.is_set():
                continue
            try:
                path, note = run_job(job, self.session, lambda d, t, j=job.id: self.progress.emit(j, d, t),
                                     ffmpeg, ffprobe)
                self.done.emit(job.id, str(path), note)
            except Cancelled:
                self.failed.emit(job.id, "Скачивание отменено.")
            except DownloadError as exc:
                self.failed.emit(job.id, str(exc))
            except OSError as exc:
                log.exception("Ошибка записи файла")
                self.failed.emit(job.id, f"Не удалось записать файл: {exc}")
            except Exception:  # noqa: BLE001
                log.exception("Ошибка скачивания")
                self.failed.emit(job.id, "Непредвиденная ошибка скачивания, подробности в журнале.")
