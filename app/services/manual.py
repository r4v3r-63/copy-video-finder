"""Получение ролика по ссылке пользователя без поискового запроса."""

import logging
import re
from urllib.parse import urlsplit, urlunsplit

from app.core import licenses
from app.core.models import LicenseInfo, VideoResult
from app.sources.web import KNOWN_DOMAINS, STOCK_DOMAINS, WebSource, known_site, parse_page, skipped, youtube_id

log = logging.getLogger(__name__)


def normalize_url(text: str) -> str:
    """HTTP(S)-страница без фрагмента; адреса с паролем и неполные ссылки не принимаются."""
    text = text.strip()
    try:
        parts = urlsplit(text)
        valid = (parts.scheme.lower() in ("http", "https") and parts.hostname
                 and parts.username is None and parts.password is None)
        _port = parts.port  # чтение свойства проверяет корректность порта
    except ValueError as exc:
        raise ValueError("Некорректная ссылка на страницу ролика.") from exc
    if not valid or any(c.isspace() or ord(c) < 32 or c == "\\" for c in text):
        raise ValueError("Вставьте полную ссылку на страницу ролика, начиная с https:// или http://.")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def _link_only(name: str, source_id: str, url: str) -> VideoResult:
    return VideoResult(source=name, source_id=source_id, title=f"Ролик {name}", description="", preview_url=None,
                       duration=None, width=None, height=None, has_audio=None, page_url=url,
                       license=LicenseInfo("Не указана", status=licenses.REVIEW,
                                           note="Ключ площадки не задан. Сохранится только ссылка; "
                                                "название и условия использования проверьте на сайте."))


def resolve_link(web: WebSource, text: str) -> VideoResult:
    url = normalize_url(text)
    parts = urlsplit(url)
    host = parts.hostname.removeprefix("www.").removeprefix("m.")
    if skipped(url):
        raise ValueError("Эта площадка не поддерживается: платные стоки и исключённые сайты не подключаются.")
    if host in ("youtube.com", "youtu.be"):
        video_id = youtube_id(url)
        if not video_id:
            raise ValueError("Нужна ссылка на отдельный ролик YouTube, а не канал, плейлист или Shorts.")
        result = web.youtube(video_id)
    elif host in ("vk.com", "vkvideo.ru") and re.fullmatch(r"/video-?\d+_\d+/?", parts.path):
        url = "https://vk.com/" + parts.path.strip("/")
        source = web.delegates.get("VK Видео")
        result = (source.get_details(url) if source and source.is_configured()
                  else _link_only("VK Видео", url, url))
    elif host == "coverr.co" and re.fullmatch(r"/videos/[a-zA-Z0-9-]+/?", parts.path):
        result = parse_page(web.pages.read(url), url) or _link_only("coverr.co", url, url)
        if result.license and licenses.is_paid(result.license.name):
            raise ValueError("Платные и редакционные ролики не добавляются.")
        # На странице может быть только preview: скачивание Coverr учитывается лишь через mp4_download из API.
        result.files, result.preview_video = [], None
        result.license = LicenseInfo("Условия Coverr", "https://coverr.co/license", status=licenses.REVIEW,
                                    note="По этой ссылке доступен просмотр на сайте. Проверьте условия ролика; "
                                         "скачивание Coverr через программу доступно для результатов его API.")
    else:
        known = known_site(url)
        if known and host == KNOWN_DOMAINS.get(known[0]):
            name, source_id = known
            source = web.delegates.get(name)
            if name == "Wikimedia Commons":
                raise ValueError("Wikimedia Commons исключена из источников программы.")
            result = (source.get_details(source_id) if source and source.is_configured()
                      else _link_only(name, source_id, url))
        elif host in STOCK_DOMAINS:
            raise ValueError("Не удалось распознать ссылку на отдельный ролик этой площадки.")
        else:
            result = parse_page(web.pages.read(url), url)
    if result is None:
        raise ValueError("На странице не удалось распознать ролик. Проверьте ссылку и доступность видео.")
    if result.license is None:
        result.license = LicenseInfo("Не указана", status=licenses.REVIEW)
    if licenses.is_paid(result.license.name):
        raise ValueError("Платные и редакционные ролики не добавляются.")
    if result.width and result.height and result.width <= result.height:
        raise ValueError("Программа работает только с горизонтальными роликами.")
    return result
