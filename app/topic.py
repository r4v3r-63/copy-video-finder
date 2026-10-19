"""Слова темы поиска: что считать значимым словом запроса и его основой."""

import re
from collections import Counter

WORD = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)

# Слова, по которым нельзя судить о теме ролика: площадки находят по ним что угодно
# (по «wide», «large», «cold» Pixabay отдаёт пляжи и города).
GENERIC = {
    "machine", "machines", "process", "printing", "print", "printer", "paper", "video", "equipment",
    "making", "office", "shop", "and", "the", "for", "with", "wide", "large", "format", "cold", "hot",
    "small", "big", "color", "colour", "digital", "roll", "sheet", "hand", "manual", "automatic",
    "professional", "stand", "pull", "taking", "production", "output", "press", "photo", "studio",
    "машина", "станок", "печать", "бумага", "видео", "оборудование", "для", "на", "широкоформатный",
    "широкоформатная", "холодная", "горячая", "цветная", "цифровая", "большой", "рулонный", "листовой",
    "установка", "сборка", "изготовление", "фото",
}


def words(text: str) -> list[str]:
    return [w.lower().replace("ё", "е") for w in WORD.findall(text or "")]


def is_significant(word: str) -> bool:
    if word in GENERIC or word.isdigit():
        return False
    # «3d», «dtf» короткие, но значимые; буквы с цифрами — тоже.
    return len(word) >= 3 or (any(c.isdigit() for c in word) and any(c.isalpha() for c in word))


def stem(word: str) -> str:
    """Грубая основа слова: lamination/laminated/laminator → lamin, ламинация → ламин."""
    return word if len(word) <= 5 else word[: max(5, len(word) - 4)]


def topic_stems(texts: list[str]) -> set[str]:
    """Основы значимых слов всех фраз поиска — на обоих языках сразу.

    Pixabay отдаёт английские теги и на русский запрос, поэтому ролик, найденный
    по русской фразе, проверяется и по английским словам той же услуги.
    """
    return {stem(w) for text in texts for w in words(text) if is_significant(w)}


# Слова, которые в фразе необязательны: служебные и уточняющие. Существительные
# вроде printer, press, paper здесь не стоят: без них «laser printer» — это дискотека.
OPTIONAL = {
    "and", "the", "for", "with", "of", "on", "in", "wide", "large", "format", "cold", "hot", "small", "big",
    "professional", "automatic", "manual", "modern", "taking", "making", "process", "production", "output",
    "video", "equipment", "machine", "machines", "close",
    "на", "для", "и", "с", "в", "широкоформатный", "широкоформатная", "холодная", "горячая", "большой",
    "рулонный", "листовой", "установка", "сборка", "изготовление", "машина", "станок", "оборудование", "видео",
}


# Широкие слова: в одиночку тему не подтверждают. Тег «этикетка» Pixabay ставит
# на ценники и таблички, «calendar» — на новогодние открытки, «печать» — на что угодно.
# Фраза из одного такого слова остаётся запросом, но ролик должен совпасть с другой фразой.
BROAD = {
    "печать", "печати", "печатей", "этикетки", "этикетка", "этикеток", "календарь", "календаря", "календарей",
    "календари", "значки", "значков", "магниты", "магнитов", "сублимация", "штамп", "штампы", "штампов",
    "баннер", "баннеры", "баннеров", "фото", "резка", "бумага", "бумаги", "принтер", "принтеры",
    "label", "labels", "calendar", "magnet", "magnets", "badge", "banner", "stamp", "photo", "cutting", "cut",
    "print", "sticker", "sublimation", "binding", "press", "heat", "laser", "vinyl", "hole", "corner", "foil",
    "spiral", "comb", "thermal", "paper", "printer",
}


def phrase_stems(texts: list[str], keep: set[str] | frozenset[str] = frozenset()) -> list[frozenset[str]]:
    """Для каждой фразы — основы её обязательных слов.

    Пропускаются фразы без обязательных слов и фразы из одного широкого слова
    (сравниваются целые слова: «фотокиоск» не широкое, хоть и начинается с «фото»).
    Фразы из keep — ключевые слова услуги, подобранные вручную, — не пропускаются:
    для лазерной печати «принтер» и есть предмет поиска.
    """
    def required(w: str) -> bool:
        return w not in OPTIONAL and not w.isdigit() and (len(w) >= 3 or is_significant(w))
    result = set()
    for t in texts:
        req = [w for w in words(t) if required(w)]
        if not req or (len(req) == 1 and req[0] in BROAD and t not in keep):
            continue
        result.add(frozenset(stem(w) for w in req))
    return list(result)


def key_words(phrases: list[str], limit: int = 1) -> list[str]:
    """Самые частые значимые слова фраз — короткие запросы, которые площадки понимают лучше длинных."""
    counts = Counter(w for p in phrases for w in dict.fromkeys(words(p)) if is_significant(w))
    return [w for w, _ in counts.most_common(limit)]


# Полиграфия и сувенирка: у ролика должно быть хотя бы одно такое слово (начало слова).
PRINT_DOMAIN = (
    "print", "paper", "office", "document", "book", "brochure", "page", "poster", "flyer", "leaflet", "card",
    "label", "sticker", "banner", "typograph", "ink", "toner", "cmyk", "copier", "copy", "xerox", "photocop",
    "bind", "lamin", "plotter", "scanner", "stapl", "folder", "envelope", "calendar", "notebook", "magazine",
    "newspaper", "catalog", "packag", "cardboard", "foam", "vinyl", "graphic", "mug", "shirt", "tshirt",
    "textile", "fabric", "apparel", "engrav", "laser", "magnet", "badge", "stamp", "souvenir", "3d", "filament",
    "photo", "camera", "passport", "portrait", "grommet", "eyelet", "guillotine", "cutter", "emboss", "foil",
    "punch", "perforat", "creas", "sublimat", "dtf", "signage", "roll-up", "rollup",
    "печат", "бумаг", "офис", "документ", "книг", "брошюр", "буклет", "плакат", "листовк", "визитк", "открытк",
    "этикет", "наклейк", "стикер", "баннер", "типограф", "полиграф", "чернил", "тонер", "копир", "ксерокс",
    "переплет", "ламин", "плоттер", "сканер", "степлер", "календар", "блокнот", "журнал", "газет", "каталог",
    "упаковк", "картон", "пенокартон", "винил", "кружк", "футболк", "текстил", "ткан", "гравир", "лазер",
    "магнит", "значк", "штамп", "сувенир", "3д", "фото", "камер", "паспорт", "портрет", "люверс", "гильотин",
    "резак", "тиснен", "фольг", "перфор", "биговк", "сублимац", "принтер", "роллап",
)

# Чужие области: ролик с такими словами — не про полиграфию, даже если совпало слово услуги
# (пневмостеплер на стройке помечен «stapler», ценник — «этикетка»).
FOREIGN = (
    "wood", "lumber", "plank", "carpent", "joiner", "construction", "builder", "roof", "brick", "concrete",
    "cement", "timber", "kitchen", "cooking", "food", "vegetable", "meat", "garlic", "recipe", "beach", "ocean",
    "coast", "mountain", "forest", "wildlife", "animal", "bird", "music", "musician", "guitar", "concert",
    "turntable", "automobile", "garden", "farm", "tractor", "steel", "physics", "experiment",
    "дерев", "древесин", "доски", "стройк", "строител", "плотник", "крыш", "кирпич", "бетон", "кухн", "еда",
    "готовк", "овощ", "мясо", "пляж", "океан", "горы", "лес", "животн", "птиц", "музык", "гитар", "концерт",
    "проигрыват", "автомобил", "сад", "ферм", "трактор",
)


def in_print_domain(tokens: set[str]) -> bool:
    """Ролик про полиграфию или сувенирку: есть слово этой области и нет слов чужих областей."""
    if any(t.startswith(f) for t in tokens for f in FOREIGN):
        return False
    return any(t.startswith(d) for t in tokens for d in PRINT_DOMAIN)
