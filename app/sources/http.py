"""Общая часть HTTP-площадок (API и страницы сайтов): кэш ответов, лимиты, понятные ошибки."""

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
        # Сколько секунд можно подождать, пока лимит освободится, вместо ошибки. В обычном поиске 0 —
        # пользователь сразу видит «повторите через…»; пакетный поиск ждёт поминутные лимиты сам.
        self.max_wait = 0.0
        self.stop = threading.Event()  # прервать ожидание лимита

    def missing_key_message(self) -> str:
        return f"Ключ API {self.name} не задан — укажите его в настройках."

    def _fetch(self, url: str, params: dict, headers: dict | None = None, secret_params: tuple = ()) -> dict:
        """GET с кэшем на 24 часа. Параметры из secret_params (ключи API) в ключ кэша не попадают."""
        text = self._fetch_text(url, params, headers, secret_params)
        try:
            return json.loads(text)
        except ValueError as exc:
            raise SourceError(self.name, f"{self.name}: непонятный ответ площадки.") from exc

    def _fetch_text(self, url: str, params: dict | None = None, headers: dict | None = None,
                    secret_params: tuple = (), method: str = "GET", check=None) -> str:
        """Запрос с кэшем на 24 часа и учётом лимитов; ответ — текст (JSON или HTML-страница).

        check(resp) может вернуть ошибку площадки для ответа с кодом 200 (например, капчу поисковика).
        """
        params = params or {}
        public = {k: v for k, v in params.items() if k not in secret_params}
        verb = "" if method == "GET" else f"{method}:"  # ключи GET — как раньше, кэш пользователя цел
        cache_key = f"{self.name.lower()}:{verb}{url}?{urlencode(sorted(public.items()))}"
        cached = self.cache.get(cache_key, CACHE_TTL)
        if cached is not None:
            return cached
        if not self.is_configured():
            raise AuthError(self.name, self.missing_key_message())
        while True:
            with self._slots:
                wait = self.limiter.wait_seconds()
                if not wait:
                    self.limiter.record()
                    try:
                        if method == "POST":
                            resp = self.session.post(url, data=params, headers=headers or {}, timeout=20)
                        else:
                            resp = self.session.get(url, params=params, headers=headers or {}, timeout=20)
                    except requests.RequestException as exc:
                        raise NetworkError(self.name, f"Нет связи с {self.name}: {_short(exc)}") from exc
            if wait:
                self._wait_for_limit(*wait)  # лимит не обходится: запрос уходит только после ожидания
                continue
            self._after_response(resp)
            if resp.status_code == 429 and self.max_wait and self.limiter.wait_seconds():
                continue  # площадка назвала время (Retry-After) — подождём его
            break
        error = (check(resp) if check else None) or self._status_error(resp)
        if error:
            raise error
        self.cache.put(cache_key, resp.text)
        return resp.text

    def _wait_for_limit(self, seconds: float, label: str) -> None:
        """Подождать, пока лимит освободится, или сообщить об исчерпанном лимите."""
        if seconds > self.max_wait or self.stop.wait(seconds + 0.5):
            raise RateLimitError(self.name, f"Исчерпан лимит {self.name} ({label}). {_retry_text(seconds)}")

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
