"""Фильтрация и порядок уже полученной выдачи без новых запросов к площадкам."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QPushButton, QWidget

from app import catalog as cat
from app import licenses
from app.ui import theme


class ResultFilters(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.order = theme.compact_combo(QComboBox())
        for title, value in (("По близости к запросу", "relevance"), ("Сначала короткие", "short"),
                             ("Сначала длинные", "long"), ("По разрешению", "size"), ("По площадке", "source")):
            self.order.addItem(title, value)
        self.source = theme.compact_combo(QComboBox())
        self.source.addItem("Все площадки", None)
        self.license = theme.compact_combo(QComboBox())
        for title, value in (("Любая лицензия", None), ("Свободные", "free"),
                             (licenses.ALLOWED, licenses.ALLOWED), (licenses.ATTRIBUTION, licenses.ATTRIBUTION),
                             (licenses.REVIEW, licenses.REVIEW), ("Без права использования", licenses.REJECTED)):
            self.license.addItem(title, value)
        self.resolution = theme.compact_combo(QComboBox())
        for title, value in (("Не ниже HD", 720), ("Не ниже Full HD", 1080), ("Не ниже 4K", 2160)):
            self.resolution.addItem(title, value)
        self.resolution.setToolTip("Ролики с неизвестным разрешением остаются в выдаче — проверьте его на сайте.")
        self.hide_catalog = QCheckBox("Скрыть уже добавленные в каталог")
        self.show_rejected = QCheckBox("Показать ранее отклонённые")
        form = QFormLayout(self)
        form.addRow("Порядок:", self.order)
        form.addRow("Площадка:", self.source)
        form.addRow("Лицензия:", self.license)
        form.addRow("Разрешение:", self.resolution)
        form.addRow(self.hide_catalog)
        form.addRow(self.show_rejected)
        reset = QPushButton("Сбросить фильтры")
        form.addRow(reset)
        reset.clicked.connect(self.reset)
        for combo in (self.order, self.source, self.license, self.resolution):
            combo.currentIndexChanged.connect(lambda _i: self.changed.emit())
        for checkbox in (self.hide_catalog, self.show_rejected):
            checkbox.toggled.connect(lambda _checked: self.changed.emit())

    def reset(self):
        for widget in (self.order, self.source, self.license, self.resolution, self.hide_catalog, self.show_rejected):
            widget.blockSignals(True)
            if isinstance(widget, QComboBox):
                widget.setCurrentIndex(0)
            else:
                widget.setChecked(False)
            widget.blockSignals(False)
        self.changed.emit()

    def set_sources(self, names):
        current = self.source.currentData()
        self.source.blockSignals(True)
        self.source.clear()
        self.source.addItem("Все площадки", None)
        for name in sorted(set(names)):
            self.source.addItem(name, name)
        self.source.setCurrentIndex(max(0, self.source.findData(current)))
        self.source.blockSignals(False)

    def allows(self, result, statuses):
        if statuses.get(result.key) == cat.REJECTED and not self.show_rejected.isChecked():
            return False
        if self.hide_catalog.isChecked() and result.key in statuses:
            return False
        if self.source.currentData() is not None and result.source != self.source.currentData():
            return False
        status = result.license.status if result.license else licenses.REVIEW
        wanted = self.license.currentData()
        if wanted == "free" and status not in licenses.SUITABLE:
            return False
        if wanted not in (None, "free") and wanted != status:
            return False
        return not (result.width and result.height and min(result.width, result.height) < self.resolution.currentData())

    def sort_key(self, result):
        order = self.order.currentData()
        tie = (result.title.casefold(), result.source, result.source_id)
        if order in ("short", "long"):
            return (result.duration is None, (result.duration or 0) * (-1 if order == "long" else 1), tie)
        if order == "size":
            return (not (result.width and result.height), -(result.width or 0) * (result.height or 0), tie)
        if order == "source":
            return (result.source.casefold(), -result.relevance, tie)
        return (-result.relevance, tie)
