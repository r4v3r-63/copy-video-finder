"""Сведения о ролике по ссылке пользователя — в фоне."""

import logging
import threading

from PyQt6.QtCore import QThread

from app.core.models import VideoResult
from app.services import manual
from app.sources.base import SourceError
from app.sources.web import WebSource

log = logging.getLogger(__name__)


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
                result = manual.resolve_link(self.web, self.url)
                if not self.cancel.is_set():
                    self.result = result
        except (SourceError, ValueError) as exc:
            self.error = str(exc)
        except Exception:  # noqa: BLE001 — ошибка разметки не должна завершать поток без сообщения
            log.exception("Ошибка получения сведений о ролике по ссылке")
            self.error = "Не удалось получить сведения о ролике. Подробности — в журнале."
