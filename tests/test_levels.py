"""Уровни расширения поиска: оборудование → действие → этапы → похожие операции."""

import pytest
from PyQt6.QtCore import QCoreApplication

from app import paths, processes
from app.query import DE, EN, FR, Dictionary, SearchPlan, SearchQuery, build_plan
from app.search import ENOUGH, SearchWorker, is_excluded, is_relevant, print_stems
from app.sources.base import LicenseInfo, SearchFilters, Source, VideoResult
from app.topic import in_print_domain, phrase_stems, words

PROCS = processes.all_processes(processes.load_catalog(paths.resources_dir() / "processes.json"))
DOMAIN = print_stems(PROCS)


def video(i, title, source="Pixabay"):
    return VideoResult(source=source, source_id=str(i), title=title, description="", preview_url=None,
                       duration=10, width=1920, height=1080, has_audio=None, page_url="",
                       license=LicenseInfo("Pixabay Content License", status="Разрешено"))


class FakeSource(Source):
    name = "Pixabay"
    languages = frozenset({"ru", "en", "de", "fr"})

    def __init__(self, answers, max_level=99):
        self.answers, self.asked, self.max_level = answers, [], max_level

    def search(self, query, filters, page=1, lang="en"):
        self.asked.append(query)
        return self.answers.get(query, [])

    def get_details(self, source_id):
        raise NotImplementedError

    def get_license(self, result):
        return result.license


def run(plan, sources):
    QCoreApplication.instance() or QCoreApplication([])
    worker = SearchWorker(plan, sources, SearchFilters(max_duration=180))
    found = []
    worker.found.connect(found.extend)
    worker.run()
    return worker, found


def test_every_service_has_levels():
    for p in PROCS:
        assert p.levels, p.id
        assert p.levels[-1].match == "смежный процесс", p.id
        for level in p.levels:
            assert level.phrases.get("en"), (p.id, level.name)


def test_level_phrases_pass_own_topic_filter():
    """Ролик, названный как запрос любого уровня, проходит фильтр темы — иначе запрос бесполезен.

    Многозначному слову («plotter») по правилу нужно ещё слово полиграфии рядом — оно добавляется.
    """
    bad = []
    for p in PROCS:
        plan = build_plan(p.display, PROCS, Dictionary.load())
        phrases = phrase_stems([q.text for q in plan.queries], keep={q.text for q in plan.queries if q.key})
        for q in plan.queries:
            in_domain = in_print_domain(set(words(q.text)), DOMAIN)
            r = video(1, q.text + " paper printing")
            if not in_domain or not is_relevant(r, phrases, domain=DOMAIN) or is_excluded(r, plan.exclude):
                bad.append((p.id, q.text))
    assert bad == []


def test_plan_has_levels_in_four_languages():
    plan = build_plan("Брошюровка на металлическую пружину (Переплёт)", PROCS, Dictionary.load())
    assert plan.levels[0] == "Название услуги" and len(plan.levels) == 5
    langs = {q.lang for q in plan.queries if q.level > 0}
    assert {EN, DE, FR, "ru"} <= langs
    assert all(q.level == 0 for q in plan.queries[:3])
    related = [q for q in plan.queries if q.match == "смежный процесс"]
    assert related and all(q.level == 4 for q in related)


def test_escalation_only_when_few_results():
    plan = SearchPlan(None, [SearchQuery("wire binding", EN), SearchQuery("wire binding machine", EN, level=1),
                             SearchQuery("spiral notebook binding", EN, level=2, match="смежный процесс")],
                      levels=["Название услуги", "Оборудование", "Похожие операции"])
    many = {"wire binding": [video(i, "wire binding office") for i in range(ENOUGH)]}
    src = FakeSource(many)
    worker, found = run(plan, [src])
    assert src.asked == ["wire binding"] and worker.levels_used == 0

    few = {"wire binding": [video(1, "wire binding office")],
           "wire binding machine": [video(2, "wire binding machine working")],
           "spiral notebook binding": [video(3, "spiral notebook binding paper")]}
    src = FakeSource(few)
    worker, found = run(plan, [src])
    assert src.asked == ["wire binding", "wire binding machine", "spiral notebook binding"]
    assert worker.levels_used == 2
    hints = {r.source_id: (r.match_hint, r.found_by) for r in found}
    assert hints["1"] == ("", "wire binding")
    assert hints["3"] == ("смежный процесс", "spiral notebook binding")


def test_source_max_level():
    plan = SearchPlan(None, [SearchQuery("laminator", EN), SearchQuery("laminating machine", EN, level=1),
                             SearchQuery("laminating paper", EN, level=2)])
    src = FakeSource({}, max_level=1)
    run(plan, [src])
    assert src.asked == ["laminator", "laminating machine"]


@pytest.mark.parametrize("title, ok", [
    ("Drahtbindung im Copyshop", True),
    ("Etikettendrucker in Aktion", True),
    ("Pose d'œillets sur bâche", True),
    ("Massicot à papier", True),
    ("Ein Hund im Park", False),
])
def test_german_french_domain(title, ok):
    assert in_print_domain(set(words(title))) is ok


def test_german_letters_are_words():
    assert words("Ösenpresse für Banner") == ["ösenpresse", "für", "banner"]


@pytest.mark.parametrize("title", [
    # Настоящие промахи живой проверки: слово услуги («wire», «pliage») без полиграфии.
    "barbed wire, insect, field, freedom, metal, close up, flies, plant, pointed",
    "power socket, electricity, fork, wire, turn on, switch off, button, extension, close-up",
    "Armature en acier de pliage",
    "board, chips, closer, computer, science, technology, wires, electricity",
])
def test_service_words_do_not_admit_junk(title):
    plan = build_plan("Брошюровка на металлическую пружину (Переплёт)", PROCS, Dictionary.load())
    phrases = phrase_stems([q.text for q in plan.queries], keep={q.text for q in plan.queries if q.key})
    assert not is_relevant(video(1, title), phrases, domain=DOMAIN)


@pytest.mark.parametrize("name, title", [
    ("Скоба внакидку (Переплёт)", "saddle stitching booklet machine"),
    ("Скругление углов (Послепечатные работы)", "corner rounder for cards"),
    ("Биговка (Послепечатные работы)", "creasing with soft rubber"),
    ("Термопереплёт Unibind (Переплёт)", "Unibind thermal binding system"),
])
def test_service_terms_now_pass(name, title):
    """Раньше эти ролики отсеивались: термины услуг не считались словами полиграфии."""
    plan = build_plan(name, PROCS, Dictionary.load())
    phrases = phrase_stems([q.text for q in plan.queries], keep={q.text for q in plan.queries if q.key})
    assert is_relevant(video(1, title), phrases, domain=DOMAIN)


def lic_video(i, title, name, status):
    r = video(i, title)
    r.license = LicenseInfo(name, status=status)
    return r


def test_unlicensed_shown_only_on_request():
    """По теме, но без свободной лицензии — отдельно, «только посмотреть»; платные и не по теме — нет."""
    plan = SearchPlan(None, [SearchQuery("wire binding", EN)])
    answers = {"wire binding": [
        video(1, "wire binding office"),
        lic_video(2, "wire binding machine demo", "все права защищены", "Отклонено"),
        lic_video(3, "wire binding paper", "CC BY-NC 3.0", "Отклонено"),
        lic_video(4, "wire binding studio", "Coverr Premium", "Отклонено"),  # платный — никогда
        lic_video(5, "cat on the sofa", "все права защищены", "Отклонено"),  # не по теме — никогда
    ]}
    worker, found = run(plan, [FakeSource(answers)])
    assert [r.source_id for r in found] == ["1"]
    extra = worker.unlicensed_results()
    assert sorted(r.source_id for r in extra) == ["2", "3"]
    assert all(r.view_only for r in extra) and not found[0].view_only


def test_is_paid():
    from app import licenses
    for name in ("Coverr Premium", "Videezy Pro License", "Editorial", "платная лицензия"):
        assert licenses.is_paid(name)
    for name in ("все права защищены", "CC BY-NC 3.0", "Pixabay Content License", "Professional footage"):
        assert not licenses.is_paid(name)
