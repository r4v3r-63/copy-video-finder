"""Справочник услуг (техпроцессов): загрузка и подсказки по вводу."""

import json
import re
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


def builtin_file() -> Path:
    return paths.resources_dir() / "processes.json"


def load_catalog(path: Path | None = None) -> list[Category]:
    """Читает справочник: пользовательскую копию из «Данные» (редактор справочника), иначе встроенный.

    В пользовательской копии недостающие поля услуги (например, уровни поиска, появившиеся
    в новой версии программы) берутся из встроенного справочника по коду услуги.
    """
    if path is None:
        user_copy = paths.processes_file()
        path = user_copy if user_copy.exists() else builtin_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if path != builtin_file() and builtin_file().exists():
        builtin = {p["id"]: p for c in json.loads(builtin_file().read_text(encoding="utf-8"))["categories"]
                   for p in c["processes"]}
        for cat in raw["categories"]:
            for p in cat["processes"]:
                for key, value in builtin.get(p["id"], {}).items():
                    p.setdefault(key, value)
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


def process_dict(p: Process) -> dict:
    """Услуга в формате справочника (пустые поля не пишутся)."""
    d = {"id": p.id, "name": p.name, "aliases": p.aliases, "phrases_ru": p.phrases_ru, "phrases_en": p.phrases_en,
         "key_ru": p.key_ru, "key_en": p.key_en, "exclude": p.exclude}
    d = {k: v for k, v in d.items() if v or k in ("id", "name")}
    if p.target != 3:
        d["target"] = p.target
    # Уровни пишутся всегда, даже пустым списком: иначе при загрузке подставятся встроенные.
    d["levels"] = [{"name": lv.name, "match": lv.match, **{lang: lv.phrases[lang] for lang in LANGS
                                                           if lv.phrases.get(lang)}} for lv in p.levels]
    return d


_LIST_FIELDS = ("aliases", "phrases_ru", "phrases_en", "key_ru", "key_en", "exclude")


def save_catalog(categories: list[Category], path: Path | None = None) -> Path:
    """Сохранить справочник (редактор услуг) — по умолчанию в «Данные/processes.json»."""
    path = path or paths.processes_file()
    builtin_raw = json.loads(builtin_file().read_text(encoding="utf-8"))
    version = builtin_raw.get("version", 1)
    builtin = {p["id"]: p for c in builtin_raw["categories"] for p in c["processes"]}
    lines = ["{", f'  "version": {version},', '  "categories": [']
    for ci, c in enumerate(categories):
        lines += ["    {", f'      "id": {json.dumps(c.id)},', f'      "name": {json.dumps(c.name, ensure_ascii=False)},',
                  '      "processes": [']
        for pi, p in enumerate(c.processes):
            d = process_dict(p)
            levels = d.pop("levels")
            # Поле, очищенное пользователем, пишется явно (пустым или со значением по умолчанию):
            # иначе при загрузке load_catalog подставит его из встроенного справочника.
            for key, value in builtin.get(p.id, {}).items():
                if key in _LIST_FIELDS and key not in d and value:
                    d[key] = []
            if "target" not in d and builtin.get(p.id, {}).get("target", 3) != 3:
                d["target"] = 3
            fields = [f"{json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in d.items()]
            lines.append("        {" + ",\n         ".join(fields) + ",")
            lines.append('         "levels": [' + ("" if levels else "]}" + ("," if pi < len(c.processes) - 1 else "")))
            if levels:
                lines += ["           " + json.dumps(lv, ensure_ascii=False) + ("," if li < len(levels) - 1 else "")
                          for li, lv in enumerate(levels)]
                lines.append("         ]}" + ("," if pi < len(c.processes) - 1 else ""))
        lines += ["      ]", "    }" + ("," if ci < len(categories) - 1 else "")]
    lines += ["  ]", "}"]
    text = "\n".join(lines) + "\n"
    json.loads(text)  # проверка: файл должен читаться
    path.write_text(text, encoding="utf-8")
    return path


_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                      "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def new_id(name: str, existing: set[str]) -> str:
    """Код новой услуги или категории из названия: «Печать на холсте» → pechat_na_holste."""
    base = "".join(_TRANSLIT.get(ch, ch) for ch in name.lower())
    base = "_".join(re.findall(r"[a-z0-9]+", base))[:40] or "service"
    candidate, n = base, 2
    while candidate in existing:
        candidate, n = f"{base}_{n}", n + 1
    return candidate


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
