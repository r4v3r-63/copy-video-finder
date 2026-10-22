"""Новая вкладка: фильтры без сети, ограничения массового выбора и изоляция каталога."""

import json

import pytest
from PyQt6.QtCore import Qt

from app.core import processes
from app.core.stock_link import parse_link
from app.sources.pixabay import parse_hit
from app.storage.settings import Settings
from app.ui.main_window import MainWindow
from tests.fakes import fixture_text


@pytest.fixture
def window(data_dir):
    data_dir.mkdir()
    value = MainWindow(Settings(check_updates=False, link_check_days=0), processes.load_catalog())
    yield value
    value.close()
    value.catalog.close()
    value.cache.close()


def populate(tab, bulk=False):
    tab.link = parse_link("https://archive.org/search?query=printing" if bulk
                          else "https://pixabay.com/videos/search/?orientation=horizontal&time_lapse=true")
    tab.model.individual = not tab.link.batch_enabled
    tab.orientation.setCurrentIndex(tab.orientation.findData(tab.link.orientation))
    items = [parse_hit(hit) for hit in json.loads(fixture_text("stock_link/pixabay.json"))["hits"]]
    tab._page(items, 800, 500)


def test_filters_recalculate_inclusively_and_do_not_touch_catalog(window):
    tab = window.stock_link
    populate(tab)
    tab.minimum.setText("10")
    tab.maximum.setText("30")
    assert [r.source_id for r in tab.model.items] == ["201", "202"]
    tab.maximum.setText("9")
    assert not tab.model.items and "превышать" in tab.filter_error.text()
    tab.maximum.setText("30")
    tab.orientation.setCurrentIndex(tab.orientation.findData("portrait"))
    assert [r.source_id for r in tab.model.items] == ["206"]
    assert window.catalog.entries() == []
    tab.reset_filters()
    assert len(tab.model.items) == 7


def test_pixabay_supports_batch_selection_and_count(window, tmp_path, monkeypatch):
    from app.ui.tabs import stock_link_tab

    queued = []
    monkeypatch.setattr(stock_link_tab.StockDownloadWorker, "start", lambda worker: queued.extend(worker.jobs))
    tab = window.stock_link
    populate(tab)
    tab.folder.setText(str(tmp_path))
    assert tab.all_select.isEnabled() and tab.download_all.isEnabled()
    tab.quantity_count.setValue(2)
    tab.model.select_all()
    assert len(tab.model.checked) > 1
    tab.start_download(True)
    assert len(queued) == 2
    assert window.catalog.entries() == []


def test_bulk_selection_is_limited_to_current_filters(window):
    tab = window.stock_link
    populate(tab, bulk=True)
    tab.minimum.setText("10")
    tab.maximum.setText("30")
    assert tab.all_select.isEnabled() and tab.download_all.isEnabled()
    tab.model.select_all()
    assert len(tab.model.checked) == 4
    tab.orientation.setCurrentIndex(tab.orientation.findData("landscape"))
    assert len(tab.model.checked) == 2
    assert "подходит: 2" in tab.stats.text()


def test_forming_queue_uses_original_quality_and_no_catalog(window, tmp_path, monkeypatch):
    from app.ui.tabs import stock_link_tab

    queued = []
    monkeypatch.setattr(stock_link_tab.StockDownloadWorker, "start", lambda worker: queued.extend(worker.jobs))
    tab = window.stock_link
    populate(tab)
    tab.folder.setText(str(tmp_path))
    tab.model.setData(tab.model.index(0, 0), Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)
    tab.quality.setCurrentIndex(tab.quality.findData(720))
    tab.start_download()
    assert len(queued) == 1
    job = queued[0]
    assert not job.convert and not job.trim and not job.mute and not job.check_content and job.flat_folder
    assert job.catalog_id is None and job.base_dir == tmp_path
    assert window.catalog.entries() == []


def test_cancel_button_requests_stop_without_waiting(window):
    class Worker:
        requested = False

        def request_stop(self):
            self.requested = True

        def stop(self):
            pytest.fail("Кнопка отмены не должна блокироваться на wait")

        def isRunning(self):
            return True

    worker = Worker()
    window.stock_link.download_worker = worker
    try:
        window.stock_link.stop()
        assert worker.requested
    finally:
        window.stock_link.download_worker = None


def test_all_on_page_does_not_include_other_api_pages(window, tmp_path, monkeypatch):
    from app.ui.tabs import stock_link_tab

    queued = []
    monkeypatch.setattr(stock_link_tab.StockDownloadWorker, "start", lambda worker: queued.extend(worker.jobs))
    tab = window.stock_link
    populate(tab)
    second = [parse_hit(hit) for hit in json.loads(fixture_text("stock_link/pixabay.json"))["hits"]]
    for video in second:
        video.source_id = "next-" + video.source_id
    tab._page(second, 800, 500)
    tab.folder.setText(str(tmp_path))
    tab.minimum.setText("10")
    tab.maximum.setText("30")
    tab.quantity_mode.setCurrentIndex(tab.quantity_mode.findData("page"))
    assert tab.source_page.currentData() == 1
    assert {video.source_id for video in tab.model.items} == {"201", "202"}
    tab.start_download(True)
    assert [job.result.source_id for job in queued] == ["201", "202"]


def test_count_limit_applies_after_filters_and_skips_completed(window, tmp_path, monkeypatch):
    from app.ui.tabs import stock_link_tab

    queued = []
    monkeypatch.setattr(stock_link_tab.StockDownloadWorker, "start", lambda worker: queued.extend(worker.jobs))
    tab = window.stock_link
    populate(tab, bulk=True)
    tab.folder.setText(str(tmp_path))
    tab.minimum.setText("10")
    tab.maximum.setText("30")
    tab.orientation.setCurrentIndex(tab.orientation.findData("landscape"))
    tab.quantity_count.setValue(1)
    tab.completed.add(tab.model.items[0].key)
    tab.start_download(True)
    assert [job.result.source_id for job in queued] == ["202"]


def test_selected_download_also_obeys_quantity_limit(window, tmp_path, monkeypatch):
    from app.ui.tabs import stock_link_tab

    queued = []
    monkeypatch.setattr(stock_link_tab.StockDownloadWorker, "start", lambda worker: queued.extend(worker.jobs))
    tab = window.stock_link
    populate(tab, bulk=True)
    tab.folder.setText(str(tmp_path))
    tab.quantity_count.setValue(2)
    tab.model.select_all()
    tab.start_download()
    assert len(queued) == 2


def test_all_found_uses_every_page_and_configured_parallelism(window, tmp_path, monkeypatch):
    from app.ui.tabs import stock_link_tab

    workers = []
    monkeypatch.setattr(stock_link_tab.StockDownloadWorker, "start", lambda worker: workers.append(worker))
    tab = window.stock_link
    populate(tab)
    second = [parse_hit(hit) for hit in json.loads(fixture_text("stock_link/pixabay.json"))["hits"]]
    for video in second:
        video.source_id = "next-" + video.source_id
    tab._page(second, 800, 500)
    tab.folder.setText(str(tmp_path))
    tab.minimum.setText("10")
    tab.maximum.setText("30")
    tab.quantity_mode.setCurrentIndex(tab.quantity_mode.findData("page"))
    tab.quantity_count.setValue(1)
    tab.completed.add(tab.model.items[0].key)
    tab.quantity_mode.setCurrentIndex(tab.quantity_mode.findData("all"))
    tab.parallel_count.setValue(8)
    assert tab.source_page.currentData() == 0 and not tab.source_page.isEnabled()
    assert not tab.quantity_count.isEnabled()
    tab.start_download(True)
    assert workers[0].parallel == 8
    assert {job.result.source_id for job in workers[0].jobs} == {"202", "next-201", "next-202"}
    assert window.catalog.entries() == []
