"""Главное окно: одно поле ввода услуги, сетка результатов и панель просмотра."""

import io
from datetime import datetime
from html import escape
from pathlib import Path

from PyQt6.QtCore import QSize, QStringListModel, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QIcon, QImage, QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (
    QApplication,
    QCompleter,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import __version__, backup, licenses, maintenance, paths, processes, query, updater
from app import catalog as cat
from app.batch import BatchWorker
from app.cache import Cache
from app.downloader import DownloadJob, DownloadQueue, safe_name
from app.export import html_catalog, playlist, registry, report, showreel, summary
from app.search import SearchWorker, print_stems
from app.settings import Settings
from app.sources.archive import ArchiveSource
from app.sources.base import SearchFilters, Source, VideoResult
from app.sources.coverr import CoverrSource
from app.sources.europeana import EuropeanaSource
from app.sources.pexels import PexelsSource
from app.sources.pixabay import PixabaySource
from app.sources.videezy import VideezySource
from app.sources.vimeo import VimeoSource
from app.sources.vk import VkSource
from app.sources.web import WebSource
from app.sources.yandex_video import YandexVideoSource
from app.ui import theme
from app.ui.add_link_dialog import AddLinkDialog
from app.ui.batch_view import BatchView
from app.ui.catalog_view import CatalogView
from app.ui.player import PlayerPanel, format_duration
from app.ui.services_dialog import ServicesDialog
from app.ui.settings_dialog import SettingsDialog
from app.ui.showreel_dialog import ShowreelDialog
from app.ui.smooth_scroll import smooth
from app.ui.tools import DictionaryDialog, DuplicatesDialog, HistoryDialog
from app.ui.update import UpdateController

THUMB = QSize(256, 144)


def load_image(data: bytes) -> QImage:
    """Картинка превью. Форматы, которых Qt не знает (Coverr отдаёт AVIF), — через Pillow."""
    image = QImage()
    if image.loadFromData(data):
        return image
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            png = io.BytesIO()
            img.convert("RGB").save(png, "PNG")
        image.loadFromData(png.getvalue())
    except (OSError, ValueError):
        pass  # битая картинка — плитка без превью
    return image


def all_sources(settings: Settings, cache: Cache) -> list[Source]:
    sources = [
        PixabaySource(settings.key("pixabay_api_key"), cache),
        CoverrSource(settings.key("coverr_api_key"), cache),
        VimeoSource(settings.key("vimeo_token"), cache),
        # Wikimedia Commons не ищется (решение пользователя 20.10.2026); его страницы из интернета — тоже.
        EuropeanaSource(settings.key("europeana_api_key"), cache),
        ArchiveSource(cache),
        VideezySource(cache),
        PexelsSource(settings.key("pexels_api_key"), cache),
        YandexVideoSource(cache),  # Rutube, VK Видео, Дзен, Одноклассники, YouTube — одной выдачей
        VkSource(settings.vk_token, cache, app_id=settings.vk_app_id, refresh_token=settings.vk_refresh_token,
                 device_id=settings.vk_device_id, expires_at=settings.vk_token_expires,
                 on_tokens=lambda t: _save_vk_tokens(settings, t)),  # после входа в VK в настройках
    ]
    # Поиск по интернету: найденные страницы известных площадок открываются их модулями.
    return sources + [WebSource(cache, delegates=sources, tavily_key=settings.key("tavily_api_key"),
                                brave_key=settings.key("brave_api_key"))]


def _save_vk_tokens(settings: Settings, tokens) -> None:
    """VK выдал новые ключи (продление) — сразу в настройки: прежний ключ обновления больше не годится."""
    settings.vk_token, settings.vk_refresh_token = tokens.access, tokens.refresh
    settings.vk_device_id, settings.vk_token_expires = tokens.device_id, tokens.expires_at
    settings.save()


def make_sources(settings: Settings, cache: Cache) -> tuple[list[Source], list[str]]:
    """Площадки для поиска и названия пропущенных из-за отсутствия ключа."""
    sources = all_sources(settings, cache)
    return [s for s in sources if s.is_configured()], [s.name for s in sources if not s.is_configured()]


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, categories: list[processes.Category], cache: Cache | None = None,
                 store: cat.Catalog | None = None):
        super().__init__()
        self.settings = settings
        self.categories = categories
        self.processes = processes.all_processes(categories)
        self.catalog = store or cat.Catalog()
        self.printing = print_stems(self.processes)
        self._thumb_items: dict[str, QListWidgetItem] = {}
        self.dictionary = query.Dictionary.load()
        self.cache = cache or Cache()
        self.worker: SearchWorker | None = None
        self.backup_worker: backup.BackupWorker | None = None
        self.link_dialog: AddLinkDialog | None = None
        self._closing = False
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
        self.query.setObjectName("query")  # крупное поле — в стиле темы

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
        theme.set_role(self.search_button, theme.PRIMARY)
        self.search_button.setProperty("size", "large")
        self.search_button.clicked.connect(self._search_or_stop)

        search_row = QHBoxLayout()
        search_row.addWidget(self.query, 1)
        search_row.addWidget(self.search_button)

        self.query_info = QLabel()
        self.query_info.setWordWrap(True)
        self.query_info.setProperty("muted", True)
        self.query_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.source_links = QLabel()
        self.source_links.setOpenExternalLinks(True)

        self.results = QListWidget()
        self.results.setViewMode(QListView.ViewMode.IconMode)
        self.results.setIconSize(THUMB)
        self.results.setGridSize(QSize(THUMB.width() + 16, THUMB.height() + 76))
        self.results.setResizeMode(QListView.ResizeMode.Adjust)
        self.results.setMovement(QListView.Movement.Static)
        self.results.setWordWrap(True)
        self.results.setUniformItemSizes(True)
        self.results_scroll = smooth(self.results, step=THUMB.height())  # колесико — плавно, не рывком
        self.results.currentItemChanged.connect(self._on_current)

        self.player = theme.make_card(PlayerPanel())
        self.player.allow_4k = settings.allow_4k
        self.player.download_requested.connect(self.download)
        self.player.open_folder_requested.connect(self._open_folder)
        self.player.select_requested.connect(self.add_to_catalog)
        self.player.reject_requested.connect(self.reject_result)

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
        search_tab = QWidget()
        search_tab.setLayout(layout)
        self.catalog_view = CatalogView(self.catalog, categories)
        self.catalog_view.add_link_requested.connect(self.add_by_link)
        # Три раздела: поиск, пакетный поиск по всем услугам, каталог сохранённых роликов.
        self.batch_view = BatchView(self.catalog, categories)
        self.batch_view.open_service.connect(self.open_service)
        self.batch_view.batch_requested.connect(self.start_batch)
        self.batch_view.stop_requested.connect(self.stop_batch)
        self.tabs = QTabWidget()
        self.tabs.addTab(search_tab, "Поиск")
        self.tabs.addTab(self.batch_view, "Пакетный поиск")
        self.tabs.addTab(self.catalog_view, "Каталог")
        self.tabs.currentChanged.connect(self._on_tab)
        self.batch: BatchWorker | None = None
        self.link_check: maintenance.LinkCheckWorker | None = None
        self.duplicates: maintenance.DuplicateWorker | None = None
        self.report_worker: report.ReportWorker | None = None
        self.showreel_worker: showreel.ShowreelWorker | None = None
        self.setCentralWidget(self.tabs)

        self.show_rejected = QPushButton()
        self.show_rejected.setFlat(True)
        self.show_rejected.setToolTip("Ролики, у которых в названии и тегах нет слов услуги. "
                                      "Лицензия, длительность и размер у них подходят.")
        self.show_rejected.clicked.connect(self._show_off_topic)
        self.show_rejected.hide()
        self.statusBar().addPermanentWidget(self.show_rejected)
        self.show_unlicensed = QPushButton()
        self.show_unlicensed.setFlat(True)
        self.show_unlicensed.setToolTip("Ролики по теме, но без свободной лицензии («все права защищены», NC, ND). "
                                        "Только посмотреть: скачать и засчитать в покрытие нельзя, использовать — "
                                        "с разрешения автора. Платные не показываются.")
        self.show_unlicensed.clicked.connect(self._show_unlicensed)
        self.show_unlicensed.hide()
        self.statusBar().addPermanentWidget(self.show_unlicensed)
        self.statusBar().showMessage(f"Папка данных: {paths.data_dir()}")
        self._show_hint()

        self.updates = UpdateController(self, settings)
        # «Как в системе»: тема переключается вслед за Windows.
        QApplication.styleHints().colorSchemeChanged.connect(
            lambda _s: self.settings.theme == theme.SYSTEM and self.apply_theme())
        # Через несколько секунд после запуска: убрать остатки прошлого обновления и проверить новое.
        QTimer.singleShot(4000, self._startup_update_check)

    def _build_menu(self):
        """Меню — только «Файл» и «Справка»: всё основное — на трёх вкладках."""
        def add(menu, title, handler):
            action = QAction(title, self)
            action.triggered.connect(handler)
            menu.addAction(action)

        menu = self.menuBar().addMenu("Файл")
        add(menu, "Настройки…", self.open_settings)
        add(menu, "Справочник услуг…", self.edit_services)
        menu.addSeparator()
        export = menu.addMenu("Экспорт каталога")
        for title, handler in (("Реестр в Excel…", self.export_xlsx), ("Реестр в CSV…", self.export_csv),
                               ("HTML-каталог…", self.export_html),
                               ("Отчёт для руководства (PDF)…", self.export_report),
                               ("Сводка покрытия…", self.export_summary),
                               ("Плейлист M3U из скачанных…", self.export_playlist),
                               ("Шоурил для экрана…", self.export_showreel)):
            add(export, title, handler)
        add(menu, "Импорт реестра CSV…", self.import_csv)
        tools = menu.addMenu("Сервис")
        for title, handler in (("История поиска…", self.show_history), ("Словарь перевода…", self.edit_dictionary),
                               ("Проверить ссылки каталога", lambda: self.check_links(manual=True)),
                               ("Найти похожие дубли…", self.find_duplicates), ("Очистить кэш", self._clear_cache),
                               ("Резервная копия данных…", self.backup_data)):
            add(tools, title, handler)
        menu.addSeparator()
        add(menu, "Выход", self.close)

        help_menu = self.menuBar().addMenu("Справка")
        add(help_menu, "Проверить обновления…", lambda: self.updates.check(manual=True))
        add(help_menu, "О программе", self._about)

    def apply_theme(self):
        theme.apply(QApplication.instance(), self.settings.theme)
        self.catalog_view.refresh()
        self.batch_view.refresh()

    def _startup_update_check(self):
        updater.cleanup()
        if self.settings.check_updates:
            self.updates.check(manual=False)
        if maintenance.link_check_due(self.catalog, self.settings.link_check_days) and self.catalog.entries():
            self.check_links(manual=False)

    # --- история, словарь, обслуживание каталога ---

    def backup_data(self):
        """Архив «Данных» вне исходной папки, без остановки интерфейса."""
        if self.backup_worker and self.backup_worker.isRunning():
            return
        if any(active for _, _, active in self.download_state.values()) or any(
                worker and worker.isRunning() for worker in (self.showreel_worker, self.report_worker)):
            QMessageBox.information(self, "Резервная копия", "Дождитесь окончания скачивания и экспорта.")
            return
        source = paths.data_dir()
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dialog = QFileDialog(self, "Резервная копия данных", str(source.parent / f"Данные_{stamp}.zip"),
                             "Архив ZIP (*.zip)")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setDefaultSuffix("zip")
        if not dialog.exec():
            return
        path = Path(dialog.selectedFiles()[0])
        if path.resolve().is_relative_to(source.resolve()):
            QMessageBox.warning(self, "Резервная копия", "Сохраните резервную копию вне папки «Данные».")
            return
        progress = QProgressDialog("Копируются каталог, настройки и файлы из «Данных».\n"
                                   "Архив содержит ключи из настроек — храните его в надёжном месте.\n"
                                   "Ролики из внешней папки в архив не входят.", "Отмена", 0, 0, self)
        progress.setWindowTitle("Резервная копия")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        worker = backup.BackupWorker(source, path, self)

        def advance(done, total):
            if not worker.cancel.is_set():
                progress.setRange(0, total)
                progress.setValue(done)

        worker.progress.connect(advance)
        progress.canceled.connect(worker.cancel.set)
        worker.finished.connect(lambda: self._backup_done(progress, path, worker))
        self.backup_worker = worker
        progress.show()
        worker.start()

    def _backup_done(self, progress: QProgressDialog, path: Path, worker: backup.BackupWorker):
        progress.close()
        if self._closing:
            return
        if worker.cancelled:
            self.statusBar().showMessage("Резервное копирование отменено.")
        elif worker.error:
            QMessageBox.warning(self, "Резервная копия", worker.error)
        else:
            QMessageBox.information(self, "Резервная копия", f"Резервная копия сохранена:\n{path}\n\n"
                                    "Архив содержит ключи из настроек — храните его в надёжном месте.\n"
                                    "Для восстановления закройте программу, переименуйте прежнюю папку «Данные» "
                                    "и распакуйте архив рядом с программой.")

    def show_history(self):
        dialog = HistoryDialog(self.catalog.search_history(), self)
        dialog.repeat.connect(self._repeat_search)
        dialog.exec()

    def _repeat_search(self, text: str):
        self.tabs.setCurrentIndex(0)
        self.query.setText(text)
        self.run_search()

    def edit_services(self):
        counts: dict[str, int] = {}
        for e in self.catalog.entries():
            for pid in e.links:
                counts[pid] = counts.get(pid, 0) + 1
        if ServicesDialog(self.categories, counts, self).exec():
            categories = processes.load_catalog()
            removed = {p.id for p in self.processes} - {p.id for p in processes.all_processes(categories)}
            self.catalog.drop_links(removed)  # ролики удалённых услуг — в «Без услуги»
            self.set_categories(categories)
            self.statusBar().showMessage("Справочник услуг обновлён.")

    def set_categories(self, categories: list[processes.Category]):
        """Новый справочник услуг: подсказки, фразы поиска, каталог и покрытие."""
        self.categories = categories
        self.processes = processes.all_processes(categories)
        self.printing = print_stems(self.processes)
        self.catalog_view.set_categories(categories)
        self.batch_view.set_categories(categories)

    def edit_dictionary(self):
        if DictionaryDialog(self).exec():
            self.dictionary = query.Dictionary.load()
            self.statusBar().showMessage("Словарь перевода обновлён.")

    def check_links(self, manual: bool):
        """Проверить, не удалены ли ролики каталога с площадок (раз в N дней при запуске или по команде)."""
        if self.link_check and self.link_check.isRunning():
            return
        sources, _ = make_sources(self.settings, self.cache)
        self.link_check = maintenance.LinkCheckWorker(self.catalog, sources, self)
        self.link_check.progress.connect(
            lambda i, n: self.statusBar().showMessage(f"Проверка ссылок каталога: {i} из {n}"))
        self.link_check.done.connect(lambda counts: self._links_checked(counts, manual))
        self.link_check.start()

    def _links_checked(self, counts: dict, manual: bool):
        text = f"Проверка ссылок: проверено {counts['checked']}"
        if counts["removed"]:
            text += f", удалены с площадки — {counts['removed']}"
        if counts["restored"]:
            text += f", снова доступны — {counts['restored']}"
        if counts["skipped"]:
            text += f", не удалось проверить — {counts['skipped']}"
        self.statusBar().showMessage(text)
        self.catalog_view.refresh()
        self.batch_view.refresh()
        if manual or counts["removed"]:
            note = ("\n\nУдалённые ролики помечены в каталоге; нескачанные не засчитываются в покрытие."
                    if counts["removed"] else "")
            QMessageBox.information(self, "Проверка ссылок", text + "." + note)

    def find_duplicates(self):
        if self.duplicates and self.duplicates.isRunning():
            return
        self.statusBar().showMessage("Поиск похожих роликов: загружаются превью…")
        self.duplicates = maintenance.DuplicateWorker(self.catalog, self)
        self.duplicates.done.connect(self._show_duplicates)
        self.duplicates.start()

    def _show_duplicates(self, pairs: list):
        self.statusBar().showMessage(f"Похожих пар: {len(pairs)}")
        dialog = DuplicatesDialog(pairs, self)
        dialog.open_entry.connect(self._open_entry)
        dialog.exec()

    def _open_entry(self, video_id: int):
        self.tabs.setCurrentWidget(self.catalog_view)
        self.catalog_view.select_entry(video_id)

    def export_playlist(self):
        folder = self.download_dir()
        folder.mkdir(parents=True, exist_ok=True)
        chosen, _ = QFileDialog.getSaveFileName(self, "Плейлист M3U", str(folder / "Плейлист.m3u8"),
                                                "Плейлист (*.m3u8 *.m3u)")
        if not chosen:
            return
        path = Path(chosen)
        if path.suffix.lower() not in (".m3u", ".m3u8"):
            path = path.with_suffix(".m3u8")
        entries = sorted((e for e in self.catalog.entries() if e.status != cat.REJECTED), key=lambda e: e.id)
        try:
            count = playlist.write_m3u(path, entries)
        except OSError as exc:
            return self._export_failed(exc)
        if not count:
            path.unlink(missing_ok=True)
            QMessageBox.information(self, "Плейлист", "В каталоге нет скачанных роликов, файлы которых есть на диске.")
            return
        self._exported(path, f"Плейлист из {count} роликов сохранён")

    def export_showreel(self):
        """Склеить скачанные ролики услуги или категории в один MP4 для экрана (в фоне, с отменой)."""
        if self.showreel_worker and self.showreel_worker.isRunning():
            return
        ffmpeg = paths.ffmpeg_tool("ffmpeg")
        if not ffmpeg:
            QMessageBox.warning(self, "Шоурил", "Для шоурила нужен FFmpeg в папке программы.")
            return
        dialog = ShowreelDialog(self.catalog, self.categories, self)
        if not dialog.exec():
            return
        title, entries = dialog.title(), dialog.entries()
        folder = self.download_dir() / "Шоурил"
        folder.mkdir(parents=True, exist_ok=True)
        chosen, _ = QFileDialog.getSaveFileName(self, "Шоурил для экрана", str(folder / f"{safe_name(title)}.mp4"),
                                                "Видео MP4 (*.mp4)")
        if not chosen:
            return
        path = Path(chosen).with_suffix(".mp4")
        progress = QProgressDialog(f"Собирается шоурил «{title}» из {len(entries)} роликов…", "Отмена", 0, 100, self)
        progress.setWindowTitle("Шоурил")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        worker = showreel.ShowreelWorker(title, entries, path, ffmpeg, paths.ffmpeg_tool("ffprobe"),
                                         dialog.clip_seconds(), dialog.fade_seconds(), self)
        worker.progress.connect(progress.setValue)
        progress.canceled.connect(worker.cancel.set)
        worker.done.connect(lambda error, used: self._showreel_done(progress, path, error, used))
        self.showreel_worker = worker
        worker.start()

    def _showreel_done(self, progress: QProgressDialog, path: Path, error: str, used: list):
        progress.close()
        if error:
            QMessageBox.warning(self, "Шоурил", error)
            return
        self._exported(path, f"Шоурил из {len(used)} роликов сохранён, список авторов — рядом в .txt")

    def _about(self):
        box = QMessageBox(self)
        box.setWindowTitle("О программе")
        box.setIconPixmap(QPixmap(str(paths.resources_dir() / "logo.png")).scaled(
            64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        box.setText(f"<b>Поиск видео для копицентров Copy.ru</b><br>Версия {__version__}")
        box.setInformativeText(f'Новые версии: <a href="{updater.RELEASES_PAGE}">{updater.RELEASES_PAGE}</a>')
        box.exec()

    def downloads_busy(self) -> bool:
        return any(busy for _, _, busy in self.download_state.values())

    def _show_hint(self):
        _, skipped = make_sources(self.settings, self.cache)
        if skipped:
            self.query_info.setText(
                f"Без ключа API пропускаются: {', '.join(skipped)}. Ключи вводятся в меню «Файл → Настройки». "
                "Internet Archive, Videezy, Яндекс Видео и поиск по интернету работают без ключа."
            )
        else:
            self.query_info.setText("")

    def _update_suggestions(self, text: str):
        found = processes.suggestion_texts(self.processes, text)
        self._suggestions.setStringList(found)
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
        # На экране — одна строка, подробности (фразы, уровни, площадки) — во всплывающей подсказке.
        self.query_info.setText(info.splitlines()[0])
        self.query_info.setToolTip(info)
        self.show_rejected.hide()
        self.show_unlicensed.hide()
        self.plan = plan
        self.plan_text = self.query.text().strip()
        self.results.clear()
        self.player.show_result(None)
        self.problems = []
        self.sources_seen = {}
        self.source_links.setText("")

        self._thumb_items = {}
        hidden = {key for key, status in self.catalog.statuses().items() if status == cat.REJECTED}
        self.worker = SearchWorker(plan, self.sources, self.filters(),
                                   self.settings.only_without_attribution, self, printing=self.printing,
                                   hidden=hidden)
        self.worker.found.connect(self._on_found)
        self.worker.problem.connect(self._on_problem)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.search_button.setText("Остановить")
        self.worker.start()

    def stop_search(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait()  # отмена прерывает ожидание лимитов; запросы — с тайм-аутом
            self.statusBar().showMessage("Поиск остановлен.")

    @staticmethod
    def _describe_plan(plan: query.SearchPlan) -> str:
        lines = []
        if plan.process:
            lines.append(f"Услуга: {plan.process.name} ({plan.process.category})")
        else:
            lines.append("Произвольный запрос")
        for lang, title in ((query.RU, "по-русски"), (query.EN, "по-английски")):
            texts = [q.text for q in plan.queries if q.lang == lang and q.level == 0]
            if texts:
                lines.append(f"Ищем {title}: {', '.join(texts)}")
        if len(plan.levels) > 1:
            lines.append("Если роликов мало, поиск расширится: " + " → ".join(plan.levels[1:]) +
                         " (в том числе по-немецки и по-французски)")
        if plan.note:
            lines.append(plan.note)
        return "\n".join(lines)

    def _from_old_search(self) -> bool:
        """Сигнал остановленного поиска, пришедший уже после запуска нового, — не наш."""
        return self.sender() is not self.worker

    def _on_found(self, results: list[VideoResult]):
        if not self._from_old_search():
            self._add_results(results)

    def _add_results(self, results: list[VideoResult], off_topic: bool = False, view_only: bool = False):
        self._remove_placeholder()
        statuses = self.catalog.statuses()
        for r in results:
            info = [format_duration(r.duration) if r.duration else "",
                    f"{r.width}×{r.height}" if r.width and r.height else ""]
            free = r.license and r.license.status in licenses.SUITABLE
            text = " · ".join(i for i in info if i) + \
                f"\n{r.source} · {r.license.status if free else 'без свободной лицензии'}"
            if not r.files:
                text += "\nскачать на сайте"
            if r.match_hint and r.match_hint != cat.EXACT:
                text += f"\n{r.match_hint}"
            if statuses.get(r.key) == cat.SELECTED:
                text += "\nв каталоге"
            if off_topic:
                r.relevance = -1  # в конец выдачи
                text = "Не по теме · " + text
            if view_only:
                r.relevance = -2  # после всех свободных
                text = f"Только посмотреть · {r.source}\n{r.license.name}"
            item = QListWidgetItem(text)
            if off_topic or view_only:
                item.setForeground(self.palette().placeholderText())
            item.setData(Qt.ItemDataRole.UserRole, r)
            item.setToolTip(r.title or r.page_url)
            item.setSizeHint(QSize(THUMB.width() + 16, THUMB.height() + 76))
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
            image = load_image(bytes(reply.readAll()))
            if not image.isNull():
                pixmap = QPixmap.fromImage(image)
                item = self._thumb_items.get(str(token))
                if item and self.results.row(item) >= 0:
                    thumb = pixmap.scaled(THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation)
                    icon = QIcon(thumb)
                    icon.addPixmap(thumb, QIcon.Mode.Selected)  # без синей подкраски у выбранного ролика
                    item.setIcon(icon)
        reply.deleteLater()

    def _on_current(self, item: QListWidgetItem | None, _previous):
        result = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.player.show_result(result)
        if result:
            entry = self.catalog.find(result.key)
            if entry:
                self.player.set_catalog_state(entry.status, entry.id)
        if result and result.key in self.download_state:
            self.player.set_download_state(*self.download_state[result.key])

    # --- экспорт и импорт каталога ---

    def _save_path(self, title: str, name: str, file_filter: str) -> Path | None:
        folder = paths.data_dir() / "Экспорт"
        folder.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d")
        chosen, _ = QFileDialog.getSaveFileName(self, title, str(folder / f"{name} {stamp}"), file_filter)
        return Path(chosen) if chosen else None

    def _exported(self, path: Path, what: str):
        answer = QMessageBox.question(self, "Экспорт", f"{what}:\n{path}\n\nОткрыть?")
        if answer == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _export_failed(self, exc: Exception):
        QMessageBox.warning(self, "Экспорт", f"Не удалось сохранить файл: {exc}")

    def export_xlsx(self):
        path = self._save_path("Реестр в Excel", "Реестр роликов", "Excel (*.xlsx)")
        if not path:
            return
        path = path.with_suffix(".xlsx")
        try:
            s = summary.build(self.catalog, self.categories)
            registry.write_xlsx(path, registry.rows(self.catalog, self.processes), summary.lines(s))
        except OSError as exc:
            return self._export_failed(exc)
        self._exported(path, "Реестр сохранён")

    def export_csv(self):
        path = self._save_path("Реестр в CSV", "Реестр роликов", "CSV (*.csv)")
        if not path:
            return
        path = path.with_suffix(".csv")
        try:
            registry.write_csv(path, registry.rows(self.catalog, self.processes))
        except OSError as exc:
            return self._export_failed(exc)
        self._exported(path, "Реестр сохранён")

    def export_html(self):
        path = self._save_path("HTML-каталог", "Каталог роликов", "HTML (*.html)")
        if not path:
            return
        path = path.with_suffix(".html")
        try:
            html_catalog.write(path, self.catalog, self.categories)
        except OSError as exc:
            return self._export_failed(exc)
        self._exported(path, "HTML-каталог сохранён")

    def export_report(self):
        """PDF для руководства: покрытие услуг и превью роликов (превью скачиваются в фоне)."""
        if self.report_worker and self.report_worker.isRunning():
            return
        path = self._save_path("Отчёт для руководства", "Отчёт о роликах", "PDF (*.pdf)")
        if not path:
            return
        path = path.with_suffix(".pdf")
        videos = report.service_videos(self.catalog, self.categories)
        entries = list({e.id: e for items in videos.values() for e in items}.values())
        self.statusBar().showMessage(f"Отчёт: загружаются превью роликов ({len(entries)})…")
        self.report_worker = report.ReportWorker(entries, self)
        self.report_worker.done.connect(lambda thumbs: self._write_report(path, thumbs))
        self.report_worker.start()

    def _write_report(self, path: Path, thumbs: dict):
        try:
            report.write_pdf(path, report.report_html(self.catalog, self.categories, thumbs), thumbs)
        except OSError as exc:
            return self._export_failed(exc)
        self.statusBar().showMessage("Отчёт сохранён.")
        self._exported(path, "Отчёт сохранён")

    def export_summary(self):
        path = self._save_path("Сводка покрытия", "Сводка покрытия", "HTML (*.html)")
        if not path:
            return
        path = path.with_suffix(".html")
        try:
            summary.write_html(path, summary.build(self.catalog, self.categories))
        except OSError as exc:
            return self._export_failed(exc)
        summary.mark_done(self.catalog)
        self._exported(path, "Сводка сохранена")

    def import_csv(self):
        chosen, _ = QFileDialog.getOpenFileName(self, "Импорт реестра CSV", str(paths.data_dir()), "CSV (*.csv)")
        if not chosen:
            return
        try:
            videos, links = registry.import_csv(Path(chosen), self.catalog, self.processes)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            QMessageBox.warning(self, "Импорт", f"Не удалось прочитать файл: {exc}")
            return
        self.catalog_view.refresh()
        self.batch_view.refresh()
        QMessageBox.information(self, "Импорт", f"Загружено роликов: {videos}, привязок к услугам: {links}.\n"
                                "Дубли не создаются: ролики, которые уже были в каталоге, обновлены.")

    def _on_tab(self, index: int):
        page = self.tabs.widget(index)
        if page is self.catalog_view:
            self.catalog_view.refresh()
        elif page is self.batch_view:
            self.batch_view.refresh()

    def open_service(self, process_id: str):
        """Открыть ролики услуги на вкладке «Поиск» (после пакетного поиска — из кэша)."""
        proc = next((p for p in self.processes if p.id == process_id), None)
        if proc:
            self.tabs.setCurrentIndex(0)
            self.query.setText(proc.display)
            self.run_search()

    def start_batch(self):
        if self.batch and self.batch.isRunning():
            return
        coverage = self.catalog.coverage()
        short = cat.batch_order(self.processes, coverage)  # сначала услуги, где роликов меньше всего
        order = short + [p for p in self.processes if p not in short]
        sources, _ = make_sources(self.settings, self.cache)
        answer = QMessageBox.question(
            self, "Пакетный поиск",
            f"Искать ролики по всем услугам ({len(order)})? Это займёт несколько минут.\n\n"
            "Ролики только находятся, ничего не скачивается — открыть услугу и сохранить ролики в каталог "
            "можно двойным щелчком. Лимиты площадок программа соблюдает сама.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.batch_view.candidates = {}
        self.batch = BatchWorker(self.processes, order, sources, self.filters(), self.dictionary, self.catalog,
                                 self.printing, self.settings.only_without_attribution, self)
        names = {p.id: p.name for p in self.processes}
        self.batch.started_service.connect(lambda pid, i, n: self.batch_view.progress.setText(
            f"Ищем: {names[pid]} ({i} из {n})…"))
        self.batch.done_service.connect(self.batch_view.service_done)
        self.batch.finished.connect(self._batch_finished)
        self.batch_view.set_running(True)
        self.batch.start()

    def stop_batch(self):
        if self.batch and self.batch.isRunning():
            self.batch.cancel()
            self.batch.wait()

    def _batch_finished(self):
        self.batch_view.set_running(False)
        done = len(self.batch_view.candidates)
        text = f"Пакетный поиск завершён: просмотрено услуг — {done}."
        if self.batch and self.batch.problems:
            text += " " + " ".join(self.batch.problems)
        self.batch_view.progress.setText(text)
        self.batch_view.refresh()

    def add_by_link(self):
        sources = all_sources(self.settings, self.cache)
        web = next(source for source in sources if isinstance(source, WebSource))
        dialog = AddLinkDialog(self.catalog, self.categories, web, self.catalog_view.selected_process(), self)
        self.link_dialog = dialog
        if dialog.exec() and dialog.video_id is not None:
            self.catalog_view.refresh()
            self.catalog_view.select_entry(dialog.video_id)
            self.batch_view.refresh()
            self.statusBar().showMessage(f"Открыт ролик № {dialog.video_id} в каталоге.")
        self.link_dialog = None
        dialog.deleteLater()

    def add_to_catalog(self, result: VideoResult):
        """«В каталог — лицензию проверил»: ролик привязывается к услуге поиска."""
        process = self.plan.process if self.plan else None
        match = result.match_hint or cat.EXACT  # найден по похожим операциям — «смежный процесс»
        video_id = self.catalog.select(result, process.id if process else None, match)
        self.player.set_catalog_state(cat.SELECTED, video_id)
        if process:
            self.statusBar().showMessage(f"Ролик № {video_id} добавлен в каталог: {process.name} ({match}). "
                                         "Степень соответствия можно поменять во вкладке «Каталог».")
        else:
            self.statusBar().showMessage(f"Ролик № {video_id} добавлен в каталог. Привяжите его к услуге "
                                         "во вкладке «Каталог».")
        self._mark_item(result, "в каталоге")

    def reject_result(self, result: VideoResult):
        """«Не подходит»: ролик убирается из выдачи и больше в поиске не появится."""
        self.catalog.reject(result)
        row = next((i for i in range(self.results.count())
                    if (self.results.item(i).data(Qt.ItemDataRole.UserRole) or None) is result), -1)
        if row >= 0:
            self.results.takeItem(row)
        self.statusBar().showMessage("Ролик отклонён и больше не появится в поиске.")

    def _mark_item(self, result: VideoResult, mark: str):
        for i in range(self.results.count()):
            item = self.results.item(i)
            if item.data(Qt.ItemDataRole.UserRole) is result and mark not in item.text():
                item.setText(f"{item.text()}\n{mark}")

    def download_dir(self) -> Path:
        return Path(self.settings.download_dir) if self.settings.download_dir else paths.default_download_dir()

    def download(self, result: VideoResult, file):
        plan = self.plan
        process = plan.process if plan else None
        # Скачанный ролик попадает в каталог; номер в каталоге — в имени файла.
        # Лицензию это не подтверждает: для этого кнопка «В каталог — лицензию проверил».
        entry = self.catalog.find(result.key)
        # Степень соответствия: уже выбранную в каталоге не перезаписываем, иначе — подсказка уровня поиска.
        match = (entry.links.get(process.id) if entry and process else None) or result.match_hint or cat.EXACT
        video_id = self.catalog.select(result, process.id if process else None, match,
                                       license_confirmed=bool(entry and entry.license_confirmed))
        self.player.set_catalog_state(cat.SELECTED, video_id)
        source = next((s for s in all_sources(self.settings, self.cache) if s.name == result.source), None)
        job = DownloadJob(result=result, file=file, process=process,
                          query_text=self.plan_text, base_dir=self.download_dir(),
                          convert=self.settings.convert_to_mp4, catalog_id=video_id,
                          trim=self.settings.trim_seconds, mute=self.settings.mute_audio,
                          resolver=getattr(source, "resolve_download", None))
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
        job = self.downloads.jobs[job_id]
        if job.catalog_id:
            self.catalog.set_downloaded(job.catalog_id, path)
            if job.loop_hint is not None:
                self.catalog.set_loop_hint(job.catalog_id, job.loop_hint)
        self._set_download_state(self._job_key(job_id), text, path, False)
        self.statusBar().showMessage(f"Скачано: {Path(path).name}")

    def _on_download_failed(self, job_id: int, message: str):
        self._set_download_state(self._job_key(job_id), f"Не скачано: {message}", "", False)

    def _open_folder(self, path: str):
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    def _on_problem(self, message: str):
        if self._from_old_search():
            return
        if message not in self.problems:
            self.problems.append(message)

    def _on_progress(self, done: int, total: int):
        if self._from_old_search():
            return
        self.statusBar().showMessage(f"Поиск: {done} из {total} запросов, найдено {self.results.count()}")

    def _on_finished(self):
        if not self.worker or self._from_old_search():
            return
        self.search_button.setText("Найти")
        count = self.results.count()
        shown = [self.results.item(i).data(Qt.ItemDataRole.UserRole) for i in range(count)]
        shown = [r for r in shown if r is not None]
        had_history = self.catalog.has_history()
        new = self.catalog.record_search(self.plan_text, [r.key for r in shown])
        if had_history:  # на самом первом поиске новыми были бы все — не помечаем
            for r in shown:
                if r.key in new:
                    self._mark_item(r, "новый")
        message = f"Найдено подходящих роликов: {count}"
        if self.worker.levels_used and self.plan and len(self.plan.levels) > self.worker.levels_used:
            message += f" (поиск расширен до уровня «{self.plan.levels[self.worker.levels_used]}»)"
        if had_history and new:
            message += f" (новых с прошлого поиска: {len(new)})"
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
        unlicensed = len(self.worker.unlicensed_results())
        if unlicensed:
            self.show_unlicensed.setText(f"Без свободной лицензии ({unlicensed})")
            self.show_unlicensed.show()
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

    def _show_unlicensed(self):
        if self.worker:
            self._add_results(self.worker.unlicensed_results(), view_only=True)
        self.show_unlicensed.hide()

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
            self.apply_theme()
            self._show_hint()

    def closeEvent(self, event):
        # Все фоновые потоки отменяются и дожидаются полностью: поток, живой после удаления окна-родителя,
        # роняет программу («QThread: Destroyed while thread is still running»). Окно прячем сразу.
        self.hide()
        self._closing = True
        if self.link_dialog:
            self.link_dialog.stop()
        self.stop_search()
        self.stop_batch()
        workers = [w for w in (self.link_check, self.duplicates, self.report_worker, self.showreel_worker,
                              self.backup_worker)
                   if w and w.isRunning()]
        for worker in workers:
            worker.cancel.set()
        for worker in workers:
            worker.wait()
        self.updates.stop()
        self.downloads.stop()
        self.player.player.stop()
        self.player.frames.stop()
        self.catalog_view.card.frames.stop()
        super().closeEvent(event)
