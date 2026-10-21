"""Обслуживание каталога в фоне: проверка ссылок и поиск похожих дублей."""

import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal

from app.core import similar
from app.services.maintenance import LAST_LINK_CHECK, check_links, hash_previews
from app.sources.base import Source
from app.storage import catalog as cat


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
