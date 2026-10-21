"""Массовые действия и фильтры каталога — с настоящей SQLite и записанным роликом."""

from dataclasses import replace

import pytest
from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import QApplication

from app.core import processes
from app.services.maintenance import LAST_LINK_CHECK
from app.sources.web import parse_page
from app.storage import catalog as cat
from app.storage.settings import Settings
from app.ui.main_window import MainWindow
from app.ui.tabs.catalog_view import CatalogView
from app.workers.maintenance import LinkCheckWorker
from tests.fakes import fixture_text


@pytest.fixture
def store(data_dir):
    data_dir.mkdir()
    catalog = cat.Catalog()
    yield catalog
    catalog.close()


def result(index, **fields):
    return replace(parse_page(fixture_text("manual_video.html"), f"https://example.org/video/{index}"), **fields)


def test_bulk_status_keeps_license_confirmation_and_links(store):
    ids = [store.select(result(i), "lam_pouch", cat.PARTIAL, license_confirmed=False) for i in range(3)]
    view = CatalogView(store, processes.load_catalog())
    view.table.selectAll()
    view._bulk_status(cat.REJECTED)
    assert {e.id for e in view.selected_entries()} == set(ids)
    assert all(e.status == cat.REJECTED and not e.license_confirmed for e in store.entries())
    view._bulk_status(cat.SELECTED)
    assert all(e.links == {"lam_pouch": cat.PARTIAL} and not e.counts_for_coverage for e in store.entries())
    view._copy_links()
    assert set(QApplication.clipboard().text().splitlines()) == {e.result.page_url for e in store.entries()}
    view.table.clearSelection()
    view._bulk_status(cat.NEW)
    assert all(e.status == cat.SELECTED for e in store.entries())
    view.close()


def test_catalog_filters_combine_and_reset(store, tmp_path):
    first = store.select(result(1), "lam_pouch", cat.PARTIAL)
    store.select(result(2, source="Другая площадка"), "lam_pouch", cat.EXACT)
    store.select(result(3), "lam_pouch", cat.EXACT)
    downloaded = tmp_path / "ролик.mp4"
    downloaded.write_bytes(b"video")
    store.set_downloaded(first, str(downloaded))
    view = CatalogView(store, processes.load_catalog())
    view.source_filter.setCurrentIndex(view.source_filter.findData("example.org"))
    view.match_filter.setCurrentIndex(view.match_filter.findData(cat.PARTIAL))
    view.downloaded_filter.setChecked(True)
    assert [e.id for e in view.entries] == [first]
    view.since_date.setDate(QDate.currentDate().addDays(1))
    view.since_filter.setChecked(True)
    assert view.entries == []
    view.reset_filters()
    assert len(view.entries) == 3
    view.select_entry(first)
    assert view.card.entry.id == first
    view.close()


def test_selected_link_check_does_not_update_global_schedule(store):
    first = store.add(result(1))
    second = store.add(result(2))
    checked = []

    class Source:
        name = "example.org"

        def is_configured(self):
            return True

        def get_details(self, source_id):
            checked.append(source_id)

    worker = LinkCheckWorker(store, [Source()], entries=[store.get(first)])
    worker.run()
    assert checked == [store.get(first).result.source_id]
    assert store.get(second).link_checked_at is None
    assert store.get_meta(LAST_LINK_CHECK) is None


def test_bulk_download_uses_catalog_group_and_preserves_rejection(store, monkeypatch):
    first = store.select(result(1), "lam_pouch", cat.PARTIAL, license_confirmed=False)
    store.set_status(first, cat.REJECTED)
    second = store.add(result(2, files=[]))
    window = MainWindow(Settings(check_updates=False, link_check_days=0), processes.load_catalog(), store=store)
    queued = []
    marks = []
    monkeypatch.setattr(window.downloads.queue, "add", queued.append)
    window.player.result = store.get(first).result
    monkeypatch.setattr(window.player, "set_catalog_state", lambda status, video_id: marks.append(status))
    try:
        window.catalog_view.download_requested.emit([store.get(first), store.get(second)])
        window.catalog_view.download_requested.emit([store.get(first)])
        assert len(queued) == 2
        assert queued[1].catalog_id == second and queued[1].file.site
        assert queued[0].catalog_id == first and queued[0].process.id == "lam_pouch"
        assert queued[0].file.url == "https://media.example.org/lamination.mp4"
        assert store.get(first).status == cat.REJECTED and not store.get(first).license_confirmed
        assert marks == [cat.REJECTED]
    finally:
        window.close()
        window.cache.close()


def test_pending_cookie_download_is_not_enqueued_after_close(store, monkeypatch):
    entry_id = store.add(result(1, files=[]))
    window = MainWindow(Settings(check_updates=False, link_check_days=0), processes.load_catalog(), store=store)
    callbacks, queued = [], []
    window.downloads.cookie_provider = lambda url, callback: callbacks.append(callback)
    monkeypatch.setattr(window.downloads.queue, "add", queued.append)
    try:
        entries = [store.get(entry_id)]
        window.downloads.download_entries(entries, None)
        window.downloads.download_entries(entries, None)
        assert len(callbacks) == 1 and not queued
        window.downloads.stop()
        callbacks[0]([{"name": "fixture", "value": "synthetic"}])
        assert not queued
    finally:
        window.close()
        window.cache.close()
