"""Общая настройка тестов.

Тестам окон нужен QApplication, а он должен быть создан раньше любого QCoreApplication
и жить до конца прогона — поэтому одно приложение Qt на весь прогон, без экрана.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QCoreApplication, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
APP = QApplication.instance() or QApplication(["pytest"])



@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Каждый тест работает со своей временной папкой «Данные»."""
    path = tmp_path / "Данные"
    monkeypatch.setenv("COPYVF_DATA_DIR", str(path))
    return path


@pytest.fixture(autouse=True)
def offline_site_player(monkeypatch):
    """Юнит-тесты не запускают браузер и не обращаются к сайтам; реальный просмотр проверяется отдельно."""
    from PyQt6.QtCore import pyqtSignal
    from PyQt6.QtWidgets import QWidget

    from app.ui.widgets.site_player import SitePlayer

    class RecordedView(QWidget):
        loadFinished = pyqtSignal(bool)

        def __init__(self, parent):
            super().__init__(parent)
            self.urls = []
            self.closed = False

        def navigate(self, url):
            self.urls.append(url)

        def shutdown(self):
            self.closed = True

    monkeypatch.setattr(SitePlayer, "_create_view", lambda panel: RecordedView(panel))
