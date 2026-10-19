"""Выполнение поиска: все фразы плана на всех площадках, параллельно, в фоне.

Результаты объединяются по паре (площадка, ID) и фильтруются по длительности,
ориентации и лицензии: в выдачу попадают только подходящие ролики.
"""

import logging
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtCore import QThread, pyqtSignal

from app import licenses
from app.query import SearchPlan
from app.sources.base import AuthError, RateLimitError, SearchFilters, Source, SourceError, VideoResult

log = logging.getLogger(__name__)


DURATION = "длительность"
SIZE = "размер или ориентация"
LICENSE = "лицензия"
OFF_TOPIC = "не по теме"

# Слова, по которым нельзя судить о теме ролика: площадки находят по ним что угодно
# (по «wide», «large», «cold» Pixabay отдаёт пляжи и города).
_GENERIC = {
    "machine", "machines", "process", "printing", "print", "printer", "paper", "video", "equipment",
    "making", "office", "shop", "and", "the", "for", "with", "wide", "large", "format", "cold", "hot",
    "small", "big", "color", "colour", "digital", "roll", "sheet", "hand", "manual", "automatic",
    "professional", "stand", "pull", "taking", "production", "output", "press", "photo", "studio",
    "машина", "станок", "печать", "бумага", "видео", "оборудование", "для", "на", "широкоформатный",
    "широкоформатная", "холодная", "горячая", "цветная", "цифровая", "большой", "рулонный", "листовой",
    "установка", "сборка", "изготовление", "фото",
}
_WORD = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def rejection_reason(result: VideoResult, filters: SearchFilters, only_without_attribution: bool = False) -> str | None:
    """Почему ролик не показывается; None — ролик подходит."""
    if result.duration is not None and not (filters.min_duration <= result.duration <= filters.max_duration):
        return DURATION
    if result.width and result.height:
        if filters.orientation == "landscape" and result.width <= result.height:
            return SIZE
        if filters.orientation == "portrait" and result.width >= result.height:
            return SIZE
        if min(result.width, result.height) < filters.min_height:
            return SIZE
    status = result.license.status if result.license else licenses.REVIEW
    allowed = {licenses.ALLOWED} if only_without_attribution else licenses.SUITABLE
    if status not in allowed:
        return LICENSE
    return None


def is_suitable(result: VideoResult, filters: SearchFilters, only_without_attribution: bool = False) -> bool:
    return rejection_reason(result, filters, only_without_attribution) is None


def _stem(word: str) -> str:
    """Грубая основа слова: lamination/laminated/laminator → lamina, ламинация → ламина."""
    return word if len(word) <= 5 else word[: max(5, len(word) - 4)]


def topic_stems(texts: list[str]) -> set[str]:
    """Основы значимых слов всех фраз поиска — на обоих языках сразу.

    Pixabay отдаёт английские теги и на русский запрос, поэтому ролик, найденный
    по русской фразе, проверяется и по английским словам той же услуги.
    """
    words = {w.lower().replace("ё", "е") for text in texts for w in _WORD.findall(text)}
    return {_stem(w) for w in words if len(w) >= 3 and w not in _GENERIC and not w.isdigit()}


# Слова полиграфии вообще: для темы поиска они слишком общие, но ролик с ними ближе к делу.
_PRINT_WORDS = {
    "print", "printing", "printer", "printed", "paper", "office", "document", "copy", "copier", "binding",
    "booklet", "brochure", "poster", "label", "sticker", "card", "typography", "ink", "toner", "cutter",
    "печать", "принтер", "бумага", "типография", "полиграфия", "копицентр", "документ", "переплет",
}


def print_stems(processes) -> set[str]:
    """Основы слов полиграфии: из всех 43 услуг справочника и общих терминов."""
    texts = [t for p in processes for t in (p.name, *p.aliases, *p.phrases_ru, *p.phrases_en)]
    return topic_stems(texts) | {_stem(w) for w in _PRINT_WORDS}


def _tokens(result: VideoResult) -> set[str]:
    return {w.lower().replace("ё", "е") for w in _WORD.findall(f"{result.title} {result.description}")}


def relevance(result: VideoResult, stems: set[str], printing: set[str]) -> int:
    """Совпадения со словами запроса весят больше, со словами полиграфии — меньше."""
    tokens = _tokens(result)
    topic = sum(1 for s in stems if any(t.startswith(s) for t in tokens))
    domain = sum(1 for s in printing if any(t.startswith(s) for t in tokens))
    return topic * 10 + domain


def is_relevant(result: VideoResult, stems: set[str], use_description: bool = True) -> bool:
    """Есть ли в названии или тегах ролика значимое слово поиска.

    Поиск площадок нечёткий: Wikimedia находит слово где угодно, вплоть до категорий,
    Pixabay — по любому слову фразы. Если судить не по чему (нет значимых слов
    в запросе или у ролика нет названия) — ролик не отсеивается.
    """
    text = f"{result.title} {result.description}" if use_description else result.title
    title = {w.lower().replace("ё", "е") for w in _WORD.findall(text or "")}
    if not stems or not title:
        return True
    return any(t.startswith(stem) for t in title for stem in stems)


def preview_file(result: VideoResult):
    """Файл для просмотра: самый лёгкий из вариантов не ниже 360p, иначе любой."""
    files = sorted(result.files, key=lambda f: f.height or 0)
    for f in files:
        if (f.height or 0) >= 360:
            return f
    return files[-1] if files else None


class SearchWorker(QThread):
    found = pyqtSignal(list)  # list[VideoResult] — новые подходящие ролики
    problem = pyqtSignal(str)  # сообщение об ошибке площадки
    progress = pyqtSignal(int, int)  # выполнено запросов, всего

    def __init__(self, plan: SearchPlan, sources: list[Source], filters: SearchFilters,
                 only_without_attribution: bool = False, parent=None, printing: set[str] | None = None):
        super().__init__(parent)
        self.plan = plan
        self.sources = sources
        self.filters = filters
        self.only_without_attribution = only_without_attribution
        self._cancel = threading.Event()
        self.seen: set[tuple[str, str]] = set()  # ролики, попавшие в выдачу
        self.rejections: dict[tuple[str, str], str] = {}  # ролик → причина отсева
        self.stems = topic_stems([q.text for q in plan.queries])
        self.printing = printing or set()

    def cancel(self):
        self._cancel.set()

    @property
    def rejected(self) -> int:
        return sum(self.rejection_counts().values())

    def rejection_counts(self) -> Counter:
        """Причины отсева по уникальным роликам, которые так и не попали в выдачу."""
        return Counter(reason for key, reason in self.rejections.items() if key not in self.seen)

    def run(self):
        tasks = [(s, q) for s in self.sources for q in self.plan.queries]
        total = len(tasks)
        done = 0
        failed_sources: set[str] = set()
        self.progress.emit(0, total)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(self._run_one, s, q): (s, q) for s, q in tasks}
            for future in as_completed(futures):
                done += 1
                if self._cancel.is_set():
                    pool.shutdown(cancel_futures=True)
                    return
                source, query = futures[future]
                try:
                    results = future.result()
                except (AuthError, RateLimitError) as exc:
                    # Одной ошибки по ключу или лимиту достаточно — не повторяем сообщение.
                    if source.name not in failed_sources:
                        failed_sources.add(source.name)
                        self.problem.emit(exc.message)
                    results = []
                except SourceError as exc:
                    self.problem.emit(exc.message)
                    results = []
                except Exception:  # noqa: BLE001 — ошибка одной площадки не должна ронять поиск
                    log.exception("Ошибка поиска на %s", source.name)
                    self.problem.emit(f"Непредвиденная ошибка при поиске на {source.name}, подробности в журнале.")
                    results = []
                fresh = []
                for r in results:
                    if r.key in self.seen:
                        continue
                    reason = rejection_reason(r, self.filters, self.only_without_attribution)
                    if reason is None and not is_relevant(r, self.stems, source.topic_in_description):
                        reason = OFF_TOPIC
                    if reason:
                        # Ролик, не подошедший по одной фразе, может подойти по другой.
                        self.rejections[r.key] = reason
                        continue
                    self.seen.add(r.key)
                    r.relevance = relevance(r, self.stems, self.printing)
                    fresh.append(r)
                if fresh:
                    self.found.emit(fresh)
                self.progress.emit(done, total)

    def _run_one(self, source: Source, query) -> list[VideoResult]:
        if self._cancel.is_set():
            return []
        return source.search(query.text, self.filters, page=1, lang=query.lang)
