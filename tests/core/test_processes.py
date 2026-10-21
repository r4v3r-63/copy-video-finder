import json

import pytest

from app import paths
from app.core import processes
from tests.fakes import FIXTURES, fixture_text


def catalog():
    return processes.load_catalog(paths.resources_dir() / "processes.json")


def test_catalog_groups_cover_all_site_services():
    """Услуги сайта, кроме исключённых пользователем, — каждая ровно в одной группе."""
    cats = catalog()
    groups = processes.all_processes(cats)
    site = [s for s in (FIXTURES / "site_services.txt").read_text(
        encoding="utf-8").splitlines() if s and s != "Перевод документов"]
    variants = [v for g in groups for v in g.variants]
    assert sorted(variants) == sorted(site)
    assert len(variants) == 375 and len(groups) == 87
    assert processes.find_by_display(groups, "Перевод документов") is None
    assert processes.find_variant(groups, "Перевод документов") is None
    assert processes.suggestion_texts(groups, "перевод документ") == []
    assert len(cats) >= 15 and len(groups) >= 60
    for g in groups:
        assert g.variants and g.phrases_ru and g.phrases_en and g.levels, g.id


def test_process_ids_unique_and_phrases_present():
    procs = processes.all_processes(catalog())
    assert len({p.id for p in procs}) == len(procs)
    for proc in procs:
        assert proc.phrases_ru and proc.phrases_en, proc.id
        assert all(p.strip() for p in proc.phrases_ru + proc.phrases_en), proc.id
        assert proc.target >= 2


def test_generic_queries_not_used():
    generic = {"printing", "print", "printer", "video", "печать", "видео"}
    for proc in processes.all_processes(catalog()):
        assert not generic & {p.lower() for p in proc.phrases_ru + proc.phrases_en}, proc.id


def test_suggest_by_name_prefix():
    procs = processes.all_processes(catalog())
    found = processes.suggest(procs, "лами")
    assert {"lam_pouch", "lam_roll", "lam_wide"} <= {p.id for p in found}


def test_suggest_by_alias():
    procs = processes.all_processes(catalog())
    ids = [p.id for p in processes.suggest(procs, "брошюровка на пружину")]
    assert set(ids[:2]) == {"wire_binding", "comb_binding"}
    assert processes.suggest(procs, "кбс")[0].id == "perfect_binding"
    assert processes.suggest(procs, "люверс")[0].id == "wf_ecosolvent"


def test_site_service_suggestions_and_variants():
    procs = processes.all_processes(catalog())
    texts = processes.suggestion_texts(procs, "визитки с фол")
    assert texts[0] == "Визитки с фольгой (Визитки)"
    group, variant = processes.find_variant(procs, texts[0])
    assert group.id == "business_cards_premium" and variant == "Визитки с фольгой"
    assert processes.find_variant(procs, "фото на визу в японию")[0].id == "photo_visa"
    assert "Фото на визу в Японию (Фото и фото на документы)" in processes.suggestion_texts(procs, "визу япон")


def test_suggest_ignores_case_and_yo():
    procs = processes.all_processes(catalog())
    assert processes.suggest(procs, "ТВЕРДЫЙ ПЕРЕПЛЕТ")[0].id == "metal_bind"


def test_suggest_empty_and_unknown():
    procs = processes.all_processes(catalog())
    assert processes.suggest(procs, "  ") == []
    assert processes.suggest(procs, "абвгдежз") == []


def test_find_by_display():
    procs = processes.all_processes(catalog())
    proc = procs[0]
    assert processes.find_by_display(procs, proc.display) is proc
    assert processes.find_by_display(procs, proc.name.upper()) is proc
    assert processes.find_by_display(procs, "что-то своё") is None


def test_user_copy_overrides_builtin(data_dir):
    custom = {"version": 5, "categories": [{"id": "c", "name": "Кат", "processes": [
        {"id": "p", "name": "Услуга", "phrases_ru": ["икс"], "phrases_en": ["x y"]}]}]}
    paths.processes_file().write_text(json.dumps(custom, ensure_ascii=False), encoding="utf-8")
    cats = processes.load_catalog()
    assert [p.id for p in processes.all_processes(cats)] == ["p"]


def test_old_user_copy_retired(data_dir):
    """Копия справочника версии 4 (43 техпроцесса) откладывается — работает справочник услуг сайта."""
    old = {"version": 4, "categories": [{"id": "c", "name": "Кат", "processes": [
        {"id": "p", "name": "Услуга", "phrases_ru": ["икс"], "phrases_en": ["x y"]}]}]}
    paths.processes_file().write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    cats = processes.load_catalog()
    assert len(processes.all_processes(cats)) > 60
    assert not paths.processes_file().exists() and (paths.data_dir() / "processes.v4.json").exists()


@pytest.mark.parametrize("explicit_path", [False, True])
def test_translation_removed_from_old_user_copy_without_losing_edits(data_dir, explicit_path):
    source = fixture_text("services_with_translation.json")
    path = paths.processes_file()
    path.write_text(source, encoding="utf-8")
    categories = processes.load_catalog(path if explicit_path else None)
    loaded = {p.id: p for p in processes.all_processes(categories)}
    assert set(loaded) == {"scanning", "my_service"}
    assert loaded["scanning"].name == "Моё сканирование"
    assert loaded["scanning"].phrases_ru == ["мой сканер документов"]
    assert loaded["my_service"].phrases_en == ["custom printing"]
    assert path.read_text(encoding="utf-8") == source
    processes.save_catalog(categories)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["removed"] == ["translation"]
    assert all(p["id"] != "translation" for c in saved["categories"] for p in c["processes"])
