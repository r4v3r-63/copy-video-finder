"""Регрессии, воспроизведённые при ревью версии 0.24.0."""

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from app import paths
from app.export import registry, showreel
from app.services import downloader, updater
from app.services.maintenance import check_links
from app.sources.cache import Cache
from app.sources.vk import Tokens, VkSource
from app.sources.web import known_site, parse_page, youtube_id
from app.storage import catalog as cat
from app.storage.settings import Settings, built_in_keys
from tests.fakes import FakeSession, StreamResponse, StreamSession, fixture_text


def video():
    return parse_page(fixture_text("manual_video.html"), "https://example.org/video/1")


@pytest.mark.parametrize("value", [[], None, "настройки", 123])
def test_settings_and_built_in_keys_ignore_non_object_json(data_dir, value):
    target = paths.settings_file()
    target.write_text(json.dumps(value), encoding="utf-8")
    assert Settings.load() == Settings()
    assert built_in_keys(target) == {}


def test_settings_ignore_wrong_types_and_nonfinite_numbers(data_dir):
    paths.settings_file().write_text(json.dumps({"max_duration": "180", "min_duration": -10,
                                                "check_updates": "нет", "vk_token_expires": float("inf"),
                                                "settings_version": "повреждено"}), encoding="utf-8")
    assert Settings.load() == Settings()


def test_concurrent_settings_writes_produce_complete_json(data_dir):
    path = paths.settings_file()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: Settings(max_duration=60 + i).save(path), range(16)))
    assert 60 <= Settings.load(path).max_duration <= 75
    assert not list(data_dir.glob(".copyvf-*.tmp"))


def test_atomic_write_failure_keeps_original(data_dir, monkeypatch):
    path = paths.settings_file()
    path.write_text("прежние данные", encoding="utf-8")

    def fail_replace(*args):
        raise OSError("Нет места")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="Нет места"):
        paths.atomic_write_text(path, "новые данные")
    assert path.read_text(encoding="utf-8") == "прежние данные"
    assert not list(data_dir.glob(".copyvf-*.tmp"))


def test_download_retry_wait_can_be_cancelled(tmp_path):
    cancel = threading.Event()
    timer = threading.Timer(0.05, cancel.set)
    session = StreamSession(StreamResponse(b"", 429, headers={"Retry-After": "30"}), StreamResponse(b"video"))
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(downloader.Cancelled):
            downloader.fetch("https://example.org/video.mp4", tmp_path / "clip.mp4", session, cancel=cancel)
        assert time.monotonic() - started < 2
        assert len(session.calls) == 1 and not (tmp_path / "clip.mp4").exists()
    finally:
        timer.cancel()


def test_cancelled_download_never_starts_request(tmp_path):
    cancel = threading.Event()
    cancel.set()
    session = StreamSession(StreamResponse(b"video"))
    with pytest.raises(downloader.Cancelled):
        downloader.fetch("https://example.org/video.mp4", tmp_path / "clip.mp4", session, cancel=cancel)
    assert not session.calls


def test_silent_showreel_process_is_killed_on_cancel(tmp_path, monkeypatch):
    store = cat.Catalog(tmp_path / "catalog.sqlite")
    vid = store.select(video(), "lam_pouch")
    marker = tmp_path / "started.txt"
    destination = tmp_path / "showreel.mp4"
    making = tmp_path / "showreel.making.mp4"
    command = [sys.executable, "-c", "import pathlib,sys,time; "
               "pathlib.Path(sys.argv[2]).write_bytes(b'partial'); "
               "pathlib.Path(sys.argv[1]).write_text('started'); time.sleep(30)", str(marker), str(making)]
    monkeypatch.setattr(showreel, "build_command", lambda *args: command)
    original_popen = showreel.subprocess.Popen
    children = []

    def popen(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(showreel.subprocess, "Popen", popen)
    cancel = threading.Event()

    def cancel_when_started():
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        cancel.set()

    controller = threading.Thread(target=cancel_when_started)
    controller.start()
    started = time.monotonic()
    try:
        with pytest.raises(showreel.Cancelled):
            showreel.make_showreel([store.get(vid)], destination, Path("ffmpeg"), None, cancel=cancel)
        assert marker.exists() and time.monotonic() - started < 3
        assert children and children[0].poll() is not None
        assert not making.exists() and not destination.exists()
    finally:
        cancel.set()
        controller.join(6)
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
        store.close()


def test_update_without_checksum_never_downloads(data_dir):
    release = updater.parse_release(json.loads(fixture_text("github_release.json")))
    session = FakeSession()
    with pytest.raises(updater.UpdateError, match="нет контрольной суммы"):
        updater.download(release, session)
    assert not session.calls


def test_update_rejects_asset_path_before_writing(data_dir):
    release = updater.parse_release(json.loads(fixture_text("github_release.json")))
    release = replace(release, zip_name="../settings.json", sha256_url="https://example.org/sha256")
    session = FakeSession()
    with pytest.raises(updater.UpdateError, match="имя файла"):
        updater.download(release, session)
    assert not session.calls and not (data_dir.parent / "settings.json").exists()


def test_changed_license_needs_new_confirmation_and_keeps_other_marks(tmp_path):
    store = cat.Catalog(tmp_path / "catalog.sqlite")
    original = replace(video(), content_orientation="горизонтальное")
    vid = store.select(original, "lam_pouch", cat.EXACT)
    store.set_comment(vid, "на экран")
    changed = replace(original, license=replace(original.license, url="https://example.org/new-terms"))
    try:
        assert store.get(vid).counts_for_coverage
        store.add(changed)
        entry = store.get(vid)
        assert not entry.license_confirmed and entry.checked_at is None and not entry.counts_for_coverage
        assert entry.comment == "на экран" and entry.links == {"lam_pouch": cat.EXACT}
        assert entry.status == cat.SELECTED
    finally:
        store.close()


def test_link_check_refreshes_license_without_confirming_it(tmp_path):
    store = cat.Catalog(tmp_path / "catalog.sqlite")
    original = replace(video(), content_orientation="горизонтальное")
    vid = store.select(original, "lam_pouch")

    class Source:
        name = original.source

        def is_configured(self):
            return True

        def get_details(self, source_id):
            return replace(original, license=replace(original.license, url="https://example.org/new-terms"))

    try:
        check_links(store, [Source()])
        assert not store.get(vid).license_confirmed
        assert store.get(vid).available
    finally:
        store.close()


@pytest.mark.parametrize("text", ["NaN", "inf", "-inf"])
def test_nonfinite_import_numbers_are_ignored(text):
    assert registry._float(text) is None
    assert registry._int(text) is None


def test_vk_instances_share_latest_rotated_refresh_token(tmp_path, monkeypatch):
    cache = Cache(tmp_path / "cache.sqlite")
    shared = [Tokens("OLD", "REFRESH-OLD", "DEVICE", 1)]
    renewed = []

    def refresh(app_id, tokens, session, now):
        renewed.append(tokens.refresh)
        return Tokens("NEW", "REFRESH-NEW", "DEVICE", now() + 3600)

    monkeypatch.setattr("app.sources.vk.refresh", refresh)
    sources = [VkSource("OLD", cache, now=lambda: 100, app_id="123", refresh_token="REFRESH-OLD",
                        device_id="DEVICE", expires_at=1, token_supplier=lambda: shared[0],
                        on_tokens=lambda tokens: shared.__setitem__(0, tokens)) for _ in range(2)]
    try:
        for source in sources:
            source._renew()
        assert renewed == ["REFRESH-OLD"]
        assert all(source.token == "NEW" for source in sources)
    finally:
        cache.close()


@pytest.mark.parametrize("url", ["https://example.org/pixabay.com/videos/printing-123/",
                                 "https://notpixabay.com/videos/printing-123/",
                                 "https://pixabay.com/search?next=pexels.com/video/printing-123/"])
def test_known_platform_needs_real_hostname_and_video_path(url):
    assert known_site(url) is None


@pytest.mark.parametrize("url", ["https://example.org/youtube.com/watch?v=AbCdEf12345",
                                 "https://youtube.com/watch?next=https://youtube.com/watch?v=AbCdEf12345"])
def test_youtube_needs_real_video_url(url):
    assert youtube_id(url) is None


def test_brand_logo_exists_and_can_be_rendered_by_qt():
    from PyQt6.QtGui import QIcon, QImage
    logo = paths.resources_dir() / "logo.svg"
    assert not QIcon(str(logo)).pixmap(48, 48).isNull()
    assert not QImage(str(logo)).isNull()


@pytest.mark.parametrize("name", ["CON", "nul.txt", "LPT1", "COM9.mov", "AUX"])
def test_windows_reserved_file_names_are_not_used(name):
    assert downloader.safe_name(name).startswith("_")


def test_invalid_resume_range_does_not_corrupt_download(tmp_path):
    target = tmp_path / "clip.mp4"
    target.with_name("clip.mp4.part").write_bytes(b"abc")
    target.with_name("clip.mp4.part.json").write_text(json.dumps({"url": "u", "validator": '"v1"'}))
    session = StreamSession(StreamResponse(b"def", 206, headers={"Content-Range": "bytes 0-2/6"}))
    with pytest.raises(downloader.DownloadError, match="неверный диапазон"):
        downloader.fetch("u", target, session)
    assert not target.exists()
    assert target.with_name("clip.mp4.part").read_bytes() == b"abc"


@pytest.mark.parametrize("contents", ["{", "[]", '{"terms": {"печать": null}}'])
def test_broken_user_dictionary_falls_back_without_overwriting_it(data_dir, contents):
    from app.core.query import Dictionary
    from app.ui.dialogs.tools import DictionaryDialog
    source = paths.terms_file()
    source.write_text(contents, encoding="utf-8")
    assert Dictionary.load().translate("печать")
    dialog = DictionaryDialog()
    assert dialog.table.rowCount() > 0
    assert source.read_text(encoding="utf-8") == contents
    dialog.close()


def test_retiring_old_directory_keeps_previous_backup(data_dir):
    from app.core import processes
    source = paths.processes_file()
    source.write_text('{"version": 3, "categories": []}', encoding="utf-8")
    previous = data_dir / "processes.v3.json"
    previous.write_text("прежний справочник", encoding="utf-8")
    assert processes._retire_old_copy(source)
    assert previous.read_text(encoding="utf-8") == "прежний справочник"
    assert json.loads((data_dir / "processes.v3.1.json").read_text(encoding="utf-8"))["version"] == 3


def test_backup_ignores_transient_json_and_database_journal(data_dir, tmp_path, monkeypatch):
    from zipfile import ZipFile

    from app.storage import backup
    store = cat.Catalog()
    temporary = data_dir / ".copyvf-unfinished.tmp"
    temporary.write_text("незавершённые настройки", encoding="utf-8")
    journal = data_dir / "catalog.sqlite-journal"
    original_is_file = Path.is_file
    original_walk = backup.os.walk

    def walk(*args, **kwargs):
        for folder, directories, names in original_walk(*args, **kwargs):
            # Журнал был в списке файлов, но SQLite уже удалила его к моменту копирования.
            yield folder, directories, names + [journal.name]

    def is_file(path):
        assert path != journal, "Временный журнал не должен открываться как обычный файл"
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", is_file)
    monkeypatch.setattr(backup.os, "walk", walk)
    try:
        destination = tmp_path / "backup.zip"
        backup.create_backup(data_dir, destination)
        with ZipFile(destination) as archive:
            assert "Данные/catalog.sqlite" in archive.namelist()
            assert not any(name.endswith((".tmp", "-journal")) for name in archive.namelist())
    finally:
        journal.unlink(missing_ok=True)
        store.close()


def test_manual_coverr_never_uses_page_preview_as_download(tmp_path):
    from app.services.manual import resolve_link
    from app.sources.web import WebSource
    from tests.fakes import FakeResponse
    cache = Cache(tmp_path / "cache.sqlite")
    source = WebSource(cache)
    source.pages.session = FakeSession(FakeResponse(text=fixture_text("manual_video.html")))
    try:
        result = resolve_link(source, "https://coverr.co/videos/lamination")
        assert result.files == [] and result.preview_video is None
        assert result.license.status == "Требует проверки"
    finally:
        cache.close()
