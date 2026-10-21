"""Проверка лицензий и выбора ролика при обновлённом оформлении, без сети."""

import json
from dataclasses import replace

import pytest
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from app.core import design, licenses, processes
from app.sources.pixabay import parse_hit
from app.sources.web import parse_page
from app.storage.settings import Settings
from app.ui import theme
from app.ui.main_window import MainWindow
from app.ui.widgets.player import PlayerPanel
from tests.fakes import fixture_text


def recorded_result():
    return replace(parse_page(fixture_text("manual_video.html"), "https://example.org/video/1"),
                   preview_url=None, preview_video=None, files=[])


def test_badges_preserve_statuses_from_saved_platform_responses():
    pixabay = parse_hit(json.loads(fixture_text("pixabay_search.json"))["hits"][0])
    manual = recorded_result()
    assert theme.license_label(pixabay.license) == ("Разрешено", 0)
    assert theme.license_label(manual.license) == ("С атрибуцией", 1)
    assert theme.license_label(None) == ("Проверить", 1)
    assert manual.license.status == licenses.ATTRIBUTION


@pytest.mark.parametrize("name", [design.LIGHT, design.DARK])
def test_selection_and_badges_have_readable_contrast(name):
    from tests.core.test_design import AA_TEXT, contrast

    palette = design.PALETTES[name]
    assert contrast(palette.text_primary, palette.selection_bg) >= AA_TEXT
    for foreground, background in zip(palette.badge_text, palette.badge_bg, strict=True):
        assert contrast(foreground, background) >= AA_TEXT


def test_player_keeps_license_warning_visible_when_details_are_collapsed():
    result = recorded_result()
    panel = PlayerPanel()
    try:
        panel.show_result(result)
        assert panel.info.isHidden()
        assert not panel.license_hint.isHidden()
        assert "подтверждаете проверку" in panel.license_hint.text()
        assert "укажите автора" in panel.license_hint.text()
        assert panel.license_badge.text() == "С атрибуцией"
        panel.details_button.setChecked(True)
        assert not panel.info.isHidden()
        assert result.license.name in panel.info.text()
        result.license = replace(result.license, status=licenses.REVIEW)
        panel.details_button.setChecked(False)
        panel.show_result(result)
        assert panel.info.isHidden()
        assert "требует проверки" in panel.license_hint.text()
        assert panel.license_badge.text() == "Проверить"
    finally:
        panel.player.stop()
        panel.frames.stop()
        panel.close()


def test_switching_to_view_on_site_clears_previous_media_and_preview(tmp_path):
    panel = PlayerPanel()
    try:
        panel.player.setSource(QUrl.fromLocalFile(str(tmp_path / "old.mp4")))
        poster = QPixmap(design.THUMB_WIDTH, design.THUMB_HEIGHT)
        poster.fill(Qt.GlobalColor.red)
        panel.set_poster(poster)
        panel.show_result(recorded_result())
        assert panel.player.source().isEmpty()
        assert panel.poster.image.isNull()
        assert panel.media.currentWidget() is panel.site
        assert panel.play_button.isEnabled()
        assert panel.play_button.text() == "Повторить просмотр"
        assert not panel.position.isEnabled()
        assert panel.download_button.isEnabled()
        assert panel.quality.currentData().site
        assert panel.open_page.isEnabled()
    finally:
        panel.player.stop()
        panel.frames.stop()
        panel.close()


def test_theme_switch_keeps_selected_result_and_user_marks(data_dir):
    data_dir.mkdir()
    window = MainWindow(Settings(check_updates=False, link_check_days=0), processes.load_catalog())
    try:
        result = replace(recorded_result(), relevance=-1)
        vid = window.catalog.select(result, "lam_pouch", license_confirmed=False)
        window.search.add_results([result])
        window.search.results.setCurrentRow(0)
        item = window.search.results.currentItem()
        assert "Не по теме" not in item.data(Qt.ItemDataRole.UserRole + 2)
        off_topic = replace(result, source_id="off-topic")
        window.search.add_results([off_topic], off_topic=True)
        hidden_item = next(window.search.results.item(i) for i in range(window.search.results.count())
                           if window.search.results.item(i).data(Qt.ItemDataRole.UserRole).key == off_topic.key)
        assert "Не по теме" in hidden_item.data(Qt.ItemDataRole.UserRole + 2)
        before = window.catalog.get(vid)
        for choice in (design.DARK, design.LIGHT):
            window.settings.theme = choice
            window.apply_theme()
            assert window.search.results.currentItem() is item
            assert window.player.result is result
            assert "В каталоге" in item.data(Qt.ItemDataRole.UserRole + 2)
            after = window.catalog.get(vid)
            assert (after.status, after.license_confirmed, after.links) == (
                before.status, before.license_confirmed, before.links)
        assert window.minimumSizeHint().height() <= 720
    finally:
        window.close()
        window.catalog.close()
        window.cache.close()
        theme.apply(QApplication.instance(), design.LIGHT)


def test_main_window_assembles_tabs_menu_and_status_buttons(data_dir):
    data_dir.mkdir()
    window = MainWindow(Settings(check_updates=False, link_check_days=0), processes.load_catalog())
    try:
        assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == ["Поиск", "Пакетный поиск", "Каталог"]
        assert window.tabs.widget(0) is window.search and window.player is window.search.player
        menus = [action.text() for action in window.menuBar().actions()]
        assert menus == ["Файл", "Справка"]
        file_menu = window.menuBar().actions()[0].menu()
        titles = [a.text() for a in file_menu.actions()]
        assert "Импорт реестра CSV…" in titles and "Настройки…" in titles
        export = next(a.menu() for a in file_menu.actions() if a.text() == "Экспорт каталога")
        assert len(export.actions()) == len(window.exports.menu())
        window.search.message.emit("проверка")
        assert window.statusBar().currentMessage() == "проверка"
        for button in window.search.status_buttons():
            assert button.parent() is not None
    finally:
        window.close()
        window.catalog.close()
        window.cache.close()
