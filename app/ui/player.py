"""Панель просмотра: встроенный плеер и сведения о ролике."""

from html import escape

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from app import licenses
from app.catalog import REJECTED, SELECTED
from app.downloader import choose_file
from app.search import preview_file
from app.sources.base import VideoResult
from app.ui import theme


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


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

        self.video = QVideoWidget()
        self.video.setMinimumSize(320, 180)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setMuted(True)  # в копицентре ролики идут без звука
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        self.player.errorOccurred.connect(self._on_error)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(lambda d: self.position.setRange(0, d))

        self.play_button = QPushButton("Пауза")
        self.play_button.clicked.connect(self.toggle)
        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.sliderMoved.connect(self.player.setPosition)
        self.open_page = QPushButton("Открыть страницу ролика")
        self.open_page.clicked.connect(self._open_page)
        controls = QHBoxLayout()
        controls.addWidget(self.play_button)
        controls.addWidget(self.position, 1)

        self.quality = QComboBox()
        self.quality.setToolTip("Качество файла для скачивания")
        self.download_button = QPushButton("Скачать")
        self.download_button.clicked.connect(self._download)
        theme.set_role(self.download_button, theme.PRIMARY)
        self.folder_button = QPushButton("Показать в папке")
        self.folder_button.clicked.connect(lambda: self.open_folder_requested.emit(self.downloaded_path))
        self.download_status = QLabel()
        self.download_status.setWordWrap(True)
        self.download_status.setProperty("muted", True)
        self.download_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.select_button = QPushButton("В каталог — лицензию проверил")
        self.select_button.setToolTip("Добавить ролик в каталог услуги. Нажимайте, когда посмотрели ролик "
                                      "и условия лицензии.")
        theme.set_role(self.select_button, theme.PRIMARY)
        self.select_button.clicked.connect(lambda: self.result and self.select_requested.emit(self.result))
        self.reject_button = QPushButton("Не подходит")
        self.reject_button.setToolTip("Больше не показывать этот ролик в поиске")
        theme.set_role(self.reject_button, theme.SECONDARY)
        self.reject_button.clicked.connect(lambda: self.result and self.reject_requested.emit(self.result))
        self.catalog_status = QLabel()
        self.catalog_status.setProperty("muted", True)
        catalog_row = QHBoxLayout()
        catalog_row.addWidget(self.select_button, 1)
        catalog_row.addWidget(self.reject_button)
        download_row = QHBoxLayout()
        download_row.addWidget(self.quality, 1)
        download_row.addWidget(self.download_button)
        download_row.addWidget(self.folder_button)

        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setOpenExternalLinks(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.info.setAlignment(Qt.AlignmentFlag.AlignTop)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SPACE_L, theme.SPACE_L, theme.SPACE_L, theme.SPACE_L)
        layout.setSpacing(theme.SPACE_S)
        layout.addWidget(self.video, 1)
        layout.addLayout(controls)
        layout.addWidget(self.info)
        layout.addLayout(catalog_row)
        layout.addWidget(self.catalog_status)
        layout.addLayout(download_row)
        layout.addWidget(self.download_status)
        layout.addWidget(self.open_page)
        self.show_result(None)

    def show_result(self, result: VideoResult | None):
        self.result = result
        self.player.stop()
        self.open_page.setEnabled(result is not None)
        self.play_button.setEnabled(result is not None)
        self._fill_qualities(result)
        self.set_download_state("", "")
        self.set_catalog_state("")
        if result is None:
            self.info.setText("Выберите ролик в списке, чтобы посмотреть его.")
            return
        self.info.setText(self.describe(result))
        file = preview_file(result)
        if file:
            self.player.setSource(QUrl(file.url))
            self.player.play()
            self.play_button.setText("Пауза")

    @staticmethod
    def describe(r: VideoResult) -> str:
        lic = r.license
        lines = []
        if r.title:
            lines.append(f"<b>{escape(r.title)}</b>")
        size = f"{r.width}×{r.height}" if r.width and r.height else "?"
        lines.append(f"{format_duration(r.duration)} · {size} · {escape(r.source)}")
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
        self.select_button.setEnabled(has and status != SELECTED)
        self.reject_button.setEnabled(has and status != REJECTED)
        if status == SELECTED:
            self.catalog_status.setText(f"В каталоге, № {catalog_id}." if catalog_id else "В каталоге.")
        elif status == REJECTED:
            self.catalog_status.setText("Отклонён — в поиске больше не появится.")
        else:
            self.catalog_status.setText("")

    def set_download_state(self, text: str, path: str, busy: bool = False):
        """Состояние скачивания для показанного ролика."""
        self.download_status.setText(text)
        self.downloaded_path = path
        self.folder_button.setVisible(bool(path))
        if self.result and self.result.files:
            self.download_button.setEnabled(not busy and not path)

    def _download(self):
        file = self.quality.currentData()
        if self.result and file:
            self.download_requested.emit(self.result, file)

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_button.setText("Играть")
        else:
            self.player.play()
            self.play_button.setText("Пауза")

    def _on_position(self, pos: int):
        if not self.position.isSliderDown():
            self.position.setValue(pos)

    def _on_error(self, _error, message: str):
        if self.result:
            self.info.setText(
                self.describe(self.result)
                + f"<br><br>Не удалось воспроизвести ролик ({escape(message)}). "
                "Откройте его страницу в браузере."
            )

    def _open_page(self):
        if self.result and self.result.page_url:
            QDesktopServices.openUrl(QUrl(self.result.page_url))
