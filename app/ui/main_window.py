"""Главное окно: три вкладки («Поиск», «Пакетный поиск», «Каталог»), меню и связи между ними.

Работа вкладок и меню — в своих модулях: `tabs/` (вкладки), `actions/` (экспорт, обслуживание,
пакетный поиск), `downloads.py` (скачивание). Окно только собирает их и закрывает фоновые потоки.
"""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QPixmap
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox, QTabWidget

from app import __version__, paths
from app.core import design, processes, query
from app.services import updater
from app.sources.cache import Cache
from app.sources.web import WebSource
from app.storage import catalog as cat
from app.storage.settings import Settings
from app.ui import theme
from app.ui.actions.batch import BatchController
from app.ui.actions.export import ExportActions
from app.ui.actions.maintenance import MaintenanceActions
from app.ui.context import AppContext
from app.ui.dialogs.add_link_dialog import AddLinkDialog
from app.ui.dialogs.guide import GuideDialog
from app.ui.dialogs.services_dialog import ServicesDialog
from app.ui.dialogs.settings_dialog import SettingsDialog
from app.ui.dialogs.tools import DictionaryDialog, HistoryDialog
from app.ui.downloads import DownloadController
from app.ui.tabs.batch_view import BatchView
from app.ui.tabs.catalog_view import CatalogView
from app.ui.tabs.search_tab import SearchTab
from app.ui.tabs.stock_link_tab import StockLinkTab
from app.ui.update import UpdateController


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, categories: list[processes.Category], cache: Cache | None = None,
                 store: cat.Catalog | None = None):
        super().__init__()
        self.ctx = AppContext(settings, categories, cache, store)
        self.link_dialog: AddLinkDialog | None = None
        self.setWindowTitle("Поиск видео для копицентров Copy.ru")
        screen = self.screen()
        if screen:
            available = screen.availableGeometry()
            self.resize(min(design.WINDOW_WIDTH, available.width() - design.WINDOW_SCREEN_MARGIN),
                        min(design.WINDOW_HEIGHT, available.height() - design.WINDOW_SCREEN_MARGIN))
        else:
            self.resize(design.WINDOW_WIDTH, design.WINDOW_HEIGHT)

        self.downloads = DownloadController(self.ctx, self)
        self.search = SearchTab(self.ctx, self.downloads)
        self.stock_link = StockLinkTab(self.ctx)
        # Три раздела: поиск, пакетный поиск по всем услугам, каталог сохранённых роликов.
        self.batch_view = BatchView(self.ctx.catalog, categories)
        self.batch_view.open_service.connect(self.open_service)
        self.batch = BatchController(self.ctx, self.batch_view, self)
        self.catalog_view = CatalogView(self.ctx.catalog, categories)
        self.exports = ExportActions(self.ctx, self)
        self.maintenance = MaintenanceActions(
            self.ctx, self, busy=lambda: self.downloads.busy() or self.stock_link.busy() or self.exports.busy())
        self.catalog_view.add_link_requested.connect(self.add_by_link)
        self.catalog_view.download_requested.connect(
            lambda entries: self.downloads.download_entries(entries, self.catalog_view.selected_process()))
        self.catalog_view.check_links_requested.connect(self.maintenance.check_selected_links)
        for part in (self.search, self.downloads, self.exports, self.maintenance):
            part.message.connect(self.statusBar().showMessage)
        for part in (self.search, self.downloads, self.exports, self.maintenance):
            part.catalog_changed.connect(self.refresh_catalog)
        self.maintenance.open_entry.connect(self._open_entry)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.search, "Поиск")
        self.tabs.addTab(self.batch_view, "Пакетный поиск")
        self.tabs.addTab(self.catalog_view, "Каталог")
        self.tabs.addTab(self.stock_link, "Загрузка по ссылке")
        self.stock_link.message.connect(self.statusBar().showMessage)
        self.tabs.currentChanged.connect(self._on_tab)
        self.setCentralWidget(self.tabs)
        for button in self.search.status_buttons():
            self.statusBar().addPermanentWidget(button)
        self.statusBar().showMessage(f"Папка данных: {paths.data_dir()}")

        self.updates = UpdateController(self, settings)
        self._build_menu()
        # «Как в системе»: тема переключается вслед за Windows.
        QApplication.styleHints().colorSchemeChanged.connect(
            lambda _s: self.settings.theme == theme.SYSTEM and self.apply_theme())
        # Через несколько секунд после запуска: убрать остатки прошлого обновления и проверить новое.
        QTimer.singleShot(4000, self._startup_update_check)

    # Короткие ссылки на общее состояние и на вкладку «Поиск».
    settings = property(lambda self: self.ctx.settings)
    catalog = property(lambda self: self.ctx.catalog)
    cache = property(lambda self: self.ctx.cache)
    player = property(lambda self: self.search.player)

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
        for title, handler in self.exports.menu():
            add(export, title, handler)
        add(menu, "Импорт реестра CSV…", self.exports.import_csv)
        tools = menu.addMenu("Сервис")
        for title, handler in (("История поиска…", self.show_history), ("Словарь перевода…", self.edit_dictionary),
                               ("Проверить ссылки каталога", lambda: self.maintenance.check_links(manual=True)),
                               ("Найти похожие дубли…", self.maintenance.find_duplicates),
                               ("Очистить кэш", self.maintenance.clear_cache),
                               ("Резервная копия данных…", self.maintenance.backup_data)):
            add(tools, title, handler)
        menu.addSeparator()
        add(menu, "Выход", self.close)

        help_menu = self.menuBar().addMenu("Справка")
        add(help_menu, "Руководство пользователя…", lambda: GuideDialog(self).exec())
        add(help_menu, "Проверить обновления…", lambda: self.updates.check(manual=True))
        add(help_menu, "О программе", self._about)

    def apply_theme(self):
        theme.apply(QApplication.instance(), self.settings.theme)
        self.refresh_catalog()

    def refresh_catalog(self):
        """Каталог изменился (скачивание, импорт, проверка ссылок) — обновить вкладки с покрытием."""
        self.catalog_view.refresh()
        self.batch_view.refresh()

    def _startup_update_check(self):
        updater.cleanup()
        if self.settings.check_updates:
            self.updates.check(manual=False)
        if self.maintenance.link_check_due():
            self.maintenance.check_links(manual=False)

    def show_history(self):
        dialog = HistoryDialog(self.catalog.search_history(), self)
        dialog.repeat.connect(self._search_text)
        dialog.exec()

    def _search_text(self, text: str):
        self.tabs.setCurrentWidget(self.search)
        self.search.search_text(text)

    def open_service(self, process_id: str):
        """Открыть ролики услуги на вкладке «Поиск» (после пакетного поиска — из кэша)."""
        proc = self.ctx.process(process_id)
        if proc:
            self._search_text(proc.display)

    def _open_entry(self, video_id: int):
        self.tabs.setCurrentWidget(self.catalog_view)
        self.catalog_view.select_entry(video_id)

    def edit_services(self):
        counts: dict[str, int] = {}
        for e in self.catalog.entries():
            for pid in e.links:
                counts[pid] = counts.get(pid, 0) + 1
        if ServicesDialog(self.ctx.categories, counts, self).exec():
            categories = processes.load_catalog()
            removed = {p.id for p in self.ctx.processes} - {p.id for p in processes.all_processes(categories)}
            self.catalog.drop_links(removed)  # ролики удалённых услуг — в «Без услуги»
            self.set_categories(categories)
            self.statusBar().showMessage("Справочник услуг обновлён.")

    def set_categories(self, categories: list[processes.Category]):
        """Новый справочник услуг: подсказки, фразы поиска, каталог и покрытие."""
        self.ctx.set_categories(categories)
        self.catalog_view.set_categories(categories)
        self.batch_view.set_categories(categories)

    def edit_dictionary(self):
        if DictionaryDialog(self).exec():
            self.ctx.dictionary = query.Dictionary.load()
            self.statusBar().showMessage("Словарь перевода обновлён.")

    def add_by_link(self):
        web = next(source for source in self.ctx.all_sources() if isinstance(source, WebSource))
        dialog = AddLinkDialog(self.catalog, self.ctx.categories, web, self.catalog_view.selected_process(), self)
        self.link_dialog = dialog
        if dialog.exec() and dialog.video_id is not None:
            self.catalog_view.refresh()
            self.catalog_view.select_entry(dialog.video_id)
            self.batch_view.refresh()
            self.statusBar().showMessage(f"Открыт ролик № {dialog.video_id} в каталоге.")
        self.link_dialog = None
        dialog.deleteLater()

    def _about(self):
        box = QMessageBox(self)
        box.setWindowTitle("О программе")
        box.setIconPixmap(QPixmap(str(paths.resources_dir() / "logo.svg")).scaled(
            64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        box.setText(f"<b>Поиск видео для копицентров Copy.ru</b><br>Версия {__version__}")
        box.setInformativeText(f'Новые версии: <a href="{updater.RELEASES_PAGE}">{updater.RELEASES_PAGE}</a>')
        box.exec()

    def downloads_busy(self) -> bool:
        return self.downloads.busy()

    def _on_tab(self, index: int):
        page = self.tabs.widget(index)
        if page is self.catalog_view:
            self.catalog_view.refresh()
        elif page is self.batch_view:
            self.batch_view.refresh()
        else:
            self.search.filter_results()

    def open_settings(self):
        if SettingsDialog(self.settings, self).exec():
            self.search.apply_settings()
            self.apply_theme()

    def closeEvent(self, event):
        # Все фоновые потоки отменяются и дожидаются полностью: поток, живой после удаления окна-родителя,
        # роняет программу («QThread: Destroyed while thread is still running»). Окно прячем сразу.
        self.hide()
        self.maintenance.closing = True
        if self.link_dialog:
            self.link_dialog.stop()
        self.search.stop_search()
        self.batch.stop()
        workers = [w for w in self.maintenance.workers() + self.exports.workers() if w and w.isRunning()]
        for worker in workers:
            worker.cancel.set()
        for worker in workers:
            worker.wait()
        self.updates.stop()
        self.stock_link.shutdown()
        self.downloads.stop()
        self.search.stop()
        self.catalog_view.card.frames.stop()
        super().closeEvent(event)
