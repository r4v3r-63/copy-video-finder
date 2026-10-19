"""Построение поисковых запросов на русском и английском.

Для услуги из справочника берутся её русские и английские фразы. Свободный
русский текст ищется как есть и в переводе по встроенному словарю, свободный
английский — как есть и с русскими фразами услуги, если он совпал с её
английской фразой. Внешние сервисы перевода не используются.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app import paths
from app.processes import Process, find_by_display

RU = "ru"
EN = "en"

_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
_TOKEN = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


@dataclass(frozen=True)
class SearchQuery:
    text: str
    lang: str  # RU или EN; площадки передают его в параметр языка API


@dataclass
class SearchPlan:
    process: Process | None
    queries: list[SearchQuery]
    note: str = ""

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
        path = path or paths.resources_dir() / "terms_ru_en.json"
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


def _unique(queries: list[SearchQuery]) -> list[SearchQuery]:
    seen, result = set(), []
    for q in queries:
        key = (_norm(q.text.strip()), q.lang)
        if key[0] and key not in seen:
            seen.add(key)
            result.append(q)
    return result


def build_plan(text: str, processes: list[Process], dictionary: Dictionary) -> SearchPlan:
    text = " ".join(text.split())
    if not text:
        return SearchPlan(None, [])

    proc = find_by_display(processes, text)
    if proc:
        queries = [SearchQuery(p, RU) for p in proc.phrases_ru] + [SearchQuery(p, EN) for p in proc.phrases_en]
        return SearchPlan(proc, _unique(queries))

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
