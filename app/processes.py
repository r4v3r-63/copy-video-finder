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
    # Услуги сайта Copy.ru в этой группе («Визитки с фольгой», «Фото на визу в Японию»): по ним
    # подсказки в строке поиска; ищем и считаем покрытие по группе.
    variants: list[str] = field(default_factory=list)
    # Основы «чужих» слов (topic.FOREIGN), которые для этой группы — по теме: «дерев» у гравировки по дереву.
    allow: list[str] = field(default_factory=list)

    @property
    def display(self) -> str:
        return f"{self.name} ({self.category})"


@dataclass
class Category:
    id: str
    name: str
    processes: list[Process]


VERSION = 5  # с версии 5 справочник — услуги сайта Copy.ru, сгруппированные для поиска


def builtin_file() -> Path:
    return paths.resources_dir() / "processes.json"


def migrated_ids() -> dict[str, str]:
    """Коды услуг прежних версий справочника → коды групп, куда перенесены их ролики."""
    return dict(json.loads(builtin_file().read_text(encoding="utf-8")).get("migrated", {}))


def removed_ids() -> set[str]:
    """Исключённые пользователем услуги: не возвращать их из старой копии справочника."""
    return set(json.loads(builtin_file().read_text(encoding="utf-8")).get("removed", []))


def _retire_old_copy(path: Path) -> bool:
    """Пользовательская копия справочника старше версии 5 — по 43 техпроцессам: откладывается."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        version = raw.get("version", 1) if isinstance(raw, dict) else VERSION
    except (OSError, ValueError):
        return False
    if not isinstance(version, int) or version >= VERSION:
        return False
    backup = path.with_name(f"processes.v{version}.json")
    index = 1
    while backup.exists():
        backup = path.with_name(f"processes.v{version}.{index}.json")
        index += 1
    path.replace(backup)
    return True


def load_catalog(path: Path | None = None) -> list[Category]:
    """Читает справочник: пользовательскую копию из «Данные» (редактор справочника), иначе встроенный.

    В пользовательской копии недостающие поля услуги (например, уровни поиска, появившиеся
    в новой версии программы) берутся из встроенного справочника по коду услуги.
    """
    if path is None:
        user_copy = paths.processes_file()
        if user_copy.exists():
            _retire_old_copy(user_copy)
        path = user_copy if user_copy.exists() else builtin_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("categories"), list):
        raise ValueError("Неверная структура справочника услуг. Восстановите его из резервной копии.")
    if path != builtin_file() and builtin_file().exists():
        builtin = {p["id"]: p for c in json.loads(builtin_file().read_text(encoding="utf-8"))["categories"]
                   for p in c["processes"]}
        for cat in raw["categories"]:
            for p in cat["processes"]:
                for key, value in builtin.get(p["id"], {}).items():
                    p.setdefault(key, value)
    removed = removed_ids()
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
                variants=list(p.get("variants", [])),
                allow=list(p.get("allow", [])),
                levels=[Level(name=lv["name"], match=lv.get("match", EXACT),
                              phrases={lang: list(lv[lang]) for lang in LANGS if lv.get(lang)})
                        for lv in p.get("levels", [])],
            )
            for p in cat["processes"] if p["id"] not in removed
        ]
        categories.append(Category(id=cat["id"], name=cat["name"], processes=processes))
    return categories


def process_dict(p: Process) -> dict:
    """Услуга в формате справочника (пустые поля не пишутся)."""
    d = {"id": p.id, "name": p.name, "variants": p.variants, "aliases": p.aliases, "phrases_ru": p.phrases_ru,
         "phrases_en": p.phrases_en, "key_ru": p.key_ru, "key_en": p.key_en, "exclude": p.exclude, "allow": p.allow}
    d = {k: v for k, v in d.items() if v or k in ("id", "name")}
    if p.target != 3:
        d["target"] = p.target
    # Уровни пишутся всегда, даже пустым списком: иначе при загрузке подставятся встроенные.
    d["levels"] = [{"name": lv.name, "match": lv.match, **{lang: lv.phrases[lang] for lang in LANGS
                                                           if lv.phrases.get(lang)}} for lv in p.levels]
    return d


_LIST_FIELDS = ("variants", "aliases", "phrases_ru", "phrases_en", "key_ru", "key_en", "exclude", "allow")


def save_catalog(categories: list[Category], path: Path | None = None) -> Path:
    """Сохранить справочник (редактор услуг) — по умолчанию в «Данные/processes.json»."""
    path = path or paths.processes_file()
    builtin_raw = json.loads(builtin_file().read_text(encoding="utf-8"))
    version = builtin_raw.get("version", 1)
    builtin = {p["id"]: p for c in builtin_raw["categories"] for p in c["processes"]}
    lines = ["{", f'  "version": {version},', '  "categories": [']
    for ci, c in enumerate(categories):
        lines += ["    {", f'      "id": {json.dumps(c.id)},',
                  f'      "name": {json.dumps(c.name, ensure_ascii=False)},',
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
    migrated = builtin_raw.get("migrated")
    removed = builtin_raw.get("removed")
    lines += ["  ]" + ("," if migrated or removed else "")]
    if migrated:
        lines.append(f'  "migrated": {json.dumps(migrated, ensure_ascii=False)}' + ("," if removed else ""))
    if removed:
        lines.append(f'  "removed": {json.dumps(removed, ensure_ascii=False)}')
    lines += ["}"]
    text = "\n".join(lines) + "\n"
    json.loads(text)  # проверка: файл должен читаться
    paths.atomic_write_text(path, text)
    return path


_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                      "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"], strict=True))


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
        haystacks = [normalize(s) for s in (proc.name, proc.category, *proc.aliases, *proc.variants)]
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


def variant_display(proc: Process, variant: str) -> str:
    return f"{variant} ({proc.category})"


def suggestion_texts(processes: list[Process], text: str, limit: int = 12) -> list[str]:
    """Подсказки строки поиска: услуги сайта (варианты) и группы, сначала начинающиеся с запроса."""
    query = normalize(text)
    if not query:
        return []
    words = query.split()
    scored = []
    for order, proc in enumerate(processes):
        for vi, name in enumerate([proc.name, *(v for v in proc.variants if normalize(v) != normalize(proc.name))]):
            tokens = normalize(name).replace("/", " ").replace("-", " ").replace("(", " ").split()
            if not all(any(t.startswith(w) for t in tokens) for w in words):
                continue
            rank = 0 if normalize(name).startswith(query) else 1
            display = proc.display if vi == 0 else variant_display(proc, name)
            scored.append((rank, order, vi, display))
    scored.sort()
    result = []
    for *_, display in scored:
        if display not in result:
            result.append(display)
    if len(result) < limit:  # совпадения по синонимам и категории — после названий
        result += [p.display for p in suggest(processes, text, limit) if p.display not in result]
    return result[:limit]


def find_variant(processes: list[Process], text: str) -> tuple[Process, str] | None:
    """Услуга сайта по подсказке «Визитки с фольгой (Визитки)» или по точному названию."""
    norm = normalize(text)
    for proc in processes:
        for v in proc.variants:
            if norm in (normalize(variant_display(proc, v)), normalize(v)):
                return proc, v
    return None


def find_by_display(processes: list[Process], text: str) -> Process | None:
    """Точное совпадение с подсказкой или названием услуги."""
    norm = normalize(text)
    for proc in processes:
        if norm in (normalize(proc.display), normalize(proc.name)):
            return proc
    return None
