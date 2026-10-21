"""Перенос старых служебных файлов без потери лицензий и пользовательских файлов."""

import json

import pytest

from app.storage.download_files import metadata_file, migrate_folder, working_file
from tests.fakes import fixture_text


def test_migration_leaves_only_ready_mp4_and_preserves_license_and_failed_file(tmp_path, data_dir):
    folder = tmp_path / "Видео"
    folder.mkdir()
    video = folder / "zapros_pixabay_201.mp4"
    video.write_bytes(b"ready")
    license_file = video.with_suffix(".txt")
    license_file.write_text(fixture_text("download_layout/license.txt"), encoding="utf-8")
    video.with_name(video.name + ".invalid").write_bytes(b"broken")
    video.with_name(video.stem + ".download.mp4.part").write_bytes(b"partial")
    migrate_folder(folder)
    assert list(folder.iterdir()) == [video]
    assert metadata_file(video, ".txt").read_text(encoding="utf-8") == fixture_text("download_layout/license.txt")
    assert [file.read_bytes() for file in (data_dir / "download-jobs" / "errors").rglob("*.invalid")] == [b"broken"]
    assert working_file(video).with_name(video.name + ".part").read_bytes() == b"partial"


def test_metadata_locations_do_not_collide_for_different_output_folders(tmp_path):
    first = tmp_path / "Первая" / "video.mp4"
    second = tmp_path / "Вторая" / "video.mp4"
    assert metadata_file(first) != metadata_file(second)
    assert working_file(first) != working_file(second)


@pytest.mark.parametrize("filename", ["../outside.mp4", "..\\outside.mp4", "C:\\outside.mp4", "other.mp4"])
def test_unknown_metadata_and_user_files_are_not_moved(tmp_path, filename):
    file = tmp_path / "video.json"
    original = {"filename": filename, "source": "Pixabay", "id": "1", "sha256": "x", "size": 1,
                "original_url": "https://example.org/video"}
    file.write_text(json.dumps(original), encoding="utf-8")
    user_text = tmp_path / "notes.txt"
    user_text.write_text("Мои заметки", encoding="utf-8")
    migrate_folder(tmp_path)
    assert file.is_file() and user_text.read_text(encoding="utf-8") == "Мои заметки"


def test_old_copy_does_not_overwrite_current_metadata(tmp_path):
    video = tmp_path / "zapros_pixabay_201.mp4"
    current = metadata_file(video, ".txt")
    current.write_text("Актуальные сведения", encoding="utf-8")
    old = video.with_suffix(".txt")
    old.write_text(fixture_text("download_layout/license.txt"), encoding="utf-8")
    migrate_folder(tmp_path)
    assert current.read_text(encoding="utf-8") == "Актуальные сведения" and not old.exists()
    assert len(list(current.parent.glob("*.legacy-*.txt"))) == 1
