"""Меню «Сервис»: проверка ссылок, похожие дубли, кэш, резервная копия."""

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog, QWidget

from app import paths
from app.services import maintenance
from app.ui.context import AppContext
from app.ui.dialogs.tools import DuplicatesDialog
from app.workers.backup import BackupWorker
from app.workers.maintenance import DuplicateWorker, LinkCheckWorker


class MaintenanceActions(QObject):
    catalog_changed = pyqtSignal()  # проверка ссылок отметила удалённые ролики
    open_entry = pyqtSignal(int)  # показать ролик каталога
    message = pyqtSignal(str)

    def __init__(self, ctx: AppContext, parent: QWidget, busy=lambda: False):
        super().__init__(parent)
        self.ctx = ctx
        self.window = parent
        self.busy = busy  # идёт скачивание или экспорт — резервную копию не начинаем
        self.closing = False
        self.link_check: LinkCheckWorker | None = None
        self.duplicates: DuplicateWorker | None = None
        self.backup_worker: BackupWorker | None = None

    def workers(self) -> list:
        return [self.link_check, self.duplicates, self.backup_worker]

    def link_check_due(self) -> bool:
        return (maintenance.link_check_due(self.ctx.catalog, self.ctx.settings.link_check_days)
                and bool(self.ctx.catalog.entries()))

    def check_links(self, manual: bool, entries=None):
        """Проверить, не удалены ли ролики каталога с площадок (раз в N дней при запуске или по команде)."""
        if self.link_check and self.link_check.isRunning():
            return
        sources, _ = self.ctx.sources()
        self.link_check = LinkCheckWorker(self.ctx.catalog, sources, self.window, entries=entries)
        self.link_check.progress.connect(lambda i, n: self.message.emit(f"Проверка ссылок каталога: {i} из {n}"))
        self.link_check.done.connect(lambda counts: self._links_checked(counts, manual))
        self.link_check.start()

    def check_selected_links(self, entries):
        if entries:
            self.check_links(manual=True, entries=entries)

    def _links_checked(self, counts: dict, manual: bool):
        text = f"Проверка ссылок: проверено {counts['checked']}"
        if counts["removed"]:
            text += f", удалены с площадки — {counts['removed']}"
        if counts["restored"]:
            text += f", снова доступны — {counts['restored']}"
        if counts["skipped"]:
            text += f", не удалось проверить — {counts['skipped']}"
        self.message.emit(text)
        self.catalog_changed.emit()
        if manual or counts["removed"]:
            note = ("\n\nУдалённые ролики помечены в каталоге; нескачанные не засчитываются в покрытие."
                    if counts["removed"] else "")
            QMessageBox.information(self.window, "Проверка ссылок", text + "." + note)

    def find_duplicates(self):
        if self.duplicates and self.duplicates.isRunning():
            return
        self.message.emit("Поиск похожих роликов: загружаются превью…")
        self.duplicates = DuplicateWorker(self.ctx.catalog, self.window)
        self.duplicates.done.connect(self._show_duplicates)
        self.duplicates.start()

    def _show_duplicates(self, pairs: list):
        self.message.emit(f"Похожих пар: {len(pairs)}")
        dialog = DuplicatesDialog(pairs, self.window)
        dialog.open_entry.connect(self.open_entry)
        dialog.exec()

    def clear_cache(self):
        self.ctx.cache.clear_responses()
        self.message.emit("Кэш ответов очищен.")

    def backup_data(self):
        """Архив «Данных» вне исходной папки, без остановки интерфейса."""
        if self.backup_worker and self.backup_worker.isRunning():
            return
        if self.busy():
            QMessageBox.information(self.window, "Резервная копия", "Дождитесь окончания скачивания и экспорта.")
            return
        source = paths.data_dir()
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dialog = QFileDialog(self.window, "Резервная копия данных", str(source.parent / f"Данные_{stamp}.zip"),
                             "Архив ZIP (*.zip)")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setDefaultSuffix("zip")
        if not dialog.exec():
            return
        path = Path(dialog.selectedFiles()[0])
        if path.resolve().is_relative_to(source.resolve()):
            QMessageBox.warning(self.window, "Резервная копия", "Сохраните резервную копию вне папки «Данные».")
            return
        progress = QProgressDialog("Копируются каталог, настройки и файлы из «Данных».\n"
                                   "Архив содержит ключи из настроек — храните его в надёжном месте.\n"
                                   "Ролики из внешней папки в архив не входят.", "Отмена", 0, 0, self.window)
        progress.setWindowTitle("Резервная копия")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        worker = BackupWorker(source, path, self.window)

        def advance(done, total):
            if not worker.cancel.is_set():
                progress.setRange(0, total)
                progress.setValue(done)

        worker.progress.connect(advance)
        progress.canceled.connect(worker.cancel.set)
        worker.finished.connect(lambda: self._backup_done(progress, path, worker))
        self.backup_worker = worker
        progress.show()
        worker.start()

    def _backup_done(self, progress: QProgressDialog, path: Path, worker: BackupWorker):
        progress.close()
        if self.closing:
            return
        if worker.cancelled:
            self.message.emit("Резервное копирование отменено.")
        elif worker.error:
            QMessageBox.warning(self.window, "Резервная копия", worker.error)
        else:
            QMessageBox.information(self.window, "Резервная копия", f"Резервная копия сохранена:\n{path}\n\n"
                                    "Архив содержит ключи из настроек — храните его в надёжном месте.\n"
                                    "Для восстановления закройте программу, переименуйте прежнюю папку «Данные» "
                                    "и распакуйте архив рядом с программой.")
