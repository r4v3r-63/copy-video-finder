"""Vimeo API (v3.4): поиск только среди роликов с лицензиями Creative Commons.

Нужен личный токен доступа с правами public (создаётся в кабинете разработчика
Vimeo). Лицензия у каждого ролика своя — поле license; NC и ND отклоняются.
Ссылки на файлы Vimeo отдаёт не для всех роликов: если их нет, ролик можно
посмотреть и скачать только на его странице (если автор разрешил скачивание).
"""

import logging
import time
from datetime import datetime

import requests

from app import licenses
from app.cache import Cache
from app.sources.base import LicenseInfo, NotFoundError, SearchFilters, VideoFile, VideoResult
from app.sources.http import HttpSource, quality_label

log = logging.getLogger(__name__)

API = "https://api.vimeo.com"
PER_PAGE = 20
FIELDS = "uri,name,description,link,duration,width,height,license,pictures.sizes,user.name,user.link,download,tags.name"

# Коды лицензий Vimeo → название и условия Creative Commons.
LICENSES = {
    "cc0": ("CC0", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "by": ("CC BY", "https://creativecommons.org/licenses/by/3.0/"),
    "by-sa": ("CC BY-SA", "https://creativecommons.org/licenses/by-sa/3.0/"),
    "by-nd": ("CC BY-ND", "https://creativecommons.org/licenses/by-nd/3.0/"),
    "by-nc": ("CC BY-NC", "https://creativecommons.org/licenses/by-nc/3.0/"),
    "by-nc-sa": ("CC BY-NC-SA", "https://creativecommons.org/licenses/by-nc-sa/3.0/"),
    "by-nc-nd": ("CC BY-NC-ND", "https://creativecommons.org/licenses/by-nc-nd/3.0/"),
}
NO_FILES_NOTE = "Vimeo не дал ссылку на файл: скачайте ролик на его странице, если автор разрешил скачивание."


def _thumbnail(v: dict) -> str | None:
    sizes = (v.get("pictures") or {}).get("sizes") or []
    sizes = [s for s in sizes if s.get("link")]
    if not sizes:
        return None
    # Ближайший к ширине сетки (около 640), чтобы не тянуть большие картинки.
    return min(sizes, key=lambda s: abs((s.get("width") or 0) - 640))["link"]


def license_info(code: str | None) -> LicenseInfo:
    name, url = LICENSES.get((code or "").lower(), ("", None))
    status, note = licenses.evaluate(name)
    return LicenseInfo(name=name or "не указана", url=url, status=status, note=note)


def parse_video(v: dict) -> VideoResult:
    files = []
    for d in v.get("download") or []:
        if not d.get("link") or d.get("quality") == "source":  # исходник бывает в любом формате и огромным
            continue
        short = min(d["width"], d["height"]) if d.get("width") and d.get("height") else d.get("height")
        files.append(VideoFile(quality=quality_label(short), url=d["link"], width=d.get("width"),
                               height=d.get("height"), mime=d.get("type")))
    files.sort(key=lambda f: f.height or 0, reverse=True)
    user = v.get("user") or {}
    tags = ", ".join(t.get("name", "") for t in v.get("tags") or [] if t.get("name"))
    lic = license_info(v.get("license"))
    if not files:
        lic.note = (lic.note + " " if lic.note else "") + NO_FILES_NOTE
    return VideoResult(
        source=VimeoSource.name,
        source_id=(v.get("uri") or "").rsplit("/", 1)[-1],
        title=v.get("name") or "",
        # Теги Vimeo заполняют как попало («3d-printer, laser-engravers, cutters»), поэтому они
        # только в описании, а тема проверяется по названию ролика.
        description=", ".join(t for t in (tags, (v.get("description") or "")[:500]) if t),
        preview_url=_thumbnail(v),
        duration=v.get("duration"),
        width=v.get("width"),
        height=v.get("height"),
        has_audio=None,
        page_url=v.get("link") or "",
        files=files,
        author=user.get("name", ""),
        author_url=user.get("link"),
        license=lic,
    )


class VimeoSource(HttpSource):
    name = "Vimeo"
    homepage = "https://vimeo.com"
    topic_in_description = False
    # Бесплатное приложение Vimeo: 50 запросов в минуту (заголовок X-RateLimit-Limit); точнее — по заголовкам.
    limits = [(50, 60, "в минуту")]

    def __init__(self, token: str, cache: Cache, session: requests.Session | None = None, now=time.time):
        super().__init__(cache, session, now)
        self.token = token

    def is_configured(self) -> bool:
        return bool(self.token)

    def missing_key_message(self) -> str:
        return "Токен Vimeo не задан — укажите его в настройках."

    def _get(self, path: str, params: dict) -> dict:
        headers = {"Authorization": f"bearer {self.token}", "Accept": "application/vnd.vimeo.*+json;version=3.4"}
        return self._fetch(API + path, params, headers=headers)

    def _after_response(self, resp) -> None:
        super()._after_response(resp)
        remaining = resp.headers.get("X-RateLimit-Remaining")
        reset = resp.headers.get("X-RateLimit-Reset")  # время сброса, ISO 8601
        if remaining is not None and reset and str(remaining).strip() == "0":
            try:
                self.cache.block_until(self.name, datetime.fromisoformat(reset.replace("Z", "+00:00")).timestamp())
            except ValueError:
                log.warning("Непонятный X-RateLimit-Reset: %s", reset)

    def search(self, query: str, filters: SearchFilters, page: int = 1, lang: str = "en") -> list[VideoResult]:
        # filter=CC — только ролики под Creative Commons; NC и ND отсеются по таблице лицензий.
        data = self._get("/videos", {"query": query, "filter": "CC", "per_page": PER_PAGE, "page": page,
                                     "sort": "relevant", "fields": FIELDS})
        return [parse_video(v) for v in data.get("data") or [] if v.get("uri")]

    def get_details(self, source_id: str) -> VideoResult:
        data = self._get(f"/videos/{source_id}", {"fields": FIELDS})
        if not data.get("uri"):
            raise NotFoundError(self.name, "Vimeo: ролик удалён с площадки.")
        return parse_video(data)

    def get_license(self, result: VideoResult) -> LicenseInfo:
        return result.license or self.get_details(result.source_id).license
