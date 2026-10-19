"""Редактор справочника услуг (ТЗ, 5.1): категории, услуги, поисковые фразы и уровни поиска.

Правки сохраняются в «Данные/processes.json» и действуют вместо встроенного справочника.
Код услуги (`Process.id`) не меняется при переименовании — на нём держатся привязки каталога.
"""

import copy

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app import paths, processes
from app.processes import EXACT, LANGS, PARTIAL, RELATED, Category, Level, Process
from app.ui import theme

MATCHES = [EXACT, PARTIAL, RELATED]
SEP = "; "  # несколько фраз в ячейке уровня


def _lines(edit: QPlainTextEdit) -> list[str]:
    return [line.strip() for line in edit.toPlainText().splitlines() if line.strip()]


def _text_field(hint: str, height: int = 64) -> QPlainTextEdit:
    edit = QPlainTextEdit()
    edit.setPlaceholderText(hint)
    edit.setMaximumHeight(height)
    return edit


class ServicesDialog(QDialog):
    def __init__(self, categories: list[Category], link_counts: dict[str, int] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Справочник услуг")
        self.resize(1100, 720)
        self.categories = copy.deepcopy(categories)
        self.link_counts = link_counts or {}
        self.current: Category | Process | None = None
        self.reset_requested = False

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(300)
        self.tree.currentItemChanged.connect(self._on_select)
        add_category = QPushButton("+ Категория")
        add_category.clicked.connect(self._add_category)
        add_service = QPushButton("+ Услуга")
        add_service.clicked.connect(self._add_service)
        self.remove = QPushButton("Удалить")
        self.remove.clicked.connect(self._remove)
        tree_buttons = QHBoxLayout()
        for b in (add_category, add_service, self.remove):
            tree_buttons.addWidget(b)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.tree)
        left_layout.addLayout(tree_buttons)

        # Форма категории
        self.category_name = QLineEdit()
        self.category_name.textEdited.connect(self._rename_current)
        category_page = QWidget()
        category_form = QFormLayout(category_page)
        category_form.addRow("Название категории:", self.category_name)
        category_form.addRow("", QLabel("Услуги категории — в дереве слева. Код категории при переименовании "
                                        "не меняется."))

        # Форма услуги
        self.name = QLineEdit()
        self.name.textEdited.connect(self._rename_current)
        self.category = QComboBox()
        self.aliases = _text_field("Синонимы для подсказок — по одному в строке: «ламинирование», «ламинация»")
        self.phrases_ru = _text_field("Поисковые фразы на русском — по одной в строке")
        self.phrases_en = _text_field("Поисковые фразы на английском — по одной в строке")
        self.key_ru = QLineEdit()
        self.key_ru.setPlaceholderText("Короткие ключевые слова через «; » — подтверждают тему даже одним словом")
        self.key_en = QLineEdit()
        self.key_en.setPlaceholderText("laminator; pouch laminator")
        self.exclude = QLineEdit()
        self.exclude.setPlaceholderText("Слова, при которых ролик точно не про эту услугу: 3d; 3д")
        self.target = QSpinBox()
        self.target.setRange(1, 50)
        self.target.setSuffix(" шт.")
        self.levels = QTableWidget(0, 2 + len(LANGS))
        self.levels.setHorizontalHeaderLabels(["Уровень", "Соответствие", *[lang.upper() for lang in LANGS]])
        header = self.levels.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col in range(2, 2 + len(LANGS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.levels.setColumnWidth(0, 150)
        self.levels.setColumnWidth(1, 190)
        self.levels.verticalHeader().setDefaultSectionSize(theme.CONTROL_HEIGHT + theme.SPACE_XS)
        self.levels.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        add_level = QPushButton("+ Уровень")
        add_level.clicked.connect(lambda: self._add_level_row(Level("Новый уровень")))
        remove_level = QPushButton("Удалить уровень")
        remove_level.clicked.connect(lambda: self.levels.removeRow(self.levels.currentRow())
                                     if self.levels.currentRow() >= 0 else None)
        level_buttons = QHBoxLayout()
        level_buttons.addWidget(add_level)
        level_buttons.addWidget(remove_level)
        level_buttons.addStretch()
        service_page = QWidget()
        form = QFormLayout(service_page)
        form.addRow("Название:", self.name)
        form.addRow("Категория:", self.category)
        form.addRow("Синонимы:", self.aliases)
        form.addRow("Фразы (русский):", self.phrases_ru)
        form.addRow("Фразы (английский):", self.phrases_en)
        form.addRow("Ключевые слова RU:", self.key_ru)
        form.addRow("Ключевые слова EN:", self.key_en)
        form.addRow("Исключения:", self.exclude)
        form.addRow("Цель покрытия:", self.target)
        levels_hint = QLabel("Уровни расширения: если по фразам выше роликов мало, поиск идёт по уровням сверху вниз. "
                             "В ячейке — фразы на языке столбца через «; ». Ролик, найденный только на уровне "
                             "со степенью «смежный процесс», так и предлагается в каталог.")
        levels_hint.setWordWrap(True)
        levels_hint.setProperty("muted", True)
        form.addRow(levels_hint)
        form.addRow(self.levels)
        form.addRow(level_buttons)

        self.empty_page = QLabel("Выберите категорию или услугу слева.")
        self.empty_page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pages = QStackedWidget()
        for page in (self.empty_page, category_page, service_page):
            self.pages.addWidget(page)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.pages)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 780])

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        reset = QPushButton("Вернуть встроенный справочник")
        reset.setEnabled(paths.processes_file().exists())
        reset.clicked.connect(self._reset)
        bottom = QHBoxLayout()
        bottom.addWidget(reset)
        bottom.addStretch()
        bottom.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom)
        self._fill_tree()

    # --- дерево ---

    def _fill_tree(self, select=None):
        self.tree.blockSignals(True)
        self.tree.clear()
        restore = None
        for c in self.categories:
            cat_item = QTreeWidgetItem([c.name])
            cat_item.setData(0, Qt.ItemDataRole.UserRole, c)
            for p in c.processes:
                item = QTreeWidgetItem([p.name])
                item.setData(0, Qt.ItemDataRole.UserRole, p)
                cat_item.addChild(item)
                if p is select:
                    restore = item
            self.tree.addTopLevelItem(cat_item)
            if c is select:
                restore = cat_item
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self.current = None
        if restore:
            self.tree.setCurrentItem(restore)
        else:
            self._show(None)

    def _on_select(self, item, _previous):
        obj = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if self._commit():
            self._fill_tree(select=obj)  # услуга перенесена в другую категорию — дерево перестроено
        else:
            self._show(obj)

    def _show(self, obj):
        self.current = obj
        self.remove.setEnabled(obj is not None)
        if isinstance(obj, Category):
            self.category_name.setText(obj.name)
            self.pages.setCurrentIndex(1)
        elif isinstance(obj, Process):
            self.name.setText(obj.name)
            self.category.clear()
            for c in self.categories:
                self.category.addItem(c.name, c.id)
            self.category.setCurrentIndex(max(self.category.findData(self._category_of(obj).id), 0))
            self.aliases.setPlainText("\n".join(obj.aliases))
            self.phrases_ru.setPlainText("\n".join(obj.phrases_ru))
            self.phrases_en.setPlainText("\n".join(obj.phrases_en))
            self.key_ru.setText(SEP.join(obj.key_ru))
            self.key_en.setText(SEP.join(obj.key_en))
            self.exclude.setText(SEP.join(obj.exclude))
            self.target.setValue(obj.target)
            self.levels.setRowCount(0)
            for level in obj.levels:
                self._add_level_row(level)
            self.pages.setCurrentIndex(2)
        else:
            self.pages.setCurrentIndex(0)

    def _add_level_row(self, level: Level):
        row = self.levels.rowCount()
        self.levels.insertRow(row)
        self.levels.setItem(row, 0, QTableWidgetItem(level.name))
        match = QComboBox()
        match.addItems(MATCHES)
        match.setCurrentText(level.match if level.match in MATCHES else EXACT)
        self.levels.setCellWidget(row, 1, match)
        for col, lang in enumerate(LANGS, 2):
            self.levels.setItem(row, col, QTableWidgetItem(SEP.join(level.phrases.get(lang, []))))

    def _category_of(self, proc: Process) -> Category:
        return next(c for c in self.categories if proc in c.processes)

    def _rename_current(self, text: str):
        item = self.tree.currentItem()
        if item and text.strip():
            item.setText(0, text.strip())

    def _commit(self) -> bool:
        """Перенести поля формы в выбранную категорию или услугу; True — услуга сменила категорию."""
        obj = self.current
        if isinstance(obj, Category):
            obj.name = self.category_name.text().strip() or obj.name
            for p in obj.processes:
                p.category = obj.name
        elif isinstance(obj, Process):
            obj.name = self.name.text().strip() or obj.name
            obj.aliases = _lines(self.aliases)
            obj.phrases_ru = _lines(self.phrases_ru)
            obj.phrases_en = _lines(self.phrases_en)
            obj.key_ru = _split(self.key_ru.text())
            obj.key_en = _split(self.key_en.text())
            obj.exclude = _split(self.exclude.text())
            obj.target = self.target.value()
            obj.levels = []
            for row in range(self.levels.rowCount()):
                name = (self.levels.item(row, 0).text() if self.levels.item(row, 0) else "").strip()
                phrases = {lang: _split(self.levels.item(row, col).text() if self.levels.item(row, col) else "")
                           for col, lang in enumerate(LANGS, 2)}
                phrases = {lang: v for lang, v in phrases.items() if v}
                if name and phrases:
                    obj.levels.append(Level(name, self.levels.cellWidget(row, 1).currentText(), phrases))
            old, new = self._category_of(obj), self._category_by_id(self.category.currentData())
            if new is not None and new is not old:  # перенос в другую категорию
                old.processes.remove(obj)
                new.processes.append(obj)
                obj.category = new.name
                return True
        return False

    def _category_by_id(self, category_id) -> Category | None:
        return next((c for c in self.categories if c.id == category_id), None)

    # --- кнопки ---

    def _ids(self) -> set[str]:
        return {c.id for c in self.categories} | {p.id for c in self.categories for p in c.processes}

    def _add_category(self):
        self._commit()  # дерево всё равно перестраивается ниже
        c = Category(processes.new_id("category", self._ids()), "Новая категория", [])
        self.categories.append(c)
        self._fill_tree(select=c)
        self.category_name.selectAll()
        self.category_name.setFocus()

    def _add_service(self):
        self._commit()
        if not self.categories:
            self._add_category()
        obj = self.current
        target = obj if isinstance(obj, Category) else (self._category_of(obj) if obj else self.categories[0])
        p = Process(id=processes.new_id("service", self._ids()), name="Новая услуга", category=target.name,
                    phrases_ru=[], phrases_en=[])
        target.processes.append(p)
        self._fill_tree(select=p)
        self.name.selectAll()
        self.name.setFocus()

    def _remove(self):
        obj = self.current
        if isinstance(obj, Process):
            links = self.link_counts.get(obj.id, 0)
            text = f"Удалить услугу «{obj.name}»?"
            if links:
                text += (f"\n\nВ каталоге к ней привязано роликов: {links}. Ролики останутся в каталоге, "
                         "но без этой услуги; в покрытие она больше не войдёт.")
            if QMessageBox.question(self, "Справочник услуг", text) == QMessageBox.StandardButton.Yes:
                self._category_of(obj).processes.remove(obj)
                self.current = None
                self._fill_tree()
        elif isinstance(obj, Category):
            if obj.processes:
                QMessageBox.information(self, "Справочник услуг",
                                        "Сначала удалите или перенесите услуги этой категории.")
                return
            self.categories.remove(obj)
            self.current = None
            self._fill_tree()

    def problems(self) -> list[str]:
        """Что мешает сохранить: услуга без названия или без поисковых фраз."""
        found = []
        for c in self.categories:
            for p in c.processes:
                if not p.phrases_ru and not p.phrases_en:
                    found.append(f"«{p.name}» ({c.name}): нет поисковых фраз")
        return found

    def _save(self):
        self._commit()
        problems = self.problems()
        if problems:
            QMessageBox.warning(self, "Справочник услуг", "Не сохранено:\n" + "\n".join(problems))
            return
        processes.save_catalog(self.categories)
        self.accept()

    def _reset(self):
        answer = QMessageBox.question(self, "Справочник услуг",
                                      "Удалить ваши правки и вернуть встроенный справочник услуг?")
        if answer == QMessageBox.StandardButton.Yes:
            paths.processes_file().unlink(missing_ok=True)
            self.reset_requested = True
            self.accept()


def _split(text: str) -> list[str]:
    return [part.strip() for part in text.replace(";", "\n").splitlines() if part.strip()]
