"""Архив восстанавливает данные, а отмена и ошибки не портят прежнюю копию."""

import sqlite3
import threading
from contextlib import closing
from zipfile import ZipFile

import pytest

from app.storage import backup
from app.storage.catalog import Catalog
from app.workers.backup import BackupWorker


def test_backup_restores_catalog_settings_and_nested_files(data_dir, tmp_path):
    data_dir.mkdir()
    store = Catalog(data_dir / "catalog.sqlite")
    store.set_meta("last_summary_at", "12345")
    settings = data_dir / "settings.json"
    settings.write_text('{"theme": "dark"}', encoding="utf-8")
    video = data_dir / "Видео" / "Ламинирование" / "ролик.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video-content")
    (data_dir / "Пустая папка").mkdir()
    (data_dir / "app.lock").write_text("lock", encoding="utf-8")
    dest = tmp_path / "Резервная копия.zip"
    try:
        backup.create_backup(data_dir, dest)
        with ZipFile(dest) as archive:
            assert archive.testzip() is None
            assert "Данные/app.lock" not in archive.namelist()
            assert "Данные/Пустая папка/" in archive.namelist()
            assert all("\\" not in name for name in archive.namelist())
            archive.extractall(tmp_path / "Восстановление")
        restored = tmp_path / "Восстановление" / "Данные"
        assert (restored / "settings.json").read_bytes() == settings.read_bytes()
        assert (restored / "Видео" / "Ламинирование" / "ролик.mp4").read_bytes() == video.read_bytes()
        with closing(sqlite3.connect(restored / "catalog.sqlite")) as db:
            assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert db.execute("SELECT value FROM meta WHERE key='last_summary_at'").fetchone() == ("12345",)
        # Открытый исходный каталог остаётся доступен для записи после копирования.
        store.set_meta("after_backup", "yes")
    finally:
        store.close()


def test_backup_snapshots_committed_wal_without_uncommitted_rows(data_dir, tmp_path):
    data_dir.mkdir()
    original = data_dir / "Каталог #1.sqlite"
    dest = tmp_path / "wal.zip"
    with closing(sqlite3.connect(original)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE saved (value TEXT)")
        db.execute("INSERT INTO saved VALUES ('сохранено')")
        db.commit()
        assert original.with_name(original.name + "-wal").is_file()
        db.execute("INSERT INTO saved VALUES ('ещё не сохранено')")
        backup.create_backup(data_dir, dest)
        with ZipFile(dest) as archive:
            assert not any(name.endswith(("-wal", "-shm", "-journal")) for name in archive.namelist())
            archive.extractall(tmp_path / "restore")
        with closing(sqlite3.connect(tmp_path / "restore" / "Данные" / original.name)) as restored:
            assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert restored.execute("SELECT value FROM saved").fetchall() == [("сохранено",)]
        db.rollback()


@pytest.mark.parametrize("failure", ["cancel", "broken_database", "missing_file"])
def test_failure_keeps_previous_backup_and_removes_temporary_files(data_dir, tmp_path, failure):
    data_dir.mkdir()
    first = data_dir / "a.txt"
    first.write_text("данные", encoding="utf-8")
    dest = tmp_path / "backup.zip"
    backup.create_backup(data_dir, dest)
    previous = dest.read_bytes()
    cancel = threading.Event()
    if failure == "broken_database":
        (data_dir / "broken.sqlite").write_bytes(b"not a database")

    def progress(done, total):
        if failure == "cancel":
            cancel.set()
        elif failure == "missing_file":
            first.unlink()

    expected = {"cancel": backup.Cancelled, "broken_database": sqlite3.Error, "missing_file": OSError}[failure]
    with pytest.raises(expected):
        backup.create_backup(data_dir, dest, cancel, progress)
    assert dest.read_bytes() == previous
    assert not list(tmp_path.glob(".copyvf-backup-*"))


def test_backup_rejects_destination_inside_data(data_dir):
    data_dir.mkdir()
    with pytest.raises(ValueError, match="вне папки"):
        backup.create_backup(data_dir, data_dir / "copies" / "backup.zip")
    assert list(data_dir.iterdir()) == []


def test_backup_rejects_symlink_without_copying_external_data(data_dir, tmp_path):
    data_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("внешние данные", encoding="utf-8")
    try:
        (data_dir / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Создание символических ссылок недоступно")
    dest = tmp_path / "backup.zip"
    with pytest.raises(ValueError, match="символическая ссылка"):
        backup.create_backup(data_dir, dest)
    assert not dest.exists()


def test_backup_worker_reports_failure_and_cancellation(data_dir, tmp_path):
    data_dir.mkdir()
    worker = BackupWorker(data_dir, data_dir / "backup.zip")
    worker.start()
    assert worker.wait(5000)
    assert "вне папки" in worker.error
    cancelled = BackupWorker(data_dir, tmp_path / "cancelled.zip")
    cancelled.cancel.set()
    cancelled.start()
    assert cancelled.wait(5000)
    assert cancelled.cancelled and not cancelled.error
    assert not (tmp_path / "cancelled.zip").exists()


def test_closing_main_window_cancels_and_waits_for_backup(data_dir, tmp_path, monkeypatch):
    from app.core import processes
    from app.storage.settings import Settings
    from app.ui.main_window import MainWindow

    started = threading.Event()

    def slow_backup(source, dest, cancel, progress):
        started.set()
        assert cancel.wait(5), "Закрытие окна не отменило копирование"
        raise backup.Cancelled()

    monkeypatch.setattr(backup, "create_backup", slow_backup)
    window = MainWindow(Settings(check_updates=False, link_check_days=0), processes.load_catalog())
    worker = BackupWorker(data_dir, tmp_path / "backup.zip", window)
    window.backup_worker = worker
    worker.start()
    try:
        assert started.wait(5)
        window.close()
        assert not worker.isRunning()
        assert worker.cancelled
    finally:
        worker.cancel.set()
        worker.wait()
        window.close()
        window.catalog.close()
