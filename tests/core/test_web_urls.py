"""Правила адресов встроенного браузера проверяются без запуска Qt."""

import pytest

from app.core.web_urls import allowed_page


@pytest.mark.parametrize("url", ["https://vk.com/video-1_2", "https://rutube.ru/video/abc/", "http://127.0.0.1:1234/"])
def test_web_pages_allowed(url):
    assert allowed_page(url)


@pytest.mark.parametrize("url", ["file:///C:/Данные/settings.json", "javascript:alert(1)", "data:text/html,test",
                                 "mailto:test@example.org", "https://user:password@example.org/video",
                                 "https://example.org:bad/video", "https://[broken", "https://example.org/\nvideo",
                                 "ftp://example.org/video", "https://example.org\\video"])
def test_local_files_credentials_and_external_protocols_are_blocked(url):
    assert not allowed_page(url)
