from app import paths, processes
from app.query import EN, RU, Dictionary, build_plan, is_russian


def setup_module():
    global PROCS, DICT
    PROCS = processes.all_processes(processes.load_catalog(paths.resources_dir() / "processes.json"))
    DICT = Dictionary.load()


def by_id(pid):
    return next(p for p in PROCS if p.id == pid)


def test_service_searches_in_both_languages():
    proc = by_id("wire_binding")
    plan = build_plan(proc.display, PROCS, DICT)
    assert plan.process is proc
    assert plan.texts(RU) == proc.phrases_ru
    assert plan.texts(EN) == proc.phrases_en


def test_every_service_has_both_languages():
    for proc in PROCS:
        plan = build_plan(proc.name, PROCS, DICT)
        assert plan.texts(RU) and plan.texts(EN), proc.id


def test_free_russian_text_is_translated():
    plan = build_plan("брошюровка на пружину", PROCS, DICT)
    assert plan.process is None
    assert plan.texts(RU) == ["брошюровка на пружину"]
    assert plan.texts(EN) == ["spiral binding"]


def test_translation_examples():
    assert DICT.translate("ламинирование документов") == "laminating document"
    assert DICT.translate("Твёрдый переплёт") == "hardcover binding"
    assert DICT.translate("установка люверсов на баннер") == "grommet banner"
    assert DICT.translate("резка бумаги на гильотине") == "cutting paper guillotine cutter"
    assert DICT.translate("3D печать") == "3d printing"
    assert DICT.translate("DTF на футболках") == "dtf printing t-shirt"


def test_untranslatable_russian_searches_russian_only():
    plan = build_plan("абракадабра", PROCS, DICT)
    assert plan.texts(RU) == ["абракадабра"]
    assert plan.texts(EN) == []
    assert "только на русском" in plan.note


def test_free_english_text():
    plan = build_plan("offset press", PROCS, DICT)
    assert plan.texts(EN) == ["offset press"]
    assert plan.texts(RU) == []


def test_english_phrase_of_service_adds_russian():
    plan = build_plan("Roll Laminator", PROCS, DICT)
    assert plan.texts(EN) == ["Roll Laminator"]
    assert plan.texts(RU) == by_id("lam_roll").phrases_ru


def test_empty_and_duplicates():
    assert build_plan("   ", PROCS, DICT).queries == []
    plan = build_plan("guillotine", PROCS, DICT)
    assert len(plan.queries) == len({(q.text.lower(), q.lang) for q in plan.queries})


def test_is_russian():
    assert is_russian("3D печать")
    assert not is_russian("3d printing")
