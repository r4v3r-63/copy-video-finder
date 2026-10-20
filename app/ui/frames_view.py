"""Полоса «Кадры из ролика»: три кадра (начало, середина, конец) по кнопке."""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app import paths
from app.frames import FramesWorker
from app.ui import theme

THUMB_WIDTH = 160


class FramesStrip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.key = None
        self.source = ""
        self.duration: float | None = None
        self.workers: list[FramesWorker] = []
        self.button = QPushButton("Кадры из ролика")
        self.button.setToolTip("Начало, середина и конец ролика — чтобы оценить содержание, не смотря его целиком. "
                               "Ролик не скачивается.")
        self.button.clicked.connect(self.load)
        self.status = QLabel()
        self.status.setProperty("muted", True)
        top = QHBoxLayout()
        top.addWidget(self.button)
        top.addWidget(self.status, 1)
        self.row = QHBoxLayout()
        self.row.setSpacing(theme.SPACE_S)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addLayout(self.row)
        self.set_video(None, "", None)

    def set_video(self, key, source: str, duration: float | None):
        """Новый ролик: прежние кадры убираются; source — ссылка для просмотра или скачанный файл."""
        for worker in self.workers:
            worker.cancel.set()
        self.key, self.source, self.duration = key, source, duration
        self._clear()
        self.status.setText("")
        self.button.setEnabled(bool(source))

    def load(self):
        ffmpeg = paths.ffmpeg_tool("ffmpeg")
        if not ffmpeg:
            self.status.setText("Нужен FFmpeg в папке программы.")
            return
        self.button.setEnabled(False)
        self.status.setText("Берём кадры…")
        worker = FramesWorker(self.key, self.source, self.duration, Path(ffmpeg), self)
        worker.done.connect(self._on_done)
        worker.finished.connect(lambda w=worker: self.workers.remove(w) if w in self.workers else None)
        self.workers.append(worker)
        worker.start()

    def _on_done(self, key, frames: list):
        if key != self.key:
            return  # кадры ролика, который уже не выбран
        self._clear()
        self.button.setEnabled(bool(self.source))
        if not frames:
            self.status.setText("Кадры взять не удалось — откройте ролик целиком.")
            return
        self.status.setText("")
        for at, png in frames:
            image = QImage()
            if not image.loadFromData(png):
                continue
            cell = QVBoxLayout()
            picture = QLabel()
            picture.setPixmap(QPixmap.fromImage(image).scaledToWidth(
                THUMB_WIDTH, Qt.TransformationMode.SmoothTransformation))
            caption = QLabel(f"{int(at) // 60}:{int(at) % 60:02d}")
            caption.setProperty("muted", True)
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.addWidget(picture)
            cell.addWidget(caption)
            self.row.addLayout(cell)
        self.row.addStretch()

    def _clear(self):
        while self.row.count():
            item = self.row.takeAt(0)
            layout = item.layout()
            if layout is not None:
                while layout.count():
                    child = layout.takeAt(0).widget()
                    if child:
                        child.deleteLater()
            elif item.widget():
                item.widget().deleteLater()

    def stop(self):
        """Перед закрытием окна: отменить и дождаться потоков — FFmpeg при отмене завершается сразу."""
        for worker in list(self.workers):
            worker.cancel.set()
        for worker in list(self.workers):
            worker.wait()
