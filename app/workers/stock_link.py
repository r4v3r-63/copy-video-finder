"""Фоновые операции раздела загрузки по ссылке, без изменения основного каталога."""

import threading

from PyQt6.QtCore import QThread, pyqtSignal

from app import paths
from app.services.stock_download import DEFAULT_PARALLEL_DOWNLOADS, run_stock_queue
from app.services.stock_listing import collect_listing
from app.sources.base import NetworkError, SourceError


class ListingWorker(QThread):
    page_found = pyqtSignal(list, int, int)
    problem = pyqtSignal(str)

    def __init__(self, link, adapter, parent=None):
        super().__init__(parent)
        self.link, self.adapter = link, adapter
        self.cancel = threading.Event()

    def request_stop(self):
        self.cancel.set()
        self.adapter.source.stop.set()

    def stop(self):
        self.request_stop()
        self.wait()

    def run(self):
        try:
            collect_listing(self.link, self.adapter, self.cancel, self.page_found.emit)
        except NetworkError:
            # requests может включать URL с ключом в текст исключения.
            self.problem.emit(f"{self.link.source}: ошибка подключения или таймаут. Проверьте сеть и повторите.")
        except SourceError as exc:
            self.problem.emit(exc.message)
        except (OSError, ValueError, TypeError, KeyError):
            self.problem.emit("Не удалось прочитать ответ источника. Попробуйте позже.")


class StockDownloadWorker(QThread):
    progress = pyqtSignal(int, object, object)
    done = pyqtSignal(int, str, str)
    failed = pyqtSignal(int, str)

    def __init__(self, jobs, parent=None, *, parallel=DEFAULT_PARALLEL_DOWNLOADS):
        super().__init__(parent)
        self.jobs = jobs
        self.parallel = parallel
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

    def request_stop(self):
        self.cancel.set()
        for job in self.jobs:
            job.cancel.set()
        self.resume()

    def stop(self):
        self.request_stop()
        self.wait()

    def run(self):
        ffmpeg, ffprobe = paths.ffmpeg_tool("ffmpeg"), paths.ffmpeg_tool("ffprobe")
        run_stock_queue(self.jobs, self.cancel, self.paused, self.progress.emit, self.done.emit,
                        self.failed.emit, ffmpeg, ffprobe, self.parallel)
