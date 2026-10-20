"""Выполнение поиска: все фразы плана на всех площадках, параллельно, в фоне.

Результаты объединяются по паре (площадка, ID) и фильтруются по длительности,
ориентации и лицензии: в выдачу попадают только подходящие ролики.
"""

import logging
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtCore import QThread, pyqtSignal

from app import licenses
from app.query import SearchPlan
from app.sources.base import (
    AuthError,
    NotFoundError,
    RateLimitError,
    SearchFilters,
    Source,
    SourceError,
    VideoFile,
    VideoResult,
)
from app.topic import AMBIGUOUS, NOT_PRINT, domain_words, in_print_domain, phrase_stems, topic_stems
from app.topic import WORD as _WORD
from app.topic import stem as _stem

log = logging.getLogger(__name__)


DURATION = "длительность"
SIZE = "размер или ориентация"
LICENSE = "лицензия"
OFF_TOPIC = "не по теме"
REJECTED_BEFORE = "отклонён ранее"
NO_FILES_PENALTY = 100  # ролики без ссылки на файл — после всех, которые можно скачать



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


# Слова полиграфии вообще: для темы поиска они слишком общие, но ролик с ними ближе к делу.
_PRINT_WORDS = {
    "print", "printing", "printer", "printed", "paper", "office", "document", "copy", "copier", "binding",
    "booklet", "brochure", "poster", "label", "sticker", "card", "typography", "ink", "toner", "cutter",
    "печать", "принтер", "бумага", "типография", "полиграфия", "копицентр", "документ", "переплет",
}


def print_stems(processes) -> set[str]:
    """Основы слов полиграфии: из всех 43 услуг справочника и общих терминов."""
    texts = [t for p in processes for t in (p.name, *p.aliases, *p.phrases_ru, *p.phrases_en, *p.key_ru, *p.key_en,
                                             *(t for level in p.levels for ts in level.phrases.values() for t in ts))]
    return (topic_stems(texts) - {_stem(w) for w in NOT_PRINT}) | {_stem(w) for w in _PRINT_WORDS}


def _tokens(result: VideoResult) -> set[str]:
    return {w.lower().replace("ё", "е") for w in _WORD.findall(f"{result.title} {result.description}")}


def relevance(result: VideoResult, stems: set[str], printing: set[str]) -> int:
    """Совпадения со словами запроса весят больше, со словами полиграфии — меньше."""
    tokens = _tokens(result)
    topic = sum(1 for s in stems if any(t.startswith(s) for t in tokens))
    domain = sum(1 for s in printing if any(t.startswith(s) for t in tokens))
    return topic * 10 + domain


def is_excluded(result: VideoResult, exclude) -> bool:
    """Есть ли в названии или тегах слово-исключение услуги."""
    if not exclude:
        return False
    tokens = _tokens(result)
    return any(t == w.lower() or t.startswith(w.lower()) and len(w) >= 4 for t in tokens for w in exclude)


_AMBIGUOUS = {_stem(w) for w in AMBIGUOUS}

def is_relevant(result: VideoResult, phrases: list[frozenset[str]], use_description: bool = True,
                domain=frozenset()) -> bool:
    """Есть ли в названии или тегах ролика ВСЕ значимые слова хотя бы одной фразы поиска.

    Поиск площадок нечёткий: Wikimedia находит слово где угодно, вплоть до категорий,
    Pixabay — по любому слову фразы. Одного слова мало: по «vinyl» находятся
    проигрыватели, по «hole» — дятел; для «vinyl cutter» нужны оба слова.
    Если судить не по чему (у фраз нет значимых слов или у ролика нет названия) —
    ролик не отсеивается.
    """
    text = f"{result.title} {result.description}" if use_description else result.title
    tokens = {w.lower().replace("ё", "е") for w in _WORD.findall(text or "")}
    if not tokens:
        return True
    # Только полиграфия и сувенирка: слово этой области и никаких слов стройки, еды, природы.
    if not in_print_domain(tokens, domain):
        return False
    if not phrases:
        return True
    matched = [p for p in phrases if all(any(t.startswith(s) for t in tokens) for s in p)]
    if not matched:
        return False
    # Совпало только многозначное слово («plotter», «scanner») — нужно ещё слово полиграфии рядом.
    if any(len(p) > 1 or next(iter(p)) not in _AMBIGUOUS for p in matched):
        return True
    return domain_words(tokens, domain) >= 2


def preview_file(result: VideoResult):
    """Файл для просмотра: отдельная ссылка площадки или самый лёгкий вариант не ниже 360p."""
    if result.preview_video:
        return VideoFile("просмотр", result.preview_video)
    files = sorted(result.files, key=lambda f: f.height or 0)
    for f in files:
        if (f.height or 0) >= 360:
            return f
    return files[-1] if files else None


# Сколько подходящих роликов достаточно, чтобы не расширять поиск дальше
# (на оборудование, действие, этапы, похожие операции).
ENOUGH = 12

class SearchWorker(QThread):
    found = pyqtSignal(list)  # list[VideoResult] — новые подходящие ролики
    problem = pyqtSignal(str)  # сообщение об ошибке площадки
    progress = pyqtSignal(int, int)  # выполнено запросов, всего

    def __init__(self, plan: SearchPlan, sources: list[Source], filters: SearchFilters,
                 only_without_attribution: bool = False, parent=None, printing: set[str] | None = None,
                 hidden: set[tuple[str, str]] | None = None):
        super().__init__(parent)
        self.plan = plan
        self.sources = sources
        self.filters = filters
        self.only_without_attribution = only_without_attribution
        self._cancel = threading.Event()
        self.seen: set[tuple[str, str]] = set()  # ролики, попавшие в выдачу
        self.rejections: dict[tuple[str, str], str] = {}  # ролик → причина отсева
        self.off_topic: dict[tuple[str, str], VideoResult] = {}  # отсеянные «не по теме» — можно показать
        self.stems = topic_stems([q.text for q in plan.queries])
        self.hidden = hidden or set()  # ролики, отклонённые пользователем, — не показываются
        self.phrases = phrase_stems([q.text for q in plan.queries], keep={q.text for q in plan.queries if q.key})
        # Фразы по уровням: ролик проверяется только фразами уже запущенных уровней, а степень
        # соответствия — по самому раннему уровню, фраза которого совпала (а не по запросу, вернувшему ролик).
        self._levels = sorted({q.level for q in plan.queries})
        self._level_phrases = {
            level: phrase_stems([q.text for q in plan.queries if q.level == level],
                                keep={q.text for q in plan.queries if q.key and q.level == level})
            for level in self._levels}
        self._level_match = {level: next(q.match for q in plan.queries if q.level == level) for level in self._levels}
        self.printing = printing or set()
        self.levels_used = 0  # до какого уровня расширения дошёл поиск
        # По теме, длительности и размеру подходят, но лицензия не свободная — показываются по кнопке.
        self.unlicensed: dict[tuple[str, str], VideoResult] = {}

    def cancel(self):
        self._cancel.set()

    @property
    def rejected(self) -> int:
        return sum(self.rejection_counts().values())

    def rejection_counts(self) -> Counter:
        """Причины отсева по уникальным роликам, которые так и не попали в выдачу."""
        return Counter(reason for key, reason in self.rejections.items() if key not in self.seen)

    def unlicensed_results(self) -> list[VideoResult]:
        """По теме подходят, лицензия не свободная (платные — никогда): только посмотреть, ближе к теме — первыми."""
        found = [r for key, r in self.unlicensed.items() if key not in self.seen]
        for r in found:
            r.view_only = True
            r.relevance = relevance(r, self.stems, self.printing)
        return sorted(found, key=lambda r: -r.relevance)

    def off_topic_results(self) -> list[VideoResult]:
        """Отсеянные только по теме (длительность, размер и лицензия подходят), ближе к полиграфии — первыми.

        Ролики с неподходящей лицензией сюда не попадают и не показываются никогда.
        """
        found = [r for key, r in self.off_topic.items() if key not in self.seen]
        for r in found:
            r.relevance = relevance(r, self.stems, self.printing)
        return sorted(found, key=lambda r: -r.relevance)

    def run(self):
        levels = sorted({q.level for q in self.plan.queries})
        failed_sources: set[str] = set()
        done = 0
        for level in levels:
            if self._cancel.is_set():
                return
            # Следующий уровень (оборудование, действие, похожие операции) — только если роликов мало.
            if level > 0 and len(self.seen) >= ENOUGH:
                break
            tasks = [(s, q) for s in self.sources for q in self.plan.queries
                     if q.level == level and q.lang in s.languages and level <= s.max_level]
            self.levels_used = level
            total = done + len(tasks)
            self.progress.emit(done, total)
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(self._run_one, s, q): (s, q) for s, q in tasks}
                for future in as_completed(futures):
                    done += 1
                    if self._cancel.is_set():
                        pool.shutdown(cancel_futures=True)
                        return
                    source, query = futures[future]
                    self._take(future, source, query, failed_sources)
                    self.progress.emit(done, total)

    def _take(self, future, source: Source, query, failed_sources: set[str]):
        """Разобрать ответ одной площадки на один запрос: отсеять и показать подходящие ролики."""
        try:
            results = future.result()
        except (AuthError, RateLimitError) as exc:
            # Одной ошибки по ключу или лимиту достаточно — не повторяем сообщение.
            if source.name not in failed_sources:
                failed_sources.add(source.name)
                self.problem.emit(exc.message)
            results = []
        except NotFoundError:
            # На поисковый запрос 404 — разовый сбой площадки, а не «ролик удалён».
            log.info("%s: 404 на поиск «%s»", source.name, query.text)
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
            level = self.matched_level(r, query.level, source.topic_in_description)
            on_topic = level is not None and not is_excluded(r, self.plan.exclude)
            reason = REJECTED_BEFORE if r.key in self.hidden else \
                rejection_reason(r, self.filters, self.only_without_attribution)
            if reason is None and not on_topic:
                reason = OFF_TOPIC
            if reason == LICENSE and not licenses.is_paid(r.license.name if r.license else "") and on_topic:
                r.match_hint, r.found_by = self._level_match[level], query.text
                self.unlicensed[r.key] = r
            if reason:
                # Ролик, не подошедший по одной фразе, может подойти по другой.
                self.rejections[r.key] = reason
                if reason == OFF_TOPIC:
                    self.off_topic[r.key] = r
                continue
            self.seen.add(r.key)
            r.match_hint, r.found_by = self._level_match[level], query.text
            r.relevance = relevance(r, self.stems, self.printing)
            if not r.files:
                r.relevance -= NO_FILES_PENALTY  # скачать можно только на сайте — ниже в выдаче
            fresh.append(r)
        if fresh:
            self.found.emit(fresh)

    def matched_level(self, r: VideoResult, upto: int, use_description: bool = True) -> int | None:
        """Самый ранний уровень (не дальше запущенного), фраза которого есть у ролика; None — не по теме."""
        for level in self._levels:
            if level > upto:
                break
            if self._level_phrases[level] and is_relevant(r, self._level_phrases[level], use_description,
                                                          self.printing):
                return level
        # Ни у одного уровня нет значимых слов — судить не по чему, как в is_relevant.
        if not any(self._level_phrases[lv] for lv in self._levels if lv <= upto):
            return upto if is_relevant(r, [], use_description, self.printing) else None
        return None

    def _run_one(self, source: Source, query) -> list[VideoResult]:
        if self._cancel.is_set():
            return []
        return source.search(query.text, self.filters, page=1, lang=query.lang)
