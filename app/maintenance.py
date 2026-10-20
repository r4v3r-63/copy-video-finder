"""Фоновое обслуживание каталога: проверка ссылок (ТЗ, 10) и хеши превью для поиска дублей."""

import logging
import threading
import time

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from app import catalog as cat
from app import similar
from app.sources.base import NotFoundError, Source, SourceError
from app.sources.http import USER_AGENT

log = logging.getLogger(__name__)

LAST_LINK_CHECK = "last_link_check_at"


def link_check_due(store: cat.Catalog, every_days: int, now=time.time) -> bool:
    if every_days <= 0:
        return False
    last = float(store.get_meta(LAST_LINK_CHECK) or 0)
    return now() - last >= every_days * 86400


def check_links(store: cat.Catalog, sources: list[Source], cancel=None, progress=None, entries=None) -> dict:
    """Проверить, есть ли ролики каталога на площадках.

    «Удалён» — только по явному ответу площадки «не найдено». Сбой сети или лимит —
    ролик не трогаем (проверим в следующий раз). Площадки без ключа пропускаются.
    """
    by_name = {s.name: s for s in sources if s.is_configured()}
    entries = entries if entries is not None else [e for e in store.entries() if e.status != cat.REJECTED]
    counts = {"checked": 0, "removed": 0, "skipped": 0, "restored": 0}
    for i, e in enumerate(entries, 1):
        if cancel and cancel.is_set():
            break
        if progress:
            progress(i, len(entries))
        source = by_name.get(e.result.source)
        if source is None:
            counts["skipped"] += 1
            continue
        try:
            source.get_details(e.result.source_id)
            available = True
        except NotFoundError:
            available = False
        except (SourceError, NotImplementedError) as exc:
            log.info("Проверка ссылки %s %s: %s", e.result.source, e.result.source_id, exc)
            counts["skipped"] += 1
            continue
        counts["checked"] += 1
        if not available and e.available:
            counts["removed"] += 1
        if available and not e.available:
            counts["restored"] += 1
        store.set_available(e.id, available)
    return counts


def hash_previews(store: cat.Catalog, session=None, cancel=None) -> int:
    """Посчитать хеши превью, которых ещё нет. Возвращает число новых хешей."""
    session = session or requests.Session()
    done = 0
    for e in store.entries():
        if cancel and cancel.is_set():
            break
        if e.phash or not e.result.preview_url:
            continue
        try:
            resp = session.get(e.result.preview_url, headers={"User-Agent": USER_AGENT}, timeout=15)
            if resp.status_code != 200:
                continue
            store.set_phash(e.id, similar.dhash(resp.content))
            done += 1
        except (requests.RequestException, OSError, ValueError) as exc:  # битая картинка — пропускаем
            log.info("Хеш превью %s: %s", e.id, exc)
    return done


class LinkCheckWorker(QThread):
    progress = pyqtSignal(int, int)
    done = pyqtSignal(dict)

    def __init__(self, store: cat.Catalog, sources: list[Source], parent=None, entries=None):
        super().__init__(parent)
        self.store = store
        self.sources = sources
        self.entries = entries
        self.cancel = threading.Event()

    def run(self):
        counts = check_links(self.store, self.sources, self.cancel, lambda i, n: self.progress.emit(i, n), self.entries)
        if not self.cancel.is_set() and self.entries is None:
            self.store.set_meta(LAST_LINK_CHECK, str(time.time()))
        self.done.emit(counts)


class DuplicateWorker(QThread):
    done = pyqtSignal(list)  # пары похожих роликов

    def __init__(self, store: cat.Catalog, parent=None):
        super().__init__(parent)
        self.store = store
        self.cancel = threading.Event()

    def run(self):
        hash_previews(self.store, cancel=self.cancel)
        self.done.emit(similar.find_duplicates([e for e in self.store.entries() if e.status != cat.REJECTED]))
