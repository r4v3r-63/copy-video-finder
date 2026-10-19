"""Главное окно: одно поле ввода услуги, сетка результатов и панель просмотра."""

from html import escape

from pathlib import Path

from PyQt6.QtCore import QSize, QStringListModel, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QIcon, QPixmap
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
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app import __version__, paths, processes, query, updater
from app.cache import Cache
from app.downloader import DownloadJob, DownloadQueue
from app.search import SearchWorker, print_stems
from app.settings import Settings
from app.sources.base import SearchFilters, Source, VideoResult
from app.sources.pexels import PexelsSource
from app.sources.coverr import CoverrSource
from app.sources.pixabay import PixabaySource
from app.sources.vimeo import VimeoSource
from app.sources.wikimedia import WikimediaSource
from app.ui.player import PlayerPanel, format_duration
from app.ui.settings_dialog import SettingsDialog
from app.ui.update import UpdateController

THUMB = QSize(256, 144)


def all_sources(settings: Settings, cache: Cache) -> list[Source]:
    return [
        PixabaySource(settings.key("pixabay_api_key"), cache),
        CoverrSource(settings.key("coverr_api_key"), cache),
        VimeoSource(settings.key("vimeo_token"), cache),
        WikimediaSource(cache),
        PexelsSource(settings.key("pexels_api_key"), cache),
    ]


def make_sources(settings: Settings, cache: Cache) -> tuple[list[Source], list[str]]:
    """Площадки для поиска и названия пропущенных из-за отсутствия ключа."""
    sources = all_sources(settings, cache)
    return [s for s in sources if s.is_configured()], [s.name for s in sources if not s.is_configured()]


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, catalog: list[processes.Category], cache: Cache | None = None):
        super().__init__()
        self.settings = settings
        self.processes = processes.all_processes(catalog)
        self.printing = print_stems(self.processes)
        self._thumb_items: dict[str, QListWidgetItem] = {}
        self.dictionary = query.Dictionary.load()
        self.cache = cache or Cache()
        self.worker: SearchWorker | None = None
        self.sources: list[Source] = []
        self.problems: list[str] = []
        self.plan: query.SearchPlan | None = None
        self.plan_text = ""
        self.downloads = DownloadQueue(self)
        self.downloads.progress.connect(self._on_download_progress)
        self.downloads.done.connect(self._on_download_done)
        self.downloads.failed.connect(self._on_download_failed)
        self.download_state: dict[tuple[str, str], tuple[str, str, bool]] = {}  # ключ ролика → (текст, путь, идёт)
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
        self.player.allow_4k = settings.allow_4k
        self.player.download_requested.connect(self.download)
        self.player.open_folder_requested.connect(self._open_folder)

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

        self.show_rejected = QPushButton()
        self.show_rejected.setFlat(True)
        self.show_rejected.setToolTip("Ролики, у которых в названии и тегах нет слов услуги. "
                                      "Лицензия, длительность и размер у них подходят.")
        self.show_rejected.clicked.connect(self._show_off_topic)
        self.show_rejected.hide()
        self.statusBar().addPermanentWidget(self.show_rejected)
        self.statusBar().showMessage(f"Папка данных: {paths.data_dir()}")
        self._show_hint()

        self.updates = UpdateController(self, settings)
        # Через несколько секунд после запуска: убрать остатки прошлого обновления и проверить новое.
        QTimer.singleShot(4000, self._startup_update_check)

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

        help_menu = self.menuBar().addMenu("Справка")
        check = QAction("Проверить обновления…", self)
        check.triggered.connect(lambda: self.updates.check(manual=True))
        help_menu.addAction(check)
        about = QAction("О программе", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

    def _startup_update_check(self):
        updater.cleanup()
        if self.settings.check_updates:
            self.updates.check(manual=False)

    def _about(self):
        QMessageBox.about(self, "О программе",
                          f"Поиск видео для копицентров Copy.ru\nВерсия {__version__}\n\n"
                          f"Новые версии: {updater.RELEASES_PAGE}")

    def downloads_busy(self) -> bool:
        return any(busy for _, _, busy in self.download_state.values())

    def _show_hint(self):
        _, skipped = make_sources(self.settings, self.cache)
        if skipped:
            self.query_info.setText(
                f"Без ключа API пропускаются: {', '.join(skipped)}. Ключи вводятся в меню «Файл → Настройки». "
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
        self.sources, skipped = make_sources(self.settings, self.cache)
        info = self._describe_plan(plan)
        info += "\nПлощадки: " + ", ".join(src.name for src in self.sources)
        if skipped:
            info += f" (без ключа пропущены: {', '.join(skipped)})"
        self.query_info.setText(info)
        self.show_rejected.hide()
        self.plan = plan
        self.plan_text = self.query.text().strip()
        self.results.clear()
        self.player.show_result(None)
        self.problems = []
        self.sources_seen = {}
        self.source_links.setText("")

        self._thumb_items = {}
        self.worker = SearchWorker(plan, self.sources, self.filters(),
                                   self.settings.only_without_attribution, self, printing=self.printing)
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

    def _add_results(self, results: list[VideoResult], off_topic: bool = False):
        self._remove_placeholder()
        for r in results:
            text = f"{format_duration(r.duration)} · {r.width}×{r.height}\n{r.source} · {r.license.status}"
            if off_topic:
                r.relevance = -1  # в конец выдачи
                text = "Не по теме · " + text
            item = QListWidgetItem(text)
            if off_topic:
                item.setForeground(self.palette().placeholderText())
            item.setData(Qt.ItemDataRole.UserRole, r)
            item.setToolTip(r.title or r.page_url)
            item.setSizeHint(QSize(THUMB.width() + 16, THUMB.height() + 56))
            # Ближе к теме и к полиграфии — выше в выдаче.
            row = next((i for i in range(self.results.count())
                        if self.results.item(i).data(Qt.ItemDataRole.UserRole) is not None
                        and self.results.item(i).data(Qt.ItemDataRole.UserRole).relevance < r.relevance),
                       self.results.count())
            self.results.insertItem(row, item)
            if r.preview_url:
                token = f"{r.source}|{r.source_id}"
                self._thumb_items[token] = item
                request = QNetworkRequest(QUrl(r.preview_url))
                request.setAttribute(QNetworkRequest.Attribute.User, token)
                self.network.get(request)
            # Рядом с результатами площадки всегда ссылка на неё (требование Pexels и Pixabay).
            homepage = next((s.homepage for s in self.sources if s.name == r.source), "")
            self.sources_seen.setdefault(r.source, homepage)
        links = [f'<a href="{escape(url)}">{escape(name)}</a>' if url else escape(name)
                 for name, url in self.sources_seen.items()]
        self.source_links.setText("Видео предоставлены: " + ", ".join(links))

    def _on_thumbnail(self, reply: QNetworkReply):
        token = reply.request().attribute(QNetworkRequest.Attribute.User)
        if reply.error() == QNetworkReply.NetworkError.NoError and token is not None:
            pixmap = QPixmap()
            if pixmap.loadFromData(reply.readAll()):
                item = self._thumb_items.get(str(token))
                if item and self.results.row(item) >= 0:
                    item.setIcon(QIcon(pixmap.scaled(THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation)))
        reply.deleteLater()

    def _on_current(self, item: QListWidgetItem | None, _previous):
        result = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.player.show_result(result)
        if result and result.key in self.download_state:
            self.player.set_download_state(*self.download_state[result.key])

    def download_dir(self) -> Path:
        return Path(self.settings.download_dir) if self.settings.download_dir else paths.default_download_dir()

    def download(self, result: VideoResult, file):
        plan = self.plan
        job = DownloadJob(result=result, file=file, process=plan.process if plan else None,
                          query_text=self.plan_text, base_dir=self.download_dir(),
                          convert=self.settings.convert_to_mp4)
        self.downloads.add(job)
        self._set_download_state(result.key, "В очереди на скачивание…", "", True)

    def _set_download_state(self, key, text: str, path: str, busy: bool):
        self.download_state[key] = (text, path, busy)
        current = self.player.result
        if current and current.key == key:
            self.player.set_download_state(text, path, busy)

    def _job_key(self, job_id: int):
        return self.downloads.jobs[job_id].result.key

    def _on_download_progress(self, job_id: int, done: int, total: int):
        if done < 0:
            text = "Конвертация в MP4…"
        elif total:
            text = f"Скачивается: {done * 100 // total}% ({done / 1e6:.1f} из {total / 1e6:.1f} МБ)"
        else:
            text = f"Скачивается: {done / 1e6:.1f} МБ"
        self._set_download_state(self._job_key(job_id), text, "", True)

    def _on_download_done(self, job_id: int, path: str, note: str):
        text = f"Скачано: {path}" + (f" ({note})" if note else "") + ". Рядом — файл с лицензией."
        self._set_download_state(self._job_key(job_id), text, path, False)
        self.statusBar().showMessage(f"Скачано: {Path(path).name}")

    def _on_download_failed(self, job_id: int, message: str):
        self._set_download_state(self._job_key(job_id), f"Не скачано: {message}", "", False)

    def _open_folder(self, path: str):
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

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
        counts = self.worker.rejection_counts()
        if counts:
            details = ", ".join(f"{reason} — {n}" for reason, n in counts.most_common())
            message += f". Отсеяно: {details}"
        self.statusBar().showMessage(message)
        if self.problems:
            self.query_info.setText(self.query_info.text() + "\n" + "\n".join(self.problems))
        rejected = len(self.worker.off_topic_results())
        if rejected:
            self.show_rejected.setText(f"Показать отсеянные «не по теме» ({rejected})")
            self.show_rejected.show()
        if count == 0 and not self.problems:
            text = "Подходящих роликов не найдено."
            if self.plan and self.plan.process:
                text += " На бесплатных стоках по этой услуге роликов почти нет."
            if rejected:
                text += "\nМожно посмотреть отсеянные «не по теме» — кнопка внизу справа."
            else:
                text += " Попробуйте другую формулировку."
            placeholder = QListWidgetItem(text)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            placeholder.setSizeHint(QSize(THUMB.width() * 2, 80))
            self.results.addItem(placeholder)

    def _remove_placeholder(self):
        for i in reversed(range(self.results.count())):
            if self.results.item(i).data(Qt.ItemDataRole.UserRole) is None:
                self.results.takeItem(i)

    def _show_off_topic(self):
        if self.worker:
            self._add_results(self.worker.off_topic_results(), off_topic=True)
        self.show_rejected.hide()

    def _clear_cache(self):
        self.cache.clear_responses()
        self.statusBar().showMessage("Кэш ответов очищен.")

    def open_settings(self):
        if SettingsDialog(self.settings, self).exec():
            self.player.allow_4k = self.settings.allow_4k
            self._show_hint()

    def closeEvent(self, event):
        self.stop_search()
        self.downloads.stop()
        self.player.player.stop()
        super().closeEvent(event)
