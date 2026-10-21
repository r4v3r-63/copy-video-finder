"""Поля вокруг вертикального ролика не делают его горизонтальным. Только локальные фикстуры."""

from dataclasses import replace
from pathlib import Path

import pytest

from app.core import orientation
from app.core.models import SearchFilters
from app.export import showreel
from app.services import downloader
from app.services.search import SIZE, rejection_reason
from app.sources.web import parse_page
from app.storage.catalog import EXACT, Catalog
from tests.fakes import FIXTURES, fixture_text


def image(name):
    return (FIXTURES / "orientation" / f"{name}.png").read_bytes()


def video(state=""):
    return replace(parse_page(fixture_text("manual_video.html"), "https://example.org/video/1"),
                   content_orientation=state)


@pytest.mark.parametrize(("name", "expected"), [
    ("landscape", orientation.LANDSCAPE), ("letterbox", orientation.LANDSCAPE),
    ("portrait_padded", orientation.PORTRAIT), ("portrait_logo", orientation.PORTRAIT),
    ("square_padded", orientation.PORTRAIT), ("portrait", orientation.PORTRAIT), ("dark", ""),
])
def test_useful_image_without_black_borders(name, expected):
    assert orientation.image_orientation(image(name)) == expected


def test_broken_image_is_unverified():
    assert orientation.image_orientation(b"broken image") == ""


def test_all_three_frames_required_and_mixed_orientation_rejected():
    horizontal = image("landscape")
    assert orientation.frames_orientation([(1, horizontal)] * 3) == orientation.LANDSCAPE
    assert orientation.frames_orientation([(1, horizontal)] * 2) == ""
    assert orientation.frames_orientation([(1, horizontal), (2, image("dark")), (3, horizontal)]) == ""
    assert orientation.frames_orientation([(1, horizontal), (2, image("portrait_padded")),
                                           (3, horizontal)]) == orientation.PORTRAIT


def test_content_orientation_overrides_horizontal_file_dimensions():
    result = video(orientation.PORTRAIT)
    assert (result.width, result.height) == (1920, 1080)
    assert rejection_reason(result, SearchFilters(max_duration=180)) == SIZE


def test_unverified_catalog_does_not_count_until_content_confirmed(tmp_path):
    path = tmp_path / "catalog.sqlite"
    with_catalog = Catalog(path)
    try:
        vid = with_catalog.select(video(), "lam_pouch", EXACT)
        assert with_catalog.get(vid).license_confirmed
        assert not with_catalog.get(vid).counts_for_coverage
        assert with_catalog.coverage() == {}
        with_catalog.set_orientation(vid, orientation.LANDSCAPE)
        assert with_catalog.coverage() == {"lam_pouch": 1}
        # Ответ площадки без проверки не сбрасывает прежнее подтверждение пользователя.
        with_catalog.add(video())
        assert with_catalog.get(vid).result.content_orientation == orientation.LANDSCAPE
        with_catalog.set_orientation(vid, orientation.PORTRAIT)
        assert with_catalog.coverage() == {}
        assert not showreel.suitable(with_catalog.get(vid))
    finally:
        with_catalog.close()
    reopened = Catalog(path)
    try:
        assert reopened.get(vid).result.content_orientation == orientation.PORTRAIT
    finally:
        reopened.close()


def test_downloaded_file_is_checked_with_cancellation_and_no_network(tmp_path, monkeypatch):
    result = video()
    job = downloader.DownloadJob(result, result.files[0], None, "Печать", tmp_path)
    calls = []

    def frames(source, duration, ffmpeg, cancel):
        calls.append((source, cancel))
        return [(1, image("portrait_padded"))] * 3

    monkeypatch.setattr(downloader, "extract_frames", frames)
    local = tmp_path / "папка" / "ролик.mp4"
    with pytest.raises(downloader.DownloadError, match="не горизонтальное"):
        downloader.check_orientation(job, local, Path("ffmpeg"))
    assert calls == [(str(local), job.cancel)]
    assert result.content_orientation == orientation.PORTRAIT
    job.cancel.set()
    with pytest.raises(downloader.Cancelled):
        downloader.check_orientation(job, local, Path("ffmpeg"))
    assert len(calls) == 1


def test_migration_preserves_old_record_without_assuming_landscape(tmp_path):
    import sqlite3

    path = tmp_path / "catalog.sqlite"
    store = Catalog(path)
    vid = store.select(video(), "lam_pouch")
    store.set_comment(vid, "Сохранённая запись")
    store.close()
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE videos DROP COLUMN content_orientation")
    store = Catalog(path)
    try:
        entry = store.get(vid)
        assert entry.comment == "Сохранённая запись" and entry.license_confirmed
        assert entry.links == {"lam_pouch": EXACT}
        assert entry.result.content_orientation == ""
        assert not entry.counts_for_coverage
    finally:
        store.close()


def test_orientation_export_roundtrip_and_statistics(tmp_path):
    from app.core import processes
    from app.storage.catalog import statistics

    categories = processes.load_catalog()
    store = Catalog(tmp_path / "source.sqlite")
    target = Catalog(tmp_path / "target.sqlite")
    try:
        vid = store.select(video(), "lam_pouch")
        assert statistics(store, categories)["counted"] == 0
        store.set_orientation(vid, orientation.LANDSCAPE)
        assert statistics(store, categories)["counted"] == 1
        path = tmp_path / "catalog.csv"
        from app.export import registry
        procs = processes.all_processes(categories)
        registry.write_csv(path, registry.rows(store, procs))
        registry.import_csv(path, target, procs)
        assert target.get(vid).result.content_orientation == orientation.LANDSCAPE
        assert target.get(vid).counts_for_coverage
    finally:
        store.close()
        target.close()


def test_unverified_and_portrait_do_not_enter_playlist(tmp_path):
    from app.export.playlist import write_m3u

    store = Catalog(tmp_path / "catalog.sqlite")
    try:
        vid = store.select(video(), "lam_pouch")
        local = tmp_path / "ролик.mp4"
        local.write_bytes(b"video")
        store.set_downloaded(vid, str(local))
        playlist = tmp_path / "плейлист.m3u"
        assert write_m3u(playlist, store.entries()) == 0
        store.set_orientation(vid, orientation.LANDSCAPE)
        assert write_m3u(playlist, store.entries()) == 1
        store.set_orientation(vid, orientation.PORTRAIT)
        assert write_m3u(playlist, store.entries()) == 0
    finally:
        store.close()


@pytest.mark.parametrize(("name", "expected"), [("portrait_padded", orientation.PORTRAIT),
                                              ("landscape", orientation.LANDSCAPE)])
def test_real_video_frames_with_local_ffmpeg(tmp_path, name, expected):
    import shutil
    import subprocess

    from app.services.frames import extract_frames

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg не установлен")
    local = tmp_path / "ролик с пробелами.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-loop", "1", "-i",
                    str(FIXTURES / "orientation" / f"{name}.png"), "-t", "3", "-r", "5",
                    "-c:v", "mpeg4", "-pix_fmt", "yuv420p", str(local)],
                   check=True, capture_output=True, timeout=20)
    frames = extract_frames(str(local), 3, Path(ffmpeg))
    assert len(frames) == 3
    assert orientation.frames_orientation(frames) == expected
