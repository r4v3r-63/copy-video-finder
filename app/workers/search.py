"""Поиск в фоне: `Search` в отдельном потоке, результаты — сигналами Qt."""

from PyQt6.QtCore import QThread, pyqtSignal

from app.core.models import SearchFilters
from app.core.query import SearchPlan
from app.services.search import Search
from app.sources.base import Source


class SearchWorker(QThread):
    found = pyqtSignal(list)  # list[VideoResult] — новые подходящие ролики
    previously_rejected_found = pyqtSignal(list)
    problem = pyqtSignal(str)  # сообщение об ошибке площадки
    progress = pyqtSignal(int, int)  # выполнено запросов, всего

    def __init__(self, plan: SearchPlan, sources: list[Source], filters: SearchFilters,
                 only_without_attribution: bool = False, parent=None, printing: set[str] | None = None,
                 hidden: set[tuple[str, str]] | None = None):
        super().__init__(parent)
        self.search = Search(plan, sources, filters, only_without_attribution, printing=printing, hidden=hidden,
                             on_found=self.found.emit, on_rejected_before=self.previously_rejected_found.emit,
                             on_problem=self.problem.emit, on_progress=self.progress.emit)

    def cancel(self):
        self.search.cancel()

    def run(self):
        self.search.run()
