"""Добавление ролика по ссылке: сведения, группа услуги и проверка лицензии."""

from dataclasses import replace

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from app.core import design
from app.services.manual import normalize_url
from app.sources.web import WebSource
from app.storage import catalog as cat
from app.ui import theme
from app.workers.manual import LinkWorker


def find_existing(store, result):
    entry = store.find(result.key)
    if entry:
        return entry
    # Из Яндекс Видео та же страница могла прийти с другим названием площадки.
    url = normalize_url(result.page_url)
    for entry in store.entries():
        try:
            if normalize_url(entry.result.page_url) == url:
                return entry
        except ValueError:
            continue
    return None


class AddLinkDialog(QDialog):
    def __init__(self, store: cat.Catalog, categories, web: WebSource, process_id=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Добавить ролик по ссылке")
        self.setMinimumWidth(design.LINK_DIALOG_WIDTH)
        self.store, self.web = store, web
        self.worker: LinkWorker | None = None
        self.result = None
        self.existing = None
        self.video_id = None
        self._rejecting = False

        self.url = QLineEdit()
        self.url.setPlaceholderText("https://… — страница отдельного ролика")
        self.url.textChanged.connect(self._reset)
        self.load = QPushButton("Получить сведения")
        theme.set_role(self.load, theme.SECONDARY)
        self.load.clicked.connect(self._load)
        self.url.returnPressed.connect(self._load)
        row = QHBoxLayout()
        row.addWidget(self.url, 1)
        row.addWidget(self.load)
        self.info = QLabel("Вставьте ссылку на страницу ролика, затем получите сведения.")
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.TextFormat.PlainText)
        self.title = QLineEdit()
        self.title.setPlaceholderText("Название ролика")
        self.title.textChanged.connect(self._update_save)
        self.process = theme.compact_combo(QComboBox())
        self.process.addItem("Без услуги — привязать позже", None)
        for category in categories:
            for p in category.processes:
                self.process.addItem(f"{category.name} → {p.name}", p.id)
        self.process.setCurrentIndex(max(0, self.process.findData(process_id)))
        self.match = theme.compact_combo(QComboBox())
        self.match.addItems(cat.MATCHES)
        self.confirmed = QCheckBox("Лицензию проверил(а) на странице ролика")
        self.confirmed.setToolTip("В покрытие входят только ролики со свободной подтверждённой лицензией.")
        self.open_page = QPushButton("Открыть страницу ролика")
        self.open_page.clicked.connect(
            lambda: self.result and QDesktopServices.openUrl(QUrl(self.result.page_url)))
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save.setText("Добавить в каталог")
        self.save.setDefault(False)
        self.save.setAutoDefault(False)
        self.load.setAutoDefault(False)
        self.load.setDefault(False)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow("Название:", self.title)
        form.addRow("Группа услуг:", self.process)
        form.addRow("Соответствие:", self.match)
        form.addRow("", self.confirmed)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.info)
        layout.addWidget(self.open_page)
        layout.addLayout(form)
        layout.addWidget(self.buttons)
        self._reset()

    def _reset(self):
        self.result = self.existing = None
        self.title.clear()
        self.confirmed.setChecked(False)
        self.info.setText("Вставьте ссылку на страницу ролика, затем получите сведения.")
        for widget in (self.title, self.process, self.match, self.confirmed, self.open_page):
            widget.setEnabled(False)
        self.save.setText("Добавить в каталог")
        self.save.setEnabled(False)

    def _load(self):
        if self.worker and self.worker.isRunning():
            return
        self._reset()
        try:
            url = normalize_url(self.url.text())
        except ValueError as exc:
            self.info.setText(str(exc))
            return
        self.url.setEnabled(False)
        self.load.setEnabled(False)
        self.info.setText("Получение сведений о ролике…")
        self.worker = LinkWorker(self.web, url, self)
        self.worker.finished.connect(self._loaded)
        self.worker.start()

    def _loaded(self):
        if self._rejecting:
            super().reject()
            return
        self.url.setEnabled(True)
        self.load.setEnabled(True)
        self.result = self.worker.result
        if self.result is None:
            self.info.setText(self.worker.error or "Получение сведений отменено.")
            return
        self.existing = find_existing(self.store, self.result)
        if self.existing:
            self.info.setText(f"Ролик уже в каталоге: № {self.existing.id}. Откроется существующая запись, "
                              "отметки и привязки сохранятся.")
            self.title.setText(self.existing.result.title)
            self.save.setText("Открыть запись")
        else:
            lic = self.result.license
            self.info.setText(f"{self.result.source}\nЛицензия: {lic.name} — {lic.status}.\n{lic.note}\n"
                              + ("Файл для скачивания доступен." if self.result.files else
                                 "Ссылки на файл нет — ролик можно смотреть на сайте."))
            self.title.setText(self.result.title)
            for widget in (self.title, self.process, self.match, self.confirmed):
                widget.setEnabled(True)
        self.open_page.setEnabled(True)
        self._update_save()

    def _update_save(self):
        if hasattr(self, "save"):
            self.save.setEnabled(self.result is not None and bool(self.title.text().strip()))

    def _save(self):
        if self.result is None or not self.title.text().strip():
            return
        existing = find_existing(self.store, self.result)
        if existing:
            self.video_id = existing.id
        else:
            self.video_id = self.store.select(replace(self.result, title=self.title.text().strip()),
                                              self.process.currentData(), self.match.currentText(),
                                              license_confirmed=self.confirmed.isChecked())
        self.accept()

    def reject(self):
        if self.worker and self.worker.isRunning():
            self._rejecting = True
            self.worker.cancel.set()
            self.info.setText("Отмена — ожидается завершение текущего запроса…")
            return
        super().reject()

    def stop(self):
        """Главное окно при выходе дожидается и этого фонового запроса."""
        self._rejecting = True
        if self.worker:
            self.worker.cancel.set()
            self.worker.wait()
        super().reject()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
