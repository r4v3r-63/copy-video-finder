"""Панель просмотра: встроенный плеер и сведения о ролике."""

from html import escape

from PyQt6.QtCore import QRectF, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)

from app.core import design, licenses
from app.core.models import VideoResult
from app.services.downloader import choose_file
from app.services.search import preview_file
from app.storage.catalog import REJECTED, SELECTED
from app.ui import theme
from app.ui.widgets.frames_view import FramesStrip


def video_facts(r: VideoResult) -> list[str]:
    """Длительность, размер и площадка — только то, что известно (Videezy их не сообщает)."""
    parts = [format_duration(r.duration) if r.duration else "", f"{r.width}×{r.height}" if r.width and r.height else ""]
    return [p for p in parts if p] + [r.source]


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class VideoPoster(QWidget):
    """Превью до начала воспроизведения и для роликов, доступных только на сайте."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = QPixmap()
        self.caption = "Выберите ролик для просмотра"
        self.setMinimumSize(design.PLAYER_MIN_WIDTH, design.PLAYER_MIN_HEIGHT)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), design.PREVIEW_RADIUS, design.PREVIEW_RADIUS)
        painter.setClipPath(clip)
        painter.fillRect(self.rect(), QColor(theme.palette().video_bg))
        if self.image.isNull():
            painter.setPen(QColor(design.WHITE))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.caption)
        else:
            image = self.image.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap(self.rect().center() - image.rect().center(), image)



class SeekSlider(QSlider):
    """Шкала времени: щелчок в любом месте сразу переводит ролик туда (не только перетаскивание ползунка)."""
    clicked = pyqtSignal(int)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            option = QStyleOptionSlider()
            self.initStyleOption(option)
            handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                                 QStyle.SubControl.SC_SliderHandle, self)
            if not handle.contains(event.position().toPoint()):
                groove = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                                     QStyle.SubControl.SC_SliderGroove, self)
                x = int(event.position().x()) - groove.x() - handle.width() // 2
                span = max(groove.width() - handle.width(), 1)
                value = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), x, span)
                self.setValue(value)
                self.clicked.emit(value)
        super().mousePressEvent(event)  # ползунок дальше можно тянуть, не отпуская кнопку

class PlayerPanel(QWidget):
    download_requested = pyqtSignal(object, object)  # VideoResult, VideoFile
    open_folder_requested = pyqtSignal(str)  # путь к скачанному файлу
    select_requested = pyqtSignal(object)  # VideoResult — «В каталог», лицензию проверил
    reject_requested = pyqtSignal(object)  # VideoResult — «Не подходит»

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result: VideoResult | None = None
        self.allow_4k = False
        self.downloaded_path = ""
        self._inline_available = False

        self.video = QVideoWidget()
        self.video.setMinimumSize(design.PLAYER_MIN_WIDTH, design.PLAYER_MIN_HEIGHT)
        self.poster = VideoPoster()
        self.media = QStackedWidget()
        self.media.addWidget(self.poster)
        self.media.addWidget(self.video)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setMuted(True)  # в копицентре ролики идут без звука
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        self.player.errorOccurred.connect(self._on_error)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.hasVideoChanged.connect(
            lambda available: self.media.setCurrentWidget(self.video if available else self.poster))

        self.play_button = QPushButton()
        self.play_button.setProperty("icon_inverse", True)
        self.play_button.setToolTip("Воспроизвести или приостановить")
        self.play_button.setAccessibleName("Воспроизвести или приостановить")
        theme.set_icon(self.play_button, "play")
        self.play_button.clicked.connect(self.toggle)
        self.position = SeekSlider(Qt.Orientation.Horizontal)
        self.position.setAccessibleName("Позиция воспроизведения")
        self.position.sliderMoved.connect(self.player.setPosition)
        self.position.clicked.connect(self.player.setPosition)
        self.open_page = QPushButton("Открыть на сайте")
        self.open_page.setFlat(True)
        theme.set_icon(self.open_page, "external")
        self.open_page.clicked.connect(self._open_page)
        controls_widget = QWidget()
        controls_widget.setObjectName("playerControls")
        controls = QHBoxLayout(controls_widget)
        controls.setContentsMargins(theme.SPACE_S, 0, theme.SPACE_S, 0)
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setMinimumWidth(design.PLAYER_TIME_WIDTH)
        controls.addWidget(self.play_button)
        controls.addWidget(self.position, 1)
        controls.addWidget(self.time_label)

        self.quality = theme.compact_combo(QComboBox())
        self.quality.setToolTip("Качество файла для скачивания")
        self.download_button = QPushButton("Скачать")
        self.download_button.clicked.connect(self._download)
        theme.set_role(self.download_button, theme.PRIMARY)
        theme.set_icon(self.download_button, "download")
        self.folder_button = QPushButton("Показать в папке")
        self.folder_button.clicked.connect(lambda: self.open_folder_requested.emit(self.downloaded_path))
        theme.set_icon(self.folder_button, "folder")
        self.download_status = QLabel()
        self.download_status.setWordWrap(True)
        self.download_status.setProperty("muted", True)
        self.download_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.select_button = QPushButton("В каталог")
        self.select_button.setToolTip("Добавить ролик в каталог услуги. Нажимайте, когда посмотрели ролик "
                                      "и условия лицензии.")
        theme.set_role(self.select_button, theme.SECONDARY)
        theme.set_icon(self.select_button, "folder")
        self.select_button.clicked.connect(lambda: self.result and self.select_requested.emit(self.result))
        self.reject_button = QPushButton("Не подходит")
        self.reject_button.setToolTip("Больше не показывать этот ролик в поиске")
        self.reject_button.setFlat(True)
        self.reject_button.clicked.connect(lambda: self.result and self.reject_requested.emit(self.result))
        self.catalog_status = QLabel()
        self.catalog_status.setProperty("muted", True)
        self.catalog_status.setWordWrap(True)
        catalog_row = QHBoxLayout()
        catalog_row.addWidget(self.select_button, 1)
        catalog_row.addWidget(self.download_button, 1)
        download_row = QHBoxLayout()
        download_row.addWidget(self.quality, 1)
        secondary_row = QHBoxLayout()
        secondary_row.addWidget(self.open_page, 1)
        secondary_row.addWidget(self.reject_button)

        self.title_label = QLabel()
        self.title_label.setProperty("title", True)
        self.title_label.setWordWrap(True)
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.facts = QLabel()
        self.facts.setProperty("muted", True)
        self.facts.setWordWrap(True)
        self.facts.setTextFormat(Qt.TextFormat.PlainText)
        self.license_badge = theme.LicenseBadge()
        badge_row = QHBoxLayout()
        badge_row.addWidget(self.license_badge)
        badge_row.addStretch()
        self.license_hint = QLabel()
        self.license_hint.setWordWrap(True)
        self.license_hint.setProperty("muted", True)
        self.play_error = QLabel()
        self.play_error.setWordWrap(True)
        self.details_button = QPushButton("Сведения о ролике")
        self.details_button.setFlat(True)
        self.details_button.setCheckable(True)

        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setOpenExternalLinks(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.info.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.details_button.toggled.connect(self.info.setVisible)
        self.info.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SPACE_L, theme.SPACE_L, theme.SPACE_L, theme.SPACE_L)
        layout.setSpacing(theme.SPACE_S)
        layout.addWidget(self.media, 1)
        layout.addWidget(controls_widget)
        # Сведения и кнопки — в прокрутке: на невысоком окне они не наезжают на видео и шкалу.
        details = QWidget()
        details.setObjectName("playerDetails")
        details.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lower = QVBoxLayout(details)
        lower.setContentsMargins(0, 0, 0, 0)
        lower.setSpacing(theme.SPACE_S)
        lower.addWidget(self.title_label)
        lower.addWidget(self.facts)
        lower.addLayout(badge_row)
        lower.addWidget(self.license_hint)
        lower.addWidget(self.play_error)
        lower.addLayout(download_row)
        lower.addLayout(catalog_row)
        lower.addWidget(self.catalog_status)
        lower.addWidget(self.download_status)
        lower.addWidget(self.folder_button)
        lower.addLayout(secondary_row)
        lower.addWidget(self.details_button)
        lower.addWidget(self.info)
        self.frames = FramesStrip()
        lower.addWidget(self.frames)
        lower.addStretch()
        scroll = QScrollArea()
        scroll.viewport().setObjectName("detailsViewport")
        scroll.setWidget(details)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(design.PLAYER_DETAILS_HEIGHT)
        layout.addWidget(scroll, 1)
        self.show_result(None)

    def show_result(self, result: VideoResult | None):
        self.result = result
        self.player.stop()
        self.player.setSource(QUrl())
        self.media.setCurrentWidget(self.poster)
        self.poster.image = QPixmap()
        self.poster.caption = "Загрузка видео…" if result else "Выберите ролик для просмотра"
        self.poster.update()
        self.play_error.clear()
        self.play_error.hide()
        self.title_label.setText(result.title or "Без названия" if result else "Выберите ролик")
        self.facts.setText(" · ".join(video_facts(result)) if result else "Превью и сведения появятся здесь")
        self.license_badge.setVisible(result is not None)
        if result:
            self.license_badge.set_license(result.license)
        self.license_hint.setText(self._license_hint(result))
        self.license_hint.setVisible(result is not None)
        self.details_button.setEnabled(result is not None)
        self.open_page.setEnabled(bool(result and result.page_url))
        self._fill_qualities(result)
        self.quality.setVisible(bool(result and result.files))
        self.set_download_state("", "")
        self.set_catalog_state("")
        preview = preview_file(result) if result else None
        self._set_playback_mode(preview is not None)
        self.position.setValue(0)
        self.time_label.setText(f"0:00 / {format_duration(result.duration) if result else '0:00'}")
        self.frames.set_video(result.key if result else None, preview.url if preview else "",
                              result.duration if result else None)
        self.frames.setVisible(preview is not None)
        if result is not None and result.view_only:
            # Лицензия не свободная: только посмотреть; «Не подходит» — можно.
            self.download_button.setEnabled(False)
            self.quality.setEnabled(False)
            self.select_button.setEnabled(False)
            self.catalog_status.setText("Свободной лицензии нет: скачать и добавить в каталог нельзя. "
                                        "Использовать — только с разрешения автора (страница ролика ниже).")
            self.catalog_status.show()
        if result is None:
            self.info.setText("Выберите ролик в списке, чтобы посмотреть его.")
            return
        self.info.setText(self.describe(result))
        file = preview_file(result)
        if file:
            self.player.setSource(QUrl(file.url))
            self.player.play()
            theme.set_icon(self.play_button, "pause")

    def _set_playback_mode(self, inline: bool):
        self._inline_available = inline
        on_site = bool(self.result and self.result.page_url and not inline)
        self.play_button.setEnabled(inline or on_site)
        self.play_button.setText("Смотреть на сайте" if on_site else "")
        hint = ("Открыть ролик в браузере: площадка не предоставила ссылку для встроенного просмотра."
                if on_site else "Воспроизвести или приостановить")
        self.play_button.setToolTip(hint)
        self.play_button.setAccessibleName("Смотреть на сайте" if on_site else "Воспроизвести или приостановить")
        theme.set_icon(self.play_button, "external" if on_site else "play")
        self.position.setEnabled(inline)
        self.position.setVisible(inline)
        self.time_label.setVisible(inline)
        if on_site:
            self.poster.caption = "Просмотр на сайте"
            self.poster.update()

    @staticmethod
    def _license_hint(result: VideoResult | None) -> str:
        if result is None:
            return ""
        status = result.license.status if result.license else licenses.REVIEW
        if status == licenses.REVIEW:
            return "Лицензия требует проверки. До использования проверьте условия на сайте ролика."
        if status not in licenses.SUITABLE:
            return "Свободной лицензии нет. Использование — только с разрешения правообладателя."
        return ("Проверьте условия на сайте. Нажимая «В каталог», вы подтверждаете проверку лицензии."
                + (" При использовании укажите автора и лицензию." if status == licenses.ATTRIBUTION else ""))

    def set_poster(self, image: QPixmap):
        self.poster.image = image
        self.poster.update()

    @staticmethod
    def describe(r: VideoResult) -> str:
        lic = r.license
        lines = []
        if r.title:
            lines.append(f"<b>{escape(r.title)}</b>")
        lines.append(" · ".join(escape(part) for part in video_facts(r)))
        if r.found_by:
            hint = f" — подсказка: {escape(r.match_hint)}" if r.match_hint and r.match_hint != "точное" else ""
            lines.append(f"Найден по запросу «{escape(r.found_by)}»{hint}")
        if r.author:
            author = escape(r.author)
            if r.author_url:
                author = f'<a href="{escape(r.author_url)}">{author}</a>'
            lines.append(f"Автор: {author}")
        if lic:
            name = escape(lic.name)
            if lic.url:
                name = f'<a href="{escape(lic.url)}">{name}</a>'
            lines.append(f"Лицензия: {name} — {escape(lic.status)}")
            if lic.status not in licenses.SUITABLE:
                lines.append("<b>Свободной лицензии нет: использовать — только с разрешения автора.</b>")
            if lic.note:
                lines.append(escape(lic.note))
            if lic.status == licenses.ATTRIBUTION:
                text = licenses.attribution_text(r.author, r.title, lic.name, r.page_url)
                lines.append(f"Атрибуция: {escape(text)}")
        if r.has_audio:
            lines.append("Есть звук — проверить права на музыку.")
        qualities = ", ".join(dict.fromkeys(
            f"{f.quality} ({f.width}×{f.height})" if f.width and f.height else f.quality for f in r.files))
        if qualities:
            lines.append(f"Качество: {escape(qualities)}")
        return "<br>".join(lines)

    def _fill_qualities(self, result: VideoResult | None):
        self.quality.clear()
        files = result.files if result else []
        for f in files:
            size = f" ({f.width}×{f.height})" if f.width and f.height else ""
            kind = f", {f.mime.split('/')[-1].upper()}" if f.mime and "mp4" not in f.mime else ""
            self.quality.addItem(f"{f.quality}{size}{kind}", f)
        preferred = choose_file(result, self.allow_4k) if result else None
        if preferred in files:
            self.quality.setCurrentIndex(files.index(preferred))
        self.quality.setEnabled(bool(files))
        self.download_button.setEnabled(bool(files))

    def set_catalog_state(self, status: str, catalog_id: int | None = None):
        """Отметка ролика в каталоге: «выбрано», «отклонено» или пусто."""
        has = self.result is not None
        self.select_button.setEnabled(has and status != SELECTED and not self.result.view_only)
        self.reject_button.setEnabled(has and status != REJECTED)
        if status == SELECTED:
            self.catalog_status.setText(f"В каталоге, № {catalog_id}." if catalog_id else "В каталоге.")
        elif status == REJECTED:
            self.catalog_status.setText("Отклонён — в поиске больше не появится.")
        else:
            self.catalog_status.setText("")
        self.catalog_status.setVisible(bool(self.catalog_status.text()))

    def set_download_state(self, text: str, path: str, busy: bool = False):
        """Состояние скачивания для показанного ролика."""
        self.download_status.setText(text)
        self.download_status.setVisible(bool(text))
        self.downloaded_path = path
        self.folder_button.setVisible(bool(path))
        if self.result and self.result.files and not self.result.view_only:
            self.download_button.setEnabled(not busy and not path)

    def _download(self):
        file = self.quality.currentData()
        if self.result and file:
            self.download_requested.emit(self.result, file)

    def toggle(self):
        if not self._inline_available:
            self._open_page()
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            theme.set_icon(self.play_button, "play")
        else:
            self.player.play()
            theme.set_icon(self.play_button, "pause")

    def _on_position(self, pos: int):
        if not self.position.isSliderDown():
            self.position.setValue(pos)
        self.time_label.setText(f"{format_duration(pos / 1000)} / {format_duration(self.player.duration() / 1000)}")

    def _on_duration(self, duration: int):
        self.position.setRange(0, duration)
        self._on_position(self.player.position())

    def _on_error(self, _error, message: str):
        if self.result:
            self.media.setCurrentWidget(self.poster)
            self._set_playback_mode(False)
            self.play_error.setText(f"Не удалось воспроизвести ролик ({message}). Откройте его на сайте.")
            self.play_error.setTextFormat(Qt.TextFormat.PlainText)
            self.play_error.show()

    def _open_page(self):
        if self.result and self.result.page_url:
            QDesktopServices.openUrl(QUrl(self.result.page_url))
