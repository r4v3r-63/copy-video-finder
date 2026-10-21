"""Выдача по ссылке, локальные фильтры и индивидуальная/разрешённая пакетная загрузка."""

from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.core import design
from app.core.stock_link import ListingFilters, choose_quality, parse_link
from app.services.downloader import DownloadJob, download_option
from app.sources.base import SourceError
from app.sources.listings import make_listing
from app.ui import theme
from app.ui.widgets.stock_results import StockResults
from app.workers.stock_link import ListingWorker, StockDownloadWorker


class StockLinkTab(QWidget):
    message = pyqtSignal(str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.link = None
        self.items = []
        self.listing_worker = None
        self.download_worker = None
        self.jobs = []
        self.completed = set()
        self.errors = set()
        self.total = self.accessible = 0
        self.closed = False
        self.model = StockResults(self)
        self.model.selection_changed.connect(self.update_stats)
        self.network = QNetworkAccessManager(self)
        self.replies = set()
        self.pending_images = []
        self.model.thumbnail_needed.connect(self._request_image)
        self.network.finished.connect(self._image_done)
        self.url = QLineEdit()
        self.url.setPlaceholderText("Ссылка на выдачу Pixabay или Internet Archive")
        self.find = QPushButton("Найти видео")
        theme.set_role(self.find, theme.PRIMARY)
        self.find.clicked.connect(self.search)
        self.url.returnPressed.connect(self.search)
        top = QHBoxLayout()
        top.addWidget(self.url, 1)
        top.addWidget(self.find)
        self.notice = QLabel("Выдача загружается через API. Скачивание не подтверждает право использования.")
        self.notice.setWordWrap(True)
        self.stats = QLabel()
        self.minimum, self.maximum = QLineEdit(), QLineEdit()
        self.minimum.setPlaceholderText("От, сек. — пусто: без границы")
        self.maximum.setPlaceholderText("До, сек. — пусто: без границы")
        self.orientation = theme.compact_combo(QComboBox())
        for title, data in (("Любая ориентация", "all"), ("Горизонтальная", "landscape"),
                            ("Вертикальная", "portrait")):
            self.orientation.addItem(title, data)
        self.quality = theme.compact_combo(QComboBox())
        for title, data in (("Максимальное качество", 0), ("1080p", 1080), ("720p", 720)):
            self.quality.addItem(title, data)
        reset = QPushButton("Сбросить фильтры")
        reset.clicked.connect(self.reset_filters)
        filters = QHBoxLayout()
        for widget in (QLabel("От, сек.:"), self.minimum, QLabel("До, сек.:"), self.maximum,
                       self.orientation, self.quality, reset):
            filters.addWidget(widget)
        self.filter_error = QLabel()
        self.filter_error.setWordWrap(True)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setWordWrap(False)
        self.table.setIconSize(QSize(design.STOCK_THUMB_WIDTH, design.STOCK_THUMB_HEIGHT))
        self.table.verticalHeader().setDefaultSectionSize(design.STOCK_ROW_HEIGHT)
        for col, width in enumerate(design.STOCK_COLUMNS):
            self.table.setColumnWidth(col, width)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.all_select = QPushButton("Выбрать все подходящие")
        self.all_select.clicked.connect(self.model.select_all)
        self.open_original = QPushButton("Открыть оригинал")
        self.open_original.clicked.connect(self._open_original)
        self.folder = QLineEdit(str(ctx.download_dir()))
        browse = QPushButton("Папка…")
        browse.clicked.connect(self._choose_folder)
        destination = QHBoxLayout()
        destination.addWidget(self.folder, 1)
        destination.addWidget(browse)
        self.download = QPushButton("Скачать выбранные")
        theme.set_role(self.download, theme.PRIMARY)
        self.download.clicked.connect(lambda: self.start_download(False))
        self.download_all = QPushButton("Скачать все подходящие")
        self.download_all.clicked.connect(lambda: self.start_download(True))
        self.pause, self.resume, self.cancel = QPushButton("Пауза"), QPushButton("Продолжить"), QPushButton("Отменить")
        self.pause.clicked.connect(self._pause)
        self.resume.clicked.connect(self._resume)
        self.cancel.clicked.connect(self.stop)
        self.pause.setToolTip("Прямой файл приостанавливается между блоками. Для страницы — после текущего ролика.")
        actions = QHBoxLayout()
        for widget in (self.all_select, self.open_original, self.download, self.download_all,
                       self.pause, self.resume, self.cancel):
            actions.addWidget(widget)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(design.SPACE_L, design.SPACE_L, design.SPACE_L, design.SPACE_L)
        for row in (top, filters):
            layout.addLayout(row)
        for widget in (self.notice, self.filter_error, self.stats, self.table):
            layout.addWidget(widget, 1 if widget is self.table else 0)
        layout.addLayout(destination)
        layout.addLayout(actions)
        layout.addWidget(self.progress)
        for edit in (self.minimum, self.maximum):
            edit.textChanged.connect(self.apply_filters)
        for combo in (self.orientation, self.quality):
            combo.currentIndexChanged.connect(self.apply_filters)
        self._policy()
        self.update_stats()

    def filters(self):
        def number(edit):
            text = edit.text().strip().replace(",", ".")
            try:
                return float(text) if text else None
            except ValueError as exc:
                raise ValueError("Продолжительность: введите число секунд или оставьте поле пустым.") from exc
        return ListingFilters(number(self.minimum), number(self.maximum), self.orientation.currentData(),
                              self.quality.currentData())

    def reset_filters(self):
        self.minimum.clear()
        self.maximum.clear()
        self.orientation.setCurrentIndex(0)
        self.quality.setCurrentIndex(0)
        self.apply_filters()

    def search(self):
        if self.busy():
            self.message.emit("Сначала отмените текущую загрузку.")
            return
        if self.listing_worker and self.listing_worker.isRunning():
            self.message.emit("Дождитесь завершения или отмены текущего поиска.")
            return
        try:
            link = parse_link(self.url.text())
            adapter = make_listing(link, self.ctx.all_sources())
        except (ValueError, SourceError) as exc:
            self.notice.setText(str(exc))
            return
        self.link = link
        self.items = []
        self.total = self.accessible = 0
        self.completed.clear()
        self.errors.clear()
        self.model.checked.clear()
        self.model.states.clear()
        self.model.individual = not link.bulk_allowed
        self.orientation.setCurrentIndex(self.orientation.findData(link.orientation))
        self.notice.setText(f"Источник: {link.source}. " + "\n".join(link.warnings))
        self._policy()
        self.apply_filters()
        self.listing_worker = ListingWorker(link, adapter, self)
        worker = self.listing_worker
        worker.page_found.connect(lambda items, total, available: self._page(items, total, available)
                                  if self.listing_worker is worker and not self.closed else None)
        worker.problem.connect(lambda text: self._problem(text)
                               if self.listing_worker is worker and not self.closed else None)
        worker.finished.connect(lambda: self._listing_finished()
                                if self.listing_worker is worker and not self.closed else None)
        self.find.setEnabled(False)
        self.progress.setRange(0, 0)
        self.listing_worker.start()

    def _page(self, items, total, accessible):
        self.items.extend(items)
        self.total, self.accessible = total, accessible
        self.apply_filters()

    def _problem(self, text):
        self.notice.setText(self.notice.text() + "\n" + text)
        self.message.emit(text)

    def _listing_finished(self):
        self.find.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        if self.listing_worker and self.listing_worker.cancel.is_set():
            self.message.emit("Получение выдачи отменено; уже полученные ролики сохранены в списке.")
        elif not self.items:
            self._problem("Видео не найдены. Проверьте запрос, API-ключ и доступность источника.")

    def apply_filters(self):
        try:
            filters = self.filters()
        except ValueError as exc:
            self.filter_error.setText(str(exc))
            self.model.set_items([])
            self.download.setEnabled(False)
            self.download_all.setEnabled(False)
            return
        self.filter_error.clear()
        self.model.quality = filters.quality
        self.model.set_items([item for item in self.items if filters.allows(item)])
        self._policy()

    def _policy(self):
        bulk = bool(self.link and self.link.bulk_allowed)
        self.all_select.setEnabled(bulk and not self.busy())
        self.download_all.setEnabled(bulk and bool(self.model.items) and not self.busy())
        self.download.setText("Скачать выбранные" if bulk else "Скачать выбранный")
        self.download.setEnabled(bool(self.model.checked) and not self.busy())
        self.pause.setEnabled(self.busy())
        self.resume.setEnabled(self.busy())

    def update_stats(self):
        self.stats.setText(f"Всего по API: {self.total}; доступно: {self.accessible}; получено: {len(self.items)}; "
                           f"подходит: {len(self.model.items)}; выбрано: {len(self.model.checked)}; "
                           f"скачано: {len(self.completed)}; ошибок: {len(self.errors)}.")
        self.download.setEnabled(bool(self.model.checked) and not self.busy())

    def start_download(self, all_items=False):
        if self.listing_worker and self.listing_worker.isRunning():
            self.message.emit("Дождитесь получения выдачи или отмените поиск.")
            return
        if self.busy() or not self.link or (all_items and not self.link.bulk_allowed):
            return
        try:
            filters = self.filters()
            folder = Path(self.folder.text().strip()).expanduser()
            if not self.folder.text().strip():
                raise ValueError("Выберите папку сохранения.")
            selected = [item for item in self.model.items if filters.allows(item)
                        and (all_items or item.key in self.model.checked)]
            if not self.link.bulk_allowed and len(selected) > 1:
                raise ValueError("Pixabay: выберите один ролик для индивидуальной загрузки.")
            if not selected:
                return
            jobs, notes = [], []
            for result in selected:
                file, note = choose_quality(result, filters.quality)
                if file is None:
                    file = download_option(result, True)
                    if file is None:
                        raise ValueError("У ролика нет доступного файла для скачивания.")
                    file.height = filters.quality or None
                if note:
                    notes.append(note)
                jobs.append(DownloadJob(result, file, None, "По ссылке", folder, convert=False,
                                        check_content=False, flat_folder=True))
            self.jobs = jobs
        except ValueError as exc:
            self._problem(str(exc))
            return
        if notes:
            self._problem("\n".join(dict.fromkeys(notes)))
        for job in jobs:
            self.completed.discard(job.result.key)
            self.errors.discard(job.result.key)
            self.model.set_state(job.result.key, "В очереди")
        self.download_worker = StockDownloadWorker(jobs, self)
        worker = self.download_worker
        worker.progress.connect(lambda i, done, total: self._progress(i, done, total)
                                if self.download_worker is worker and not self.closed else None)
        worker.done.connect(lambda i, path, note: self._done(i, path, note)
                            if self.download_worker is worker and not self.closed else None)
        worker.failed.connect(lambda i, text: self._failed(i, text)
                              if self.download_worker is worker and not self.closed else None)
        worker.finished.connect(lambda: self._downloads_finished()
                                if self.download_worker is worker and not self.closed else None)
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.download_worker.start()
        self._policy()

    def _progress(self, index, done, total):
        text = "Определение потока…" if done < 0 else (f"{done * 100 // total}%" if total else f"{done / 1e6:.1f} МБ")
        self.model.set_state(self.jobs[index].result.key, text)

    def _done(self, index, path, note):
        key = self.jobs[index].result.key
        self.completed.add(key)
        self.model.set_state(key, "Скачан: " + path + (f" ({note})" if note else ""))
        self._tick()

    def _failed(self, index, message):
        key = self.jobs[index].result.key
        self.errors.add(key)
        self.model.set_state(key, message)
        self._tick()

    def _tick(self):
        keys = {job.result.key for job in self.jobs}
        self.progress.setValue(len(keys & (self.completed | self.errors)))
        self.update_stats()

    def _downloads_finished(self):
        self._policy()
        self.update_stats()
        self.message.emit("Очередь завершена. Скачанные ролики не добавлены в каталог услуг.")

    def busy(self):
        return bool(self.download_worker and self.download_worker.isRunning())

    def _pause(self):
        if self.busy():
            self.download_worker.pause()
            self.message.emit("Пауза. Для страницы площадки текущий ролик завершается перед остановкой очереди.")

    def _resume(self):
        if self.busy():
            self.download_worker.resume()

    def stop(self):
        for worker in (self.listing_worker, self.download_worker):
            if worker:
                worker.stop() if self.closed else worker.request_stop()
        self._policy()

    def shutdown(self):
        self.closed = True
        self.stop()
        for reply in list(self.replies):
            reply.abort()
        self.pending_images.clear()

    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Папка сохранения", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def _open_original(self):
        index = self.table.currentIndex()
        if index.isValid():
            url = QUrl(self.model.items[index.row()].page_url)
            if url.scheme() in ("https", "http"):
                QDesktopServices.openUrl(url)

    def _request_image(self, url):
        if QUrl(url).scheme() in ("http", "https") and not self.closed:
            self.pending_images.append(url)
            self._pump_images()

    def _pump_images(self):
        while self.pending_images and len(self.replies) < 4 and not self.closed:
            url = self.pending_images.pop(0)
            request = QNetworkRequest(QUrl(url))
            request.setAttribute(QNetworkRequest.Attribute.User, url)
            request.setTransferTimeout(15000)
            reply = self.network.get(request)
            reply.downloadProgress.connect(lambda received, total, r=reply: r.abort()
                                           if max(received, total) > design.STOCK_IMAGE_LIMIT else None)
            self.replies.add(reply)

    def _image_done(self, reply):
        url = reply.request().attribute(QNetworkRequest.Attribute.User)
        self.replies.discard(reply)
        image = QPixmap()
        if reply.error() == QNetworkReply.NetworkError.NoError:
            image.loadFromData(bytes(reply.readAll()))
        if not image.isNull():
            image = image.scaled(design.STOCK_THUMB_WIDTH, design.STOCK_THUMB_HEIGHT,
                                 Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.model.set_image(str(url), image if not image.isNull() else None)
        reply.deleteLater()
        self._pump_images()
