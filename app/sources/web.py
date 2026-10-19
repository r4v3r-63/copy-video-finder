"""Поиск роликов по всему интернету (разрешено пользователем): поисковик → страницы роликов.

Поисковик — Brave Search API, если задан ключ, иначе DuckDuckGo (его HTML-выдача; без ключа,
но при частых запросах он временно блокирует — тогда программа просит ключ Brave).

Найденная страница разбирается так:
- известная площадка (Pexels, Pixabay, Vimeo, Wikimedia, Internet Archive, Videezy) — через
  её модуль: сведения и лицензия те же, что при поиске на ней;
- любой другой сайт — по разметке страницы (Open Graph, schema.org VideoObject): нужен файл
  ролика и открытая лицензия по ссылке на Creative Commons. Без этого ролик не показывается.

Mixkit и YouTube не подключаются (правило 5), платные стоки пропускаются.
"""

import html
import json
import re
import time
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from app import licenses
from app.cache import Cache
from app.sources.base import (LicenseInfo, NotFoundError, RateLimitError, SearchFilters, Source, SourceError,
                              VideoFile, VideoResult)
from app.sources.http import HttpSource
from app.sources.videezy import BROWSER_UA

DDG_URL = "https://html.duckduckgo.com/html/"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_MONTHLY = 900
MAX_PAGES = 8  # страниц из выдачи поисковика на один запрос
BLOCK_SECONDS = 600  # DuckDuckGo показал «anomaly» — 10 минут не беспокоим

# Не подключаются (правило 5) и платные стоки, соцсети, магазины — там нет бесплатных роликов.
SKIP_DOMAINS = (
    "youtube.com", "youtu.be", "mixkit.co", "shutterstock.com", "pond5.com", "stock.adobe.com", "istockphoto.com",
    "gettyimages", "storyblocks.com", "envato.com", "videohive.net", "artgrid.io", "motionarray.com",
    "dreamstime.com", "depositphotos.com", "123rf.com", "alamy.com", "canva.com", "amazon.", "facebook.com",
    "instagram.com", "tiktok.com", "pinterest.", "reddit.com", "twitter.com", "x.com", "linkedin.com",
    "freepik.com", "videvo.net", "vecteezy.com", "dissolve.com", "filmsupply.com", "wikipedia.org",
)

_DDG_LINK = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_TAGS = re.compile(r"<[^>]+>")
_META = re.compile(r'<meta\s+[^>]*?(?:property|name|itemprop)="([^"]+)"[^>]*?content="([^"]*)"[^>]*>', re.I)
_META_REV = re.compile(r'<meta\s+[^>]*?content="([^"]*)"[^>]*?(?:property|name|itemprop)="([^"]+)"[^>]*>', re.I)
_LD = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)
_LICENSE_LINK = re.compile(r'<a[^>]+rel="license"[^>]*href="([^"]+)"|<link[^>]+rel="license"[^>]*href="([^"]+)"', re.I)
_CC_ANY = re.compile(r'https?://creativecommons\.org/(?:licenses|publicdomain)/[a-z\-]+/?[\d.]*/?', re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_VIDEO_EXT = (".mp4", ".webm", ".mov", ".m4v", ".ogv")
_ISO_DURATION = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?")

# Ссылки на страницы известных площадок → (имя площадки, ID для её модуля)
_KNOWN = (
    ("Pexels", re.compile(r"pexels\.com/(?:[a-z-]+/)?video/[^/?#]*?-?(\d+)/?")),
    ("Pixabay", re.compile(r"pixabay\.com/(?:[a-z-]+/)?videos/[^/?#]*?-(\d+)/?")),
    ("Vimeo", re.compile(r"vimeo\.com/(?:channels/[^/]+/)?(\d{5,})")),
    ("Wikimedia Commons", re.compile(r"commons\.wikimedia\.org/wiki/(File:[^?#]+)")),
    ("Internet Archive", re.compile(r"archive\.org/details/([^/?#]+)")),
    ("Videezy", re.compile(r"videezy\.com/([a-z0-9-]+/\d+-[a-z0-9-]+)")),
)


def parse_ddg(page: str) -> list[tuple[str, str]]:
    """Выдача DuckDuckGo: (ссылка, заголовок). Ссылки бывают вида //duckduckgo.com/l/?uddg=…"""
    result = []
    for href, title in _DDG_LINK.findall(page):
        href = html.unescape(href)
        if "duckduckgo.com/l/" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [""])[0])
        if href.startswith("http"):
            result.append((href, html.unescape(_TAGS.sub("", title)).strip()))
    return result


def ddg_blocked(page: str, status: int) -> bool:
    return status == 202 or "anomaly" in page[:20000].lower()


def skipped(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(d in host for d in SKIP_DOMAINS)


def known_site(url: str) -> tuple[str, str] | None:
    for name, pattern in _KNOWN:
        m = pattern.search(url)
        if m:
            return name, unquote(m.group(1))
    return None


def _meta(page: str) -> dict[str, str]:
    meta = {}
    for key, value in _META.findall(page):
        meta.setdefault(key.lower(), html.unescape(value))
    for value, key in _META_REV.findall(page):
        meta.setdefault(key.lower(), html.unescape(value))
    return meta


def _video_objects(page: str) -> list[dict]:
    objects = []
    for raw in _LD.findall(page):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if item.get("@type") == "VideoObject":
                    objects.append(item)
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
            elif isinstance(item, list):
                stack.extend(item)
    return objects


def _duration(text: str) -> float | None:
    m = _ISO_DURATION.fullmatch(text or "")
    if not m or not any(m.groups()):
        return None
    hours, minutes, seconds = (float(g or 0) for g in m.groups())
    total = hours * 3600 + minutes * 60 + seconds
    return total or None


def parse_page(page: str, url: str) -> VideoResult | None:
    """Ролик с произвольной страницы: нужен файл ролика и открытая лицензия, иначе None."""
    meta, objects = _meta(page), _video_objects(page)
    obj = objects[0] if objects else {}
    video = next((v for v in (obj.get("contentUrl"), meta.get("og:video:secure_url"), meta.get("og:video:url"),
                              meta.get("og:video"), meta.get("contenturl"))
                  if isinstance(v, str) and urlparse(v).path.lower().endswith(_VIDEO_EXT)), None)
    if not video:
        return None
    license_url = obj.get("license") if isinstance(obj.get("license"), str) else ""
    if not license_url:
        m = _LICENSE_LINK.search(page)
        license_url = (m.group(1) or m.group(2)) if m else ""
    if not licenses.name_from_url(license_url):
        m = _CC_ANY.search(page)
        license_url = m.group(0) if m else license_url
    name = licenses.name_from_url(license_url)
    if not name:
        return None  # лицензия не распознана — такой ролик всё равно не показывается
    status, note = licenses.evaluate(name)
    title = obj.get("name") or meta.get("og:title") or html.unescape(_TAGS.sub("", (_TITLE.search(page) or [0, ""])[1]))
    thumb = obj.get("thumbnailUrl") or meta.get("og:image")
    if isinstance(thumb, list):
        thumb = thumb[0] if thumb else None
    author = obj.get("author") or obj.get("creator") or ""
    if isinstance(author, dict):
        author = author.get("name", "")
    width, height = obj.get("width"), obj.get("height")
    host = urlparse(url).netloc.lower().removeprefix("www.")
    video = urljoin(url, video)
    return VideoResult(
        source=host, source_id=url, title=str(title).strip()[:200],
        description=str(obj.get("description") or meta.get("og:description") or meta.get("description") or "")[:500],
        preview_url=urljoin(url, thumb) if isinstance(thumb, str) else None,
        duration=_duration(obj.get("duration", "")),
        width=int(width) if str(width).isdigit() else None, height=int(height) if str(height).isdigit() else None,
        has_audio=None, page_url=url, files=[VideoFile("с сайта", video)], author=str(author),
        license=LicenseInfo(name=name, url=license_url, status=status,
                            note=(note + " Найдено в интернете — проверьте условия на странице.").strip()),
        preview_video=video,
    )


class PageReader(HttpSource):
    """Чтение страниц произвольных сайтов — со своими бережными лимитами."""
    name = "Страницы сайтов"
    limits = [(30, 60, "в минуту")]

    def __init__(self, cache: Cache, session=None, now=time.time):
        super().__init__(cache, session, now=now)
        if hasattr(self.session, "headers"):
            self.session.headers["User-Agent"] = BROWSER_UA

    def read(self, url: str) -> str:
        return self._fetch_text(url)

    def search(self, query, filters, page=1, lang="en"):
        return []

    def get_details(self, source_id):
        result = parse_page(self.read(source_id), source_id)
        if result is None:
            raise NotFoundError(self.name, "На странице нет ролика с открытой лицензией.")
        return result

    def get_license(self, result):
        return result.license


class WebSource(HttpSource):
    name = "Интернет"
    homepage = "https://duckduckgo.com"
    languages = frozenset({"en", "de", "fr"})
    max_level = 2  # название, оборудование, действие — дальше поисковик расходует лимиты впустую
    max_parallel = 1

    def __init__(self, brave_key: str, cache: Cache, delegates: list[Source] | None = None, session=None,
                 now=time.time):
        self.brave_key = brave_key or ""
        if self.brave_key:
            # Brave платный: $5 за 1000 запросов, бесплатного кредита $5 в месяц хватает на 1000.
            # Не больше 900 в месяц — чтобы никогда не выйти за бесплатный кредит.
            self.limits = [(1, 1.2, "в секунду"), (BRAVE_MONTHLY, 30 * 86400, "в месяц — в пределах бесплатных $5")]
            self.homepage = "https://search.brave.com"
        else:
            self.limits = [(6, 60, "в минуту"), (200, 86400, "в сутки")]  # реже — DuckDuckGo блокирует
        super().__init__(cache, session, now=now)
        if hasattr(self.session, "headers"):
            self.session.headers["User-Agent"] = BROWSER_UA
        self.delegates = {s.name: s for s in (delegates or [])}
        self.pages = PageReader(cache, now=now)

    @property
    def engine(self) -> str:
        return "Brave" if self.brave_key else "DuckDuckGo"

    def search(self, query: str, filters: SearchFilters, page: int = 1, lang: str = "en") -> list[VideoResult]:
        words = {"de": "kostenlos Video", "fr": "vidéo gratuite"}.get(lang, "free stock video")
        found, results = self.find_pages(f"{query} {words}"), []
        for url, _title in found[:MAX_PAGES]:
            try:
                r = self.open(url)
            except SourceError:
                continue
            if r is not None:
                results.append(r)
        return results

    def find_pages(self, text: str) -> list[tuple[str, str]]:
        """Ссылки из выдачи поисковика без платных стоков, соцсетей, YouTube и Mixkit."""
        if self.brave_key:
            data = json.loads(self._fetch_text(BRAVE_URL, {"q": text, "count": 20}, headers={
                "X-Subscription-Token": self.brave_key, "Accept": "application/json"}))
            pairs = [(r.get("url", ""), r.get("title", "")) for r in (data.get("web") or {}).get("results", [])]
        else:
            pairs = parse_ddg(self._fetch_text(DDG_URL, {"q": text}, method="POST", check=self._ddg_check))
        seen, result = set(), []
        for url, title in pairs:
            if url and url not in seen and not skipped(url):
                seen.add(url)
                result.append((url, title))
        return result

    def _ddg_check(self, resp):
        if ddg_blocked(resp.text, resp.status_code):
            self.cache.block_until(self.name, self._now() + BLOCK_SECONDS)
            return RateLimitError(self.name, "DuckDuckGo временно ограничил поиск из программы. Повторите через "
                                             "10 минут или укажите ключ Brave Search в настройках.")
        return None

    def open(self, url: str) -> VideoResult | None:
        """Ролик по ссылке со страницы: известная площадка — через её модуль, иначе разбор страницы."""
        known = known_site(url)
        if known:
            name, source_id = known
            source = self.delegates.get(name)
            if source is None or not source.is_configured():
                return None
            if name == "Wikimedia Commons":
                return source.get_by_title(source_id)
            return source.get_details(source_id)
        return parse_page(self.pages.read(url), url)

    def get_details(self, source_id: str) -> VideoResult:
        result = self.open(source_id)
        if result is None:
            raise NotFoundError(self.name, "Ролик по ссылке не найден.")
        return result

    def get_license(self, result: VideoResult) -> LicenseInfo:
        return result.license
