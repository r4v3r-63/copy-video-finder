"""Яндекс Видео: ролики Rutube, VK Видео, Дзена, YouTube из выдачи (сохранённая страница)."""

import pytest

from app.core import licenses
from app.core.models import SearchFilters
from app.sources.base import RateLimitError
from app.sources.yandex_video import YandexVideoSource, blocked, parse_results
from tests.fakes import FIXTURES, FakeResponse, FakeSession


def fixture_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_results_from_many_sites():
    results = parse_results(fixture_text("yandex_video.html"))
    sites = {r.source for r in results}
    assert {"YouTube", "Rutube", "Дзен", "VK Видео"} <= sites
    for r in results:
        assert r.files == [] and r.page_url.startswith("https://") and r.license.status == licenses.REVIEW
        assert "\u0007" not in r.title and "&amp;" not in r.title
    youtube = next(r for r in results if r.source == "YouTube")
    assert len(youtube.source_id) == 11  # та же пара (площадка, ID), что из поиска по интернету
    vk = next(r for r in results if r.source == "VK Видео")
    assert vk.source_id == vk.page_url == "https://vk.com/video-222262532_456239058"
    assert vk.duration == 637 and vk.preview_url.startswith("https://avatars.mds.yandex.net/")


def test_captcha_rests_ten_minutes(tmp_path):
    from app.sources.cache import Cache
    from tests.fakes import Clock
    clock = Clock()
    cache = Cache(tmp_path / "c.sqlite", now=clock)
    src = YandexVideoSource(cache, session=FakeSession(FakeResponse(200, "<form action='/checkcaptcha'>")), now=clock)
    with pytest.raises(RateLimitError, match="не робот"):
        src.search("лазерная гравировка", SearchFilters())
    with pytest.raises(RateLimitError):
        src.search("переплёт", SearchFilters())
    assert len(src.session.calls) == 1  # блокировку не обходим
    assert blocked("<html>SmartCaptcha</html>", 200) and not blocked(fixture_text("yandex_video.html"), 200)
