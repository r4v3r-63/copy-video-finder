"""Поиск роликов по всему интернету (разрешено пользователем): поисковик → страницы роликов.

Поисковики по очереди: Tavily (бесплатный ключ, 1000 запросов в месяц), Brave (если есть ключ),
затем без ключа — DuckDuckGo (его HTML-выдача). Заблокировал один — пробуем следующий;
блокировку не обходим: заблокировавший поисковик отдыхает 10 минут. Bing не используется:
программам он отдаёт подменённую выдачу (на «binding machine» — дверные замки).

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
from app.sources.base import (
    LicenseInfo,
    NotFoundError,
    RateLimitError,
    SearchFilters,
    Source,
    SourceError,
    VideoFile,
    VideoResult,
)
from app.sources.http import HttpSource
from app.sources.videezy import BROWSER_UA

DDG_URL = "https://html.duckduckgo.com/html/"
TAVILY_URL = "https://api.tavily.com/search"
TAVILY_MONTHLY = 900  # бесплатно 1000 в месяц
# Бесплатные стоки, где поисковик находит страницы отдельных роликов (как «site:» в запросе).
# Их страницы открываются модулями площадок: лицензия и ссылки на файлы — из их API.
# Pexels сюда не входит: его страницы закрыты от программ (403), а новых ключей API он не выдаёт.
# С Vimeo из поисковика приходят в основном «все права защищены» — они видны по кнопке
# «Без свободной лицензии» (только посмотреть); свободные Vimeo находит и его поиск с фильтром CC.
FREE_STOCK_DOMAINS = ["pixabay.com", "videezy.com", "vimeo.com", "commons.wikimedia.org", "archive.org"]
STOCK_DOMAINS = FREE_STOCK_DOMAINS + ["pexels.com", "coverr.co"]  # их страницы-не-ролики не открываем
_WIKI_VIDEO = (".webm", ".ogv", ".ogg", ".mp4", ".mpg", ".mpeg")
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


def _free_words(lang: str) -> str:
    """Уточнение для поисковиков по всему интернету: ищем бесплатные ролики."""
    return {"de": "kostenlos Video", "fr": "vidéo gratuite"}.get(lang, "free stock video")


def ddg_blocked(page: str, status: int) -> bool:
    return status == 202 or "anomaly" in page[:20000].lower()


def _on_domain(host: str, domain: str) -> bool:
    """Сайт на этом домене: «x.com» — это x.com и www.x.com, но не dropbox.com.

    Записи без точки на конце домена («gettyimages», «amazon.») — часть имени: gettyimages.de, amazon.co.uk.
    """
    if domain.endswith("."):
        return domain.rstrip(".") in host.split(".")
    if "." not in domain:
        return any(label.startswith(domain) for label in host.split("."))
    return host == domain or host.endswith("." + domain)


def skipped(url: str) -> bool:
    host = urlparse(url).netloc.lower().split(":")[0]
    return any(_on_domain(host, d) for d in SKIP_DOMAINS)


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


def _duration(text) -> float | None:
    if not isinstance(text, str):  # в разметке бывает число или объект — длительность неизвестна
        return None
    m = _ISO_DURATION.fullmatch(text)
    if not m or not any(m.groups()):
        return None
    hours, minutes, seconds = (float(g or 0) for g in m.groups())
    total = hours * 3600 + minutes * 60 + seconds
    return total or None


def _object_license(obj: dict) -> str:
    """Лицензия из разметки VideoObject: строка, {"url"/"@id": …} или список таких значений."""
    value = obj.get("license")
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = value.get("url") or value.get("@id") or value.get("name") or ""
    return value.strip() if isinstance(value, str) else ""


def parse_page(page: str, url: str) -> VideoResult | None:
    """Ролик с произвольной страницы: нужен файл ролика и открытая лицензия, иначе None."""
    meta, objects = _meta(page), _video_objects(page)
    obj = objects[0] if objects else {}
    video = next((v for v in (obj.get("contentUrl"), meta.get("og:video:secure_url"), meta.get("og:video:url"),
                              meta.get("og:video"), meta.get("contenturl"))
                  if isinstance(v, str) and urlparse(v).path.lower().endswith(_VIDEO_EXT)), None)
    if not video:
        return None
    own = _object_license(obj)
    page_wide = False
    if own:
        # Лицензия задана у самого ролика: её и берём. Нераспознанные условия («все права защищены»)
        # нельзя подменять ссылкой на Creative Commons из другого блока страницы.
        license_url = own
    else:
        # Лицензия страницы (rel="license" или просто ссылка) с роликом не связана: она может
        # относиться к тексту или фото. Такие ролики — только на ручную проверку.
        m = _LICENSE_LINK.search(page)
        license_url = (m.group(1) or m.group(2)) if m else ""
        if not licenses.name_from_url(license_url):
            m = _CC_ANY.search(page)
            license_url = m.group(0) if m else license_url
        page_wide = bool(license_url)
    name = licenses.name_from_url(license_url)
    if not name:
        return None  # лицензия не распознана — такой ролик всё равно не показывается
    status, note = licenses.evaluate(name)
    if page_wide and status != licenses.REJECTED:
        status = licenses.REVIEW
        note = ("Лицензия указана для страницы, а не для ролика: она может относиться к тексту или фото. "
                + note).strip()
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


class Engine(HttpSource):
    """Поисковик: find(text) → [(ссылка, заголовок)]. У каждого свои лимиты и пауза после блокировки."""
    homepage = ""

    def __init__(self, cache: Cache, session=None, now=time.time):
        super().__init__(cache, session, now=now)
        if hasattr(self.session, "headers"):
            self.session.headers["User-Agent"] = BROWSER_UA

    def find(self, query: str, lang: str = "en") -> list[tuple[str, str]]:
        raise NotImplementedError

    def _blocked(self, resp, test) -> RateLimitError | None:
        """Капча или «anomaly»: пауза 10 минут, блокировку не обходим."""
        if test(resp):
            self.cache.block_until(self.name, self._now() + BLOCK_SECONDS)
            return RateLimitError(self.name, f"{self.name} временно ограничил поиск из программы.")
        return None

    def search(self, query, filters, page=1, lang="en"):
        return []

    def get_details(self, source_id):
        raise NotImplementedError

    def get_license(self, result):
        return result.license


class TavilyEngine(Engine):
    """Tavily: бесплатно 1000 запросов в месяц, ключ без карты. Программа тратит не больше TAVILY_MONTHLY."""
    name = "Tavily"
    homepage = "https://tavily.com"

    def __init__(self, key: str, cache: Cache, session=None, now=time.time):
        self.key = key or ""
        self.limits = [(1, 1.0, "в секунду"), (TAVILY_MONTHLY, 30 * 86400, "в месяц — в пределах бесплатных 1000")]
        super().__init__(cache, session, now=now)

    def is_configured(self) -> bool:
        return bool(self.key)

    def find(self, query: str, lang: str = "en") -> list[tuple[str, str]]:
        # Только бесплатные стоки, как «site:»; запрос как есть: со словами «free stock video»
        # в выдаче в основном страницы поиска стоков, а не ролики.
        data = json.loads(self._fetch_text(TAVILY_URL, {"query": query, "max_results": 20, "search_depth": "basic",
                                                        "include_domains": FREE_STOCK_DOMAINS},
                                           headers={"Authorization": f"Bearer {self.key}"}, method="POST_JSON"))
        return [(r.get("url", ""), r.get("title", "")) for r in data.get("results", [])]


class BraveEngine(Engine):
    """Brave Search API: платный ($5 за 1000), кредит $5 в месяц — тратим не больше BRAVE_MONTHLY."""
    name = "Brave"
    homepage = "https://search.brave.com"

    def __init__(self, key: str, cache: Cache, session=None, now=time.time):
        self.key = key or ""
        self.limits = [(1, 1.2, "в секунду"), (BRAVE_MONTHLY, 30 * 86400, "в месяц — в пределах бесплатных $5")]
        super().__init__(cache, session, now=now)

    def is_configured(self) -> bool:
        return bool(self.key)

    def find(self, query: str, lang: str = "en") -> list[tuple[str, str]]:
        data = json.loads(self._fetch_text(BRAVE_URL, {"q": f"{query} {_free_words(lang)}", "count": 20}, headers={
            "X-Subscription-Token": self.key, "Accept": "application/json"}))
        return [(r.get("url", ""), r.get("title", "")) for r in (data.get("web") or {}).get("results", [])]


class DuckDuckGoEngine(Engine):
    """DuckDuckGo без ключа (HTML-выдача). Частые запросы он блокирует — поэтому редко."""
    name = "DuckDuckGo"
    homepage = "https://duckduckgo.com"
    limits = [(6, 60, "в минуту"), (200, 86400, "в сутки")]

    def find(self, query: str, lang: str = "en") -> list[tuple[str, str]]:
        page = self._fetch_text(DDG_URL, {"q": f"{query} {_free_words(lang)}"}, method="POST",
                                check=lambda r: self._blocked(r, lambda x: ddg_blocked(x.text, x.status_code)))
        return parse_ddg(page)


class WebSource(HttpSource):
    """Поиск по интернету: поисковики по очереди (Tavily и Brave — если есть ключ, затем DuckDuckGo)."""
    name = "Интернет"
    homepage = "https://duckduckgo.com"
    languages = frozenset({"en", "de", "fr"})
    max_level = 2  # название, оборудование, действие — дальше поисковик расходует лимиты впустую
    max_parallel = 1

    def __init__(self, cache: Cache, delegates: list[Source] | None = None, tavily_key: str = "", brave_key: str = "",
                 now=time.time):
        super().__init__(cache, now=now)
        self.engines = [e for e in (TavilyEngine(tavily_key, cache, now=now), BraveEngine(brave_key, cache, now=now))
                        if e.is_configured()] + [DuckDuckGoEngine(cache, now=now)]
        self.delegates = {s.name: s for s in (delegates or [])}
        self.pages = PageReader(cache, now=now)

    @property
    def engine(self) -> str:
        return self.engines[0].name

    def search(self, query: str, filters: SearchFilters, page: int = 1, lang: str = "en") -> list[VideoResult]:
        found, results = self.find_pages(query, lang), []
        for url, _title in found[:MAX_PAGES]:
            try:
                r = self.open(url)
            except SourceError:
                continue
            if r is not None:
                results.append(r)
        return results

    def find_pages(self, query: str, lang: str = "en") -> list[tuple[str, str]]:
        """Ссылки из выдачи первого ответившего поисковика без платных стоков, соцсетей, YouTube и Mixkit."""
        problems = []
        for engine in self.engines:
            engine.max_wait, engine.stop = self.max_wait, self.stop  # пакетный поиск ждёт и здесь
            try:
                pairs = engine.find(query, lang)
            except SourceError as exc:
                problems.append(exc.message)
                continue
            seen, result = set(), []
            for url, title in pairs:
                if url and url not in seen and not skipped(url):
                    seen.add(url)
                    result.append((url, title))
            return result
        raise RateLimitError(self.name, "Поиск по интернету временно недоступен: " + " ".join(problems) +
                             " Повторите позже.")

    def open(self, url: str) -> VideoResult | None:
        """Ролик по ссылке со страницы: известная площадка — через её модуль, иначе разбор страницы."""
        known = known_site(url)
        if known:
            name, source_id = known
            source = self.delegates.get(name)
            if source is None or not source.is_configured():
                return None
            if name == "Wikimedia Commons":
                if not source_id.lower().endswith(_WIKI_VIDEO):
                    return None  # картинка или документ, а не ролик
                return source.get_by_title(source_id)
            return source.get_details(source_id)
        if any(_on_domain(urlparse(url).netloc.lower().split(":")[0], d) for d in STOCK_DOMAINS):
            return None  # страница поиска или картинок стока, а не ролик — не открываем
        return parse_page(self.pages.read(url), url)

    def get_details(self, source_id: str) -> VideoResult:
        result = self.open(source_id)
        if result is None:
            raise NotFoundError(self.name, "Ролик по ссылке не найден.")
        return result

    def get_license(self, result: VideoResult) -> LicenseInfo:
        return result.license
