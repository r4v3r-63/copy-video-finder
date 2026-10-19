"""Выполнение поиска: все фразы плана на всех площадках, параллельно, в фоне.

Результаты объединяются по паре (площадка, ID) и фильтруются по длительности,
ориентации и лицензии: в выдачу попадают только подходящие ролики.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtCore import QThread, pyqtSignal

from app import licenses
from app.query import SearchPlan
from app.sources.base import AuthError, RateLimitError, SearchFilters, Source, SourceError, VideoResult

log = logging.getLogger(__name__)


def is_suitable(result: VideoResult, filters: SearchFilters, only_without_attribution: bool = False) -> bool:
    if result.duration is not None and not (filters.min_duration <= result.duration <= filters.max_duration):
        return False
    if result.width and result.height:
        if filters.orientation == "landscape" and result.width <= result.height:
            return False
        if filters.orientation == "portrait" and result.width >= result.height:
            return False
        if min(result.width, result.height) < filters.min_height:
            return False
    status = result.license.status if result.license else licenses.REVIEW
    if only_without_attribution:
        return status == licenses.ALLOWED
    return status in licenses.SUITABLE


def preview_file(result: VideoResult):
    """Файл для просмотра: самый лёгкий из вариантов не ниже 360p, иначе любой."""
    files = sorted(result.files, key=lambda f: f.height or 0)
    for f in files:
        if (f.height or 0) >= 360:
            return f
    return files[-1] if files else None


class SearchWorker(QThread):
    found = pyqtSignal(list)  # list[VideoResult] — новые подходящие ролики
    problem = pyqtSignal(str)  # сообщение об ошибке площадки
    progress = pyqtSignal(int, int)  # выполнено запросов, всего

    def __init__(self, plan: SearchPlan, sources: list[Source], filters: SearchFilters,
                 only_without_attribution: bool = False, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.sources = sources
        self.filters = filters
        self.only_without_attribution = only_without_attribution
        self._cancel = threading.Event()
        self.seen: set[tuple[str, str]] = set()
        self.rejected = 0

    def cancel(self):
        self._cancel.set()

    def run(self):
        tasks = [(s, q) for s in self.sources for q in self.plan.queries]
        total = len(tasks)
        done = 0
        failed_sources: set[str] = set()
        self.progress.emit(0, total)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(self._run_one, s, q): s for s, q in tasks}
            for future in as_completed(futures):
                done += 1
                if self._cancel.is_set():
                    pool.shutdown(cancel_futures=True)
                    return
                source = futures[future]
                try:
                    results = future.result()
                except (AuthError, RateLimitError) as exc:
                    # Одной ошибки по ключу или лимиту достаточно — не повторяем сообщение.
                    if source.name not in failed_sources:
                        failed_sources.add(source.name)
                        self.problem.emit(exc.message)
                    results = []
                except SourceError as exc:
                    self.problem.emit(exc.message)
                    results = []
                except Exception:  # noqa: BLE001 — ошибка одной площадки не должна ронять поиск
                    log.exception("Ошибка поиска на %s", source.name)
                    self.problem.emit(f"Непредвиденная ошибка при поиске на {source.name}, подробности в журнале.")
                    results = []
                fresh = []
                for r in results:
                    if r.key in self.seen:
                        continue
                    self.seen.add(r.key)
                    if is_suitable(r, self.filters, self.only_without_attribution):
                        fresh.append(r)
                    else:
                        self.rejected += 1
                if fresh:
                    self.found.emit(fresh)
                self.progress.emit(done, total)

    def _run_one(self, source: Source, query) -> list[VideoResult]:
        if self._cancel.is_set():
            return []
        return source.search(query.text, self.filters, page=1, lang=query.lang)
