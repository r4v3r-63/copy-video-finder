"""Coverr API: https://api.coverr.co/docs/videos/

Лицензия Coverr: коммерческое использование и монтаж без атрибуции; запрещены
перепродажа роликов и обучение ИИ. Бесплатный ключ (статус Demo) — 50 запросов
в час. Поиск только на английском. Скачивание — только по ссылке mp4_download:
так Coverr учитывает скачивания (для просмотра — mp4_preview).
"""

import time

import requests

from app import licenses
from app.cache import Cache
from app.sources.base import LicenseInfo, NotFoundError, SearchFilters, VideoFile, VideoResult
from app.sources.http import HttpSource, quality_label

API = "https://api.coverr.co"
LICENSE_NAME = "Coverr License"
LICENSE_URL = "https://coverr.co/license"
PER_PAGE = 20


def parse_video(v: dict) -> VideoResult:
    urls = v.get("urls") or {}
    width, height = v.get("max_width") or None, v.get("max_height") or None
    files = []
    if urls.get("mp4_download"):
        short = min(width, height) if width and height else height
        files.append(VideoFile(quality=quality_label(short), url=urls["mp4_download"],
                               width=width, height=height, mime="video/mp4"))
    tags = ", ".join(v.get("tags") or [])
    title = v.get("title") or ""
    result = VideoResult(
        source=CoverrSource.name,
        source_id=str(v["id"]),
        title=", ".join(t for t in (title, tags) if t),
        description=v.get("description") or "",
        preview_url=v.get("thumbnail") or v.get("poster"),
        duration=v.get("duration"),
        width=width,
        height=height,
        has_audio=None,  # API Coverr не сообщает о звуковой дорожке
        # Адреса страницы ролика в API нет; ведём на сайт площадки.
        page_url=v.get("url") or CoverrSource.homepage,
        files=files,
        author="Coverr",
        author_url=CoverrSource.homepage,
        preview_video=urls.get("mp4_preview") or None,
    )
    result.license = CoverrSource.license_for(result)
    return result


class CoverrSource(HttpSource):
    name = "Coverr"
    homepage = "https://coverr.co"
    limits = [(50, 3600, "в час")]  # статус Demo; Production — 2000 в час по платной подписке
    languages = frozenset({"en"})

    def __init__(self, api_key: str, cache: Cache, session: requests.Session | None = None, now=time.time):
        super().__init__(cache, session, now)
        self.api_key = api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: dict) -> dict:
        return self._fetch(API + path, params, headers={"Authorization": f"Bearer {self.api_key}"})

    def search(self, query: str, filters: SearchFilters, page: int = 1, lang: str = "en") -> list[VideoResult]:
        data = self._get("/videos", {"query": query, "page": page - 1, "page_size": PER_PAGE, "urls": "true"})
        hits = data.get("hits") if isinstance(data, dict) else data
        return [parse_video(v) for v in hits or []]

    def get_details(self, source_id: str) -> VideoResult:
        data = self._get(f"/videos/{source_id}", {})
        if not data or not data.get("id"):
            raise NotFoundError(self.name, "Coverr: ролик удалён с площадки.")
        return parse_video(data)

    @staticmethod
    def license_for(result: VideoResult) -> LicenseInfo:
        status, _ = licenses.evaluate(LICENSE_NAME)
        return LicenseInfo(name=LICENSE_NAME, url=LICENSE_URL, status=status,
                           note="Запрещено перепродавать ролики и использовать их для обучения ИИ.")

    def get_license(self, result: VideoResult) -> LicenseInfo:
        return self.license_for(result)
