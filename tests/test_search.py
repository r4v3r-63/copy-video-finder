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


def tagged(sid, tags, source="Pixabay"):
    v = video(sid, source=source)
    v.title = v.description = tags
    return v


def test_topic_stems_skip_generic_words():
    from app.search import topic_stems
    stems = topic_stems(["wide format laminator", "cold laminator", "холодная ламинация"])
    assert stems == {"lamin", "ламин"}


def test_pixabay_beaches_rejected_for_lamination():
    from app.search import is_relevant, topic_stems
    stems = topic_stems(["широкоформатный ламинатор", "wide format laminator", "cold laminator"])
    assert not is_relevant(tagged("1", "beach, sea, ocean, coast"), stems)
    assert not is_relevant(tagged("2", "sydney, city, aerial, large"), stems)
    assert is_relevant(tagged("3", "laminating, office, document"), stems)


def test_wikimedia_checked_by_title_only(tmp_path):
    from app.cache import Cache
    from app.search import is_relevant, topic_stems
    from app.sources.wikimedia import WikimediaSource
    from tests.fakes import FakeResponse, FakeSession, fixture_text
    session = FakeSession(FakeResponse(text=fixture_text("wikimedia_search.json")))
    session.headers = {}
    cache = Cache(tmp_path / "c.sqlite")
    src = WikimediaSource(cache, session=session)
    found = {r.title: r for r in src.search("laminator", SearchFilters())}
    cache.close()
    stems = topic_stems(["roll laminator", "lamination"])
    assert is_relevant(found["Heated Roll Lamination Machine"], stems, use_description=False)
    # В описании есть «laminated», но ролик о спицах — по названию он не проходит.
    assert not is_relevant(found["Knitting needle"], stems, use_description=False)


def test_worker_rejects_off_topic_and_counts_reasons():
    from app.search import DURATION, OFF_TOPIC
    plan = SearchPlan(None, [SearchQuery("laminator", EN)])
    src = FakeSource({"laminator": [tagged("1", "laminator, office"), tagged("2", "beach, sea"),
                                    video("3", duration=300)]})
    src.per_query["laminator"][2].title = "laminator"
    worker, found, _ = run_worker(plan, [src])
    assert [r.source_id for r in found] == ["1"]
    assert worker.rejection_counts() == {OFF_TOPIC: 1, DURATION: 1}


def test_rejected_by_one_phrase_accepted_by_another_not_counted():
    plan = SearchPlan(None, [SearchQuery("a", EN), SearchQuery("b", EN)])
    long_clip = video("1", duration=300)
    src = FakeSource({"a": [long_clip], "b": [video("1")]})
    worker, found, _ = run_worker(plan, [src])
    assert [r.source_id for r in found] == ["1"]
    assert worker.rejected == 0


def test_results_ranked_by_topic_then_printing():
    from app import paths, processes
    from app.search import print_stems, relevance, topic_stems
    procs = processes.all_processes(processes.load_catalog(paths.resources_dir() / "processes.json"))
    printing = print_stems(procs)
    stems = topic_stems(["roll laminator"])
    exact = tagged("1", "laminator, office, paper")
    loose = tagged("2", "laminator, garden")
    other = tagged("3", "printer, office")
    nothing = tagged("4", "beach, sea")
    scores = [relevance(v, stems, printing) for v in (exact, loose, other, nothing)]
    assert scores[0] > scores[1] > scores[2] > scores[3] == 0
