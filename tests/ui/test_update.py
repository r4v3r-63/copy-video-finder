"""Закрытие через closeEvent и понятный отказ при несостоявшемся запуске установщика."""

from types import SimpleNamespace

from app.services import updater
from app.ui.update import UpdateController


def test_window_closes_only_after_installer_is_ready(monkeypatch):
    events = []
    window = SimpleNamespace(close=lambda: events.append("close"))
    controller = UpdateController(window, None)
    monkeypatch.setattr(updater, "prepare_and_launch", lambda path: events.append("ready"))
    controller._on_downloaded("update.zip")
    assert events == ["ready", "close"]


def test_failed_installer_keeps_window_open(monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    events = []
    window = SimpleNamespace(close=lambda: events.append("close"))
    controller = UpdateController(window, None)

    def fail(path):
        raise updater.UpdateError("Не запустился установщик")

    monkeypatch.setattr(updater, "prepare_and_launch", fail)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: events.append("warning"))
    controller._on_downloaded("update.zip")
    assert events == ["warning"]
