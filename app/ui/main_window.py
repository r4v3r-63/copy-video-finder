"""Главное окно: одно поле ввода услуги и область результатов."""

from PyQt6.QtCore import QStringListModel, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QCompleter,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import paths, processes
from app.settings import Settings
from app.ui.settings_dialog import SettingsDialog


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, catalog: list[processes.Category]):
        super().__init__()
        self.settings = settings
        self.processes = processes.all_processes(catalog)
        self.setWindowTitle("Поиск видео для копицентров Copy.ru")
        self.resize(1000, 700)

        self._build_menu()

        self.query = QLineEdit()
        self.query.setPlaceholderText("Название услуги, например «ламинация» или «брошюровка на пружину»")
        self.query.setClearButtonEnabled(True)
        font = self.query.font()
        font.setPointSize(font.pointSize() + 3)
        self.query.setFont(font)
        self.query.setMinimumHeight(40)

        self._suggestions = QStringListModel(self)
        self.completer = QCompleter(self._suggestions, self)
        self.completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setMaxVisibleItems(10)
        self.query.setCompleter(self.completer)
        self.query.textEdited.connect(self._update_suggestions)
        self.completer.activated.connect(self._on_suggestion_chosen)
        self.query.returnPressed.connect(self.run_search)

        self.search_button = QPushButton("Найти")
        self.search_button.setMinimumHeight(40)
        self.search_button.clicked.connect(self.run_search)

        search_row = QHBoxLayout()
        search_row.addWidget(self.query, 1)
        search_row.addWidget(self.search_button)

        self.query_info = QLabel()
        self.query_info.setWordWrap(True)
        self.query_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.results = QListWidget()

        layout = QVBoxLayout()
        layout.addLayout(search_row)
        layout.addWidget(self.query_info)
        layout.addWidget(self.results, 1)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.statusBar().showMessage(f"Папка данных: {paths.data_dir()}")
        self._show_hint()

    def _build_menu(self):
        menu = self.menuBar().addMenu("Файл")
        settings_action = QAction("Настройки…", self)
        settings_action.triggered.connect(self.open_settings)
        menu.addAction(settings_action)
        menu.addSeparator()
        quit_action = QAction("Выход", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)

    def _show_hint(self):
        if not (self.settings.pexels_api_key or self.settings.pixabay_api_key):
            self.query_info.setText(
                "Ключи API Pexels и Pixabay не заданы — укажите их в меню «Файл → Настройки». "
                "Wikimedia Commons работает без ключа."
            )
        else:
            self.query_info.setText("")

    def _update_suggestions(self, text: str):
        found = processes.suggest(self.processes, text)
        self._suggestions.setStringList([p.display for p in found])
        if found:
            self.completer.complete()

    def _on_suggestion_chosen(self, text: str):
        self.query.setText(text)
        self.run_search()

    def search_phrases(self) -> tuple[processes.Process | None, list[str]]:
        """Услуга из справочника и фразы для поиска; для произвольного текста — сам текст."""
        text = self.query.text().strip()
        proc = processes.find_by_display(self.processes, text)
        if proc:
            return proc, proc.phrases
        return None, [text] if text else []

    def run_search(self):
        proc, phrases = self.search_phrases()
        if not phrases:
            return
        if proc:
            header = f"Услуга: {proc.name} ({proc.category}). Поисковые фразы: {', '.join(phrases)}"
        else:
            header = f"Произвольный запрос: {phrases[0]}"
        self.query_info.setText(header)
        self.results.clear()
        # Поиск по площадкам подключается на этапе 2.
        self.results.addItem("Поиск по площадкам будет подключён на следующем этапе.")

    def open_settings(self):
        if SettingsDialog(self.settings, self).exec():
            self._show_hint()
