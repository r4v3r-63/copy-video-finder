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
    # Сначала фразы услуги, затем её ключевые слова.
    assert plan.texts(RU)[:len(proc.phrases_ru)] == proc.phrases_ru
    assert plan.texts(EN)[:len(proc.phrases_en)] == proc.phrases_en


def test_every_service_has_both_languages():
    for proc in PROCS:
        plan = build_plan(proc.name, PROCS, DICT)
        assert plan.texts(RU) and plan.texts(EN), proc.id


def test_free_russian_text_is_translated():
    plan = build_plan("брошюровка на скобу и пружину", PROCS, DICT)
    assert plan.process is None
    assert plan.texts(RU) == ["брошюровка на скобу и пружину"]
    assert plan.texts(EN) == ["binding staple spiral"]


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


def test_category_word_searches_all_its_services():
    plan = build_plan("ламинация", PROCS, DICT)
    assert plan.process is None
    lam = [by_id(i) for i in ("lam_pouch", "lam_roll", "lam_wide")]
    assert plan.texts(RU) == [p.phrases_ru[0] for p in lam] + ["ламинатор"]
    assert plan.texts(EN) == [ph for p in lam for ph in p.phrases_en[:2]] + ["laminator"]
    assert "Запрос подходит к услугам" in plan.note


def test_alias_of_one_service_is_that_service():
    plan = build_plan("люверсы", PROCS, DICT)
    assert plan.process is by_id("eyelets")
    plan = build_plan("брошюровка на пружину", PROCS, DICT)  # синоним двух услуг
    assert {by_id("wire_binding").phrases_ru[0], by_id("comb_binding").phrases_ru[0]} <= set(plan.texts(RU))


def test_free_text_goes_to_catalog_services_first():
    plan = build_plan("гравировка", PROCS, DICT)  # часть названия одной услуги
    assert plan.process is by_id("laser_engraving")
    plan = build_plan("термо", PROCS, DICT)  # начало слов нескольких услуг
    assert plan.process is None and "Запрос подходит к услугам" in plan.note
    assert len(plan.texts(RU)) <= 2 * 4  # до 4 услуг: главная фраза + ключевое слово каждой


def test_text_without_service_is_translated():
    plan = build_plan("офсетная машина", PROCS, DICT)
    assert plan.texts(RU) == ["офсетная машина"]
    assert plan.texts(EN) == ["machine"]


def test_every_service_has_key_words_and_they_are_searched():
    for proc in PROCS:
        assert proc.key_ru and proc.key_en, proc.id
        plan = build_plan(proc.display, PROCS, DICT)
        lower = lambda texts: {t.lower() for t in texts}
        assert lower(proc.key_en) <= lower(plan.texts(EN)), proc.id
        assert lower(proc.key_ru) <= lower(plan.texts(RU)), proc.id


def test_lamination_searched_by_short_key_word():
    plan = build_plan("Широкоформатная ламинация (Ламинация)", PROCS, DICT)
    assert "laminator" in plan.texts(EN) and "ламинатор" in plan.texts(RU)
