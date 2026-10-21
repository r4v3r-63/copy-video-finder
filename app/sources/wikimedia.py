"""Wikimedia Commons: MediaWiki Action API, без ключа.

Лицензия у каждого файла своя — берётся из extmetadata (LicenseShortName).
Форматы WebM/OGV; при скачивании возможна конвертация в MP4 (этап 4).
Запросы идут последовательно и с подписанным User-Agent, как просит Wikimedia.
"""

import html
import re
import time

import requests

from app.core import licenses
from app.core.models import LicenseInfo, SearchFilters, VideoFile, VideoResult
from app.sources.base import NotFoundError, SourceError
from app.sources.cache import Cache
from app.sources.http import HttpSource, quality_label

API = "https://commons.wikimedia.org/w/api.php"
PER_PAGE = 20
META_FIELDS = "LicenseShortName|LicenseUrl|Artist|Attribution|ObjectName|ImageDescription|UsageTerms"
_TAG = re.compile(r"<[^>]+>")
_HREF = re.compile(r'href="([^"]+)"')
_AUDIO_CODECS = ("vorbis", "opus", "mp4a", "aac", "mp3", "flac")


def strip_html(value: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", value or "")).split())


def _meta(info: dict, name: str) -> str:
    value = (info.get("extmetadata") or {}).get(name, {}).get("value", "")
    return str(value)


def _absolute(url: str) -> str:
    return "https:" + url if url.startswith("//") else url


def _has_audio(info: dict) -> bool | None:
    """Звук — по кодекам исходного файла; перекодированные версии всегда объявляют аудио."""
    for d in info.get("derivatives") or []:
        if not d.get("transcodekey"):
            codecs = (d.get("type") or "").lower()
            if "codecs" not in codecs:
                return None
            return any(c in codecs for c in _AUDIO_CODECS)
    return None


def parse_page(page: dict) -> VideoResult:
    info = (page.get("videoinfo") or page.get("imageinfo") or [{}])[0]
    title = page.get("title", "")
    name = _meta(info, "ObjectName") or re.sub(r"^File:|\.[a-z0-9]+$", "", title, flags=re.IGNORECASE)

    files: list[VideoFile] = []
    for d in info.get("derivatives") or []:
        if d.get("src"):
            files.append(VideoFile(
                quality=quality_label(d.get("height")) + ("" if d.get("transcodekey") else ", исходник"),
                url=d["src"], width=d.get("width"), height=d.get("height"),
                mime=(d.get("type") or "").split(";")[0] or None,
            ))
    if not files and info.get("url"):
        files.append(VideoFile(quality=quality_label(info.get("height")) + ", исходник", url=info["url"],
                               width=info.get("width"), height=info.get("height"), mime=info.get("mime")))
    files.sort(key=lambda f: f.height or 0, reverse=True)

    artist_html = _meta(info, "Artist")
    href = _HREF.search(artist_html)
    author = _meta(info, "Attribution") or strip_html(artist_html)

    license_name = _meta(info, "LicenseShortName")
    status, note = licenses.evaluate(license_name)
    result = VideoResult(
        source=WikimediaSource.name,
        source_id=str(page.get("pageid", title)),
        title=strip_html(name),
        description=strip_html(_meta(info, "ImageDescription"))[:500],
        preview_url=info.get("thumburl"),
        duration=info.get("duration"),
        width=info.get("width"),
        height=info.get("height"),
        has_audio=_has_audio(info),
        page_url=info.get("descriptionurl", ""),
        files=files,
        author=author,
        author_url=_absolute(href.group(1)) if href else None,
        license=LicenseInfo(name=license_name or "не указана", url=_meta(info, "LicenseUrl") or None,
                            status=status, note=note),
    )
    return result


class WikimediaSource(HttpSource):
    name = "Wikimedia Commons"
    homepage = "https://commons.wikimedia.org"
    # Описания Commons слишком широкие («laminated» в научных роликах) — тема по названию файла.
    topic_in_description = False
    # Своё вежливое ограничение: у Wikimedia нет фиксированного лимита для анонимных запросов,
    # но частые запросы с одного адреса получают 429.
    limits = [(30, 60, "в минуту"), (500, 3600, "в час")]
    max_parallel = 1

    def __init__(self, cache: Cache, session: requests.Session | None = None, now=time.time):
        super().__init__(cache, session, now)

    def _status_error(self, resp) -> SourceError | None:
        if resp.status_code == 403:
            return SourceError(self.name, "Wikimedia Commons: запрос отклонён (403). Повторите позже.")
        return super()._status_error(resp)

    def _query(self, params: dict) -> dict:
        base = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "videoinfo",
            "viprop": "url|size|mime|extmetadata|derivatives",
            "viextmetadatafilter": META_FIELDS,
            "viextmetadatalanguage": "ru",
            "viurlwidth": "320",
            "maxlag": "5",
        }
        data = self._fetch(API, {**base, **params})
        if "error" in data:
            code = data["error"].get("code", "")
            if code == "maxlag":
                raise SourceError(self.name, "Wikimedia Commons: площадка перегружена. Повторите позже.")
            raise SourceError(self.name, f"Wikimedia Commons: ошибка {code}.")
        return data

    def search(self, query: str, filters: SearchFilters, page: int = 1, lang: str = "en") -> list[VideoResult]:
        # Поиск Commons ищет по описаниям на всех языках; язык фразы передавать не нужно.
        data = self._query({
            "generator": "search",
            "gsrsearch": f"filetype:video {query}",
            "gsrnamespace": "6",
            "gsrlimit": str(PER_PAGE),
            "gsroffset": str((page - 1) * PER_PAGE),
        })
        pages = sorted(data.get("query", {}).get("pages", []), key=lambda p: p.get("index", 0))
        return [parse_page(p) for p in pages if not p.get("missing")]

    def get_details(self, source_id: str) -> VideoResult:
        data = self._query({"pageids": source_id})
        pages = data.get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing"):
            raise NotFoundError(self.name, "Wikimedia Commons: файл удалён с площадки.")
        return parse_page(pages[0])

    def get_by_title(self, title: str) -> VideoResult:
        """Файл по названию из ссылки («File:Paper Cutter.webm») — для страниц, найденных в интернете."""
        data = self._query({"titles": title})
        pages = data.get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing"):
            raise NotFoundError(self.name, "Wikimedia Commons: файл не найден.")
        return parse_page(pages[0])

    def get_license(self, result: VideoResult) -> LicenseInfo:
        return result.license or self.get_details(result.source_id).license
