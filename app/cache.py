"""Кэш ответов API и учёт запросов для соблюдения лимитов площадок.

Хранится в «Данные/cache.sqlite», поэтому лимиты считаются и после перезапуска.
"""

import sqlite3
import threading
import time
from pathlib import Path

from app import paths

CACHE_TTL = 24 * 3600  # Pixabay требует кэшировать ответы 24 часа; для остальных — так же


class Cache:
    def __init__(self, path: Path | None = None, now=time.time):
        self._now = now
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path or paths.data_dir() / "cache.sqlite", check_same_thread=False)
        with self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, body TEXT, fetched_at REAL);
                CREATE TABLE IF NOT EXISTS requests (source TEXT, at REAL);
                CREATE INDEX IF NOT EXISTS requests_source_at ON requests (source, at);
                CREATE TABLE IF NOT EXISTS blocks (source TEXT PRIMARY KEY, until REAL);
                """
            )

    def get(self, key: str, max_age: float = CACHE_TTL) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT body, fetched_at FROM responses WHERE key = ?", (key,)).fetchone()
        if row and self._now() - row[1] < max_age:
            return row[0]
        return None

    def put(self, key: str, body: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO responses VALUES (?, ?, ?)", (key, body, self._now()))

    def record_request(self, source: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO requests VALUES (?, ?)", (source, self._now()))

    def count_requests(self, source: str, window: float) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM requests WHERE source = ? AND at > ?", (source, self._now() - window)
            ).fetchone()
        return row[0]

    def oldest_request(self, source: str, window: float) -> float | None:
        with self._lock:
            row = self._db.execute(
                "SELECT MIN(at) FROM requests WHERE source = ? AND at > ?", (source, self._now() - window)
            ).fetchone()
        return row[0]

    def block_until(self, source: str, until: float) -> None:
        """Площадка сама сообщила, что лимит исчерпан до указанного времени."""
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO blocks VALUES (?, ?)", (source, until))

    def blocked_until(self, source: str) -> float | None:
        with self._lock:
            row = self._db.execute("SELECT until FROM blocks WHERE source = ?", (source,)).fetchone()
        if row and row[0] > self._now():
            return row[0]
        return None

    def clear_responses(self) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM responses")

    def close(self) -> None:
        self._db.close()


class RateLimiter:
    """Проверка лимитов перед запросом. Лимиты не обходятся — запрос просто не отправляется."""

    def __init__(self, cache: Cache, source: str, limits: list[tuple[int, float, str]], now=time.time):
        self.cache = cache
        self.source = source
        self.limits = limits  # (число запросов, окно в секундах, подпись «в час»)
        self._now = now

    def wait_seconds(self) -> tuple[float, str] | None:
        """None — можно отправлять; иначе (сколько ждать, какой лимит)."""
        until = self.cache.blocked_until(self.source)
        if until:
            return until - self._now(), "лимит площадки"
        for count, window, label in self.limits:
            if self.cache.count_requests(self.source, window) >= count:
                oldest = self.cache.oldest_request(self.source, window) or self._now()
                return max(oldest + window - self._now(), 1), f"{count} запросов {label}"
        return None

    def record(self) -> None:
        self.cache.record_request(self.source)
