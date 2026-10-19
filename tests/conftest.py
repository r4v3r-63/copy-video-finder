"""Общая настройка тестов.

Тестам окон нужен QApplication, а он должен быть создан раньше любого QCoreApplication
и жить до конца прогона — поэтому одно приложение Qt на весь прогон, без экрана.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])



@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Каждый тест работает со своей временной папкой «Данные»."""
    path = tmp_path / "Данные"
    monkeypatch.setenv("COPYVF_DATA_DIR", str(path))
    return path
