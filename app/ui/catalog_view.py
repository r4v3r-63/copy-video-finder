"""Вкладка «Каталог»: дерево услуг, список роликов, карточка ролика."""

from datetime import datetime
from html import escape
from pathlib import Path

from PyQt6.QtCore import QDate, QItemSelectionModel, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app import catalog as cat
from app import design, licenses, similar
from app.processes import Category, Process
from app.search import preview_file
from app.ui import theme
from app.ui.batch_view import level_brush
from app.ui.frames_view import FramesStrip
from app.ui.player import format_duration, video_facts

ALL = "__all__"
NO_SERVICE = "__none__"
STATUS_FILTERS = [("Все отметки", None), ("Выбранные", cat.SELECTED), ("Отклонённые", cat.REJECTED),
                  ("Без отметки", cat.NEW)]
STATUS_NAMES = {cat.SELECTED: "выбрано", cat.REJECTED: "отклонено", cat.NEW: "—"}
LOOP_CHOICES = [("не проверено", None), ("да", True), ("нет", False)]


def _date(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y") if ts else "—"


class VideoCard(QWidget):
    """Карточка ролика в каталоге: редактируемые отметки, услуги и степень соответствия."""

    changed = pyqtSignal()

    def __init__(self, catalog: cat.Catalog, processes: list[Process], parent=None):
        super().__init__(parent)
        self.catalog = catalog
        self.processes = {p.id: p for p in processes}
        self.entry: cat.Entry | None = None

        self.header = QLabel()
        self.header.setWordWrap(True)
        self.header.setOpenExternalLinks(True)
        self.header.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.header.setProperty("muted", True)
        self.license_badge = theme.LicenseBadge()
        badge_row = QHBoxLayout()
        badge_row.addWidget(self.license_badge)
        badge_row.addStretch()

        self.title = QLineEdit()
        self.title.editingFinished.connect(self._save_title)
        self.status = theme.compact_combo(QComboBox())
        for value, name in STATUS_NAMES.items():
            self.status.addItem(name, value)
        self.status.activated.connect(self._save_status)
        self.confirmed = QCheckBox("Лицензию проверил(а)")
        self.confirmed.toggled.connect(self._save_confirmed)
        self.loop = theme.compact_combo(QComboBox())
        for name, value in LOOP_CHOICES:
            self.loop.addItem(name, value)
        self.loop.setToolTip("Можно ли крутить ролик по кругу без заметного скачка на стыке.")
        self.loop.activated.connect(self._save_loop)
        self.comment = QPlainTextEdit()
        self.comment.setPlaceholderText("Комментарий")
        self.comment.setMaximumHeight(70)
        self.save_comment = QPushButton("Сохранить комментарий")
        theme.set_role(self.save_comment, theme.SECONDARY)
        self.save_comment.clicked.connect(self._save_comment)

        self.links = QTableWidget(0, 3)
        self.links.setHorizontalHeaderLabels(["Услуга", "Соответствие", ""])
        self.links.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, width in ((1, 170), (2, 130)):
            self.links.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.links.setColumnWidth(col, width)
        self.links.verticalHeader().hide()
        self.links.setShowGrid(False)
        self.links.verticalHeader().setDefaultSectionSize(theme.CONTROL_HEIGHT + theme.SPACE_S)
        self.links.setMaximumHeight(150)
        self.add_process = theme.compact_combo(QComboBox())
        for p in processes:
            self.add_process.addItem(p.display, p.id)
        self.add_match = theme.compact_combo(QComboBox(), 12)
        self.add_match.addItems(cat.MATCHES)
        self.link_button = QPushButton("Привязать")
        theme.set_role(self.link_button, theme.SECONDARY)
        self.link_button.clicked.connect(self._add_link)
        add_row = QVBoxLayout()
        add_row.addWidget(self.add_process)
        add_actions = QHBoxLayout()
        add_actions.addWidget(self.add_match, 1)
        add_actions.addWidget(self.link_button)
        add_row.addLayout(add_actions)

        self.open_page = QPushButton("Открыть страницу ролика")
        theme.set_icon(self.open_page, "external")
        self.open_page.clicked.connect(
            lambda: self.entry and QDesktopServices.openUrl(QUrl(self.entry.result.page_url)))
        self.open_file = QPushButton("Показать файл")
        theme.set_icon(self.open_file, "folder")
        self.open_file.clicked.connect(self._show_file)
        self.delete = QPushButton("Удалить из каталога")
        self.delete.clicked.connect(self._delete)
        buttons = QVBoxLayout()
        buttons.addWidget(self.open_page)
        buttons.addWidget(self.open_file)
        buttons.addWidget(self.delete)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.addRow("Название:", self.title)
        form.addRow("Отметка:", self.status)
        form.addRow("", self.confirmed)
        form.addRow("Для зацикливания:", self.loop)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SPACE_L, theme.SPACE_L, theme.SPACE_L, theme.SPACE_L)
        layout.setSpacing(theme.SPACE_M)
        layout.addLayout(badge_row)
        layout.addWidget(self.header)
        layout.addLayout(form)
        layout.addWidget(QLabel("Услуги и степень соответствия:"))
        layout.addWidget(self.links)
        layout.addLayout(add_row)
        layout.addWidget(self.comment)
        layout.addWidget(self.save_comment)
        self.frames = FramesStrip()
        layout.addWidget(self.frames)
        layout.addLayout(buttons)
        layout.addStretch()
        self.show_entry(None)

    def set_processes(self, processes: list[Process]):
        self.processes = {p.id: p for p in processes}
        self.add_process.clear()
        for p in processes:
            self.add_process.addItem(p.display, p.id)
        if self.entry:
            self._fill_links()

    def show_entry(self, entry: cat.Entry | None):
        self.entry = entry
        self.license_badge.setVisible(entry is not None)
        if entry:
            self.license_badge.set_license(entry.result.license)
        for w in (self.title, self.status, self.confirmed, self.loop, self.comment, self.links, self.add_process,
                  self.add_match, self.link_button, self.save_comment, self.open_page, self.delete):
            w.setEnabled(entry is not None)
        self.open_file.setEnabled(bool(entry and entry.downloaded_path))
        self.frames.set_video(entry.id if entry else None, self._frames_source(entry),
                              entry.result.duration if entry else None)
        if entry is None:
            self.header.setText("Выберите ролик в списке.")
            self.title.clear()
            self.comment.clear()
            self.links.setRowCount(0)
            return
        r, lic = entry.result, entry.result.license
        facts = video_facts(r)[:-1]  # площадка уже в начале строки
        lines = [f"<b>№ {entry.id}</b> · {escape(r.source)} · ID {escape(r.source_id)}"
                 + "".join(f" · {escape(f)}" for f in facts)]
        name = escape(lic.name or "не указана")
        if lic.url:
            name = f'<a href="{escape(lic.url)}">{name}</a>'
        lines.append(f"Лицензия: {name} — {escape(lic.status)}")
        if lic.note:
            lines.append(escape(lic.note))
        if lic.status == licenses.ATTRIBUTION:
            lines.append("Атрибуция: " + escape(licenses.attribution_text(r.author, r.title, lic.name, r.page_url)))
        if r.author:
            lines.append(f"Автор: {escape(r.author)}")
        lines.append(f"Добавлен: {_date(entry.added_at)} · лицензия проверена: {_date(entry.checked_at)}")
        if entry.downloaded_path:
            lines.append(f"Скачан: {escape(entry.downloaded_path)}")
        if not entry.available:
            lines.append(f"<b>Удалён с площадки</b> (проверено {_date(entry.link_checked_at)})")
        if entry.loop_hint is not None and entry.loop is None:
            lines.append("Подсказка: первый и последний кадр " +
                         ("похожи — вероятно, подходит для зацикливания." if entry.loop_hint else
                          "заметно отличаются — на стыке будет скачок."))
        similar_ids = self._similar(entry)
        if similar_ids:
            lines.append("Похож на ролики № " + ", ".join(map(str, similar_ids)) + " — возможный дубль.")
        if not entry.counts_for_coverage:
            lines.append("<i>В покрытие не засчитывается: нужна подходящая подтверждённая лицензия, "
                         "отметка не «отклонено» и ролик на площадке или скачанный.</i>")
        self.header.setText("<br>".join(lines))
        self.title.setText(r.title)
        self.status.setCurrentIndex(max(self.status.findData(entry.status), 0))
        self.confirmed.blockSignals(True)
        self.confirmed.setChecked(entry.license_confirmed)
        self.confirmed.blockSignals(False)
        self.loop.setCurrentIndex([v for _, v in LOOP_CHOICES].index(entry.loop))
        self.comment.setPlainText(entry.comment)
        self._fill_links()

    def _fill_links(self):
        self.links.setRowCount(0)
        for process_id, match in sorted(self.entry.links.items()):
            row = self.links.rowCount()
            self.links.insertRow(row)
            proc = self.processes.get(process_id)
            self.links.setItem(row, 0, QTableWidgetItem(proc.display if proc else process_id))
            combo = theme.compact_combo(QComboBox(), 12)
            combo.addItems(cat.MATCHES)
            combo.setCurrentText(match)
            combo.activated.connect(lambda _i, pid=process_id, c=combo: self._relink(pid, c.currentText()))
            self.links.setCellWidget(row, 1, combo)
            remove = QPushButton("Отвязать")
            remove.clicked.connect(lambda _c=False, pid=process_id: self._unlink(pid))
            self.links.setCellWidget(row, 2, remove)

    def _similar(self, entry: cat.Entry) -> list[int]:
        if not entry.phash:
            return []
        return [e.id for e in self.catalog.entries() if e.id != entry.id and e.phash
                and similar.distance(e.phash, entry.phash) <= similar.DUPLICATE_DISTANCE]

    @staticmethod
    def _frames_source(entry: cat.Entry | None) -> str:
        """Кадры — из скачанного файла, если он есть, иначе по ссылке для просмотра."""
        if entry is None:
            return ""
        if entry.downloaded_path and Path(entry.downloaded_path).exists():
            return entry.downloaded_path
        preview = preview_file(entry.result)
        return preview.url if preview else ""

    def _reload(self):
        self.show_entry(self.catalog.get(self.entry.id))
        self.changed.emit()

    def _save_title(self):
        if self.entry and self.title.text().strip() and self.title.text() != self.entry.result.title:
            self.catalog.set_title(self.entry.id, self.title.text().strip())
            self._reload()

    def _save_status(self):
        if self.entry:
            self.catalog.set_status(self.entry.id, self.status.currentData())
            self._reload()

    def _save_confirmed(self, checked: bool):
        if self.entry:
            self.catalog.confirm_license(self.entry.id, checked)
            self._reload()

    def _save_loop(self):
        if self.entry:
            self.catalog.set_loop(self.entry.id, LOOP_CHOICES[self.loop.currentIndex()][1])
            self._reload()

    def _save_comment(self):
        if self.entry:
            self.catalog.set_comment(self.entry.id, self.comment.toPlainText().strip())
            self._reload()

    def _add_link(self):
        if self.entry:
            self.catalog.link(self.entry.id, self.add_process.currentData(), self.add_match.currentText())
            self._reload()

    def _relink(self, process_id: str, match: str):
        self.catalog.link(self.entry.id, process_id, match)
        self._reload()

    def _unlink(self, process_id: str):
        self.catalog.unlink(self.entry.id, process_id)
        self._reload()

    def _show_file(self):
        if self.entry and self.entry.downloaded_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.entry.downloaded_path).parent)))

    def _delete(self):
        if not self.entry:
            return
        answer = QMessageBox.question(self, "Удалить из каталога",
                                      f"Удалить ролик № {self.entry.id} из каталога? Скачанный файл останется.")
        if answer == QMessageBox.StandardButton.Yes:
            self.catalog.delete(self.entry.id)
            self.show_entry(None)
            self.changed.emit()


class CatalogView(QWidget):
    add_link_requested = pyqtSignal()
    download_requested = pyqtSignal(list)
    check_links_requested = pyqtSignal(list)

    def __init__(self, catalog: cat.Catalog, categories: list[Category], parent=None):
        super().__init__(parent)
        self.catalog = catalog
        self.categories = categories
        self.processes = [p for c in categories for p in c.processes]

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Услуга", "Роликов", "В покрытии"])
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setMinimumWidth(360)
        self.tree.currentItemChanged.connect(lambda *_: self.refresh_list())

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по названию, описанию, площадке, комментарию")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.refresh_list())
        self.status_filter = theme.compact_combo(QComboBox())
        for name, value in STATUS_FILTERS:
            self.status_filter.addItem(name, value)
        self.status_filter.activated.connect(lambda _i: self.refresh_list())
        filters = QHBoxLayout()
        self.add_link = QPushButton("Добавить по ссылке…")
        theme.set_role(self.add_link, theme.SECONDARY)
        theme.set_icon(self.add_link, "link")
        self.add_link.clicked.connect(self.add_link_requested.emit)
        filters.addWidget(self.add_link)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.status_filter)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["№", "Название", "Площадка", "Длит.", "Лицензия", "Отметка", "Скачан"])
        columns = self.table.horizontalHeader()
        for col, width in {0: 44, 2: 120, 3: 52, 4: 170, 5: 80, 6: 60}.items():
            columns.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(col, width)
        columns.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumWidth(420)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(design.TABLE_ROW_HEIGHT)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.currentCellChanged.connect(lambda row, *_: self._show_row(row))

        self.card = theme.make_card(VideoCard(catalog, self.processes))
        self.card.changed.connect(self.refresh)

        self.bulk = QToolButton()
        self.bulk.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        actions = QMenu(self.bulk)
        actions.addAction("Выделить все", self.table.selectAll)
        actions.addAction("Снять выделение", self.table.clearSelection)
        actions.addSeparator()
        for title, status in (("Отметить выбранными", cat.SELECTED), ("Отклонить", cat.REJECTED),
                              ("Снять отметку", cat.NEW)):
            actions.addAction(title, lambda _checked=False, s=status: self._bulk_status(s))
        actions.addAction("Скачать выбранные", lambda: self.download_requested.emit(self.selected_entries()))
        actions.addAction("Скопировать ссылки", self._copy_links)
        actions.addAction("Проверить ссылки выбранных",
                          lambda: self.check_links_requested.emit(self.selected_entries()))
        self.bulk.setMenu(actions)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self._selection_changed()
        self.filter_button = QPushButton("Фильтры…")
        theme.set_icon(self.filter_button, "filters")
        self.filter_button.setCheckable(True)
        options = QHBoxLayout()
        options.addWidget(self.bulk)
        options.addStretch()
        options.addWidget(self.filter_button)
        self.filter_panel = QWidget()
        form = QFormLayout(self.filter_panel)
        self.source_filter = theme.compact_combo(QComboBox())
        self.license_filter = theme.compact_combo(QComboBox())
        self.match_filter = theme.compact_combo(QComboBox())
        self.match_filter.addItem("Любое соответствие", None)
        for match in cat.MATCHES:
            self.match_filter.addItem(match, match)
        self.downloaded_filter = QCheckBox("Только скачанные (файл на диске)")
        self.since_filter = QCheckBox("Добавленные с даты:")
        self.since_date = QDateEdit(QDate.currentDate())
        self.since_date.setCalendarPopup(True)
        self.since_date.setEnabled(False)
        self.since_filter.toggled.connect(self.since_date.setEnabled)
        form.addRow("Площадка:", self.source_filter)
        form.addRow("Лицензия:", self.license_filter)
        form.addRow("Соответствие:", self.match_filter)
        form.addRow(self.since_filter, self.since_date)
        form.addRow(self.downloaded_filter)
        reset = QPushButton("Сбросить фильтры")
        reset.clicked.connect(self.reset_filters)
        form.addRow(reset)
        for combo in (self.source_filter, self.license_filter, self.match_filter):
            combo.currentIndexChanged.connect(lambda _i: self.refresh_list())
        for checkbox in (self.downloaded_filter, self.since_filter):
            checkbox.toggled.connect(lambda _checked: self.refresh_list())
        self.since_date.dateChanged.connect(lambda _date: self.refresh_list())
        self.filter_button.toggled.connect(self.filter_panel.setVisible)
        self.filter_panel.hide()

        middle = QWidget()
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.addLayout(filters)
        middle_layout.addLayout(options)
        middle_layout.addWidget(self.filter_panel)
        middle_layout.addWidget(self.table)

        splitter = QSplitter()
        card_scroll = QScrollArea()
        card_scroll.setWidget(self.card)
        card_scroll.setWidgetResizable(True)
        card_scroll.setFrameShape(QFrame.Shape.NoFrame)
        card_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        splitter.addWidget(self.tree)
        splitter.addWidget(middle)
        splitter.addWidget(card_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([420, 520, 520])
        # Скрытая вкладка тоже задаёт минимальную ширину окна: панели не должны раздуваться
        # от длинных названий и подписей, иначе окно уезжает за экран.
        for pane, width in ((self.tree, 180), (middle, 220), (card_scroll, 300)):
            pane.setMinimumWidth(width)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)
        self.refresh()

    def set_categories(self, categories: list[Category]):
        self.categories = categories
        self.processes = [p for c in categories for p in c.processes]
        self.card.set_processes(self.processes)
        self.refresh()

    def selected_process(self) -> str:
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else ALL

    def refresh(self):
        """Обновить дерево с числами и список (после изменений в каталоге)."""
        current = self.selected_process()
        entries = self.catalog.entries()
        for combo, title, values in (
                (self.source_filter, "Все площадки", {e.result.source for e in entries}),
                (self.license_filter, "Все лицензии", {e.result.license.name for e in entries})):
            value = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(title, None)
            for name in sorted(values):
                combo.addItem(name or "Не указана", name)
            combo.setCurrentIndex(max(0, combo.findData(value)))
            combo.blockSignals(False)
        coverage = self.catalog.coverage()
        totals: dict[str, int] = {}
        for e in entries:
            for pid in e.links:
                totals[pid] = totals.get(pid, 0) + 1
        self.tree.blockSignals(True)
        self.tree.clear()
        all_item = QTreeWidgetItem(["Все ролики", str(len(entries)), ""])
        all_item.setData(0, Qt.ItemDataRole.UserRole, ALL)
        none_item = QTreeWidgetItem(["Без услуги", str(sum(1 for e in entries if not e.links)), ""])
        none_item.setData(0, Qt.ItemDataRole.UserRole, NO_SERVICE)
        self.tree.addTopLevelItems([all_item, none_item])
        restore = all_item
        for c in self.categories:
            cat_item = QTreeWidgetItem([c.name, str(sum(totals.get(p.id, 0) for p in c.processes)),
                                        str(sum(coverage.get(p.id, 0) for p in c.processes))])
            cat_item.setData(0, Qt.ItemDataRole.UserRole, None)
            for p in c.processes:
                item = QTreeWidgetItem([p.name, str(totals.get(p.id, 0)), f"{coverage.get(p.id, 0)} из {p.target}"])
                item.setData(0, Qt.ItemDataRole.UserRole, p.id)
                item.setToolTip(0, p.name + ("\n\nУслуги на сайте:\n" + "\n".join(p.variants) if p.variants else ""))
                item.setBackground(2, level_brush(coverage.get(p.id, 0), p.target))
                cat_item.addChild(item)
                if p.id == current:
                    restore = item
            self.tree.addTopLevelItem(cat_item)
        self.tree.expandAll()
        if current == NO_SERVICE:
            restore = none_item
        self.tree.setCurrentItem(restore)
        self.tree.blockSignals(False)
        self.refresh_list()

    def refresh_list(self):
        selected_ids = {e.id for e in self.selected_entries()} if hasattr(self, "entries") else set()
        selected_id = self.card.entry.id if self.card.entry else None
        pid = self.selected_process()
        if pid is None:  # выбрана категория — все её услуги
            item = self.tree.currentItem()
            ids = {item.child(i).data(0, Qt.ItemDataRole.UserRole) for i in range(item.childCount())}
            entries = [e for e in self.catalog.entries(status=self.status_filter.currentData(),
                                                       text=self.search.text()) if ids & set(e.links)]
        else:
            entries = self.catalog.entries(process_id=None if pid in (ALL, NO_SERVICE) else pid,
                                           status=self.status_filter.currentData(), text=self.search.text())
            if pid == NO_SERVICE:
                entries = [e for e in entries if not e.links]
        since = self.since_date.date().startOfDay().toSecsSinceEpoch() if self.since_filter.isChecked() else None
        source, license_name, match = (c.currentData() for c in
                                       (self.source_filter, self.license_filter, self.match_filter))
        scope = ids if pid is None else {pid} if pid not in (ALL, NO_SERVICE) else None
        entries = [e for e in entries if (source is None or e.result.source == source)
                   and (license_name is None or e.result.license.name == license_name)
                   and (match is None or any(m == match and (scope is None or p in scope) for p, m in e.links.items()))
                   and (not self.downloaded_filter.isChecked() or e.has_file)
                   and (since is None or e.added_at >= since)]
        self.entries = entries
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        restore = -1
        for e in entries:
            row = self.table.rowCount()
            self.table.insertRow(row)
            lic = e.result.license
            license_text = f"{lic.status}{'' if e.license_confirmed else ' (не проверена)'}"
            title = e.result.title + (" (удалён с площадки)" if not e.available else "")
            for col, text in enumerate([str(e.id), title, e.result.source, format_duration(e.result.duration),
                                        license_text, STATUS_NAMES.get(e.status, e.status),
                                        "да" if e.downloaded_path else ""]):
                item = QTableWidgetItem(text)
                if not e.available:
                    item.setForeground(QColor(theme.palette().text_secondary))
                    item.setToolTip("Удалён с площадки")
                self.table.setItem(row, col, item)
            if e.id == selected_id:
                restore = row
        self.table.clearSelection()
        for row, entry in enumerate(entries):
            if entry.id in selected_ids:
                self.table.selectionModel().select(self.table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.table.blockSignals(False)
        if restore >= 0:
            self.card.show_entry(entries[restore])
        else:
            self.card.show_entry(None)
        self._selection_changed()

    def select_entry(self, video_id: int):
        """Показать ролик: сбросить фильтры, выбрать «Все ролики» и строку ролика."""
        self.search.clear()
        self.status_filter.setCurrentIndex(0)
        self.reset_filters()
        self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.refresh_list()
        for row, e in enumerate(self.entries):
            if e.id == video_id:
                self.table.selectRow(row)
                self.card.show_entry(e)
                break

    def _show_row(self, row: int):
        self.card.show_entry(self.entries[row] if 0 <= row < len(self.entries) else None)

    def selected_entries(self) -> list[cat.Entry]:
        if not hasattr(self, "entries"):
            return []
        return [self.entries[index.row()] for index in sorted(self.table.selectionModel().selectedRows(),
                                                              key=lambda i: i.row())
                if index.row() < len(self.entries)]

    def _selection_changed(self):
        self.bulk.setText(f"Выбранные: {len(self.selected_entries())}")

    def _bulk_status(self, status):
        entries = self.selected_entries()
        if not entries:
            return
        for entry in entries:
            self.catalog.set_status(entry.id, status)
        self.refresh()

    def _copy_links(self):
        entries = self.selected_entries()
        if entries:
            QApplication.clipboard().setText("\n".join(dict.fromkeys(e.result.page_url for e in entries)))

    def reset_filters(self):
        for combo in (self.source_filter, self.license_filter, self.match_filter):
            combo.setCurrentIndex(0)
        self.downloaded_filter.setChecked(False)
        self.since_filter.setChecked(False)
        self.refresh_list()
