"""Вкладка «Пакетный поиск»: поиск роликов по всем услугам справочника одной кнопкой."""

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
from app import design
from app.ui import theme

START_TEXT = "Искать по всем услугам"


def level_brush(count: int, target: int) -> QBrush:
    """Цвет покрытия (нет роликов / 1 / 2 / цель выполнена) — из токенов текущей темы."""
    return QBrush(theme.coverage_color(cat.coverage_level(count, target)))


class BatchView(QWidget):
    """Вкладка «Пакетный поиск»: одна кнопка — поиск по всем услугам, таблица — что нашлось по каждой."""
    open_service = pyqtSignal(str)  # услуга — открыть её ролики на вкладке «Поиск»
    batch_requested = pyqtSignal()
    stop_requested = pyqtSignal()

    def __init__(self, store: cat.Catalog, categories, parent=None):
        super().__init__(parent)
        self.catalog = store
        self.categories = categories
        self.processes = [p for c in categories for p in c.processes]
        self.candidates: dict[str, tuple[int, int]] = {}  # услуга → (найдено, новых) в пакетном поиске
        self.running = False

        self.batch_button = QPushButton(START_TEXT)
        self.batch_button.setToolTip("Сначала услуги, где роликов меньше всего. Ролики только находятся — "
                                     "посмотреть, сохранить в каталог и скачать можно, открыв услугу.")
        theme.set_role(self.batch_button, theme.PRIMARY)
        theme.set_icon(self.batch_button, "search")
        self.batch_button.clicked.connect(lambda: self.stop_requested.emit() if self.running
                                          else self.batch_requested.emit())
        self.progress = QLabel()
        self.progress.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(self.batch_button)
        top.addWidget(self.progress, 1)
        self.summary = QLabel()
        self.summary.setProperty("muted", True)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Категория", "Услуга", "Найдено", "В каталоге"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(design.TABLE_ROW_HEIGHT)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(lambda row, _c: self.open_service.emit(
            self.table.item(row, 1).data(Qt.ItemDataRole.UserRole)))
        hint = QLabel("Двойной щелчок по услуге — её ролики на вкладке «Поиск»: там их можно посмотреть, "
                      "сохранить в каталог и скачать.")
        hint.setWordWrap(True)
        hint.setProperty("muted", True)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.summary)
        layout.addWidget(self.table, 1)
        layout.addWidget(hint)
        self.refresh()

    def set_categories(self, categories):
        self.categories = categories
        self.processes = [p for c in categories for p in c.processes]
        self.refresh()

    def refresh(self):
        coverage = self.catalog.coverage()
        done = sum(1 for p in self.processes if coverage.get(p.id, 0) >= p.target)
        self.summary.setText(f"Услуг с достаточным числом роликов в каталоге: {done} из {len(self.processes)}")
        self.table.setRowCount(0)
        for c in self.categories:
            for p in c.processes:
                row = self.table.rowCount()
                self.table.insertRow(row)
                n = coverage.get(p.id, 0)
                found = self.candidates.get(p.id)
                cells = [c.name, p.name, "" if found is None else f"{found[0]} (новых: {found[1]})",
                         f"{n} из {p.target}"]
                for col, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    if col == 3:
                        item.setBackground(level_brush(n, p.target))
                    self.table.setItem(row, col, item)
                self.table.item(row, 1).setData(Qt.ItemDataRole.UserRole, p.id)

    def set_running(self, running: bool):
        self.running = running
        self.batch_button.setText("Остановить" if running else START_TEXT)

    def service_done(self, process_id: str, found: int, fresh: int):
        self.candidates[process_id] = (found, fresh)
        self.refresh()
