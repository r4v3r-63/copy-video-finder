"""Pexels Videos API: https://www.pexels.com/api/documentation/

Лимиты: 200 запросов в час и 20 000 в месяц. Рядом с результатами
обязательна заметная ссылка на Pexels.
"""

import logging
import re
import time

import requests

from app import licenses
from app.cache import Cache
from app.sources.base import LicenseInfo, SearchFilters, VideoFile, VideoResult
from app.sources.http import HttpSource, quality_label

log = logging.getLogger(__name__)

API = "https://api.pexels.com/videos"
LICENSE_URL = "https://www.pexels.com/license/"
PER_PAGE = 20


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


class PexelsSource(HttpSource):
    name = "Pexels"
    homepage = "https://www.pexels.com"
    limits = [(200, 3600, "в час"), (20000, 30 * 24 * 3600, "в месяц")]

    def __init__(self, api_key: str, cache: Cache, session: requests.Session | None = None, now=time.time):
        super().__init__(cache, session, now)
        self.api_key = api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: dict) -> dict:
        return self._fetch(API + path, params, headers={"Authorization": self.api_key})

    def _after_response(self, resp) -> None:
        super()._after_response(resp)
        remaining = resp.headers.get("X-Ratelimit-Remaining")
        reset = resp.headers.get("X-Ratelimit-Reset")  # время сброса, Unix
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
