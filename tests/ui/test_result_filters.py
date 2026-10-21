"""Фильтры не отправляют запросы, сохраняют карточки и не меняют отметки пользователя."""

from concurrent.futures import Future
from dataclasses import replace

from app.core import licenses, processes
from app.core.models import SearchFilters
from app.core.query import Dictionary, build_plan
from app.services.search import REJECTED_BEFORE, Search
from app.sources.base import Source
from app.sources.web import parse_page
from app.storage import catalog as cat
from app.storage.settings import Settings
from app.ui.main_window import MainWindow
from app.ui.widgets.result_filters import ResultFilters
from tests.fakes import fixture_text


def video(index=1, **fields):
    return replace(parse_page(fixture_text("manual_video.html"), f"https://example.org/video/{index}"),
                   preview_url=None, **fields)


def test_results_filter_combines_source_license_resolution_and_marks():
    filters = ResultFilters()
    result = video()
    filters.set_sources(["example.org", "Другая"])
    assert filters.allows(result, {})
    filters.source.setCurrentIndex(filters.source.findData("Другая"))
    assert not filters.allows(result, {})
    filters.source.setCurrentIndex(0)
    filters.license.setCurrentIndex(filters.license.findData(licenses.ALLOWED))
    assert not filters.allows(result, {})
    filters.license.setCurrentIndex(filters.license.findData("free"))
    assert filters.allows(result, {})
    filters.resolution.setCurrentIndex(2)
    assert not filters.allows(result, {})
    assert filters.allows(video(width=None, height=None), {})
    filters.reset()
    assert not filters.allows(result, {result.key: cat.REJECTED})
    filters.show_rejected.setChecked(True)
    assert filters.allows(result, {result.key: cat.REJECTED})
    filters.hide_catalog.setChecked(True)
    assert not filters.allows(result, {result.key: cat.NEW})


def test_sorting_puts_unknown_duration_last_and_uses_selected_order():
    filters = ResultFilters()
    results = [video(1, duration=30, relevance=10), video(2, duration=None, relevance=100),
               video(3, duration=10, relevance=20)]
    assert [r.source_id for r in sorted(results, key=filters.sort_key)] == [r.source_id for r in results[1:]] + [
        results[0].source_id]
    filters.order.setCurrentIndex(filters.order.findData("short"))
    assert [r.duration for r in sorted(results, key=filters.sort_key)] == [10, 30, None]
    filters.order.setCurrentIndex(filters.order.findData("long"))
    assert [r.duration for r in sorted(results, key=filters.sort_key)] == [30, 10, None]


def test_rejected_results_are_separate_and_do_not_stop_search_expansion():
    procs = processes.all_processes(processes.load_catalog())
    plan = build_plan("ламинирование", procs, Dictionary.load())
    result = video()
    worker = Search(plan, [], SearchFilters(), hidden={result.key})
    found, hidden = [], []
    worker.on_found = found.extend
    worker.on_rejected_before = hidden.extend
    future = Future()
    future.set_result([result])

    class TestSource(Source):
        def search(self, *args, **kwargs):
            return []

        def get_details(self, source_id):
            return result

        def get_license(self, video):
            return video.license

    worker._take(future, TestSource(), plan.queries[0], set())
    assert hidden == [result] and not found and not worker.seen
    assert worker.rejections[result.key] == REJECTED_BEFORE
    worker._take(future, TestSource(), plan.queries[0], set())
    assert hidden == [result]  # повторные фразы не дублируют карточку


def test_toggling_filters_preserves_items_and_rejection(data_dir):
    data_dir.mkdir()
    window = MainWindow(Settings(check_updates=False, link_check_days=0), processes.load_catalog())
    try:
        first, second = video(1), video(2, duration=10)
        window.search.add_results([first, second])
        item = next(window.search.results.item(i) for i in range(window.search.results.count())
                    if window.search.results.item(i).data(256).key == first.key)
        window.search.reject_result(first)
        assert item.isHidden()
        window.search.result_filters.show_rejected.setChecked(True)
        assert not item.isHidden()
        assert window.catalog.find(first.key).status == cat.REJECTED
        window.search.result_filters.order.setCurrentIndex(window.search.result_filters.order.findData("short"))
        assert window.search.results.item(0).data(256).key == second.key
        assert window.search.results.item(1) is item
    finally:
        window.close()
        window.catalog.close()
        window.cache.close()
