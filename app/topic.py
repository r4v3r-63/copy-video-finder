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


def key_words(phrases: list[str], limit: int = 1) -> list[str]:
    """Самые частые значимые слова фраз — короткие запросы, которые площадки понимают лучше длинных."""
    counts = Counter(w for p in phrases for w in dict.fromkeys(words(p)) if is_significant(w))
    return [w for w, _ in counts.most_common(limit)]
