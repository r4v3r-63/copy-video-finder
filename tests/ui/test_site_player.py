"""Веб-просмотр остаётся в панели, без обращений к площадкам и внешнему браузеру."""

import pytest

from app.ui.widgets.site_player import SitePlayer, allowed_page


@pytest.mark.parametrize("url", ["https://vk.com/video-1_2", "https://rutube.ru/video/abc/", "http://127.0.0.1:1234/"])
def test_web_pages_allowed(url):
    assert allowed_page(url)


@pytest.mark.parametrize("url", ["file:///C:/Данные/settings.json", "javascript:alert(1)", "data:text/html,test",
                                 "mailto:test@example.org", "https://user:password@example.org/video",
                                 "https://example.org:bad/video", "https://[broken", "https://example.org/\nvideo",
                                 "ftp://example.org/video", "https://example.org\\video"])
def test_local_files_credentials_and_external_protocols_are_blocked(url):
    assert not allowed_page(url)


def test_original_page_loads_in_panel_and_is_stopped_before_next_video():
    panel = SitePlayer()
    try:
        assert panel.open("https://rutube.ru/video/first/")
        view = panel.view
        assert view.urls == ["https://rutube.ru/video/first/"]
        panel.stop()
        assert view.urls[-1] == "about:blank" and panel.current_url == ""
        assert panel.open("https://vk.com/video-1_2")
        assert panel.view is view and view.urls[-1] == "https://vk.com/video-1_2"
        view.loadFinished.emit(False)
        assert "Страница не загрузилась" in panel.status.text()
        panel.shutdown()
        assert view.closed
    finally:
        panel.close()


def test_invalid_page_does_not_start_web_engine():
    panel = SitePlayer()
    try:
        assert not panel.open("file:///C:/Данные/settings.json")
        assert panel.view is None
    finally:
        panel.close()
