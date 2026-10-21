"""Просмотр страницы видеоплощадки внутри панели, без извлечения ссылок на файлы."""

import sys
from urllib.parse import urlsplit

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.core import design
from app.ui import theme


def allowed_page(url: str) -> bool:
    """Во встроенном просмотре открываются только веб-страницы, без внешних протоколов и файлов."""
    try:
        parts = urlsplit(url)
        _port = parts.port
        return bool(parts.scheme in ("https", "http") and parts.hostname
                    and parts.username is None and parts.password is None
                    and not any(c.isspace() or ord(c) < 32 or c == "\\" for c in url))
    except ValueError:
        return False


class SitePlayer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.view = None
        self.current_url = ""
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setProperty("muted", True)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.status)
        self.setMinimumSize(design.PLAYER_MIN_WIDTH, design.PLAYER_MIN_HEIGHT)

    def _create_view(self):
        # На Windows WebView2 поддерживает H.264; в Linux — Qt WebEngine для разработки.
        if sys.platform == "win32":
            from app.ui.widgets.windows_site_view import WindowsSiteView
            return WindowsSiteView(self)
        from app.ui.widgets.qt_site_view import QtSiteView
        return QtSiteView(self)

    def open(self, url: str) -> bool:
        if not allowed_page(url):
            self.status.setText("Не удалось открыть страницу ролика: нужна ссылка HTTP или HTTPS.")
            return False
        self.current_url = url
        self.status.setText("Загрузка плеера сайта…")
        if self.view is None:
            try:
                self.view = self._create_view()
            except (ImportError, OSError, RuntimeError):
                self.status.setText("Не удалось запустить встроенный просмотр. Попробуйте перезапустить программу.")
                return False
            self.view.loadFinished.connect(self._loaded)
            self.layout.addWidget(self.view, 1)
        self.view.navigate(url)
        return True

    def _loaded(self, ok: bool):
        if self.current_url:
            self.status.setText("Управление — в плеере сайта. При необходимости войдите на сайт в этой панели."
                                if ok else "Страница не загрузилась. Нажмите «Повторить просмотр». "
                                "Доступность и ограничения ролика определяет площадка.")

    def stop(self):
        self.current_url = ""
        if self.view is not None:
            self.view.navigate("about:blank")

    def shutdown(self):
        self.current_url = ""
        if self.view is not None:
            self.view.shutdown()

    def apply_theme(self):
        self.setPalette(theme.qt_palette(theme.palette()))
