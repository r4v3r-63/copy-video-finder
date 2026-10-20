"""VK Видео: вход в браузере (токен из адреса), поиск через VK API, ошибки VK."""

import pytest

from app import licenses
from app.cache import Cache
from app.sources import vk
from app.sources.base import AuthError, RateLimitError, SearchFilters
from app.sources.vk import VkSource, token_from_text
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


def test_vk_id_login_url_has_pkce():
    import base64
    import hashlib
    from urllib.parse import parse_qs, urlparse
    login = vk.Login.start(" 54837826 ")
    params = {k: v[0] for k, v in parse_qs(urlparse(login.url).query).items()}
    assert login.url.startswith("https://id.vk.com/authorize?")
    assert params["client_id"] == "54837826" and params["response_type"] == "code" and params["scope"] == "video"
    assert params["redirect_uri"] == "https://oauth.vk.com/blank.html" and params["state"] == login.state
    expected = base64.urlsafe_b64encode(hashlib.sha256(login.verifier.encode()).digest()).rstrip(b"=").decode()
    assert params["code_challenge"] == expected and params["code_challenge_method"] == "S256"


class PostSession(FakeSession):
    def post(self, url, data=None, timeout=None):
        self.calls.append({"url": url, "params": dict(data or {})})
        return self.responses.pop(0)


def test_exchange_code_for_tokens():
    login = vk.Login("54837826", "verifier-123", "state-abc")
    pasted = "https://oauth.vk.com/blank.html?code=CODE1&expires_in=600&device_id=DEV9&state=state-abc&type=code_v2"
    assert vk.has_code(pasted)
    session = PostSession(FakeResponse(text='{"access_token": "vk2.a.ACCESS", "refresh_token": "vk2.a.REFRESH", '
                                            '"expires_in": 3600, "user_id": 1}'))
    tokens = vk.exchange(login, pasted, session, now=lambda: 1000.0)
    assert (tokens.access, tokens.refresh, tokens.device_id, tokens.expires_at) == \
        ("vk2.a.ACCESS", "vk2.a.REFRESH", "DEV9", 4600.0)
    form = session.calls[0]["params"]
    assert session.calls[0]["url"] == "https://id.vk.com/oauth2/auth"
    assert form["grant_type"] == "authorization_code" and form["code_verifier"] == "verifier-123"
    assert form["code"] == "CODE1" and form["device_id"] == "DEV9"
    with pytest.raises(vk.VkLoginError, match="другого входа"):
        vk.exchange(login, pasted.replace("state-abc", "other"), session)
    bad = PostSession(FakeResponse(text='{"error": "invalid_grant", "error_description": "code expired"}'))
    with pytest.raises(vk.VkLoginError, match="code expired"):
        vk.exchange(login, pasted, bad)


def test_expired_key_renewed_and_saved(tmp_path):
    saved = []
    clock = Clock()
    session = PostSession(FakeResponse(text='{"access_token": "NEW", "refresh_token": "R2", "expires_in": 3600}'),
                          FakeResponse(text=fixture_text("vk_video_search.json")))
    src = VkSource("OLD", Cache(tmp_path / "c.sqlite", now=clock), session=session, now=clock, app_id="54837826",
                   refresh_token="R1", device_id="DEV9", expires_at=clock() + 30, on_tokens=saved.append)
    assert len(src.search("ламинация", SearchFilters())) == 2
    renew = session.calls[0]["params"]
    assert renew["grant_type"] == "refresh_token" and renew["refresh_token"] == "R1"
    assert session.calls[1]["params"]["access_token"] == "NEW"
    assert saved and saved[0].refresh == "R2"  # новый ключ обновления сохранён — старый больше не годится


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


def test_settings_dialog_vk_id_login(data_dir, monkeypatch):
    from app.settings import Settings
    from app.ui.settings_dialog import SettingsDialog
    monkeypatch.setattr("app.ui.settings_dialog.QDesktopServices.openUrl", lambda url: True)
    monkeypatch.setattr(vk, "exchange", lambda login, pasted: vk.Tokens("ACC", "REF", "DEV", 99.0))
    settings = Settings()
    dialog = SettingsDialog(settings, None)
    dialog.vk_app_id.setText("54837826")
    dialog._vk_open_login()
    dialog.vk_answer.setText(f"https://oauth.vk.com/blank.html?code=C&device_id=DEV&state={dialog.vk_pending.state}")
    assert "Сохранить" in dialog.vk_status.text()
    dialog.accept()
    assert (settings.vk_token, settings.vk_refresh_token, settings.vk_device_id) == ("ACC", "REF", "DEV")
    assert settings.vk_app_id == "54837826"
