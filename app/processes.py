"""Справочник услуг (техпроцессов): загрузка и подсказки по вводу."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from app import paths

LANGS = ("en", "ru", "de", "fr")


# Степени соответствия (те же, что в каталоге).
EXACT, PARTIAL, RELATED = "точное", "частичное", "смежный процесс"


@dataclass
class Level:
    """Уровень расширения поиска: если по прежним уровням роликов мало, ищем по этим фразам.

    Например: оборудование → действие → отдельные этапы → похожие операции. match —
    степень соответствия, которую подсказывает ролик, найденный только на этом уровне.
    """
    name: str
    match: str = EXACT
    phrases: dict[str, list[str]] = field(default_factory=dict)  # язык → фразы


@dataclass
class Process:
    id: str
    name: str
    category: str
    phrases_ru: list[str]
    phrases_en: list[str]
    aliases: list[str] = field(default_factory=list)
    target: int = 3
    # Короткие ключевые слова: площадки находят по ним больше, чем по длинным фразам.
    key_ru: list[str] = field(default_factory=list)
    key_en: list[str] = field(default_factory=list)
    # Слова, при которых ролик точно не про эту услугу (для лазерной печати — «3d»).
    exclude: list[str] = field(default_factory=list)
    levels: list[Level] = field(default_factory=list)  # расширение поиска, от узкого к общему

    @property
    def display(self) -> str:
        return f"{self.name} ({self.category})"


@dataclass
class Category:
    id: str
    name: str
    processes: list[Process]


def load_catalog(path: Path | None = None) -> list[Category]:
    """Читает справочник: пользовательскую копию из «Данные», иначе встроенный."""
    if path is None:
        user_copy = paths.processes_file()
        path = user_copy if user_copy.exists() else paths.resources_dir() / "processes.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    categories = []
    for cat in raw["categories"]:
        processes = [
            Process(
                id=p["id"],
                name=p["name"],
                category=cat["name"],
                phrases_ru=list(p.get("phrases_ru", [])),
                phrases_en=list(p.get("phrases_en", p.get("phrases", []))),
                aliases=list(p.get("aliases", [])),
                target=int(p.get("target", 3)),
                key_ru=list(p.get("key_ru", [])),
                key_en=list(p.get("key_en", [])),
                exclude=list(p.get("exclude", [])),
                levels=[Level(name=lv["name"], match=lv.get("match", EXACT),
                              phrases={lang: list(lv[lang]) for lang in LANGS if lv.get(lang)})
                        for lv in p.get("levels", [])],
            )
            for p in cat["processes"]
        ]
        categories.append(Category(id=cat["id"], name=cat["name"], processes=processes))
    return categories


def all_processes(categories: list[Category]) -> list[Process]:
    return [p for c in categories for p in c.processes]


def normalize(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def suggest(processes: list[Process], text: str, limit: int = 10) -> list[Process]:
    """Подсказки по введённому тексту.

    Совпадение ищется в названии, категории и синонимах. Каждое слово запроса
    должно встречаться хотя бы как начало слова. Сначала идут услуги, у которых
    название начинается с запроса.
    """
    query = normalize(text)
    if not query:
        return []
    words = query.split()
    scored = []
    for order, proc in enumerate(processes):
        haystacks = [normalize(s) for s in (proc.name, proc.category, *proc.aliases)]
        tokens = [t for h in haystacks for t in h.replace("/", " ").replace("-", " ").split()]
        if not all(any(t.startswith(w) for t in tokens) for w in words):
            continue
        name = normalize(proc.name)
        if name.startswith(query):
            rank = 0
        elif any(h.startswith(query) for h in haystacks):
            rank = 1
        else:
            rank = 2
        scored.append((rank, order, proc))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [proc for _, _, proc in scored[:limit]]


def find_by_display(processes: list[Process], text: str) -> Process | None:
    """Точное совпадение с подсказкой или названием услуги."""
    norm = normalize(text)
    for proc in processes:
        if norm in (normalize(proc.display), normalize(proc.name)):
            return proc
    return None
