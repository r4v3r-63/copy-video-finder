"""Главное окно: одно поле ввода услуги, сетка результатов и панель просмотра."""

from html import escape

from PyQt6.QtCore import QSize, QStringListModel, Qt, QUrl
from PyQt6.QtGui import QAction, QIcon, QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (
    QCompleter,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app import paths, processes, query
from app.cache import Cache
from app.search import SearchWorker
from app.settings import Settings
from app.sources.base import SearchFilters, Source, VideoResult
from app.sources.pexels import PexelsSource
from app.ui.player import PlayerPanel, format_duration
from app.ui.settings_dialog import SettingsDialog

THUMB = QSize(256, 144)


def make_sources(settings: Settings, cache: Cache) -> list[Source]:
    return [PexelsSource(settings.pexels_api_key, cache)]


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, catalog: list[processes.Category], cache: Cache | None = None):
        super().__init__()
        self.settings = settings
        self.processes = processes.all_processes(catalog)
        self.dictionary = query.Dictionary.load()
        self.cache = cache or Cache()
        self.worker: SearchWorker | None = None
        self.sources: list[Source] = []
        self.problems: list[str] = []
        self.sources_seen: dict[str, str] = {}
        self.network = QNetworkAccessManager(self)
        self.network.finished.connect(self._on_thumbnail)
        self.setWindowTitle("Поиск видео для копицентров Copy.ru")
        self.resize(1200, 760)

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
        self.search_button.clicked.connect(self._search_or_stop)

        search_row = QHBoxLayout()
        search_row.addWidget(self.query, 1)
        search_row.addWidget(self.search_button)

        self.query_info = QLabel()
        self.query_info.setWordWrap(True)
        self.query_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.source_links = QLabel()
        self.source_links.setOpenExternalLinks(True)

        self.results = QListWidget()
        self.results.setViewMode(QListView.ViewMode.IconMode)
        self.results.setIconSize(THUMB)
        self.results.setGridSize(QSize(THUMB.width() + 16, THUMB.height() + 56))
        self.results.setResizeMode(QListView.ResizeMode.Adjust)
        self.results.setMovement(QListView.Movement.Static)
        self.results.setWordWrap(True)
        self.results.setUniformItemSizes(True)
        self.results.currentItemChanged.connect(self._on_current)

        self.player = PlayerPanel()

        splitter = QSplitter()
        splitter.addWidget(self.results)
        splitter.addWidget(self.player)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout()
        layout.addLayout(search_row)
        layout.addWidget(self.query_info)
        layout.addWidget(self.source_links)
        layout.addWidget(splitter, 1)
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
        clear_cache = QAction("Очистить кэш", self)
        clear_cache.triggered.connect(self._clear_cache)
        menu.addAction(clear_cache)
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

    def search_plan(self) -> query.SearchPlan:
        """Запросы на русском и английском для введённой услуги или свободного текста."""
        return query.build_plan(self.query.text(), self.processes, self.dictionary)

    def filters(self) -> SearchFilters:
        return SearchFilters(min_duration=self.settings.min_duration, max_duration=self.settings.max_duration)

    def _search_or_stop(self):
        if self.worker and self.worker.isRunning():
            self.stop_search()
        else:
            self.run_search()

    def run_search(self):
        plan = self.search_plan()
        if not plan.queries:
            return
        self.stop_search()
        self.query_info.setText(self._describe_plan(plan))
        self.results.clear()
        self.player.show_result(None)
        self.problems = []
        self.sources_seen = {}
        self.source_links.setText("")

        self.sources = make_sources(self.settings, self.cache)
        self.worker = SearchWorker(plan, self.sources, self.filters(),
                                   self.settings.only_without_attribution, self)
        self.worker.found.connect(self._add_results)
        self.worker.problem.connect(self._on_problem)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.search_button.setText("Остановить")
        self.worker.start()

    def stop_search(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(5000)
            self.statusBar().showMessage("Поиск остановлен.")

    @staticmethod
    def _describe_plan(plan: query.SearchPlan) -> str:
        lines = []
        if plan.process:
            lines.append(f"Услуга: {plan.process.name} ({plan.process.category})")
        else:
            lines.append("Произвольный запрос")
        for lang, title in ((query.RU, "по-русски"), (query.EN, "по-английски")):
            texts = plan.texts(lang)
            if texts:
                lines.append(f"Ищем {title}: {', '.join(texts)}")
        if plan.note:
            lines.append(plan.note)
        return "\n".join(lines)

    def _add_results(self, results: list[VideoResult]):
        for r in results:
            text = f"{format_duration(r.duration)} · {r.width}×{r.height}\n{r.source} · {r.license.status}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, r)
            item.setToolTip(r.title or r.page_url)
            item.setSizeHint(QSize(THUMB.width() + 16, THUMB.height() + 56))
            self.results.addItem(item)
            if r.preview_url:
                request = QNetworkRequest(QUrl(r.preview_url))
                request.setAttribute(QNetworkRequest.Attribute.User, self.results.count() - 1)
                self.network.get(request)
            # Рядом с результатами площадки всегда ссылка на неё (требование Pexels и Pixabay).
            homepage = next((s.homepage for s in self.sources if s.name == r.source), "")
            self.sources_seen.setdefault(r.source, homepage)
        links = [f'<a href="{escape(url)}">{escape(name)}</a>' if url else escape(name)
                 for name, url in self.sources_seen.items()]
        self.source_links.setText("Видео предоставлены: " + ", ".join(links))

    def _on_thumbnail(self, reply: QNetworkReply):
        row = reply.request().attribute(QNetworkRequest.Attribute.User)
        if reply.error() == QNetworkReply.NetworkError.NoError and row is not None:
            pixmap = QPixmap()
            if pixmap.loadFromData(reply.readAll()):
                item = self.results.item(int(row))
                if item:
                    item.setIcon(QIcon(pixmap.scaled(THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation)))
        reply.deleteLater()

    def _on_current(self, item: QListWidgetItem | None, _previous):
        self.player.show_result(item.data(Qt.ItemDataRole.UserRole) if item else None)

    def _on_problem(self, message: str):
        if message not in self.problems:
            self.problems.append(message)

    def _on_progress(self, done: int, total: int):
        self.statusBar().showMessage(f"Поиск: {done} из {total} запросов, найдено {self.results.count()}")

    def _on_finished(self):
        self.search_button.setText("Найти")
        if not self.worker:
            return
        count = self.results.count()
        message = f"Найдено подходящих роликов: {count}"
        if self.worker.rejected:
            message += f" (отсеяно по длительности, размеру или лицензии: {self.worker.rejected})"
        self.statusBar().showMessage(message)
        if self.problems:
            self.query_info.setText(self.query_info.text() + "\n" + "\n".join(self.problems))
        if count == 0 and not self.problems:
            self.results.addItem("Ничего не найдено. Попробуйте другую формулировку.")

    def _clear_cache(self):
        self.cache.clear_responses()
        self.statusBar().showMessage("Кэш ответов очищен.")

    def open_settings(self):
        if SettingsDialog(self.settings, self).exec():
            self._show_hint()

    def closeEvent(self, event):
        self.stop_search()
        self.player.player.stop()
        super().closeEvent(event)
