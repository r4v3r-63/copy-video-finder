import pytest
from PyQt6.QtCore import QCoreApplication

from app.core import licenses
from app.core.models import LicenseInfo, SearchFilters, VideoFile, VideoResult
from app.core.query import EN, RU, SearchPlan, SearchQuery
from app.services.search import Search, is_suitable, preview_file
from app.sources.base import RateLimitError, Source


def video(sid="1", duration=15, w=1920, h=1080, status=licenses.ALLOWED, source="Fake"):
    return VideoResult(source=source, source_id=sid, title="", description="", preview_url=None,
                       duration=duration, width=w, height=h, has_audio=None, page_url="",
                       license=LicenseInfo("x", status=status))


@pytest.mark.parametrize("v, ok", [
    (video(), True),
    (video(duration=4), False),
    (video(duration=46), False),
    (video(w=1080, h=1920), False),  # вертикальные не нужны — только горизонтальные
    (video(w=1080, h=1080), False),
    (video(w=640, h=360), False),
    (video(status=licenses.ATTRIBUTION), True),
    # Без свободной лицензии тоже показываются (решение пользователя 20.10.2026), платные — нет.
    (video(status=licenses.REVIEW), True),
    (video(status=licenses.REJECTED), True),
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
    from app import paths
    from app.core import processes
    from app.services.search import print_stems
    procs = processes.all_processes(processes.load_catalog(paths.resources_dir() / "processes.json"))
    worker = Search(plan, sources, SearchFilters(), printing=print_stems(procs))
    found, problems = [], []
    worker.on_found = found.extend
    worker.on_problem = problems.append
    worker.run()
    return worker, found, problems


def test_worker_merges_duplicates_across_languages_and_filters():
    plan = SearchPlan(None, [SearchQuery("ламинатор", RU), SearchQuery("laminator", EN)])
    src = FakeSource({
        "ламинатор": [video("1"), video("2", duration=200)],
        "laminator": [video("1"), video("3", status=licenses.REJECTED), video("4")],
    })
    worker, found, problems = run_worker(plan, [src])
    assert sorted(src.calls) == [("laminator", EN), ("ламинатор", RU)]
    assert sorted(r.source_id for r in found) == ["1", "3", "4"]  # без свободной лицензии — тоже
    assert worker.rejected == 1
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
    from app.services.search import topic_stems
    stems = topic_stems(["wide format laminator", "cold laminator", "холодная ламинация"])
    assert stems == {"lamin", "ламин"}


def test_pixabay_beaches_rejected_for_lamination():
    from app.core.topic import phrase_stems
    from app.services.search import is_relevant
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
    from app.core.topic import phrase_stems
    from app.services.search import is_relevant
    assert is_relevant(tagged("1", tags), phrase_stems([phrase])) is ok


def test_single_broad_word_does_not_confirm_topic():
    from app.core.topic import phrase_stems
    from app.services.search import is_relevant
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
    from app.core.topic import phrase_stems
    assert frozenset({"lamin"}) in phrase_stems(["laminator"])
    assert frozenset({"плотт"}) in phrase_stems(["плоттер"])
    assert phrase_stems(["этикетки"]) == [] and phrase_stems(["calendar"]) == []
    assert phrase_stems(["сборка календарей"]) == [] and phrase_stems(["изготовление печатей"]) == []


def test_wikimedia_checked_by_title_only(tmp_path):
    from app.core.topic import phrase_stems
    from app.services.search import is_relevant
    from app.sources.cache import Cache
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
    from app.services.search import DURATION, OFF_TOPIC
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
    from app import paths
    from app.core import processes
    from app.services.search import print_stems, relevance, topic_stems
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
    from app.services.search import topic_stems
    assert "3d" in topic_stems(["3d printer"])
    assert "dtf" in topic_stems(["dtf printing"])


def test_off_topic_results_can_be_shown_but_never_paid():
    plan = SearchPlan(None, [SearchQuery("laminator", EN)])
    beach = tagged("1", "beach, sea, paper")
    city = tagged("2", "city, aerial")
    bad_license = tagged("3", "beach")
    bad_license.license.name, bad_license.license.status = "Coverr Premium", licenses.REJECTED
    src = FakeSource({"laminator": [city, beach, bad_license]})
    worker, found, _ = run_worker(plan, [src])
    assert found == []
    shown = worker.off_topic_results()
    assert [r.source_id for r in shown] == ["1", "2"]  # «paper» ближе к полиграфии — выше


def test_broad_words_compared_as_whole_words():
    from app.core.topic import phrase_stems
    assert phrase_stems(["фотокиоск"])  # начинается с «фото», но не широкое
    assert phrase_stems(["сборка календарей"]) == []


def test_curated_key_word_confirms_topic_even_if_broad():
    from app import paths
    from app.core import processes
    from app.core.query import Dictionary, build_plan
    from app.core.topic import phrase_stems
    from app.services.search import is_relevant
    procs = processes.all_processes(processes.load_catalog(paths.resources_dir() / "processes.json"))
    plan = build_plan("Цифровая цветная лазерная печать", procs, Dictionary.load())
    phrases = phrase_stems([q.text for q in plan.queries], keep={q.text for q in plan.queries if q.key})
    office_printer = tagged("1", "printing device, printer, office, cmyk")
    assert is_relevant(office_printer, phrases)
    # У термоэтикеток «этикетки» больше не ключевое слово — ценники не проходят.
    plan = build_plan("Печать на термоэтикетках", procs, Dictionary.load())
    phrases = phrase_stems([q.text for q in plan.queries], keep={q.text for q in plan.queries if q.key})
    assert not is_relevant(tagged("2", "распродажа, цена, знак, этикетка"), phrases)


def test_service_exclude_words():
    from app import paths
    from app.core import processes
    from app.core.query import Dictionary, build_plan
    from app.services.search import is_excluded, is_relevant
    procs = processes.all_processes(processes.load_catalog(paths.resources_dir() / "processes.json"))
    plan = build_plan("Цифровая цветная лазерная печать", procs, Dictionary.load())
    assert is_excluded(tagged("1", "принтер, 3д печать, 3d"), plan.exclude)
    assert not is_excluded(tagged("2", "printer, office, paper"), plan.exclude)
    assert build_plan("3D-печать", procs, Dictionary.load()).exclude == frozenset()
    guillotine = build_plan("Гильотинная резка", procs, Dictionary.load())
    assert is_excluded(tagged("3", "A man opens the shredder machine after cutting a sheet, paper"), guillotine.exclude)
    assert is_excluded(tagged("4", "automatic paper tube cutting machine"), guillotine.exclude)
    assert not is_excluded(tagged("5", "Paper cutting process in a press"), guillotine.exclude)
    perforation = build_plan("Перфорация", procs, Dictionary.load())
    assert is_excluded(tagged("6", "Tectonic Penetrating Keratoplasty for Corneal Perforation"), perforation.exclude)
    assert not is_relevant(tagged("7", "пощечина, перфоратор, бить, драка"), [])
    eyelets = build_plan("люверсы", procs, Dictionary.load())  # синоним группы баннеров
    assert is_excluded(tagged("8", "Car Air Box Grommet Assembly"), eyelets.exclude)
    assert not is_excluded(tagged("9", "Heavy_Duty_Grommet_Press"), eyelets.exclude)


@pytest.mark.parametrize("tags, ok", [
    # Настоящий промах Coverr: пневмостеплер на стройке помечен «stapler».
    ("Stapling wood, staple gun, stapling, stapler, machine, plank, wood, construction, carpenter", False),
    ("stapler, office, office supplies, paper", True),
    ("принтер, распечатать, компьютер, бумага, офис", True),
    ("chopping, cutting, ingredients, kitchen, garlic", False),
    ("sydney, city, aerial", False),  # ни одного слова полиграфии
    # Настоящий промах Wikimedia: физический опыт с «laminated steel core».
    ("Solid State Delayed Lenz Effect in laminated steel core proved with the VRM measurement", False),
    ("mug, sublimation, heat press, souvenir", True),
    ("printing 3d, printer 3d, close 3d printing", True),
    # Настоящий промах Pixabay по «лазерной гравировке»: плазменная резка металла с искрами.
    ("лазер, вырезать, элемент дизайна, точность, гравировка, пламя, горящий, искры, самодельный, кузнец, "
     "стали, ремесло, металлический, металлоконструкции, ручной работы", False),
    ("laser, cut out, design element, accuracy, engraving, flame, burning, sparks, blacksmith, steel, "
     "metalwork", False),
    ("машина лазера, лазерная резка, технология, лазер, плазмы, промышленный, фабрика, стали, металл", False),
    ("laser engraving, acrylic, souvenir, gift, keychain", True),
    ("лазерная гравировка, металлическая ручка, сувенир, подарок", True),
    ("How to Properly Cut a Cigar with a Guillotine Cutter", False),
    ("mother board, electronic, computer, board, chips, digital, circuit, printed circuit board", False),
    ("typewriter, typing, writing, print, printing, vintage", False),
    ("origami, paper art, fold, paper folding", False),
    ("DC Catwoman Lonely City Hardcover Comic Book Trailer", False),
    ("crocus, blossom, bloom, yellow flower, rubber stamp, time lapse", False),
    ("принтер, 3д принтер, зубы, протез, 3d, человек, искусственные зубы", False),
    ("Wayne Rich - The Boardroom Surfboard Show - Chunk of Foam Challenge", False),
])
def test_only_printing_domain(tags, ok):
    from app.services.search import is_relevant
    assert is_relevant(tagged("1", tags), []) is ok


def test_rejected_before_are_hidden():
    from app.services.search import REJECTED_BEFORE
    plan = SearchPlan(None, [SearchQuery("laminator", EN)])
    src = FakeSource({"laminator": [tagged("1", "laminator, office"), tagged("2", "laminator, paper")]})
    QCoreApplication.instance() or QCoreApplication([])
    worker = Search(plan, [src], SearchFilters(), hidden={("Pixabay", "1")})
    found = []
    worker.on_found = found.extend
    worker.run()
    assert [r.source_id for r in found] == ["2"]
    assert worker.rejection_counts() == {REJECTED_BEFORE: 1}


@pytest.mark.parametrize("title, ok", [
    # Настоящие промахи Vimeo и Internet Archive: одно слово «plotter»/«scanner» без полиграфии рядом.
    ("Meet Fethullah Gulen, Deep State Plotter", False),
    ("Creative Sandbox: Spider Plotter", False),
    ("A Scanner Darkly V12", False),
    ("Car Scanner // Product Video", False),
    ("DTF ST. LOUIS - Georgia Entertainment Industries/Elephant Pictures", False),
    ("DTF Printing on t-shirt", True),
    ("плоттер, принтер, наклейка, бумага", True),
    ("Wide format plotter printing banners", True),
])
def test_single_key_word_needs_print_context(title, ok):
    from app.core.topic import phrase_stems
    from app.services.search import is_relevant
    phrases = phrase_stems(["plotter", "scanner", "плоттер", "dtf", "wide format plotter printing"],
                           keep={"plotter", "scanner", "плоттер", "dtf"})
    assert is_relevant(tagged("1", title), phrases) is ok
