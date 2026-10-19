"""Панель просмотра: встроенный плеер и сведения о ролике."""

from html import escape

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from app import licenses
from app.search import preview_file
from app.sources.base import VideoResult


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class PlayerPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.result: VideoResult | None = None

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

        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setOpenExternalLinks(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.info.setAlignment(Qt.AlignmentFlag.AlignTop)

        layout = QVBoxLayout(self)
        layout.addWidget(self.video, 1)
        layout.addLayout(controls)
        layout.addWidget(self.info)
        layout.addWidget(self.open_page)
        self.show_result(None)

    def show_result(self, result: VideoResult | None):
        self.result = result
        self.player.stop()
        self.open_page.setEnabled(result is not None)
        self.play_button.setEnabled(result is not None)
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
