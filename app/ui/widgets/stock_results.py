"""Табличная модель больших выдач: строки создаются по требованию Qt."""

from PyQt6.QtCore import QAbstractTableModel, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap

from app.core.stock_link import choose_quality

HEADERS = ("Выбор", "Превью", "Название / теги", "Секунды", "Разрешение", "Качество / размер", "Источник", "Статус")


class StockResults(QAbstractTableModel):
    selection_changed = pyqtSignal()
    thumbnail_needed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.checked = set()
        self.states = {}
        self.images = {}
        self.requested = set()
        self.quality = 0
        self.individual = True

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.items)

    def columnCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        result = self.items[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.CheckStateRole and col == 0:
            return Qt.CheckState.Checked if result.key in self.checked else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.DecorationRole and col == 1 and result.preview_url:
            url = result.preview_url
            if url not in self.requested:
                self.requested.add(url)
                self.thumbnail_needed.emit(url)
            return self.images.get(url)
        if role == Qt.ItemDataRole.ToolTipRole:
            file, note = choose_quality(result, self.quality)
            options = ", ".join(f"{f.quality} ({f.width or '?'}×{f.height or '?'})" for f in result.files)
            return f"{result.title}\n{result.page_url}\nВарианты: {options or 'определяются при скачивании'}\n{note}"
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        file, _note = choose_quality(result, self.quality)
        values = ("", "" if result.preview_url else "Без превью", result.title,
                  str(result.duration) if result.duration is not None else "Неизвестна",
                  f"{result.width}×{result.height}" if result.width and result.height else "Неизвестно",
                  (file.quality + (f" / {file.size / 1024 / 1024:.1f} МБ" if file.size else " / размер неизвестен"))
                  if file else "Авто / размер неизвестен", result.source, self.states.get(result.key, "Найден"))
        return values[col]

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and index.column() == 0:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.CheckStateRole or index.column() != 0:
            return False
        key = self.items[index.row()].key
        if value == Qt.CheckState.Checked.value:
            if self.individual:
                self.checked.clear()
            self.checked.add(key)
        else:
            self.checked.discard(key)
        self.dataChanged.emit(self.index(0, 0), self.index(max(0, len(self.items) - 1), 0))
        self.selection_changed.emit()
        return True

    def set_items(self, items):
        self.beginResetModel()
        self.items = items
        self.checked.intersection_update(result.key for result in items)
        self.endResetModel()
        self.selection_changed.emit()

    def select_all(self):
        if not self.individual:
            self.checked = {result.key for result in self.items}
            if self.items:
                self.dataChanged.emit(self.index(0, 0), self.index(len(self.items) - 1, 0))
            self.selection_changed.emit()

    def set_state(self, key, text):
        self.states[key] = text
        for row, result in enumerate(self.items):
            if result.key == key:
                self.dataChanged.emit(self.index(row, 7), self.index(row, 7))
                break

    def set_image(self, url: str, image: QPixmap | None):
        self.images[url] = image
        for row, result in enumerate(self.items):
            if result.preview_url == url:
                self.dataChanged.emit(self.index(row, 1), self.index(row, 1))
