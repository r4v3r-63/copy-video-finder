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
    from app import paths, processes
    from app.search import print_stems
    procs = processes.all_processes(processes.load_catalog(paths.resources_dir() / "processes.json"))
    worker = SearchWorker(plan, sources, SearchFilters(), printing=print_stems(procs))
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
    from app.search import is_relevant
    from app.topic import phrase_stems
    phrases = phrase_stems(["широкоформатный ламинатор", "wide format laminator", "cold laminator"])
    assert not is_relevant(tagged("1", "beach, sea, ocean, coast"), phrases)
    assert not is_relevant(tagged("2", "sydney, city, aerial, large"), phrases)
    assert is_relevant(tagged("3", "laminating, office, document"), phrases)


@pytest.mark.parametrize("phrase, tags, ok", [
    # Настоящие промахи из выдачи Pixabay: одного совпавшего слова мало.
    ("vinyl cutter", "record, record player, vinyl, turntable, retro", False),
    ("sticker cutting", "chopping, cutting, ingredients, mortar, garlic", False),
    ("spiral binding", "beach, waves, ocean, spiral, sand, water", False),
    ("hole punch", "woodpecker, bird, nature, hole, tree", False),
    ("heat press", "embers, glow, fire, heat, campfire, hot", False),
    ("laser printer", "disco, laser, discotheque, light show", False),
    ("vinyl cutter", "vinyl cutter, plotter, sticker", True),
    ("hole punch", "office, hole punch, paper", True),
    ("3d printer", "printing 3d, printer 3d, close 3d printing", True),
])
def test_all_significant_words_of_a_phrase_required(phrase, tags, ok):
    from app.search import is_relevant
    from app.topic import phrase_stems
    assert is_relevant(tagged("1", tags), phrase_stems([phrase])) is ok


def test_single_broad_word_does_not_confirm_topic():
    from app.search import is_relevant
    from app.topic import phrase_stems
    phrases = phrase_stems(["печать этикеток", "термопринтер этикеток", "этикетки", "label printer"])
    sale_sign = tagged("1", "распродажа, цена, рекламный щит, знак, этикетка")
    turntable = tagged("2", "проигрыватель, старый, винил, музыка, этикетка")
    thermal = tagged("3", "термопринтер, этикетка, склад")
    english = tagged("4", "label, printer, barcode, warehouse")
    assert not is_relevant(sale_sign, phrases)
    assert not is_relevant(turntable, phrases)
    assert is_relevant(thermal, phrases)
    assert is_relevant(english, phrases)


def test_specific_single_words_still_confirm_topic():
    from app.topic import phrase_stems
    assert frozenset({"lamin"}) in phrase_stems(["laminator"])
    assert frozenset({"плотт"}) in phrase_stems(["плоттер"])
    assert phrase_stems(["этикетки"]) == [] and phrase_stems(["calendar"]) == []
    assert phrase_stems(["сборка календарей"]) == [] and phrase_stems(["изготовление печатей"]) == []


def test_wikimedia_checked_by_title_only(tmp_path):
    from app.cache import Cache
    from app.search import is_relevant
    from app.topic import phrase_stems
    from app.sources.wikimedia import WikimediaSource
    from tests.fakes import FakeResponse, FakeSession, fixture_text
    session = FakeSession(FakeResponse(text=fixture_text("wikimedia_search.json")))
    session.headers = {}
    cache = Cache(tmp_path / "c.sqlite")
    src = WikimediaSource(cache, session=session)
    found = {r.title: r for r in src.search("laminator", SearchFilters())}
    cache.close()
    stems = phrase_stems(["roll laminator", "lamination"])
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


def test_short_mixed_words_are_significant():
    from app.search import topic_stems
    assert "3d" in topic_stems(["3d printer"])
    assert "dtf" in topic_stems(["dtf printing"])


def test_off_topic_results_can_be_shown_but_never_bad_licenses():
    plan = SearchPlan(None, [SearchQuery("laminator", EN)])
    beach = tagged("1", "beach, sea, paper")
    city = tagged("2", "city, aerial")
    bad_license = tagged("3", "beach")
    bad_license.license.status = licenses.REJECTED
    src = FakeSource({"laminator": [city, beach, bad_license]})
    worker, found, _ = run_worker(plan, [src])
    assert found == []
    shown = worker.off_topic_results()
    assert [r.source_id for r in shown] == ["1", "2"]  # «paper» ближе к полиграфии — выше
