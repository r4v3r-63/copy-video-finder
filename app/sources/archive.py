"""Internet Archive: Advanced Search API, без ключа.

Только ролики с лицензией Creative Commons или общественного достояния (licenseurl).
Готовых ссылок на файлы API поиска не даёт, а собирать их из частей запрещено
(правило 2), поэтому ролики Internet Archive смотрят и скачивают на их странице.
Копии роликов с YouTube (идентификатор youtube-…) не показываются (правило 5).
"""

import re
import time

import requests

from app.core import licenses
from app.core.models import LicenseInfo, SearchFilters, VideoResult
from app.sources.base import NotFoundError
from app.sources.cache import Cache
from app.sources.http import HttpSource

API = "https://archive.org/advancedsearch.php"
PER_PAGE = 20
FIELDS = ("identifier", "title", "description", "licenseurl", "runtime", "creator")
NO_FILES_NOTE = "Скачайте ролик на его странице в Internet Archive."
_TAG = re.compile(r"<[^>]+>")


def parse_runtime(value) -> float | None:
    """«00:00:53», «1:20:18», «2:15», «53 seconds» → секунды."""
    if isinstance(value, list):
        value = value[0] if value else None
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if re.fullmatch(r"\d+(:\d{1,2}){1,2}(\.\d+)?", text):
        total = 0.0
        for part in text.split(":"):
            total = total * 60 + float(part)
        return total
    m = re.fullmatch(r"([\d.]+)\s*(s|sec|secs|seconds|min|mins|minutes)?", text)
    if m:
        number = float(m.group(1))
        return number * 60 if (m.group(2) or "").startswith("min") else number
    return None


def _first(value) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "")


def parse_doc(doc: dict) -> VideoResult:
    identifier = doc["identifier"]
    name = licenses.name_from_url(_first(doc.get("licenseurl")))
    status, note = licenses.evaluate(name)
    note = (note + " " if note else "") + NO_FILES_NOTE
    return VideoResult(
        source=ArchiveSource.name,
        source_id=identifier,
        title=_first(doc.get("title")),
        description=" ".join(_TAG.sub(" ", _first(doc.get("description"))).split())[:500],
        # Служба превью Internet Archive (картинка, не файл ролика).
        preview_url=f"https://archive.org/services/img/{identifier}",
        duration=parse_runtime(doc.get("runtime")),
        width=None,
        height=None,
        has_audio=None,
        page_url=f"https://archive.org/details/{identifier}",
        files=[],
        author=_first(doc.get("creator")),
        author_url=None,
        license=LicenseInfo(name=name or "не распознана", url=_first(doc.get("licenseurl")) or None,
                            status=status, note=note),
    )


class ArchiveSource(HttpSource):
    name = "Internet Archive"
    homepage = "https://archive.org"
    # Без ключа и без объявленного лимита — вежливо и по одному запросу.
    limits = [(30, 60, "в минуту"), (500, 3600, "в час")]
    max_parallel = 1
    topic_in_description = False  # описания длинные и случайные; тема — по названию
    languages = frozenset({"en", "de", "fr"})

    def __init__(self, cache: Cache, session: requests.Session | None = None, now=time.time):
        super().__init__(cache, session, now)

    def search(self, query: str, filters: SearchFilters, page: int = 1, lang: str = "en") -> list[VideoResult]:
        q = (f"({query}) AND mediatype:movies AND "
             "(licenseurl:*creativecommons.org* OR licenseurl:*publicdomain*) AND NOT identifier:youtube-*")
        params = {"q": q, "fl[]": list(FIELDS), "rows": PER_PAGE, "page": page, "output": "json"}
        data = self._fetch(API, params)
        docs = (data.get("response") or {}).get("docs") or []
        return [parse_doc(d) for d in docs if d.get("identifier") and not d["identifier"].startswith("youtube-")]

    def get_details(self, source_id: str) -> VideoResult:
        found = self._fetch(API, {"q": f"identifier:{source_id}", "fl[]": list(FIELDS), "rows": 1, "output": "json"})
        docs = (found.get("response") or {}).get("docs") or []
        if not docs:
            raise NotFoundError(self.name, "Internet Archive: ролик удалён.")
        return parse_doc(docs[0])

    def get_license(self, result: VideoResult) -> LicenseInfo:
        return result.license
