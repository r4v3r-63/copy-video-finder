"""Картинки и просмотр роликов с сайтов: ответы сохранены, сеть не используется."""

from dataclasses import replace

import pytest
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PyQt6.QtGui import QDesktopServices, QImage
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtNetwork import QNetworkReply

from app.core import licenses, processes
from app.sources.web import parse_page
from app.sources.yandex_video import parse_results
from app.storage.settings import Settings
from app.ui.main_window import MainWindow
from app.ui.widgets.player import PlayerPanel
from tests.fakes import fixture_text


def video(index=1, **fields):
    return replace(parse_page(fixture_text("manual_video.html"), f"https://example.org/video/{index}"),
                   preview_video=None, files=[], **fields)


@pytest.fixture
def window(data_dir, monkeypatch):
    data_dir.mkdir()
    instance = MainWindow(Settings(check_updates=False, link_check_days=0), processes.load_catalog())
    instance.requests = []
    monkeypatch.setattr(instance.search.network, "get", instance.requests.append)
    yield instance
    instance.close()
    instance.catalog.close()
    instance.cache.close()


class Reply:
    def __init__(self, request, fail=False):
        self._request, self.fail, self.deleted = request, fail, False

    def request(self):
        return self._request

    def error(self):
        return QNetworkReply.NetworkError.ContentAccessDenied if self.fail else QNetworkReply.NetworkError.NoError

    def readAll(self):
        data = QByteArray()
        output = QBuffer(data)
        output.open(QIODevice.OpenModeFlag.WriteOnly)
        image = QImage(32, 18, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.gray)
        assert image.save(output, "PNG")
        return data

    def deleteLater(self):
        self.deleted = True


def test_yandex_combines_separate_descriptions_and_posters():
    results = parse_results(fixture_text("yandex_preview_parts.html"))
    assert [r.preview_url for r in results] == [
        "https://images.example.org/card.jpg?size=large&v=1",
        "https://images.example.org/document.jpg",
        "https://images.example.org/run.jpg",
    ]
    assert results[0].description == "Работа печатной машины"
    assert all(not r.files and r.license.status == licenses.REVIEW for r in results)


def test_one_image_is_shared_by_all_waiting_and_later_cards(window):
    url = "https://images.example.org/shared.jpg"
    window.search.add_results([video(1, preview_url=url), video(2, preview_url=url)])
    window.search.results.setCurrentRow(0)
    assert len(window.requests) == 1
    response = Reply(window.requests[0])
    window.search._on_thumbnail(response)
    assert response.deleted
    assert all(not window.search.results.item(i).icon().isNull() for i in range(2))
    assert not window.player.poster.image.isNull()
    window.search.add_results([video(3, preview_url=url)])
    assert len(window.requests) == 1
    assert all(not window.search.results.item(i).icon().isNull() for i in range(3))


def test_failed_image_does_not_cause_retries_for_each_card(window):
    url = "https://images.example.org/unavailable.jpg"
    window.search.add_results([video(1, preview_url=url), video(2, preview_url=url)])
    window.search._on_thumbnail(Reply(window.requests[0], fail=True))
    window.search.add_results([video(3, preview_url=url)])
    assert len(window.requests) == 1
    for index in range(3):
        item = window.search.results.item(index)
        assert item.icon().isNull()
        assert item.data(Qt.ItemDataRole.UserRole + 4) == "Картинка недоступна"


def test_reply_from_cleared_search_does_not_touch_deleted_items(window):
    window.search.add_results([video(1, preview_url="https://images.example.org/old.jpg")])
    response = Reply(window.requests[0])
    window.search.results.clear()
    window.search._thumb_items.clear()
    window.search._thumb_images.clear()
    window.search._on_thumbnail(response)
    assert response.deleted and not window.search._thumb_images


def test_site_only_play_button_opens_original_page(monkeypatch):
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    panel = PlayerPanel()
    try:
        result = video(preview_url=None)
        panel.show_result(result)
        assert panel.play_button.isEnabled() and panel.play_button.text() == "Смотреть на сайте"
        assert panel.position.isHidden() and panel.time_label.isHidden()
        assert panel.quality.isHidden()
        assert panel.player.source().isEmpty()
        panel.play_button.click()
        assert opened == [result.page_url]
        panel.show_result(None)
        assert not panel.play_button.isEnabled()
    finally:
        panel.player.stop()
        panel.frames.stop()
        panel.close()


def test_direct_preview_still_plays_and_error_offers_site(monkeypatch):
    opened, played, paused, sources = [], [], [], []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    panel = PlayerPanel()
    monkeypatch.setattr(panel.player, "setSource", lambda url: sources.append(url.toString()))
    monkeypatch.setattr(panel.player, "play", lambda: played.append(True))
    monkeypatch.setattr(panel.player, "pause", lambda: paused.append(True))
    try:
        result = parse_page(fixture_text("manual_video.html"), "https://example.org/video/1")
        panel.show_result(result)
        assert sources[-1] == result.preview_video and played == [True]
        assert panel.play_button.text() == "" and panel.position.isEnabled()
        monkeypatch.setattr(panel.player, "playbackState", lambda: QMediaPlayer.PlaybackState.PlayingState)
        panel.play_button.click()
        assert paused == [True]
        panel._on_error(None, "Файл недоступен")
        assert panel.play_button.text() == "Смотреть на сайте" and panel.play_button.isEnabled()
        panel.play_button.click()
        assert opened == [result.page_url] and played == [True]
    finally:
        panel.player.stop()
        panel.frames.stop()
        panel.close()


def test_catalog_selection_requires_content_confirmation(monkeypatch):
    from app.core import orientation

    panel = PlayerPanel()
    monkeypatch.setattr(panel.player, "setSource", lambda url: None)
    monkeypatch.setattr(panel.player, "play", lambda: None)
    try:
        result = video(preview_url=None)
        panel.show_result(result)
        assert not panel.select_button.isEnabled()
        assert not panel.horizontal.isChecked()
        panel.horizontal.setChecked(True)
        assert panel.select_button.isEnabled()
        assert result.content_orientation == orientation.LANDSCAPE
        panel.horizontal.setChecked(False)
        assert not panel.select_button.isEnabled() and not result.content_orientation
        panel._checked_orientation(result.key, orientation.PORTRAIT)
        assert not panel.select_button.isEnabled() and not panel.horizontal.isEnabled()
        panel.show_result(video(2, preview_url=None))
        panel._checked_orientation(result.key, orientation.LANDSCAPE)
        assert not panel.horizontal.isChecked()  # поздние кадры прежнего ролика
    finally:
        panel.frames.stop()
        panel.player.stop()
        panel.close()


def test_padded_portrait_thumbnail_is_hidden(window):
    from app.core import orientation
    from tests.fakes import FIXTURES

    class PortraitReply(Reply):
        def readAll(self):
            return QByteArray((FIXTURES / "orientation" / "portrait_logo.png").read_bytes())

    result = video(preview_url="https://images.example.org/portrait.jpg")
    window.search.add_results([result])
    window.search._on_thumbnail(PortraitReply(window.requests[0]))
    assert result.content_orientation == orientation.PORTRAIT
    assert window.search.results.item(0).isHidden()


def test_saved_content_confirmation_is_reused_when_selecting_card(window):
    from app.core import orientation

    result = video(preview_url=None)
    vid = window.catalog.add(result)
    window.catalog.set_orientation(vid, orientation.LANDSCAPE)
    window.search.add_results([result])
    window.search.results.setCurrentRow(0)
    assert window.player.horizontal.isChecked()
    assert result.content_orientation == orientation.LANDSCAPE
