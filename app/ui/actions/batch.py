"""Пакетный поиск по всем услугам с вкладки «Пакетный поиск»."""

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QMessageBox, QWidget

from app.storage import catalog as cat
from app.ui.context import AppContext
from app.ui.tabs.batch_view import BatchView
from app.workers.batch import BatchWorker


class BatchController(QObject):
    def __init__(self, ctx: AppContext, view: BatchView, parent: QWidget):
        super().__init__(parent)
        self.ctx = ctx
        self.view = view
        self.window = parent
        self.worker: BatchWorker | None = None
        view.batch_requested.connect(self.start)
        view.stop_requested.connect(self.stop)

    def start(self):
        if self.worker and self.worker.isRunning():
            return
        processes = self.ctx.processes
        short = cat.batch_order(processes, self.ctx.catalog.coverage())  # сначала услуги, где роликов меньше всего
        order = short + [p for p in processes if p not in short]
        sources, _ = self.ctx.sources()
        answer = QMessageBox.question(
            self.window, "Пакетный поиск",
            f"Искать ролики по всем услугам ({len(order)})? Это займёт несколько минут.\n\n"
            "Ролики только находятся, ничего не скачивается — открыть услугу и сохранить ролики в каталог "
            "можно двойным щелчком. Лимиты площадок программа соблюдает сама.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.view.candidates = {}
        self.worker = BatchWorker(processes, order, sources, self.ctx.filters(), self.ctx.dictionary,
                                  self.ctx.catalog, self.ctx.printing, self.ctx.settings.only_without_attribution,
                                  self.window)
        names = {p.id: p.name for p in processes}
        self.worker.started_service.connect(lambda pid, i, n: self.view.progress.setText(
            f"Ищем: {names[pid]} ({i} из {n})…"))
        self.worker.done_service.connect(self.view.service_done)
        self.worker.finished.connect(self._finished)
        self.view.set_running(True)
        self.worker.start()

    def stop(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait()

    def _finished(self):
        self.view.set_running(False)
        text = f"Пакетный поиск завершён: просмотрено услуг — {len(self.view.candidates)}."
        if self.worker and self.worker.problems:
            text += " " + " ".join(self.worker.problems)
        self.view.progress.setText(text)
        self.view.refresh()
