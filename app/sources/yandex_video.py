"""Яндекс Видео: одна выдача — ролики Rutube, VK Видео, Дзена, Одноклассников, YouTube и других сайтов.

API для программ у Яндекс Видео нет, поэтому читается страница выдачи (решение пользователя
20.10.2026: «добавь поиск на Rutube, VK Видео, Яндекс Видео и подобных сайтах»). В странице
встроены данные роликов: название, описание, длительность, сайт, ссылка на страницу, превью.
Читаем бережно: свои лимиты, кэш 24 часа; на капчу — пауза 10 минут, обходить не пытаемся.

Файлов роликов здесь нет: такие ролики смотрят на сайте («Открыть страницу ролика»).
Лицензия — условия сайта, все права у автора: «Требует проверки».
"""

import html
import json
import re
import time
from urllib.parse import urlparse

from app.core import licenses
from app.core.models import LicenseInfo, SearchFilters, VideoResult
from app.sources.base import RateLimitError
from app.sources.cache import Cache
from app.sources.http import HttpSource
from app.sources.videezy import BROWSER_UA

SEARCH_URL = "https://yandex.ru/video/search"
BLOCK_SECONDS = 600
MAX_RESULTS = 30
_CLIP = re.compile(r'"(\d{6,})":\s*\{"videoId":\s*"\1"')
_YOUTUBE = re.compile(r"youtube\.com/(?:watch\?(?:[^#]*&)?v=|live/|embed/)([A-Za-z0-9_-]{11})"
                      r"|youtu\.be/([A-Za-z0-9_-]{11})")
_MARKS = re.compile("[\u0007]|\\[|\\]")  # «\u0007[Laser\u0007]» — подсветка слов запроса


def blocked(page: str, status: int) -> bool:
    """Капча вместо выдачи."""
    head = page[:30000].lower()
    return status in (403, 429) or "showcaptcha" in head or "checkcaptcha" in head or "smartcaptcha" in head


def parse_results(page: str) -> list[VideoResult]:
    """Ролики из встроенных данных страницы выдачи (два вида записей: ролик и его описание с превью)."""
    decoder = json.JSONDecoder()
    clips: dict[str, dict] = {}
    docs: dict[str, dict] = {}
    for m in _CLIP.finditer(page):
        try:
            obj, _ = decoder.raw_decode(page, m.start() + m.group(0).index("{"))
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("cleanTitle") and isinstance(obj.get("host"), dict):
            clips.setdefault(obj["videoId"], obj)
        elif obj.get("preview") or obj.get("description"):
            docs.setdefault(obj["videoId"], obj)
    results = []
    for video_id, clip in clips.items():
        r = _result(clip, docs.get(video_id, {}))
        if r is not None:
            results.append(r)
    return results


def _https(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    return "https://" + url[len("http://"):] if url.startswith("http://") else url


def _result(clip: dict, doc: dict) -> VideoResult | None:
    host = clip["host"]
    page_url = _https(str(host.get("href") or (clip.get("player") or {}).get("videoUrl") or ""))
    if not page_url.startswith("https://"):
        return None
    site = str(host.get("title") or urlparse(page_url).netloc.removeprefix("www."))
    youtube = _YOUTUBE.search(page_url)
    if youtube:  # тот же ролик YouTube, что и из поиска по интернету, — одна пара (площадка, ID)
        site, source_id = "YouTube", youtube.group(1) or youtube.group(2)
        page_url = f"https://www.youtube.com/watch?v={source_id}"
    elif "/shorts/" in page_url:
        return None
    else:
        source_id = page_url
    duration = (clip.get("duration") or {}).get("value")
    poster = ((doc.get("preview") or {}).get("posterSrc") or "") if isinstance(doc.get("preview"), dict) else ""
    channel = (host.get("secondPart") or {}).get("name", "") if isinstance(host.get("secondPart"), dict) else ""
    title = html.unescape(_MARKS.sub("", str(clip.get("cleanTitle") or clip.get("title"))))[:200]
    return VideoResult(
        source=site, source_id=source_id, title=title,
        description=html.unescape(str(doc.get("description") or ""))[:500],
        preview_url=_https(poster) if poster else None,
        duration=float(duration) if isinstance(duration, (int, float)) else None, width=None, height=None,
        has_audio=None, page_url=page_url, files=[], author=str(channel),
        license=LicenseInfo(name=f"Условия {site}", url=None, status=licenses.REVIEW,
                            note="Все права у автора, если на странице ролика не сказано иное. "
                                 "Скачать нельзя — смотрите на сайте."),
    )


class YandexVideoSource(HttpSource):
    name = "Яндекс Видео"
    homepage = "https://yandex.ru/video"
    # Только русские фразы названия и оборудования: англоязычные ролики YouTube приходят и из интернета,
    # а лимит у страницы выдачи невелик — на одну услугу хватает (решение 20.10.2026, проверено вживую).
    languages = frozenset({"ru"})
    max_level = 1
    max_parallel = 1
    topic_in_description = True
    limits = [(10, 60, "в минуту"), (300, 86400, "в сутки")]

    def __init__(self, cache: Cache, session=None, now=time.time):
        super().__init__(cache, session, now=now)
        if hasattr(self.session, "headers"):
            self.session.headers["User-Agent"] = BROWSER_UA

    def _blocked(self, resp) -> RateLimitError | None:
        if blocked(resp.text, resp.status_code):
            self.cache.block_until(self.name, self._now() + BLOCK_SECONDS)
            return RateLimitError(self.name, "Яндекс Видео попросил подтвердить, что вы не робот, — "
                                             "10 минут поиск идёт без него.")
        return None

    def search(self, query: str, filters: SearchFilters, page: int = 1, lang: str = "ru") -> list[VideoResult]:
        params = {"text": query}
        if page > 1:
            params["p"] = page - 1
        return parse_results(self._fetch_text(SEARCH_URL, params, check=self._blocked))[:MAX_RESULTS]

    def get_details(self, source_id: str) -> VideoResult:
        raise NotImplementedError  # отдельной страницы ролика у выдачи нет — проверка ссылок его пропускает

    def get_license(self, result: VideoResult) -> LicenseInfo:
        return result.license
