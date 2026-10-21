"""Вкладка «Поиск»: поле ввода услуги, сетка результатов и панель просмотра."""

import io
from html import escape
from pathlib import Path

from PyQt6.QtCore import QSize, QStringListModel, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon, QImage, QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (
    QCompleter,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.core import design, licenses, processes, query
from app.core.models import VideoResult
from app.sources.base import Source
from app.sources.http import USER_AGENT
from app.storage import catalog as cat
from app.ui import theme
from app.ui.context import AppContext
from app.ui.downloads import DownloadController
from app.ui.widgets.player import PlayerPanel, format_duration
from app.ui.widgets.result_cards import ResultCardDelegate, card_notes
from app.ui.widgets.result_filters import ResultFilters
from app.ui.widgets.smooth_scroll import smooth
from app.workers.search import SearchWorker

THUMB = QSize(design.THUMB_WIDTH, design.THUMB_HEIGHT)


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


class SearchTab(QWidget):
    message = pyqtSignal(str)  # текст для строки состояния окна

    def __init__(self, ctx: AppContext, downloads: DownloadController, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.downloads = downloads
        self.worker: SearchWorker | None = None
        self.sources: list[Source] = []
        self.problems: list[str] = []
        self.plan: query.SearchPlan | None = None
        self.plan_text = ""
        self.sources_seen: dict[str, str] = {}
        self._thumb_items: dict[str, list[QListWidgetItem]] = {}
        self._thumb_images: dict[str, QPixmap | None] = {}
        self.network = QNetworkAccessManager(self)
        self.network.finished.connect(self._on_thumbnail)
        downloads.state_changed.connect(self._on_download_state)
        downloads.catalog_state.connect(self._on_catalog_state)

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
        self.completer.activated.connect(self.search_text)
        self.query.returnPressed.connect(self.run_search)

        self.search_button = QPushButton("Найти")
        theme.set_role(self.search_button, theme.PRIMARY)
        self.search_button.setProperty("large", True)
        theme.set_icon(self.search_button, "search")
        self.search_button.clicked.connect(self._search_or_stop)

        search_row = QHBoxLayout()
        search_row.addWidget(self.query, 1)
        search_row.addWidget(self.search_button)
        filter_button = QPushButton("Фильтры")
        filter_button.setCheckable(True)
        theme.set_icon(filter_button, "filters")
        self.result_filters = ResultFilters()
        self.result_filters.hide()
        filter_button.toggled.connect(self.result_filters.setVisible)

        self.query_info = QLabel()
        self.query_info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.query_info.setProperty("muted", True)
        self.query_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self.query_info, 1)
        toolbar.addWidget(filter_button)
        toolbar.addWidget(self.result_filters.order)

        self.source_links = QLabel()
        self.source_links.setOpenExternalLinks(True)
        self.source_links.setWordWrap(True)
        self.source_links.setProperty("muted", True)
        self.source_links.hide()

        self.results = QListWidget()
        self.results.setObjectName("results")
        self.results.setItemDelegate(ResultCardDelegate(self.results))
        self.results.setMouseTracking(True)
        self.results.setViewMode(QListView.ViewMode.IconMode)
        self.results.setIconSize(THUMB)
        self.results.setGridSize(QSize(design.RESULT_WIDTH + design.RESULT_GAP,
                                       design.RESULT_HEIGHT + design.RESULT_GAP))
        self.results.setResizeMode(QListView.ResizeMode.Adjust)
        self.results.setMovement(QListView.Movement.Static)
        self.results.setWordWrap(True)
        self.results.setUniformItemSizes(True)
        self.results_scroll = smooth(self.results, step=THUMB.height())  # колесико — плавно, не рывком
        self.results.currentItemChanged.connect(self._on_current)

        self.player = theme.make_card(PlayerPanel())
        self.player.allow_4k = ctx.settings.allow_4k
        self.player.download_requested.connect(self.download)
        self.player.open_folder_requested.connect(self._open_folder)
        self.player.select_requested.connect(self.add_to_catalog)
        self.player.reject_requested.connect(self.reject_result)

        splitter = QSplitter()
        splitter.addWidget(self.results)
        splitter.addWidget(self.player)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SPACE_L, theme.SPACE_L, theme.SPACE_L, theme.SPACE_L)
        layout.setSpacing(theme.SPACE_M)
        layout.addLayout(search_row)
        layout.addLayout(toolbar)
        layout.addWidget(self.result_filters)
        layout.addWidget(self.source_links)
        layout.addWidget(splitter, 1)

        # Кнопки «показать отсеянные» живут в строке состояния окна (`status_buttons`).
        self.show_rejected = QPushButton()
        self.show_rejected.setFlat(True)
        self.show_rejected.setToolTip("Ролики, у которых в названии и тегах нет слов услуги. "
                                      "Лицензия, длительность и размер у них подходят.")
        self.show_rejected.clicked.connect(self._show_off_topic)
        self.show_rejected.hide()
        self.show_unlicensed = QPushButton()
        self.show_unlicensed.setFlat(True)
        self.show_unlicensed.setToolTip("Ролики по теме, но без свободной лицензии («все права защищены», NC, ND). "
                                        "Только посмотреть: скачать и засчитать в покрытие нельзя, использовать — "
                                        "с разрешения автора. Платные не показываются.")
        self.show_unlicensed.clicked.connect(self._show_unlicensed)
        self.show_unlicensed.hide()
        self.show_hint()
        self.result_filters.changed.connect(self.filter_results)

    def status_buttons(self) -> list[QPushButton]:
        return [self.show_rejected, self.show_unlicensed]

    def apply_settings(self):
        self.player.allow_4k = self.ctx.settings.allow_4k
        self.show_hint()

    def stop(self):
        """Окно закрывается: остановить поиск, плеер и кадры."""
        self.stop_search()
        self.player.player.stop()
        self.player.frames.stop()

    def show_hint(self):
        _, skipped = self.ctx.sources()
        if skipped:
            hint = (
                f"Без ключа API пропускаются: {', '.join(skipped)}. Ключи вводятся в меню «Файл → Настройки». "
                "Internet Archive, Videezy, Яндекс Видео и поиск по интернету работают без ключа."
            )
            self.query_info.setText("Введите услугу для поиска · часть площадок требует ключ API")
            self.query_info.setToolTip(hint)
        else:
            self.query_info.setText("")

    def _update_suggestions(self, text: str):
        found = processes.suggestion_texts(self.ctx.processes, text)
        self._suggestions.setStringList(found)
        if found:
            self.completer.complete()

    def search_text(self, text: str):
        """Подставить запрос и сразу искать (подсказка, история, услуга из пакетного поиска)."""
        self.query.setText(text)
        self.run_search()

    def search_plan(self) -> query.SearchPlan:
        """Запросы на русском и английском для введённой услуги или свободного текста."""
        return query.build_plan(self.query.text(), self.ctx.processes, self.ctx.dictionary)

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
        self.sources, skipped = self.ctx.sources()
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
        self.result_filters.set_sources([])
        self.source_links.setText("")
        self.source_links.hide()

        self._thumb_items = {}
        self._thumb_images = {}
        hidden = {key for key, status in self.ctx.catalog.statuses().items() if status == cat.REJECTED}
        self.worker = SearchWorker(plan, self.sources, self.ctx.filters(),
                                   self.ctx.settings.only_without_attribution, self, printing=self.ctx.printing,
                                   hidden=hidden)
        self.worker.found.connect(self._on_found)
        self.worker.previously_rejected_found.connect(self._on_found)
        self.worker.problem.connect(self._on_problem)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.search_button.setText("Остановить")
        self.worker.start()

    def stop_search(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait()  # отмена прерывает ожидание лимитов; запросы — с тайм-аутом
            self.message.emit("Поиск остановлен.")

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
            self.add_results(results)

    def add_results(self, results: list[VideoResult], off_topic: bool = False, view_only: bool = False):
        self._remove_placeholder()
        statuses = self.ctx.catalog.statuses()
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
            elif statuses.get(r.key) == cat.REJECTED:
                text += "\nотклонён ранее"
            if off_topic:
                r.relevance = -1  # в конец выдачи
                text = "Не по теме · " + text
            if view_only:
                r.relevance = -2  # после всех свободных
                text = f"Только посмотреть · {r.source}\n{r.license.name}"
            item = QListWidgetItem((r.title + "\n" if r.title else "") + text)
            if off_topic or view_only:
                item.setForeground(self.palette().placeholderText())
            item.setData(Qt.ItemDataRole.UserRole, r)
            item.setData(Qt.ItemDataRole.UserRole + 1, off_topic or view_only)
            item.setData(Qt.ItemDataRole.UserRole + 2, card_notes(r, statuses.get(r.key), off_topic))
            item.setData(Qt.ItemDataRole.UserRole + 3, off_topic)
            item.setData(Qt.ItemDataRole.UserRole + 4,
                         "Загрузка превью…" if r.preview_url else "Картинка не предоставлена")
            item.setToolTip(r.title or r.page_url)
            item.setSizeHint(QSize(design.RESULT_WIDTH, design.RESULT_HEIGHT))
            # Ближе к теме и к полиграфии — выше в выдаче.
            row = next((i for i in range(self.results.count())
                        if self.results.item(i).data(Qt.ItemDataRole.UserRole) is not None
                        and self.results.item(i).data(Qt.ItemDataRole.UserRole).relevance < r.relevance),
                       self.results.count())
            self.results.insertItem(row, item)
            if r.preview_url:
                self._request_thumbnail(item, r.preview_url)
            # Рядом с результатами площадки всегда ссылка на неё (требование Pexels и Pixabay).
            homepage = next((s.homepage for s in self.sources if s.name == r.source), "")
            self.sources_seen.setdefault(r.source, homepage)
        links = [f'<a href="{escape(url)}">{escape(name)}</a>' if url else escape(name)
                 for name, url in self.sources_seen.items()]
        self.source_links.setText("Видео предоставлены: " + ", ".join(links))
        self.source_links.setVisible(bool(links))
        self.result_filters.set_sources(self.sources_seen)
        self.filter_results()

    def filter_results(self):
        statuses = self.ctx.catalog.statuses()
        current = self.results.currentItem()
        items = [self.results.item(i) for i in range(self.results.count())]
        real = [item for item in items if item.data(Qt.ItemDataRole.UserRole) is not None]
        ordered = sorted(real, key=lambda item: (bool(item.data(Qt.ItemDataRole.UserRole + 1)),
                                                 self.result_filters.sort_key(item.data(Qt.ItemDataRole.UserRole))))
        # Переставляем те же карточки: загруженные превью и ссылки фоновых запросов сохраняются.
        if real != ordered:
            self.results.blockSignals(True)
            for item in real:
                self.results.takeItem(self.results.row(item))
            for item in ordered:
                self.results.addItem(item)
            if current in ordered:
                self.results.setCurrentItem(current)
            self.results.blockSignals(False)
        for item in ordered:
            result = item.data(Qt.ItemDataRole.UserRole)
            item.setData(Qt.ItemDataRole.UserRole + 2, card_notes(result, statuses.get(result.key),
                                                               bool(item.data(Qt.ItemDataRole.UserRole + 3))))
            item.setHidden(not self.result_filters.allows(result, statuses))
        for item in items:
            if item.data(Qt.ItemDataRole.UserRole) is None:
                item.setHidden(any(not card.isHidden() for card in ordered))
        if current and current.isHidden():
            self.results.setCurrentItem(None)
            self.player.show_result(None)

    def _request_thumbnail(self, item: QListWidgetItem, url: str):
        # Одна картинка может относиться к нескольким результатам; загружаем её один раз за поиск.
        waiting = self._thumb_items.setdefault(url, [])
        waiting.append(item)
        if url in self._thumb_images:
            self._set_thumbnail(item, self._thumb_images[url])
        elif len(waiting) == 1:
            request = QNetworkRequest(QUrl(url))
            request.setAttribute(QNetworkRequest.Attribute.User, url)
            request.setRawHeader(b"User-Agent", USER_AGENT.encode("ascii"))
            request.setRawHeader(b"Accept", b"image/*")
            request.setTransferTimeout(15000)
            self.network.get(request)

    def _set_thumbnail(self, item: QListWidgetItem, pixmap: QPixmap | None):
        if pixmap is None:
            item.setData(Qt.ItemDataRole.UserRole + 4, "Картинка недоступна")
            return
        icon = QIcon(pixmap)
        icon.addPixmap(pixmap, QIcon.Mode.Selected)
        item.setIcon(icon)
        if self.player.result and item.data(Qt.ItemDataRole.UserRole).key == self.player.result.key:
            self.player.set_poster(pixmap)

    def _on_thumbnail(self, reply: QNetworkReply):
        token = reply.request().attribute(QNetworkRequest.Attribute.User)
        if token is not None and str(token) in self._thumb_items:
            pixmap = None
            if reply.error() == QNetworkReply.NetworkError.NoError:
                image = load_image(bytes(reply.readAll()))
                if not image.isNull():
                    pixmap = QPixmap.fromImage(image).scaled(THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                                           Qt.TransformationMode.SmoothTransformation)
            self._thumb_images[str(token)] = pixmap
            for item in self._thumb_items[str(token)]:
                if self.results.row(item) >= 0:
                    self._set_thumbnail(item, pixmap)
        reply.deleteLater()

    def _on_current(self, item: QListWidgetItem | None, _previous):
        result = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.player.show_result(result)
        if result and not item.icon().isNull():
            self.player.set_poster(item.icon().pixmap(THUMB))
        if result:
            entry = self.ctx.catalog.find(result.key)
            if entry:
                self.player.set_catalog_state(entry.status, entry.id)
        if result and result.key in self.downloads.state:
            self.player.set_download_state(*self.downloads.state[result.key])

    def _on_download_state(self, key, text: str, path: str, busy: bool):
        current = self.player.result
        if current and current.key == key:
            self.player.set_download_state(text, path, busy)

    def _on_catalog_state(self, key, status: str, video_id: int):
        if self.player.result and self.player.result.key == key:
            self.player.set_catalog_state(status, video_id)

    def add_to_catalog(self, result: VideoResult):
        """«В каталог — лицензию проверил»: ролик привязывается к услуге поиска."""
        process = self.plan.process if self.plan else None
        match = result.match_hint or cat.EXACT  # найден по похожим операциям — «смежный процесс»
        video_id = self.ctx.catalog.select(result, process.id if process else None, match)
        self.player.set_catalog_state(cat.SELECTED, video_id)
        if process:
            self.message.emit(f"Ролик № {video_id} добавлен в каталог: {process.name} ({match}). "
                              "Степень соответствия можно поменять во вкладке «Каталог».")
        else:
            self.message.emit(f"Ролик № {video_id} добавлен в каталог. Привяжите его к услуге "
                              "во вкладке «Каталог».")
        self._mark_item(result, "в каталоге")

    def reject_result(self, result: VideoResult):
        """«Не подходит»: ролик убирается из выдачи и больше в поиске не появится."""
        self.ctx.catalog.reject(result)
        row = next((i for i in range(self.results.count())
                    if (self.results.item(i).data(Qt.ItemDataRole.UserRole) or None) is result), -1)
        if row >= 0:
            item = self.results.item(row)
            if "отклонён ранее" not in item.text():
                item.setText(item.text() + "\nотклонён ранее")
        self.filter_results()
        self.message.emit("Ролик отклонён. Его можно показать через фильтр «Показать ранее отклонённые».")

    def _mark_item(self, result: VideoResult, mark: str):
        for i in range(self.results.count()):
            item = self.results.item(i)
            if item.data(Qt.ItemDataRole.UserRole) is result and mark not in item.text():
                item.setText(f"{item.text()}\n{mark}")

    def download(self, result: VideoResult, file):
        process = self.plan.process if self.plan else None
        self.downloads.download(result, file, process, self.plan_text)

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
        self.message.emit(f"Поиск: {done} из {total} запросов, найдено {self.results.count()}")

    def _on_finished(self):
        if not self.worker or self._from_old_search():
            return
        search = self.worker.search
        self.search_button.setText("Найти")
        count = self.results.count()
        shown = [self.results.item(i).data(Qt.ItemDataRole.UserRole) for i in range(count)]
        shown = [r for r in shown if r is not None and r.key in search.seen]
        count = len(shown)
        had_history = self.ctx.catalog.has_history()
        new = self.ctx.catalog.record_search(self.plan_text, [r.key for r in shown])
        if had_history:  # на самом первом поиске новыми были бы все — не помечаем
            for r in shown:
                if r.key in new:
                    self._mark_item(r, "новый")
        message = f"Найдено подходящих роликов: {count}"
        if search.levels_used and self.plan and len(self.plan.levels) > search.levels_used:
            message += f" (поиск расширен до уровня «{self.plan.levels[search.levels_used]}»)"
        if had_history and new:
            message += f" (новых с прошлого поиска: {len(new)})"
        counts = search.rejection_counts()
        if counts:
            details = ", ".join(f"{reason} — {n}" for reason, n in counts.most_common())
            message += f". Отсеяно: {details}"
        self.message.emit(message)
        if self.problems:
            self.query_info.setText(self.query_info.text() + "\n" + "\n".join(self.problems))
        rejected = len(search.off_topic_results())
        if rejected:
            self.show_rejected.setText(f"Показать отсеянные «не по теме» ({rejected})")
            self.show_rejected.show()
        unlicensed = len(search.unlicensed_results())
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
            self.add_results(self.worker.search.unlicensed_results(), view_only=True)
        self.show_unlicensed.hide()

    def _show_off_topic(self):
        if self.worker:
            self.add_results(self.worker.search.off_topic_results(), off_topic=True)
        self.show_rejected.hide()
