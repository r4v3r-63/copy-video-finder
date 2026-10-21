"""Pixabay Videos API: https://pixabay.com/api/docs/#api_search_videos

Лимит около 100 запросов в минуту, ответы кэшируются 24 часа. Пользователю
показывается источник. Массовое автоматическое скачивание запрещено.
"""

import logging
import time

import requests

from app.core import licenses
from app.core.models import LicenseInfo, SearchFilters, VideoFile, VideoResult
from app.sources.base import AuthError, NotFoundError, SourceError
from app.sources.cache import Cache
from app.sources.http import HttpSource, quality_label

log = logging.getLogger(__name__)

API = "https://pixabay.com/api/videos/"
LICENSE_NAME = "Pixabay Content License"
LICENSE_URL = "https://pixabay.com/service/license-summary/"
PER_PAGE = 20
SIZES = ("large", "medium", "small", "tiny")


def parse_hit(hit: dict) -> VideoResult:
    videos = hit.get("videos") or {}
    files = []
    for size in SIZES:
        v = videos.get(size) or {}
        if v.get("url"):
            files.append(VideoFile(quality=quality_label(v.get("height")), url=v["url"],
                                   width=v.get("width"), height=v.get("height"), mime="video/mp4", size=v.get("size")))
    # Для сетки достаточно превью среднего размера.
    thumbnail = next((videos[s]["thumbnail"] for s in ("medium", "small", "large")
                      if (videos.get(s) or {}).get("thumbnail")), None)
    files.sort(key=lambda f: f.height or 0, reverse=True)
    biggest = files[0] if files else None
    user, user_id = hit.get("user", ""), hit.get("user_id")
    result = VideoResult(
        source=PixabaySource.name,
        source_id=str(hit["id"]),
        title=hit.get("tags", ""),
        description=hit.get("tags", ""),
        video_type=hit.get("type", ""),
        preview_url=thumbnail,
        duration=hit.get("duration"),
        width=biggest.width if biggest else None,
        height=biggest.height if biggest else None,
        has_audio=None,  # API Pixabay не сообщает о звуковой дорожке
        page_url=hit.get("pageURL", ""),
        files=files,
        author=user,
        # Ссылка на профиль автора (не на файл): https://pixabay.com/users/<имя>-<id>/
        author_url=f"https://pixabay.com/users/{user}-{user_id}/" if user and user_id else None,
    )
    result.license = PixabaySource.license_for(result)
    return result


class PixabaySource(HttpSource):
    name = "Pixabay"
    homepage = "https://pixabay.com"
    limits = [(100, 60, "в минуту")]

    def __init__(self, api_key: str, cache: Cache, session: requests.Session | None = None, now=time.time):
        super().__init__(cache, session, now)
        self.api_key = api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _get(self, params: dict) -> dict:
        return self._fetch(API, {"key": self.api_key, **params}, secret_params=("key",))

    def _after_response(self, resp) -> None:
        super()._after_response(resp)
        remaining = resp.headers.get("X-RateLimit-Remaining")
        reset = resp.headers.get("X-RateLimit-Reset")  # секунд до сброса
        if remaining is not None and reset and str(remaining).strip() == "0":
            try:
                self.cache.block_until(self.name, self._now() + float(reset))
            except ValueError:
                log.warning("Непонятный X-RateLimit-Reset: %s", reset)

    def _status_error(self, resp) -> SourceError | None:
        if resp.status_code == 400 and "key" in resp.text.lower():
            return AuthError(self.name, "Pixabay: ключ API отклонён — проверьте его в настройках.")
        return super()._status_error(resp)

    def search(self, query: str, filters: SearchFilters, page: int = 1, lang: str = "en") -> list[VideoResult]:
        params = {
            "q": query[:100],  # ограничение API
            "lang": lang if lang in ("ru", "de", "fr") else "en",
            "video_type": "film",
            "safesearch": "true",
            "per_page": PER_PAGE,
            "page": page,
        }
        data = self._get(params)
        return [parse_hit(h) for h in data.get("hits", [])]

    def get_details(self, source_id: str) -> VideoResult:
        data = self._get({"id": source_id})
        hits = data.get("hits", [])
        if not hits:
            raise NotFoundError(self.name, "Pixabay: ролик удалён с площадки.")
        return parse_hit(hits[0])

    @staticmethod
    def license_for(result: VideoResult) -> LicenseInfo:
        status, note = licenses.evaluate(LICENSE_NAME)
        return LicenseInfo(name=LICENSE_NAME, url=LICENSE_URL, status=status, note=note)

    def get_license(self, result: VideoResult) -> LicenseInfo:
        return self.license_for(result)
