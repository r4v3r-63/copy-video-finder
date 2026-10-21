"""Проверка и установка обновлений из интерфейса."""

import logging
from html import escape

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from app import __version__
from app.services import updater
from app.workers.update import CheckWorker, DownloadWorker

log = logging.getLogger(__name__)


class UpdateController:
    """Связывает проверку, вопрос пользователю, скачивание и перезапуск."""

    def __init__(self, window, settings):
        self.window = window
        self.settings = settings
        self.checker: CheckWorker | None = None
        self.downloader: DownloadWorker | None = None
        self.dialog: QProgressDialog | None = None

    def stop(self):
        """Перед закрытием окна: потоки должны завершиться раньше, чем окно-родитель будет удалено."""
        if self.downloader and self.downloader.isRunning():
            self.downloader.cancel.set()
            self.downloader.wait()
        if self.checker and self.checker.isRunning():
            self.checker.wait()  # один запрос к GitHub с тайм-аутом

    def check(self, manual: bool = False):
        if self.checker and self.checker.isRunning():
            return
        self.checker = CheckWorker(self.window)
        self.checker.found.connect(lambda r: self.offer(r, manual))
        if manual:
            self.checker.nothing.connect(lambda: QMessageBox.information(
                self.window, "Обновления", f"У вас последняя версия программы ({__version__})."))
            self.checker.failed.connect(lambda m: QMessageBox.warning(self.window, "Обновления", m))
        self.checker.start()

    def offer(self, release, manual: bool):
        if not manual and release.version == self.settings.skipped_version:
            return
        box = QMessageBox(self.window)
        box.setWindowTitle("Доступно обновление")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"Доступна новая версия {escape(release.version)} (у вас {__version__}).")
        can_install = updater.can_self_update() and bool(release.sha256_url)
        if can_install:
            box.setInformativeText("Обновить сейчас? Программа скачает новую версию, закроется и запустится "
                                   "снова. Настройки, ключи и скачанные ролики сохранятся.")
            update = box.addButton("Обновить", QMessageBox.ButtonRole.AcceptRole)
        else:
            box.setInformativeText("Скачайте новую версию со страницы выпуска.")
            update = box.addButton("Открыть страницу выпуска", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Позже", QMessageBox.ButtonRole.RejectRole)
        skip = box.addButton("Пропустить эту версию", QMessageBox.ButtonRole.DestructiveRole)
        if release.notes:
            box.setDetailedText(release.notes)
        box.exec()
        clicked = box.clickedButton()
        if clicked is skip:
            self.settings.skipped_version = release.version
            self.settings.save()
        elif clicked is update:
            if can_install:
                self.install(release)
            else:
                QDesktopServices.openUrl(QUrl(release.page_url))

    def install(self, release):
        if self.window.downloads_busy():
            QMessageBox.information(self.window, "Обновление",
                                    "Сейчас скачиваются ролики. Дождитесь окончания и обновите программу "
                                    "через «Справка → Проверить обновления».")
            return
        self.dialog = QProgressDialog("Скачивается обновление…", "Отмена", 0, 100, self.window)
        self.dialog.setWindowTitle("Обновление")
        self.dialog.setMinimumDuration(0)
        self.downloader = DownloadWorker(release, self.window)
        self.dialog.canceled.connect(self.downloader.cancel.set)
        self.downloader.progress.connect(self._on_progress)
        self.downloader.done.connect(self._on_downloaded)
        self.downloader.failed.connect(self._on_failed)
        self.downloader.start()

    def _on_progress(self, done: int, total: int):
        if self.dialog and total > 0:
            self.dialog.setValue(min(done * 100 // total, 99))
            self.dialog.setLabelText(f"Скачивается обновление: {done / 1e6:.0f} из {total / 1e6:.0f} МБ")

    def _on_failed(self, message: str):
        if self.dialog:
            self.dialog.close()
        if message:
            QMessageBox.warning(self.window, "Обновление", f"Обновление не установлено: {message}")

    def _on_downloaded(self, zip_path: str):
        if self.dialog:
            self.dialog.setLabelText("Подготовка обновления…")
            self.dialog.setCancelButton(None)
        try:
            from pathlib import Path
            updater.prepare_and_launch(Path(zip_path))
        except Exception as exc:  # noqa: BLE001
            log.exception("Не удалось подготовить обновление")
            self._on_failed(str(exc))
            return
        log.info("Обновление запущено, программа закрывается")
        QApplication.quit()
