"""Пакетный поиск по незаполненным услугам (ТЗ, 5.2).

Только находит кандидатов — ничего не скачивает (массовое автоматическое
скачивание запрещено). Ответы площадок кэшируются на 24 часа, поэтому потом
открыть найденное по услуге можно без новых запросов.
"""

import logging
import re
import threading

from PyQt6.QtCore import QThread, pyqtSignal

from app.core import query
from app.core.models import SearchFilters
from app.services.search import Search
from app.sources.base import Source
from app.storage import catalog as cat

log = logging.getLogger(__name__)

# Поминутные лимиты пакетный поиск пережидает; часовые (Coverr — 50 в час) — нет, площадка пропускается.
MAX_WAIT = 70


class BatchWorker(QThread):
    started_service = pyqtSignal(str, int, int)  # услуга, номер, всего
    done_service = pyqtSignal(str, int, int)  # услуга, найдено подходящих, из них новых для каталога
    problem = pyqtSignal(str)

    def __init__(self, processes, order, sources: list[Source], filters: SearchFilters, dictionary,
                 store: cat.Catalog, printing: set[str], only_without_attribution: bool = False, parent=None):
        super().__init__(parent)
        self.processes = processes
        self.order = order
        self.sources = sources
        self.filters = filters
        self.dictionary = dictionary
        self.catalog = store
        self.printing = printing
        self.only_without_attribution = only_without_attribution
        self._cancel = threading.Event()
        self._current: Search | None = None
        self.problems: list[str] = []
        for source in sources:
            if hasattr(source, "max_wait"):
                source.max_wait = MAX_WAIT

    def cancel(self):
        self._cancel.set()
        for source in self.sources:
            if hasattr(source, "stop"):
                source.stop.set()
        if self._current:
            self._current.cancel()

    def run(self):
        statuses = self.catalog.statuses()
        hidden = {k for k, s in statuses.items() if s == cat.REJECTED}
        total = len(self.order)
        for i, proc in enumerate(self.order, 1):
            if self._cancel.is_set():
                return
            self.started_service.emit(proc.id, i, total)
            plan = query.build_plan(proc.display, self.processes, self.dictionary)
            found = []
            search = Search(plan, self.sources, self.filters, self.only_without_attribution,
                            printing=self.printing, hidden=hidden, on_found=found.extend, on_problem=self._on_problem)
            self._current = search
            search.run()  # синхронно, в этом потоке
            self._current = None
            keys = [r.key for r in found]
            self.catalog.record_search(proc.display, keys)
            fresh = sum(1 for k in keys if statuses.get(k) != cat.SELECTED)
            self.done_service.emit(proc.id, len(found), fresh)

    def _on_problem(self, message: str):
        # «Повторите через 12 с.» меняется от услуги к услуге — одна и та же беда показывается один раз.
        message = re.sub(r"\s*Повторите через [^.]*\.", "", message).strip()
        if message not in self.problems:
            self.problems.append(message)
            self.problem.emit(message)
