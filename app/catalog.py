"""Каталог найденных роликов: SQLite в «Данные/catalog.sqlite».

Ролик однозначно определяется парой (площадка, ID на площадке) — дублей нет.
Ролик привязывается к одной или нескольким услугам со степенью соответствия.
В покрытие услуги засчитывается только ролик, который не отклонён, с лицензией
«Разрешено» или «Разрешено с атрибуцией», подтверждённой пользователем, и со
степенью соответствия не «только готовая продукция».
"""

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app import licenses, paths
from app.sources.base import LicenseInfo, VideoFile, VideoResult

# Степень соответствия ролика услуге.
EXACT = "точное"
PARTIAL = "частичное"
RELATED = "смежный процесс"
PRODUCT = "только готовая продукция"  # в покрытие не засчитывается
MATCHES = (EXACT, PARTIAL, RELATED, PRODUCT)

# Отметки ролика.
SELECTED = "выбрано"
REJECTED = "отклонено"
NEW = ""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT, description TEXT, preview_url TEXT,
    duration REAL, width INTEGER, height INTEGER, has_audio INTEGER,
    page_url TEXT, author TEXT, author_url TEXT,
    license_name TEXT, license_url TEXT, license_status TEXT, license_note TEXT,
    files TEXT,
    status TEXT NOT NULL DEFAULT '',
    license_confirmed INTEGER NOT NULL DEFAULT 0,
    comment TEXT NOT NULL DEFAULT '',
    downloaded_path TEXT NOT NULL DEFAULT '',
    added_at REAL, checked_at REAL, updated_at REAL,
    UNIQUE (source, source_id)
);
CREATE TABLE IF NOT EXISTS seen (
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    first_seen REAL NOT NULL,
    PRIMARY KEY (source, source_id)
);
CREATE TABLE IF NOT EXISTS searches (
    at REAL NOT NULL,
    query TEXT NOT NULL,
    found INTEGER NOT NULL,
    new INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    process_id TEXT NOT NULL,
    match TEXT NOT NULL,
    added_at REAL,
    PRIMARY KEY (video_id, process_id)
);
"""


@dataclass
class Entry:
    """Ролик в каталоге: данные площадки и отметки пользователя."""

    id: int
    result: VideoResult
    status: str = NEW
    license_confirmed: bool = False
    comment: str = ""
    downloaded_path: str = ""
    added_at: float | None = None
    checked_at: float | None = None
    links: dict[str, str] = field(default_factory=dict)  # услуга → степень соответствия

    @property
    def counts_for_coverage(self) -> bool:
        lic = self.result.license
        return (self.status != REJECTED and self.license_confirmed
                and lic is not None and lic.status in licenses.SUITABLE)


def _files_to_json(files: list[VideoFile]) -> str:
    return json.dumps([f.__dict__ for f in files], ensure_ascii=False)


def _files_from_json(text: str | None) -> list[VideoFile]:
    try:
        return [VideoFile(**f) for f in json.loads(text or "[]")]
    except (ValueError, TypeError):
        return []


class Catalog:
    def __init__(self, path: Path | None = None, now=time.time):
        self._now = now
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path or paths.data_dir() / "catalog.sqlite", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.execute("PRAGMA foreign_keys = ON")
            self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    # --- запись ---

    def add(self, r: VideoResult) -> int:
        """Добавить ролик или обновить данные площадки; отметки пользователя не трогаются."""
        lic = r.license or LicenseInfo(name="", status=licenses.REVIEW)
        now = self._now()
        values = dict(
            source=r.source, source_id=r.source_id, title=r.title, description=r.description,
            preview_url=r.preview_url, duration=r.duration, width=r.width, height=r.height,
            has_audio=None if r.has_audio is None else int(r.has_audio), page_url=r.page_url,
            author=r.author, author_url=r.author_url, license_name=lic.name, license_url=lic.url,
            license_status=lic.status, license_note=lic.note, files=_files_to_json(r.files), updated_at=now,
        )
        with self._lock, self._db:
            row = self._db.execute("SELECT id FROM videos WHERE source = ? AND source_id = ?",
                                   (r.source, r.source_id)).fetchone()
            if row:
                sets = ", ".join(f"{k} = :{k}" for k in values if k not in ("source", "source_id"))
                # Пользователь мог поправить название — его не перезаписываем.
                sets = sets.replace("title = :title, ", "")
                self._db.execute(f"UPDATE videos SET {sets} WHERE id = :id", {**values, "id": row["id"]})
                return row["id"]
            cols = ", ".join(values) + ", added_at"
            marks = ", ".join(f":{k}" for k in values) + ", :added_at"
            cur = self._db.execute(f"INSERT INTO videos ({cols}) VALUES ({marks})", {**values, "added_at": now})
            return cur.lastrowid

    def _update(self, video_id: int, **fields) -> None:
        fields["updated_at"] = self._now()
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._lock, self._db:
            self._db.execute(f"UPDATE videos SET {sets} WHERE id = ?", (*fields.values(), video_id))

    def select(self, r: VideoResult, process_id: str | None = None, match: str = EXACT,
               license_confirmed: bool = True) -> int:
        """«В каталог»: ролик выбран, лицензия проверена пользователем, привязка к услуге."""
        video_id = self.add(r)
        fields = {"status": SELECTED}
        if license_confirmed:
            fields.update(license_confirmed=1, checked_at=self._now())
        self._update(video_id, **fields)
        if process_id:
            self.link(video_id, process_id, match)
        return video_id

    def reject(self, r: VideoResult) -> int:
        """«Не подходит»: ролик больше не показывается в поиске."""
        video_id = self.add(r)
        self._update(video_id, status=REJECTED)
        return video_id

    def set_status(self, video_id: int, status: str) -> None:
        assert status in (NEW, SELECTED, REJECTED)
        self._update(video_id, status=status)

    def confirm_license(self, video_id: int, confirmed: bool = True) -> None:
        self._update(video_id, license_confirmed=int(confirmed), checked_at=self._now() if confirmed else None)

    def set_comment(self, video_id: int, comment: str) -> None:
        self._update(video_id, comment=comment)

    def set_title(self, video_id: int, title: str) -> None:
        self._update(video_id, title=title)

    def set_downloaded(self, video_id: int, path: str) -> None:
        self._update(video_id, downloaded_path=path)

    def link(self, video_id: int, process_id: str, match: str = EXACT) -> None:
        assert match in MATCHES, match
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO links VALUES (?, ?, ?, ?) "
                "ON CONFLICT (video_id, process_id) DO UPDATE SET match = excluded.match",
                (video_id, process_id, match, self._now()))

    def unlink(self, video_id: int, process_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM links WHERE video_id = ? AND process_id = ?", (video_id, process_id))

    def delete(self, video_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM videos WHERE id = ?", (video_id,))

    # --- чтение ---

    def _entry(self, row: sqlite3.Row) -> Entry:
        result = VideoResult(
            source=row["source"], source_id=row["source_id"], title=row["title"] or "",
            description=row["description"] or "", preview_url=row["preview_url"], duration=row["duration"],
            width=row["width"], height=row["height"],
            has_audio=None if row["has_audio"] is None else bool(row["has_audio"]),
            page_url=row["page_url"] or "", files=_files_from_json(row["files"]), author=row["author"] or "",
            author_url=row["author_url"],
            license=LicenseInfo(name=row["license_name"] or "", url=row["license_url"],
                                status=row["license_status"] or licenses.REVIEW, note=row["license_note"] or ""),
        )
        links = {r["process_id"]: r["match"] for r in
                 self._db.execute("SELECT process_id, match FROM links WHERE video_id = ?", (row["id"],))}
        return Entry(id=row["id"], result=result, status=row["status"], license_confirmed=bool(row["license_confirmed"]),
                     comment=row["comment"], downloaded_path=row["downloaded_path"], added_at=row["added_at"],
                     checked_at=row["checked_at"], links=links)

    def get(self, video_id: int) -> Entry | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            return self._entry(row) if row else None

    def find(self, key: tuple[str, str]) -> Entry | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM videos WHERE source = ? AND source_id = ?", key).fetchone()
            return self._entry(row) if row else None

    def statuses(self) -> dict[tuple[str, str], str]:
        """Отметки всех роликов по ключу (площадка, ID) — для выдачи поиска."""
        with self._lock:
            return {(r["source"], r["source_id"]): r["status"]
                    for r in self._db.execute("SELECT source, source_id, status FROM videos")}

    def entries(self, process_id: str | None = None, status: str | None = None, text: str = "") -> list[Entry]:
        """Ролики каталога с фильтрами: по услуге, отметке и тексту (название, описание, площадка, комментарий)."""
        sql = "SELECT DISTINCT v.* FROM videos v LEFT JOIN links l ON l.video_id = v.id WHERE 1 = 1"
        args: list = []
        if process_id:
            sql += " AND l.process_id = ?"
            args.append(process_id)
        if status is not None:
            sql += " AND v.status = ?"
            args.append(status)
        if text.strip():
            sql += " AND (v.title LIKE ? OR v.description LIKE ? OR v.source LIKE ? OR v.comment LIKE ?)"
            args += [f"%{text.strip()}%"] * 4
        sql += " ORDER BY v.id DESC"
        with self._lock:
            return [self._entry(row) for row in self._db.execute(sql, args).fetchall()]

    # --- история поиска: что найдено впервые ---

    def record_search(self, query: str, keys: list[tuple[str, str]]) -> set[tuple[str, str]]:
        """Запомнить выдачу поиска; вернуть ключи роликов, найденных впервые."""
        now = self._now()
        with self._lock, self._db:
            known = {(r["source"], r["source_id"]) for r in self._db.execute("SELECT source, source_id FROM seen")}
            new = [k for k in dict.fromkeys(keys) if k not in known]
            self._db.executemany("INSERT INTO seen VALUES (?, ?, ?)", [(*k, now) for k in new])
            self._db.execute("INSERT INTO searches VALUES (?, ?, ?, ?)", (now, query, len(set(keys)), len(new)))
        return set(new)

    def has_history(self) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM searches LIMIT 1").fetchone() is not None

    def new_since_previous_search(self) -> int:
        """Сколько роликов впервые найдено с даты предыдущего поиска (то есть в последнем)."""
        with self._lock:
            times = [r["at"] for r in self._db.execute("SELECT DISTINCT at FROM searches ORDER BY at DESC LIMIT 2")]
            if not times:
                return 0
            since = times[1] if len(times) > 1 else 0
            return self._db.execute("SELECT COUNT(*) FROM seen WHERE first_seen > ?", (since,)).fetchone()[0]

    def coverage(self) -> dict[str, int]:
        """Число засчитанных роликов по каждой услуге (правила — в начале модуля)."""
        counts: dict[str, int] = {}
        for e in self.entries():
            if not e.counts_for_coverage:
                continue
            for process_id, match in e.links.items():
                if match != PRODUCT:
                    counts[process_id] = counts.get(process_id, 0) + 1
        return counts


def coverage_level(count: int, target: int) -> int:
    """Уровень покрытия для цвета: 0 — нет роликов, 1, 2, 3 — цель выполнена."""
    if count >= target:
        return 3
    return min(count, 2)


def batch_order(processes, coverage: dict[str, int]) -> list:
    """Порядок пакетного поиска (ТЗ, 5.2): сначала услуги без роликов, затем с одним,
    затем остальные с недостатком; услуги с выполненной целью пропускаются."""
    short = [p for p in processes if coverage.get(p.id, 0) < p.target]
    return sorted(short, key=lambda p: (min(coverage.get(p.id, 0), 2), processes.index(p)))


def statistics(catalog: "Catalog", categories) -> dict:
    """Статистика каталога (ТЗ, 7.2)."""
    processes = [p for c in categories for p in c.processes]
    entries = [e for e in catalog.entries() if e.status != REJECTED]
    coverage = catalog.coverage()
    by_level = {"0": 0, "1": 0, "2": 0, "3 и более": 0}
    for p in processes:
        n = coverage.get(p.id, 0)
        by_level["3 и более" if n >= 3 else str(n)] += 1
    by_source: dict[str, int] = {}
    for e in entries:
        by_source[e.result.source] = by_source.get(e.result.source, 0) + 1
    return {
        "categories": len(categories),
        "services": len(processes),
        "videos": len(entries),
        "counted": sum(1 for e in entries if e.counts_for_coverage),
        "exact": sum(1 for e in entries if e.counts_for_coverage and EXACT in e.links.values()),
        "services_by_count": by_level,
        "services_done": sum(1 for p in processes if coverage.get(p.id, 0) >= p.target),
        "by_source": dict(sorted(by_source.items(), key=lambda kv: -kv[1])),
        "new_since_previous_search": catalog.new_since_previous_search(),
    }
