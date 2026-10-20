"""Руководство доступно в разработке и из каталога ресурсов собранной программы."""

import sys

from app.ui.guide import GuideDialog


def test_guide_opens_without_network():
    dialog = GuideDialog()
    text = dialog.browser.toPlainText()
    assert "Руководство пользователя Copy.ru" in text
    for topic in ("Резервная копия", "Добавление по ссылке", "Пакетный поиск", "Ctrl", "Лицензия"):
        assert topic in text
    dialog.close()


def test_guide_uses_frozen_resources_directory(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "USER_GUIDE.md").write_text("# Руководство из поставки", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    dialog = GuideDialog()
    assert "Руководство из поставки" in dialog.browser.toPlainText()
    dialog.close()
