"""Videezy: у сайта нет API, ролики ищутся по его страницам (разрешено пользователем).

Поиск — страница https://www.videezy.com/free-video/<запрос>, сведения о ролике — со
страницы ролика: микроразметка schema.org VideoObject (название, описание, автор, превью),
теги, признак бесплатности (`resourceType = 'free'`) и тип лицензии (`data-license-type`).

Лицензия бесплатных роликов — Videezy Free License: коммерческое использование с указанием
Videezy.com. Pro и Editorial — «Отклонено». Ссылка на файл живёт только в сессии сайта,
поэтому при скачивании страница открывается заново (`resolve_download`), а ссылка берётся
с неё — не конструируется.
"""

import html
import re
from urllib.parse import quote, urljoin

from app.core import licenses
from app.core.models import LicenseInfo, SearchFilters, VideoFile, VideoResult
from app.sources.base import NotFoundError
from app.sources.cache import Cache
from app.sources.http import HttpSource

BASE = "https://www.videezy.com"
LICENSE_URL = "https://www.videezy.com/licensing"
FREE_LICENSE = "Videezy Free License"
PRO_LICENSE = "Videezy Pro License"  # платные — «Отклонено»
EDITORIAL = "Editorial"  # только для новостей — «Отклонено»
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/141.0 Safari/537.36")
MAX_RESULTS = 8  # страниц роликов на один запрос: каждая — отдельный запрос к сайту

_CARD = re.compile(r'<a href="(/[a-z0-9-]+/(\d+)-[^"]+)" class="ez-resource-thumb__link internal-resource[^"]*"'
                   r'(?P<attrs>[^>]*)>\s*<img[^>]*?data-lazy-src="([^"]+)"', re.S)
_ITEMPROP = re.compile(r'<meta itemprop="(\w+)" content="([^"]*)"')
_TAG = re.compile(r'<li class="tag">\s*<a href="/free-video/[^"]*" class="tag-link" title="([^"]*)"')
_LICENSE_TYPE = re.compile(r'data-license-type="([a-z]+)"')
_RESOURCE_TYPE = re.compile(r"var resourceType = '([a-z]+)'")
_DOWNLOAD = re.compile(r'href="(/download/\d+\?[^"]+)"')
_ID = re.compile(r"^/?([a-z0-9-]+/\d+-[a-z0-9-]+)$")


def _meta(page: str) -> dict[str, str]:
    return {k: html.unescape(v) for k, v in _ITEMPROP.findall(page)}


def license_of(page: str) -> LicenseInfo:
    free = (_RESOURCE_TYPE.search(page) or [None, ""])[1] == "free"
    kind = (_LICENSE_TYPE.search(page) or [None, ""])[1]
    if kind == "editorial":
        name = EDITORIAL
    elif free and kind == "standard":
        name = FREE_LICENSE
    else:
        name = PRO_LICENSE
    status, note = licenses.evaluate(name)
    if name == FREE_LICENSE:
        note = "Укажите в титрах: «Видео: Videezy.com». " + note
    return LicenseInfo(name=name, url=LICENSE_URL, status=status, note=note.strip())


def parse_video(page: str, path: str) -> VideoResult:
    """Ролик со страницы Videezy (path — «industry/48837-old-printing-machine-working»)."""
    meta = _meta(page)
    if not meta.get("name"):
        raise NotFoundError(VideezySource.name, f"{VideezySource.name}: ролик удалён с площадки.")
    tags = [html.unescape(t) for t in _TAG.findall(page)]
    description = meta.get("description", "")
    page_url = f"{BASE}/{path}"
    return VideoResult(
        source=VideezySource.name,
        source_id=path,
        title=meta["name"],
        description=" ".join([description, ", ".join(tags)]).strip(),
        preview_url=meta.get("thumbnailUrl") or None,
        duration=None,  # Videezy пишет «PT0M0S» — длительность неизвестна
        width=None,
        height=None,
        has_audio=None,
        page_url=page_url,
        # Файл — по ссылке со страницы при скачивании (живёт только в сессии сайта).
        files=[VideoFile("оригинал с сайта", page_url, mime=None, page=True)],
        author=meta.get("author", ""),
        author_url=None,
        license=license_of(page),
        preview_video=meta.get("contentUrl") or None,
    )


def parse_search(page: str) -> list[tuple[str, str]]:
    """Карточки выдачи: (путь ролика, превью). Рекламные ролики платных стоков — не internal-resource."""
    seen, result = set(), []
    for m in _CARD.finditer(page):
        path = m.group(1).lstrip("/")
        if path not in seen and 'resource_is_premium=true' not in _decoded_hash(m.group("attrs")):
            seen.add(path)
            result.append((path, html.unescape(m.group(4))))
    return result


def _decoded_hash(attrs: str) -> str:
    import base64
    m = re.search(r'data-hash="([^"]+)"', attrs)
    if not m:
        return ""
    try:
        return base64.b64decode(m.group(1) + "===").decode("utf-8", "ignore")
    except ValueError:
        return ""


class VideezySource(HttpSource):
    name = "Videezy"
    homepage = BASE
    languages = frozenset({"en"})  # сайт ищет по английским словам
    max_level = 2
    max_parallel = 2
    limits = [(30, 60, "в минуту")]  # бережно к сайту: у него нет API и своих лимитов для программ

    def __init__(self, cache: Cache, session=None, now=None):
        super().__init__(cache, session, **({"now": now} if now else {}))
        if hasattr(self.session, "headers"):
            self.session.headers["User-Agent"] = BROWSER_UA

    def search(self, query: str, filters: SearchFilters, page: int = 1, lang: str = "en") -> list[VideoResult]:
        slug = "-".join(re.findall(r"[a-z0-9]+", query.lower()))
        if not slug:
            return []
        listing = self._fetch_text(f"{BASE}/free-video/{quote(slug)}", {"page": page} if page > 1 else {})
        results = []
        for path, thumb in parse_search(listing)[:MAX_RESULTS]:
            try:
                r = self.get_details(path)
            except NotFoundError:
                continue
            r.preview_url = r.preview_url or thumb
            results.append(r)
        return results

    def get_details(self, source_id: str) -> VideoResult:
        m = _ID.match(source_id)
        if not m:
            raise NotFoundError(self.name, f"{self.name}: непонятная ссылка на ролик.")
        return parse_video(self._fetch_text(f"{BASE}/{m.group(1)}"), m.group(1))

    def get_license(self, result: VideoResult) -> LicenseInfo:
        return result.license

    def resolve_download(self, file: VideoFile, session) -> str:
        """Ссылка на файл: открыть страницу ролика в сессии и пройти по её кнопке «Free Download»."""
        page = session.get(file.url, headers={"User-Agent": BROWSER_UA}, timeout=30)
        page.raise_for_status()
        link = _DOWNLOAD.search(page.text)
        if not link or license_of(page.text).status == licenses.REJECTED:
            raise NotFoundError(self.name, f"{self.name}: на странице нет бесплатного скачивания.")
        resp = session.get(urljoin(BASE, html.unescape(link.group(1))), headers={
            "User-Agent": BROWSER_UA, "Referer": file.url}, allow_redirects=False, timeout=30)
        target = resp.headers.get("Location", "")
        if resp.status_code not in (301, 302, 303, 307) or not target or "videezy.com/download" in target \
                or target.rstrip("/") == file.url.rstrip("/"):
            raise NotFoundError(self.name, f"{self.name}: сайт не отдал файл — скачайте ролик на его странице.")
        return target
