import pytest
from PyQt6.QtCore import QCoreApplication

from app import licenses
from app.query import EN, RU, SearchPlan, SearchQuery
from app.search import SearchWorker, is_suitable, preview_file
from app.sources.base import LicenseInfo, RateLimitError, SearchFilters, Source, VideoFile, VideoResult


def video(sid="1", duration=15, w=1920, h=1080, status=licenses.ALLOWED, source="Fake"):
    return VideoResult(source=source, source_id=sid, title="", description="", preview_url=None,
                       duration=duration, width=w, height=h, has_audio=None, page_url="",
                       license=LicenseInfo("x", status=status))


@pytest.mark.parametrize("v, ok", [
    (video(), True),
    (video(duration=4), False),
    (video(duration=46), False),
    (video(w=1080, h=1920), True),
    (video(w=640, h=360), False),
    (video(status=licenses.ATTRIBUTION), True),
    (video(status=licenses.REVIEW), False),
    (video(status=licenses.REJECTED), False),
])
def test_is_suitable(v, ok):
    assert is_suitable(v, SearchFilters()) is ok


def test_only_without_attribution():
    assert not is_suitable(video(status=licenses.ATTRIBUTION), SearchFilters(), only_without_attribution=True)
    assert is_suitable(video(), SearchFilters(), only_without_attribution=True)


def test_orientation_filter():
    assert not is_suitable(video(w=1080, h=1920), SearchFilters(orientation="landscape"))
    assert not is_suitable(video(), SearchFilters(orientation="portrait"))


def test_preview_file_picks_lightest_watchable():
    v = video()
    v.files = [VideoFile("4K", "u4k", height=2160), VideoFile("SD", "u240", height=240),
               VideoFile("SD", "u360", height=360)]
    assert preview_file(v).url == "u360"
    v.files = [VideoFile("SD", "u240", height=240)]
    assert preview_file(v).url == "u240"


class FakeSource(Source):
    name = "Fake"

    def __init__(self, per_query, error=None):
        self.per_query = per_query
        self.error = error
        self.calls = []

    def search(self, query, filters, page=1, lang="en"):
        self.calls.append((query, lang))
        if self.error:
            raise self.error
        return self.per_query.get(query, [])

    def get_details(self, source_id):
        raise NotImplementedError

    def get_license(self, result):
        return result.license


def run_worker(plan, sources):
    QCoreApplication.instance() or QCoreApplication([])
    worker = SearchWorker(plan, sources, SearchFilters())
    found, problems = [], []
    worker.found.connect(found.extend)
    worker.problem.connect(problems.append)
    worker.run()  # синхронно, без отдельного потока
    return worker, found, problems


def test_worker_merges_duplicates_across_languages_and_filters():
    plan = SearchPlan(None, [SearchQuery("ламинатор", RU), SearchQuery("laminator", EN)])
    src = FakeSource({
        "ламинатор": [video("1"), video("2", duration=200)],
        "laminator": [video("1"), video("3", status=licenses.REJECTED), video("4")],
    })
    worker, found, problems = run_worker(plan, [src])
    assert sorted(src.calls) == [("laminator", EN), ("ламинатор", RU)]
    assert sorted(r.source_id for r in found) == ["1", "4"]
    assert worker.rejected == 2
    assert problems == []


def test_worker_reports_limit_once():
    plan = SearchPlan(None, [SearchQuery("a", RU), SearchQuery("b", EN)])
    src = FakeSource({}, error=RateLimitError("Fake", "Исчерпан лимит"))
    _, found, problems = run_worker(plan, [src])
    assert found == [] and problems == ["Исчерпан лимит"]
