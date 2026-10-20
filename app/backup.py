"""Резервная копия «Данных»: ZIP с согласованными снимками открытых баз SQLite."""

import os
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from PyQt6.QtCore import QThread, pyqtSignal

from app import paths


class Cancelled(Exception):
    pass


def _check_cancel(cancel):
    if cancel and cancel.is_set():
        raise Cancelled()


def _snapshot(source: Path, dest: Path, cancel):
    """Включить завершённые транзакции, в том числе из WAL, без копирования служебных файлов."""
    started = time.monotonic()

    def progress(status, remaining, total):
        _check_cancel(cancel)
        if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) and time.monotonic() - started > 30:
            raise OSError("База данных занята. Повторите резервное копирование позже.")

    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=1)) as src:
        with closing(sqlite3.connect(dest)) as dst:
            src.backup(dst, pages=256, progress=progress, sleep=0.05)


def create_backup(source: Path, dest: Path, cancel=None, progress=None) -> None:
    """Сохранить всю папку, кроме блокировки запуска и служебных файлов SQLite.

    Символические ссылки не включаются: иначе архив мог бы незаметно захватить внешние данные.
    При ошибке или отмене прежний архив остаётся целым. progress(готово файлов, всего файлов).
    """
    source, dest = Path(source).resolve(), Path(dest).resolve()
    if dest.is_relative_to(source):
        raise ValueError("Сохраните резервную копию вне папки «Данные».")
    if not source.is_dir():
        raise OSError("Папка «Данные» не найдена.")
    _check_cancel(cancel)

    def walk_error(error):
        raise error

    files = []
    directories = []
    for folder, dirs, names in os.walk(source, onerror=walk_error):
        _check_cancel(cancel)
        root = Path(folder)
        directories.append(root)
        for name in dirs + names:
            if (root / name).is_symlink() or (root / name).is_junction():
                raise ValueError("В папке «Данные» есть символическая ссылка. Копирование отменено.")
        for name in sorted(names):
            path = root / name
            if path == source / "app.lock":
                continue
            if name.startswith(".copyvf-") and name.endswith(".tmp"):
                continue  # незавершённая атомарная запись — в архиве нужен только готовый JSON
            if any(name.endswith(suffix) and Path(name[:-len(suffix)]).suffix.lower() in (".sqlite", ".sqlite3")
                   for suffix in ("-wal", "-shm", "-journal")):
                continue  # SQLite может удалить журнал во время обхода; данные берутся через backup API
            if not path.is_file():
                raise ValueError("В папке «Данные» есть файл неподдерживаемого типа. Копирование отменено.")
            files.append(path)

    databases = set()
    for path in files:
        _check_cancel(cancel)
        with path.open("rb") as file:
            if file.read(16) == b"SQLite format 3\x00" or path.suffix.lower() in (".sqlite", ".sqlite3"):
                databases.add(path)
    sidecars = {db.with_name(db.name + suffix) for db in databases for suffix in ("-wal", "-shm", "-journal")}
    files = [path for path in files if path not in sidecars]

    # Временные файлы рядом с архивом: замена готового файла атомарна и работает на Windows.
    with tempfile.TemporaryDirectory(prefix=".copyvf-backup-", dir=dest.parent) as tmp:
        temporary = Path(tmp)
        archive_path = temporary / "backup.zip"
        archive_path.touch(mode=0o600)
        with ZipFile(archive_path, "w", compression=ZIP_DEFLATED, compresslevel=1) as archive:
            for directory in directories:
                name = (Path(paths.DATA_DIR_NAME) / directory.relative_to(source)).as_posix()
                archive.mkdir(name + "/")
            for i, path in enumerate(files):
                _check_cancel(cancel)
                if progress:
                    progress(i, len(files))
                saved = path
                if path in databases:
                    saved = temporary / "snapshot.sqlite"
                    saved.unlink(missing_ok=True)
                    _snapshot(path, saved, cancel)
                name = (Path(paths.DATA_DIR_NAME) / path.relative_to(source)).as_posix()
                with saved.open("rb") as file, archive.open(name, "w", force_zip64=True) as target:
                    # Ограничиваем чтение исходным размером: журнал может расти во время копирования.
                    remaining = os.fstat(file.fileno()).st_size
                    while remaining:
                        _check_cancel(cancel)
                        chunk = file.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise OSError("Файл изменился во время копирования. Повторите попытку.")
                        target.write(chunk)
                        remaining -= len(chunk)
        _check_cancel(cancel)
        archive_path.replace(dest)


class BackupWorker(QThread):
    """Копирование в фоне с отменой между порциями файла и страницами SQLite."""
    progress = pyqtSignal(int, int)

    def __init__(self, source: Path, dest: Path, parent=None):
        super().__init__(parent)
        self.source, self.dest = source, dest
        self.cancel = threading.Event()
        self.error = ""
        self.cancelled = False

    def run(self):
        try:
            create_backup(self.source, self.dest, self.cancel, self.progress.emit)
        except Cancelled:
            self.cancelled = True
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.error = f"Не удалось создать резервную копию: {exc}"
