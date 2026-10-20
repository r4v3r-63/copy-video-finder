"""VK Видео: вход в браузере (токен из адреса), поиск через VK API, ошибки VK."""

import pytest

from app import licenses
from app.cache import Cache
from app.sources.base import AuthError, RateLimitError, SearchFilters
from app.sources.vk import VkSource, auth_url, token_from_text
from tests.fakes import Clock, FakeResponse, FakeSession, fixture_text


def source(tmp_path, *responses, token="vk1.a.token"):
    clock = Clock()
    return VkSource(token, Cache(tmp_path / "c.sqlite", now=clock), session=FakeSession(*responses), now=clock)


def test_token_from_pasted_address():
    url = "https://oauth.vk.com/blank.html#access_token=vk1.a.ABCdef_123-xyz.long.token&expires_in=0&user_id=42"
    assert token_from_text(url) == "vk1.a.ABCdef_123-xyz.long.token"
    assert token_from_text("  vk1.a.ABCdef_123-xyz.long.token ") == "vk1.a.ABCdef_123-xyz.long.token"
    assert token_from_text("https://oauth.vk.com/blank.html#error=access_denied") == ""
    assert token_from_text("") == ""
    login = auth_url(" 51234567 ")
    assert login.startswith("https://oauth.vk.com/authorize?") and "client_id=51234567" in login
    assert "scope=video%2Coffline" in login and "response_type=token" in login


def test_search_results(tmp_path):
    src = source(tmp_path, FakeResponse(text=fixture_text("vk_video_search.json")))
    results = src.search("лазерная гравировка", SearchFilters())
    first = results[0]
    assert first.source == "VK Видео" and first.source_id == first.page_url == "https://vk.com/video-222262532_456239058"
    assert first.duration == 64 and (first.width, first.height) == (1920, 1080)
    assert first.preview_url == "https://sun9-1.userapi.com/a_320.jpg"  # не меньше 320 — и не огромное
    assert first.files == [] and first.license.status == licenses.REVIEW
    assert results[1].preview_url == "https://sun9-2.userapi.com/b.jpg" and results[1].width is None
    call = src.session.calls[0]
    assert call["url"] == "https://api.vk.com/method/video.search" and call["params"]["q"] == "лазерная гравировка"
    assert call["params"]["access_token"] == "vk1.a.token"


def test_errors_not_cached(tmp_path):
    expired = FakeResponse(text='{"error": {"error_code": 5, "error_msg": "User authorization failed"}}')
    src = source(tmp_path, expired, FakeResponse(text=fixture_text("vk_video_search.json")))
    with pytest.raises(AuthError, match="войдите заново"):
        src.search("переплёт", SearchFilters())
    assert len(src.search("переплёт", SearchFilters())) == 2  # ошибка не запомнилась в кэше
    many = source(tmp_path, FakeResponse(text='{"error": {"error_code": 6, "error_msg": "Too many requests"}}'))
    with pytest.raises(RateLimitError):
        many.search("ламинация", SearchFilters())


def test_without_login_not_configured(tmp_path):
    assert not source(tmp_path, token="").is_configured()


def test_settings_dialog_takes_token_from_address(data_dir):
    from app.settings import Settings
    from app.ui.settings_dialog import SettingsDialog
    settings = Settings()
    dialog = SettingsDialog(settings, None)
    dialog.vk_app_id.setText("51234567")
    dialog.vk_answer.setText("https://oauth.vk.com/blank.html#access_token=vk1.a.TOKEN_from_vk_123456&expires_in=0")
    assert "Сохранить" in dialog.vk_status.text()
    dialog.accept()
    assert settings.vk_token == "vk1.a.TOKEN_from_vk_123456" and settings.vk_app_id == "51234567"
