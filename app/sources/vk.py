"""VK Видео через официальный VK API (video.search) — по токену пользователя.

Поиск видео VK отдаёт только с ключом доступа пользователя. Пароль программа не видит:
пользователь входит на сайте VK ID в своём браузере (OAuth 2.1 с PKCE, `Login`), а в программу
вставляет адрес страницы, куда VK перенаправил после входа, — из него берётся одноразовый код,
который программа обменивает на ключ (`exchange`). Ключ живёт около часа и продлевается сам
по ключу обновления (`refresh`). Всё хранится только в «Данные/settings.json»
(решение пользователя 20.10.2026). Старый вход oauth.vk.com для новых приложений VK ID не работает
(«Security Error»), поэтому — VK ID.

Файлов роликов VK API для чужих видео не отдаёт — ролики смотрят на сайте. Лицензия —
условия VK, все права у автора: «Требует проверки».
"""

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from app import licenses
from app.cache import Cache
from app.sources.base import (
    AuthError,
    LicenseInfo,
    NotFoundError,
    RateLimitError,
    SearchFilters,
    SourceError,
    VideoResult,
)
from app.sources.http import HttpSource

API = "https://api.vk.com/method"
VERSION = "5.199"
VKID_AUTH = "https://id.vk.com/authorize"
VKID_TOKEN = "https://id.vk.com/oauth2/auth"
REDIRECT = "https://oauth.vk.com/blank.html"  # доверенный Redirect URL в настройках приложения VK ID
PER_PAGE = 50
NAME = "VK Видео"  # как у роликов VK из выдачи Яндекс Видео — одна площадка, без дублей
REFRESH_BEFORE = 120  # секунд до истечения ключа — продлеваем заранее


class VkLoginError(Exception):
    pass


@dataclass
class Tokens:
    access: str
    refresh: str = ""
    device_id: str = ""
    expires_at: float = 0.0  # 0 — без срока (старый ключ, вставленный вручную)


@dataclass
class Login:
    """Один вход: проверочная строка PKCE и state живут, пока открыт диалог настроек."""
    app_id: str
    verifier: str
    state: str

    @classmethod
    def start(cls, app_id: str) -> "Login":
        return cls(app_id.strip(), secrets.token_urlsafe(48), secrets.token_urlsafe(24))

    @property
    def url(self) -> str:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b"=").decode()
        return VKID_AUTH + "?" + urlencode({"response_type": "code", "client_id": self.app_id, "scope": "video",
                                            "redirect_uri": REDIRECT, "state": self.state,
                                            "code_challenge": challenge, "code_challenge_method": "S256"})


def parse_redirect(text: str) -> dict[str, str]:
    """Параметры из вставленного адреса после входа (в query или после #): code, state, device_id…"""
    text = (text or "").strip()
    parts = urlparse(text)
    params = {}
    for chunk in (parts.query, parts.fragment):
        params.update({k: v[0] for k, v in parse_qs(chunk).items() if v})
    return params


def token_from_text(text: str) -> str:
    """Готовый ключ из адреса «…#access_token=…» или сам ключ (если его вставили вручную)."""
    text = (text or "").strip()
    if "access_token=" in text:
        return parse_redirect(text).get("access_token", "")
    return text if re.fullmatch(r"[A-Za-z0-9._-]{20,}", text) else ""


def has_code(text: str) -> bool:
    params = parse_redirect(text)
    return bool(params.get("code") and params.get("device_id"))


def _tokens(data: dict, device_id: str, now: float) -> Tokens:
    if not isinstance(data, dict) or not data.get("access_token"):
        message = (data or {}).get("error_description") or (data or {}).get("error") or "VK не выдал ключ."
        raise VkLoginError(str(message))
    expires = data.get("expires_in")
    return Tokens(access=data["access_token"], refresh=data.get("refresh_token") or "", device_id=device_id,
                  expires_at=now + float(expires) if expires else 0.0)


def exchange(login: Login, pasted: str, session=None, now=time.time) -> Tokens:
    """Код из адреса после входа → ключи (access и refresh)."""
    params = parse_redirect(pasted)
    if not params.get("code") or not params.get("device_id"):
        raise VkLoginError("В адресе нет кода входа — скопируйте адрес страницы целиком.")
    if params.get("state") and params["state"] != login.state:
        raise VkLoginError("Адрес от другого входа — нажмите «Войти в VK» ещё раз.")
    form = {"grant_type": "authorization_code", "code": params["code"], "code_verifier": login.verifier,
            "client_id": login.app_id, "device_id": params["device_id"], "redirect_uri": REDIRECT,
            "state": login.state}
    try:
        resp = (session or requests).post(VKID_TOKEN, data=form, timeout=20)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise VkLoginError(f"Не удалось связаться с VK: {exc}") from exc
    return _tokens(data, params["device_id"], now())


def refresh(app_id: str, tokens: Tokens, session=None, now=time.time) -> Tokens:
    """Продлить ключ по ключу обновления (VK выдаёт новый ключ обновления — старый больше не годится)."""
    form = {"grant_type": "refresh_token", "refresh_token": tokens.refresh, "client_id": app_id,
            "device_id": tokens.device_id, "state": secrets.token_urlsafe(16)}
    try:
        data = (session or requests).post(VKID_TOKEN, data=form, timeout=20).json()
    except (requests.RequestException, ValueError) as exc:
        raise VkLoginError(f"Не удалось связаться с VK: {exc}") from exc
    return _tokens(data, tokens.device_id, now())


def page_url(owner_id: int, video_id: int) -> str:
    return f"https://vk.com/video{owner_id}_{video_id}"


def _preview(v: dict) -> str | None:
    images = [i for i in (v.get("image") or []) if isinstance(i, dict) and i.get("url")]
    if not images:
        return None
    fit = [i for i in images if (i.get("width") or 0) >= 320]
    def width(i):
        return i.get("width") or 0
    return (min(fit, key=width) if fit else max(images, key=width))["url"]


def parse_video(v: dict) -> VideoResult | None:
    if not isinstance(v, dict) or "id" not in v or "owner_id" not in v:
        return None
    url = page_url(v["owner_id"], v["id"])
    width, height = v.get("width"), v.get("height")
    return VideoResult(
        source=NAME, source_id=url, title=str(v.get("title") or "")[:200],
        description=str(v.get("description") or "")[:500], preview_url=_preview(v),
        duration=float(v["duration"]) if isinstance(v.get("duration"), (int, float)) else None,
        width=width if isinstance(width, int) else None, height=height if isinstance(height, int) else None,
        has_audio=None, page_url=url, files=[], author="",
        license=LicenseInfo(name="Условия VK", url="https://vk.com/terms", status=licenses.REVIEW,
                            note="Все права у автора, если в описании ролика не сказано иное. "
                                 "Скачать нельзя — смотрите на VK."),
    )


def api_error(name: str, text: str) -> SourceError | None:
    """Ошибка VK API из ответа (приходит с кодом 200) или None."""
    try:
        error = json.loads(text).get("error")
    except (ValueError, AttributeError):
        return None
    if not isinstance(error, dict):
        return None
    code, message = error.get("error_code"), error.get("error_msg", "")
    if code in (5, 1116):
        return AuthError(name, "Вход в VK устарел или отозван — войдите заново в «Файл → Настройки».")
    if code in (6, 9, 29):
        return RateLimitError(name, "VK временно ограничил число запросов. Повторите позже.")
    if code in (15, 18, 100, 104):
        return NotFoundError(name, f"VK: {message}")
    return SourceError(name, f"VK: {message} (код {code}).")


class VkSource(HttpSource):
    name = NAME
    homepage = "https://vkvideo.ru"
    languages = frozenset({"ru", "en"})
    max_level = 2
    max_parallel = 1
    topic_in_description = True
    limits = [(3, 1.0, "в секунду"), (5000, 86400, "в сутки")]  # лимиты VK API для пользовательского ключа

    def __init__(self, token: str, cache: Cache, session=None, now=time.time, app_id: str = "",
                 refresh_token: str = "", device_id: str = "", expires_at: float = 0.0, on_tokens=None):
        super().__init__(cache, session, now=now)
        self.token = token or ""
        self.app_id = app_id
        self.tokens = Tokens(self.token, refresh_token or "", device_id or "", expires_at or 0.0)
        self.on_tokens = on_tokens  # сохранить продлённые ключи в настройках
        self._refresh_lock = threading.Lock()

    def is_configured(self) -> bool:
        return bool(self.token)

    def _can_refresh(self) -> bool:
        return bool(self.app_id and self.tokens.refresh and self.tokens.device_id)

    def _renew(self) -> None:
        with self._refresh_lock:
            try:
                self.tokens = refresh(self.app_id, self.tokens, self.session, self._now)
            except VkLoginError as exc:
                raise AuthError(self.name, f"Вход в VK устарел ({exc}) — войдите заново в «Файл → Настройки».") from exc
            self.token = self.tokens.access
            if self.on_tokens:
                self.on_tokens(self.tokens)

    def missing_key_message(self) -> str:
        return "Войдите в VK в «Файл → Настройки», чтобы искать в VK Видео."

    def _call(self, method: str, params: dict) -> dict:
        if self._can_refresh() and self.tokens.expires_at and self.tokens.expires_at - self._now() < REFRESH_BEFORE:
            self._renew()
        try:
            return self._call_once(method, params)
        except AuthError:
            if not self._can_refresh():
                raise
            self._renew()  # ключ отозван или истёк раньше срока — один раз продлеваем и повторяем
            return self._call_once(method, params)

    def _call_once(self, method: str, params: dict) -> dict:
        # Ошибку VK присылает с кодом 200 — проверяем до кэша, чтобы она не запомнилась на сутки.
        text = self._fetch_text(f"{API}/{method}", {**params, "v": VERSION, "access_token": self.token},
                                secret_params=("access_token",), check=lambda resp: api_error(self.name, resp.text))
        try:
            return json.loads(text).get("response") or {}
        except ValueError as exc:
            raise SourceError(self.name, "VK ответил непонятно.") from exc

    def search(self, query: str, filters: SearchFilters, page: int = 1, lang: str = "ru") -> list[VideoResult]:
        data = self._call("video.search", {"q": query, "sort": 2, "adult": 0, "count": PER_PAGE,
                                           "offset": (page - 1) * PER_PAGE})
        return [r for r in (parse_video(v) for v in data.get("items") or []) if r is not None]

    def get_details(self, source_id: str) -> VideoResult:
        m = re.search(r"video(-?\d+_\d+)", source_id)
        if not m:
            raise NotFoundError(self.name, "Ссылка не на ролик VK.")
        items = self._call("video.get", {"videos": m.group(1)}).get("items") or []
        result = parse_video(items[0]) if items else None
        if result is None:
            raise NotFoundError(self.name, "Ролик VK не найден.")
        return result

    def get_license(self, result: VideoResult) -> LicenseInfo:
        return result.license
