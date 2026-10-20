"""Получение ролика по ссылке пользователя без поискового запроса."""

import logging
import re
import threading
from urllib.parse import parse_qs, urlsplit, urlunsplit

from PyQt6.QtCore import QThread

from app import licenses
from app.sources.base import LicenseInfo, SourceError, VideoResult
from app.sources.web import STOCK_DOMAINS, WebSource, known_site, parse_page, skipped

log = logging.getLogger(__name__)
_DOMAINS = {"Pexels": "pexels.com", "Pixabay": "pixabay.com", "Vimeo": "vimeo.com",
            "Wikimedia Commons": "commons.wikimedia.org", "Internet Archive": "archive.org", "Videezy": "videezy.com"}


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
        video_id = (parts.path.strip("/") if host == "youtu.be" else
                    (parse_qs(parts.query).get("v", [""])[0] if parts.path == "/watch" else
                     parts.path.rsplit("/", 1)[-1] if parts.path.startswith(("/embed/", "/live/")) else ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError("Нужна ссылка на отдельный ролик YouTube, а не канал, плейлист или Shorts.")
        result = web.youtube(video_id)
    elif host in ("vk.com", "vkvideo.ru") and re.fullmatch(r"/video-?\d+_\d+/?", parts.path):
        url = "https://vk.com/" + parts.path.strip("/")
        source = web.delegates.get("VK Видео")
        result = (source.get_details(url) if source and source.is_configured()
                  else _link_only("VK Видео", url, url))
    else:
        known = known_site(url)
        if known and host == _DOMAINS.get(known[0]):
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


class LinkWorker(QThread):
    """Сетевые запросы в фоне; отмена запрещает использование полученного результата."""

    def __init__(self, web: WebSource, url: str, parent=None):
        super().__init__(parent)
        self.web, self.url = web, url
        self.cancel = threading.Event()
        self.result: VideoResult | None = None
        self.error = ""
        # У запроса остаётся штатный тайм-аут, ожидание лимита прерывается сразу.
        for source in (web, web.pages, *web.delegates.values()):
            source.stop = self.cancel

    def run(self):
        try:
            if not self.cancel.is_set():
                result = resolve_link(self.web, self.url)
                if not self.cancel.is_set():
                    self.result = result
        except (SourceError, ValueError) as exc:
            self.error = str(exc)
        except Exception:  # noqa: BLE001 — ошибка разметки не должна завершать поток без сообщения
            log.exception("Ошибка получения сведений о ролике по ссылке")
            self.error = "Не удалось получить сведения о ролике. Подробности — в журнале."
