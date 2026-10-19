"""Проверка и установка обновлений из интерфейса."""

import logging
import threading
from html import escape

import requests
from PyQt6.QtCore import QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from app import __version__, updater

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


class UpdateController:
    """Связывает проверку, вопрос пользователю, скачивание и перезапуск."""

    def __init__(self, window, settings):
        self.window = window
        self.settings = settings
        self.checker: CheckWorker | None = None
        self.downloader: DownloadWorker | None = None
        self.dialog: QProgressDialog | None = None

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
        if updater.can_self_update():
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
            if updater.can_self_update():
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
