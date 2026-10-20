"""Построение поисковых запросов на русском и английском (и уровни расширения на немецком и французском).

Для услуги из справочника берутся её русские и английские фразы. Свободный
русский текст ищется как есть и в переводе по встроенному словарю, свободный
английский — как есть и с русскими фразами услуги, если он совпал с её
английской фразой. Внешние сервисы перевода не используются.

У услуги из справочника, кроме фраз по названию (уровень 0), есть уровни расширения:
оборудование, действие, отдельные этапы, похожие операции. Поиск переходит к следующему
уровню, только если роликов по прежним мало (`search.ENOUGH`).
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app import paths
from app.processes import LANGS, Process, find_by_display, normalize, suggest
from app.topic import key_words

RU = "ru"
EN = "en"
DE = "de"
FR = "fr"
BASE_LEVEL = "Название услуги"
MAX_GROUP = 4  # больше услуг на один запрос — слишком много запросов к площадкам

_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
_TOKEN = re.compile(r"[a-zа-яё0-9äöüßàâæçéèêëîïôœùûÿ]+", re.IGNORECASE)


@dataclass(frozen=True)
class SearchQuery:
    text: str
    lang: str  # RU, EN, DE или FR; площадки передают его в параметр языка API
    key: bool = False  # ключевое слово услуги из справочника (подтверждает тему даже одним словом)
    level: int = 0  # уровень расширения поиска: 0 — название услуги
    match: str = ""  # степень соответствия, которую подсказывает уровень («» — точное)


@dataclass
class SearchPlan:
    process: Process | None
    queries: list[SearchQuery]
    note: str = ""
    exclude: frozenset[str] = frozenset()  # слова-исключения услуг плана
    levels: list[str] = None  # названия уровней: [0] — название услуги, дальше — расширение

    def __post_init__(self):
        if self.levels is None:
            self.levels = [BASE_LEVEL]

    def texts(self, lang: str) -> list[str]:
        return [q.text for q in self.queries if q.lang == lang]


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


class Dictionary:
    """Словарь основ русских слов для перевода запроса на английский."""

    def __init__(self, terms: dict[str, str], stopwords: list[str]):
        self.stopwords = {_norm(w) for w in stopwords}
        # Ключ — кортеж основ; длинные ключи проверяются первыми.
        self.terms = sorted(
            ((tuple(_norm(k).split()), v) for k, v in terms.items()),
            key=lambda item: (-len(item[0]), -sum(len(s) for s in item[0])),
        )

    @classmethod
    def load(cls, path: Path | None = None) -> "Dictionary":
        if path is None:
            user_copy = paths.terms_file()
            path = user_copy if user_copy.exists() else paths.resources_dir() / "terms_ru_en.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(raw["terms"], raw.get("stopwords", []))

    def translate(self, text: str) -> str:
        """Перевод по словарю; непереведённые слова отбрасываются.

        Пустая строка — если не удалось перевести ни одного слова.
        """
        tokens = [t for t in _TOKEN.findall(_norm(text)) if t not in self.stopwords]
        words: list[str] = []
        i = 0
        while i < len(tokens):
            for stems, english in self.terms:
                n = len(stems)
                if all(i + k < len(tokens) and tokens[i + k].startswith(stems[k]) for k in range(n)):
                    for word in english.split():
                        if word not in words:
                            words.append(word)
                    i += n
                    break
            else:
                if not _CYRILLIC.search(tokens[i]):
                    words.append(tokens[i])  # латиница и цифры остаются как есть
                i += 1
        return " ".join(words)


def is_russian(text: str) -> bool:
    return bool(_CYRILLIC.search(text))


def with_key_words(queries: list[SearchQuery], group: list[Process]) -> list[SearchQuery]:
    """Добавить ключевые слова услуг («laminator», «ламинатор»).

    Длинные фразы площадки понимают плохо (Pixabay ищет по любому слову фразы),
    а по короткому ключевому слову находятся хотя бы близкие ролики. Ключевые слова
    задаются в справочнике; если их нет — берётся самое частое значимое слово фраз.
    """
    extra = []
    for p in group:
        extra += [SearchQuery(w, RU, key=bool(p.key_ru)) for w in (p.key_ru or key_words(p.phrases_ru))]
        extra += [SearchQuery(w, EN, key=bool(p.key_en)) for w in (p.key_en or key_words(p.phrases_en))]
    return queries + extra


def _unique(queries: list[SearchQuery]) -> list[SearchQuery]:
    seen, result = set(), []
    for q in queries:
        key = (_norm(q.text.strip()), q.lang)
        if key[0] and key not in seen:
            seen.add(key)
            result.append(q)
    return result


def matching_group(processes: list[Process], text: str) -> list[Process]:
    """Услуги, у которых запрос совпал с названием категории или с синонимом."""
    norm = normalize(text)
    return [p for p in processes if norm == normalize(p.category) or norm in (normalize(a) for a in p.aliases)]


def build_plan(text: str, processes: list[Process], dictionary: Dictionary) -> SearchPlan:
    text = " ".join(text.split())
    if not text:
        return SearchPlan(None, [])

    proc = find_by_display(processes, text)
    if proc:
        queries = [SearchQuery(p, RU) for p in proc.phrases_ru] + [SearchQuery(p, EN) for p in proc.phrases_en]
        queries = with_key_words(queries, [proc])
        for i, level in enumerate(proc.levels, 1):
            queries += [SearchQuery(t, lang, level=i, match=level.match)
                        for lang in LANGS for t in level.phrases.get(lang, [])]
        return SearchPlan(proc, _unique(queries), exclude=frozenset(proc.exclude),
                          levels=[BASE_LEVEL] + [lv.name for lv in proc.levels])

    # В первую очередь — полиграфические услуги справочника: точное совпадение с категорией
    # или синонимом, иначе услуги, в названии которых есть все слова запроса.
    group = matching_group(processes, text)
    if not group and len(text) >= 4:
        group = suggest(processes, text, limit=MAX_GROUP)
    if len(group) == 1:
        return build_plan(group[0].display, processes, dictionary)
    if group:
        # По главной русской и двум английским фразам каждой услуги — чтобы не тратить лимиты.
        queries = [SearchQuery(p.phrases_ru[0], RU) for p in group if p.phrases_ru]
        queries += [SearchQuery(phrase, EN) for p in group for phrase in p.phrases_en[:2]]
        names = ", ".join(p.name for p in group)
        note = (f"Запрос подходит к услугам: {names}. "
                "Ищем по фразам всех; для точного поиска выберите услугу из подсказок.")
        return SearchPlan(None, _unique(with_key_words(queries, group)), note,
                          exclude=frozenset(w for p in group for w in p.exclude))

    if is_russian(text):
        english = dictionary.translate(text)
        queries = [SearchQuery(text, RU)]
        note = ""
        if english:
            queries.append(SearchQuery(english, EN))
        else:
            note = "Перевод на английский не найден в словаре — поиск только на русском."
        return SearchPlan(None, _unique(queries), note)

    # Английский текст: если он совпал с фразой услуги, добавляем её русские фразы.
    queries = [SearchQuery(text, EN)]
    for p in processes:
        if _norm(text) in (_norm(phrase) for phrase in p.phrases_en):
            queries += [SearchQuery(phrase, RU) for phrase in p.phrases_ru]
            break
    return SearchPlan(None, _unique(queries))
