"""Вкладка «Покрытие»: статистика каталога и пакетный поиск по незаполненным услугам."""

from html import escape

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app import catalog as cat
from app.ui import theme

LEVEL_NAMES = {0: "нет роликов", 1: "1 ролик", 2: "2 ролика", 3: "цель выполнена"}


def level_brush(count: int, target: int) -> QBrush:
    """Цвет покрытия (нет роликов / 1 / 2 / цель выполнена) — из токенов текущей темы."""
    return QBrush(theme.coverage_color(cat.coverage_level(count, target)))


class CoverageView(QWidget):
    open_service = pyqtSignal(str)  # услуга — открыть её поиск на главном экране
    batch_requested = pyqtSignal()
    stop_requested = pyqtSignal()

    def __init__(self, store: cat.Catalog, categories, parent=None):
        super().__init__(parent)
        self.catalog = store
        self.categories = categories
        self.processes = [p for c in categories for p in c.processes]
        self.candidates: dict[str, tuple[int, int]] = {}  # услуга → (найдено, новых) в пакетном поиске
        self.running = False

        self.stats = QLabel()
        self.stats.setWordWrap(True)
        self.stats.setTextFormat(Qt.TextFormat.RichText)

        self.batch_button = QPushButton("Искать по незаполненным услугам")
        self.batch_button.setToolTip("Сначала услуги без роликов, затем с одним, затем остальные с недостатком. "
                                     "Ролики только находятся — решаете и скачиваете вы.")
        theme.set_role(self.batch_button, theme.PRIMARY)
        self.batch_button.clicked.connect(lambda: self.stop_requested.emit() if self.running
                                          else self.batch_requested.emit())
        self.progress = QLabel()
        self.progress.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(self.batch_button)
        top.addWidget(self.progress, 1)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Категория", "Услуга", "В покрытии", "Найдено в пакетном поиске",
                                              "Состояние"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(lambda row, _c: self.open_service.emit(
            self.table.item(row, 1).data(Qt.ItemDataRole.UserRole)))
        hint = QLabel("Двойной щелчок по услуге — открыть её ролики на вкладке «Поиск» "
                      "(после пакетного поиска — из кэша, без новых запросов).")
        hint.setWordWrap(True)
        hint.setProperty("muted", True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.stats)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(hint)
        self.refresh()

    def refresh(self):
        st = cat.statistics(self.catalog, self.categories)
        levels = " · ".join(f"{name}: {st['services_by_count'][key]}" for key, name in
                            (("0", "без роликов"), ("1", "с 1"), ("2", "с 2"), ("3 и более", "с 3 и более")))
        sources = ", ".join(f"{escape(k)} — {v}" for k, v in st["by_source"].items()) or "—"
        self.stats.setText(
            f"<b>Категорий:</b> {st['categories']} · <b>услуг:</b> {st['services']} · "
            f"<b>цель выполнена:</b> {st['services_done']} из {st['services']}<br>"
            f"<b>Роликов в каталоге:</b> {st['videos']} (засчитано в покрытие: {st['counted']}, "
            f"с точным соответствием: {st['exact']})<br>"
            f"<b>Услуг по числу засчитанных роликов:</b> {levels}<br>"
            f"<b>По площадкам:</b> {sources}<br>"
            f"<b>Новых роликов с предыдущего поиска:</b> {st['new_since_previous_search']}")
        coverage = self.catalog.coverage()
        self.table.setRowCount(0)
        for c in self.categories:
            for p in c.processes:
                row = self.table.rowCount()
                self.table.insertRow(row)
                n = coverage.get(p.id, 0)
                found = self.candidates.get(p.id)
                cells = [c.name, p.name, f"{n} из {p.target}",
                         "" if found is None else f"{found[0]} (новых для каталога: {found[1]})",
                         LEVEL_NAMES[cat.coverage_level(n, p.target)]]
                for col, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    item.setBackground(level_brush(n, p.target))
                    self.table.setItem(row, col, item)
                self.table.item(row, 1).setData(Qt.ItemDataRole.UserRole, p.id)

    def set_running(self, running: bool):
        self.running = running
        self.batch_button.setText("Остановить" if running else "Искать по незаполненным услугам")

    def service_done(self, process_id: str, found: int, fresh: int):
        self.candidates[process_id] = (found, fresh)
        self.refresh()
