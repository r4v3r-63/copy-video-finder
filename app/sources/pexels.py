"""Pexels Videos API: https://www.pexels.com/api/documentation/

Лимиты: 200 запросов в час и 20 000 в месяц. Рядом с результатами
обязательна заметная ссылка на Pexels.
"""

import json
import logging
import re
import time
from urllib.parse import urlencode

import requests

from app import licenses
from app.cache import CACHE_TTL, Cache, RateLimiter
from app.sources.base import (
    AuthError,
    LicenseInfo,
    NetworkError,
    NotFoundError,
    RateLimitError,
    SearchFilters,
    Source,
    SourceError,
    VideoFile,
    VideoResult,
)

log = logging.getLogger(__name__)

API = "https://api.pexels.com/videos"
LICENSE_URL = "https://www.pexels.com/license/"
PER_PAGE = 20
LIMITS = [(200, 3600, "в час"), (20000, 30 * 24 * 3600, "в месяц")]


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


def _title_from_url(url: str) -> str:
    """Pexels не отдаёт название ролика; берём описание из адреса его страницы."""
    match = re.search(r"/video/([^/]+)-\d+/?$", url)
    if not match:
        return ""
    return match.group(1).replace("-", " ").strip().capitalize()


def parse_video(v: dict) -> VideoResult:
    files = [
        VideoFile(
            quality=quality_label(f.get("height")),
            url=f["link"],
            width=f.get("width"),
            height=f.get("height"),
            mime=f.get("file_type"),
        )
        for f in v.get("video_files", [])
        if f.get("link")
    ]
    files.sort(key=lambda f: (f.height or 0), reverse=True)
    user = v.get("user") or {}
    result = VideoResult(
        source=PexelsSource.name,
        source_id=str(v["id"]),
        title=_title_from_url(v.get("url", "")),
        description="",
        preview_url=v.get("image"),
        duration=v.get("duration"),
        width=v.get("width"),
        height=v.get("height"),
        has_audio=None,  # API Pexels не сообщает о звуковой дорожке
        page_url=v.get("url", ""),
        files=files,
        author=user.get("name", ""),
        author_url=user.get("url"),
    )
    result.license = PexelsSource.license_for(result)
    return result


class PexelsSource(Source):
    name = "Pexels"
    homepage = "https://www.pexels.com"

    def __init__(self, api_key: str, cache: Cache, session: requests.Session | None = None, now=time.time):
        self.api_key = api_key
        self.cache = cache
        self.session = session or requests.Session()
        self.limiter = RateLimiter(cache, self.name, LIMITS, now=now)
        self._now = now

    def _get(self, path: str, params: dict) -> dict:
        key = f"pexels:{path}?{urlencode(sorted(params.items()))}"
        cached = self.cache.get(key, CACHE_TTL)
        if cached is not None:
            return json.loads(cached)
        if not self.api_key:
            raise AuthError(self.name, "Ключ API Pexels не задан — укажите его в настройках.")
        wait = self.limiter.wait_seconds()
        if wait:
            seconds, label = wait
            raise RateLimitError(
                self.name, f"Исчерпан лимит Pexels ({label}). Повторите через {max(int(seconds // 60), 1)} мин."
            )
        self.limiter.record()
        try:
            resp = self.session.get(
                API + path, params=params, headers={"Authorization": self.api_key}, timeout=15
            )
        except requests.RequestException as exc:
            raise NetworkError(self.name, f"Нет связи с Pexels: {exc}") from exc
        self._remember_limit(resp.headers)
        if resp.status_code in (401, 403):
            raise AuthError(self.name, "Pexels отклонил ключ API — проверьте его в настройках.")
        if resp.status_code == 404:
            raise NotFoundError(self.name, "Ролик удалён с Pexels.")
        if resp.status_code == 429:
            raise RateLimitError(self.name, "Pexels сообщил, что лимит запросов исчерпан.")
        if resp.status_code != 200:
            raise SourceError(self.name, f"Pexels вернул ошибку {resp.status_code}.")
        body = resp.text
        self.cache.put(key, body)
        return json.loads(body)

    def _remember_limit(self, headers) -> None:
        remaining = headers.get("X-Ratelimit-Remaining")
        reset = headers.get("X-Ratelimit-Reset")
        if remaining is not None and reset and str(remaining).strip() == "0":
            try:
                self.cache.block_until(self.name, float(reset))
            except ValueError:
                log.warning("Непонятный X-Ratelimit-Reset: %s", reset)

    def search(self, query: str, filters: SearchFilters, page: int = 1, lang: str = "en") -> list[VideoResult]:
        params = {
            "query": query,
            "per_page": PER_PAGE,
            "page": page,
            "locale": "ru-RU" if lang == "ru" else "en-US",
        }
        if filters.orientation in ("landscape", "portrait"):
            params["orientation"] = filters.orientation
        if filters.min_height >= 2160:
            params["size"] = "large"
        elif filters.min_height >= 1080:
            params["size"] = "medium"
        data = self._get("/search", params)
        return [parse_video(v) for v in data.get("videos", [])]

    def get_details(self, source_id: str) -> VideoResult:
        return parse_video(self._get(f"/videos/{source_id}", {}))

    @staticmethod
    def license_for(result: VideoResult) -> LicenseInfo:
        status, note = licenses.evaluate("Pexels License")
        return LicenseInfo(name="Pexels License", url=LICENSE_URL, status=status, note=note)

    def get_license(self, result: VideoResult) -> LicenseInfo:
        return self.license_for(result)
