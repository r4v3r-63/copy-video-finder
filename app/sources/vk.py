"""VK Видео через официальный VK API (video.search) — по токену пользователя.

Поиск видео VK отдаёт только с ключом доступа пользователя. Пароль программа не видит:
пользователь входит на сайте VK в своём браузере (`auth_url`), а в программу вставляет
адрес страницы, куда VK перенаправил после входа, — из него берётся токен (`token_from_text`).
Токен хранится только в «Данные/settings.json» (решение пользователя 20.10.2026).

Файлов роликов VK API для чужих видео не отдаёт — ролики смотрят на сайте. Лицензия —
условия VK, все права у автора: «Требует проверки».
"""

import json
import re
import time
from urllib.parse import parse_qs, urlencode, urlparse

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
AUTH_URL = "https://oauth.vk.com/authorize"
REDIRECT = "https://oauth.vk.com/blank.html"
PER_PAGE = 50
NAME = "VK Видео"  # как у роликов VK из выдачи Яндекс Видео — одна площадка, без дублей


def auth_url(app_id: str) -> str:
    """Страница входа VK: после входа браузер откроет blank.html с токеном в адресе."""
    return AUTH_URL + "?" + urlencode({"client_id": app_id.strip(), "display": "page", "redirect_uri": REDIRECT,
                                       "scope": "video,offline", "response_type": "token", "v": VERSION})


def token_from_text(text: str) -> str:
    """Токен из вставленного адреса «…blank.html#access_token=…&expires_in=0…» или сам токен."""
    text = (text or "").strip()
    if "access_token=" in text:
        fragment = urlparse(text).fragment or urlparse(text).query or text
        return (parse_qs(fragment).get("access_token") or [""])[0]
    return text if re.fullmatch(r"[A-Za-z0-9._-]{20,}", text) else ""


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

    def __init__(self, token: str, cache: Cache, session=None, now=time.time):
        super().__init__(cache, session, now=now)
        self.token = token or ""

    def is_configured(self) -> bool:
        return bool(self.token)

    def missing_key_message(self) -> str:
        return "Войдите в VK в «Файл → Настройки», чтобы искать в VK Видео."

    def _call(self, method: str, params: dict) -> dict:
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
