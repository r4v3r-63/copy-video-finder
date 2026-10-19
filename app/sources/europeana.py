"""Europeana Search API: https://pro.europeana.eu/page/search

Видео музеев и архивов Европы. Только открытые лицензии (reusability=open:
CC0, Public Domain, CC BY, CC BY-SA); лицензия каждого ролика — из поля rights.
Файл — прямая ссылка edmIsShownBy из ответа API. Нужен бесплатный ключ.
Названия часто на нидерландском и других языках — берутся английские, если есть.
"""

import time
from pathlib import PurePosixPath
from urllib.parse import urlparse

import requests

from app import licenses
from app.cache import Cache
from app.sources.base import LicenseInfo, NotFoundError, SearchFilters, VideoFile, VideoResult
from app.sources.http import HttpSource

API = "https://api.europeana.eu/record/v2/search.json"
PER_PAGE = 20
VIDEO_EXT = {".mp4", ".m4v", ".webm", ".ogv", ".ogg", ".mov"}


def _lang(item: dict, field: str, plain: str) -> str:
    """Английский вариант поля, иначе первый попавшийся."""
    aware = item.get(field) or {}
    for lang in ("en", "def"):
        if aware.get(lang):
            return aware[lang][0]
    for values in aware.values():
        if values:
            return values[0]
    return (item.get(plain) or [""])[0]


def parse_item(item: dict) -> VideoResult:
    media = (item.get("edmIsShownBy") or [""])[0]
    files = []
    if media and PurePosixPath(urlparse(media).path).suffix.lower() in VIDEO_EXT:
        files.append(VideoFile(quality="?", url=media))
    rights = (item.get("rights") or [""])[0]
    name = licenses.name_from_url(rights)
    status, note = licenses.evaluate(name)
    creator = (item.get("dcCreator") or item.get("dataProvider") or [""])[0]
    return VideoResult(
        source=EuropeanaSource.name,
        source_id=item.get("id", "").strip("/"),
        title=_lang(item, "dcTitleLangAware", "title"),
        description=_lang(item, "dcDescriptionLangAware", "dcDescription")[:500],
        preview_url=(item.get("edmPreview") or [None])[0],
        duration=None,  # в ответе поиска длительности нет
        width=None,
        height=None,
        has_audio=None,
        page_url=item.get("guid") or (item.get("edmIsShownAt") or [""])[0],
        files=files,
        author=creator,
        author_url=None,
        license=LicenseInfo(name=name or "не распознана", url=rights or None, status=status, note=note),
    )


class EuropeanaSource(HttpSource):
    name = "Europeana"
    homepage = "https://www.europeana.eu"
    limits = [(100, 60, "в минуту")]
    languages = frozenset({"en", "de", "fr"})  # европейский архив: много немецких и французских названий

    def __init__(self, api_key: str, cache: Cache, session: requests.Session | None = None, now=time.time):
        super().__init__(cache, session, now)
        self.api_key = api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _get(self, params: dict) -> dict:
        return self._fetch(API, {"wskey": self.api_key, **params}, secret_params=("wskey",))

    def search(self, query: str, filters: SearchFilters, page: int = 1, lang: str = "en") -> list[VideoResult]:
        data = self._get({"query": query, "qf": "TYPE:VIDEO", "reusability": "open", "media": "true",
                          "profile": "rich", "rows": PER_PAGE, "start": (page - 1) * PER_PAGE + 1})
        return [parse_item(i) for i in data.get("items") or [] if i.get("id")]

    def get_details(self, source_id: str) -> VideoResult:
        data = self._get({"query": f'europeana_id:"/{source_id}"', "profile": "rich", "rows": 1})
        items = data.get("items") or []
        if not items:
            raise NotFoundError(self.name, "Europeana: ролик удалён.")
        return parse_item(items[0])

    def get_license(self, result: VideoResult) -> LicenseInfo:
        return result.license or self.get_details(result.source_id).license
