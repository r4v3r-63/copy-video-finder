"""Общая часть площадок с HTTP API: кэш ответов, лимиты, понятные ошибки."""

import json
import logging
import threading
import time
from urllib.parse import urlencode

import requests

from app import __version__
from app.cache import CACHE_TTL, Cache, RateLimiter
from app.sources.base import AuthError, NetworkError, NotFoundError, RateLimitError, Source, SourceError

log = logging.getLogger(__name__)

USER_AGENT = f"CopyVideoFinder/{__version__} (https://github.com/r4v3r-63/copy-video-finder)"


class HttpSource(Source):
    # (число запросов, окно в секундах, подпись «в час») — лимиты площадки
    limits: list[tuple[int, float, str]] = []
    # Сколько запросов к площадке может идти одновременно.
    max_parallel = 2

    def __init__(self, cache: Cache, session: requests.Session | None = None, now=time.time):
        self.cache = cache
        self.session = session or requests.Session()
        if hasattr(self.session, "headers"):
            self.session.headers["User-Agent"] = USER_AGENT
        self.limiter = RateLimiter(cache, self.name, self.limits, now=now)
        self._now = now
        self._slots = threading.Semaphore(self.max_parallel)

    def missing_key_message(self) -> str:
        return f"Ключ API {self.name} не задан — укажите его в настройках."

    def _fetch(self, url: str, params: dict, headers: dict | None = None, secret_params: tuple = ()) -> dict:
        """GET с кэшем на 24 часа. Параметры из secret_params (ключи API) в ключ кэша не попадают."""
        public = {k: v for k, v in params.items() if k not in secret_params}
        cache_key = f"{self.name.lower()}:{url}?{urlencode(sorted(public.items()))}"
        cached = self.cache.get(cache_key, CACHE_TTL)
        if cached is not None:
            return json.loads(cached)
        if not self.is_configured():
            raise AuthError(self.name, self.missing_key_message())
        with self._slots:
            wait = self.limiter.wait_seconds()
            if wait:
                seconds, label = wait
                raise RateLimitError(self.name, f"Исчерпан лимит {self.name} ({label}). {_retry_text(seconds)}")
            self.limiter.record()
            try:
                resp = self.session.get(url, params=params, headers=headers or {}, timeout=20)
            except requests.RequestException as exc:
                raise NetworkError(self.name, f"Нет связи с {self.name}: {_short(exc)}") from exc
        self._after_response(resp)
        error = self._status_error(resp)
        if error:
            raise error
        try:
            data = json.loads(resp.text)
        except ValueError as exc:
            raise SourceError(self.name, f"{self.name}: непонятный ответ площадки.") from exc
        self.cache.put(cache_key, resp.text)
        return data

    def _after_response(self, resp) -> None:
        """Учесть заголовки лимитов площадки. Retry-After понимают все."""
        retry = resp.headers.get("Retry-After")
        if resp.status_code == 429 and retry:
            try:
                self.cache.block_until(self.name, self._now() + float(retry))
            except ValueError:
                pass

    def _status_error(self, resp) -> SourceError | None:
        code = resp.status_code
        if code == 200:
            return None
        if code in (401, 403):
            return AuthError(self.name, f"{self.name}: ключ API отклонён — проверьте его в настройках.")
        if code == 404:
            return NotFoundError(self.name, f"{self.name}: ролик удалён с площадки.")
        if code == 429:
            return RateLimitError(self.name, f"{self.name}: лимит запросов исчерпан. Повторите позже.")
        return SourceError(self.name, f"{self.name}: ошибка {code}.")


def _retry_text(seconds: float) -> str:
    if seconds < 90:
        return f"Повторите через {max(int(seconds), 1)} с."
    return f"Повторите через {int(seconds // 60)} мин."


def _short(exc: Exception) -> str:
    text = str(exc)
    return text if len(text) < 120 else text[:117] + "…"


def quality_label(height: int | None) -> str:
    if not height:
        return "?"
    if height >= 2160:
        return "4K"
    if height >= 1080:
        return "Full HD"
    if height >= 720:
        return "HD"
    return "SD"
