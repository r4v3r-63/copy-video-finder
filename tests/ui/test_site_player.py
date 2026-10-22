"""Веб-просмотр остаётся в панели, без обращений к площадкам и внешнему браузеру."""

from app.ui.widgets.site_player import SitePlayer


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
