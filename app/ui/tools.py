"""Окна этапа 8: история поиска, редактор словаря перевода, похожие дубли."""

import json
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app import paths
from app.ui import theme


def _table(headers: list[str], stretch: int = 0) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().hide()
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    return table


class HistoryDialog(QDialog):
    """История поиска: повтор запроса одной кнопкой (ТЗ, 10)."""

    repeat = pyqtSignal(str)

    def __init__(self, history: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("История поиска")
        self.resize(760, 480)
        self.table = _table(["Запрос", "Последний раз", "Сколько раз", "Найдено", "Новых тогда"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for h in history:
            row = self.table.rowCount()
            self.table.insertRow(row)
            when = datetime.fromtimestamp(h["last_at"]).strftime("%d.%m.%Y %H:%M")
            for col, text in enumerate([h["query"], when, str(h["times"]), str(h["found"]), str(h["new"])]):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.cellDoubleClicked.connect(lambda row, _c: self._repeat(row))
        repeat = QPushButton("Повторить поиск")
        theme.set_role(repeat, theme.PRIMARY)
        repeat.clicked.connect(lambda: self._repeat(self.table.currentRow()))
        hint = QLabel("Двойной щелчок по строке — повторить поиск. Ролики, найденные впервые, будут помечены «новый».")
        hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(hint)
        layout.addWidget(repeat)
        if not history:
            hint.setText("История пуста: здесь появятся ваши поиски.")

    def _repeat(self, row: int):
        if row >= 0:
            self.repeat.emit(self.table.item(row, 0).text())
            self.accept()


class DictionaryDialog(QDialog):
    """Редактор словаря перевода «русская основа → английский» (ТЗ, 10).

    Правки сохраняются в «Данные/terms_ru_en.json» и действуют вместо встроенного словаря.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Словарь перевода")
        self.resize(720, 560)
        self.data = self._load()
        hint = QLabel("Слева — начало русского слова (основа, «ламинир» подходит к «ламинирование» и «ламинировать»), "
                      "несколько основ через пробел — словосочетание. Справа — английский перевод. "
                      "Словарь используется для запросов, которые не подошли ни к одной услуге справочника.")
        hint.setWordWrap(True)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Найти в словаре")
        self.filter.textChanged.connect(self._apply_filter)
        self.table = _table(["Русская основа", "Английский"], stretch=1)
        for ru, en in self.data["terms"].items():
            self._add_row(ru, en)
        add = QPushButton("Добавить строку")
        add.clicked.connect(lambda: (self._add_row("", ""), self.table.scrollToBottom(),
                                     self.table.editItem(self.table.item(self.table.rowCount() - 1, 0))))
        remove = QPushButton("Удалить строку")
        remove.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()) if self.table.currentRow() >= 0 else None)
        reset = QPushButton("Вернуть встроенный словарь")
        reset.clicked.connect(self._reset)
        row = QHBoxLayout()
        for b in (add, remove, reset):
            row.addWidget(b)
        row.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(self.filter)
        layout.addWidget(self.table)
        layout.addLayout(row)
        layout.addWidget(buttons)

    @staticmethod
    def _load() -> dict:
        user = paths.terms_file()
        source = user if user.exists() else paths.resources_dir() / "terms_ru_en.json"
        return json.loads(source.read_text(encoding="utf-8"))

    def _add_row(self, ru: str, en: str):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(ru))
        self.table.setItem(row, 1, QTableWidgetItem(en))

    def _apply_filter(self, text: str):
        text = text.lower().strip()
        for row in range(self.table.rowCount()):
            cells = " ".join((self.table.item(row, c).text() if self.table.item(row, c) else "") for c in (0, 1))
            self.table.setRowHidden(row, bool(text) and text not in cells.lower())

    def terms(self) -> dict[str, str]:
        terms = {}
        for row in range(self.table.rowCount()):
            ru = (self.table.item(row, 0).text() if self.table.item(row, 0) else "").strip().lower().replace("ё", "е")
            en = (self.table.item(row, 1).text() if self.table.item(row, 1) else "").strip()
            if ru and en:
                terms[ru] = en
        return terms

    def _save(self):
        self.data["terms"] = self.terms()
        paths.terms_file().write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.accept()

    def _reset(self):
        answer = QMessageBox.question(self, "Словарь перевода", "Удалить ваши правки и вернуть встроенный словарь?")
        if answer == QMessageBox.StandardButton.Yes:
            paths.terms_file().unlink(missing_ok=True)
            self.accept()


class DuplicatesDialog(QDialog):
    """Возможные дубли: один ролик на разных стоках под разными ID (ТЗ, 10)."""

    open_entry = pyqtSignal(int)

    def __init__(self, pairs: list[tuple], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Похожие ролики")
        self.resize(820, 420)
        self.table = _table(["№", "Ролик", "№", "Похожий ролик", "Сходство"], stretch=1)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for a, b, d in pairs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            cells = [str(a.id), f"{a.result.source}: {a.result.title}", str(b.id), f"{b.result.source}: {b.result.title}",
                     "почти одинаковые" if d <= 2 else "очень похожи"]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, a.id if col < 2 else b.id)
                self.table.setItem(row, col, item)
        self.table.cellDoubleClicked.connect(
            lambda row, col: self.open_entry.emit(self.table.item(row, col).data(Qt.ItemDataRole.UserRole)))
        hint = QLabel(("Сравниваются превью роликов. Двойной щелчок — открыть ролик в каталоге; дубль можно "
                       "отметить «отклонено», чтобы он не засчитывался дважды.") if pairs else
                      "Похожих роликов не найдено.")
        hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(hint)
