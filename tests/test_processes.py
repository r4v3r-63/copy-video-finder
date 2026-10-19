import json

from app import paths, processes


def catalog():
    return processes.load_catalog(paths.resources_dir() / "processes.json")


def test_catalog_has_11_categories_and_43_processes():
    cats = catalog()
    assert len(cats) == 11
    assert len(processes.all_processes(cats)) == 43


def test_process_ids_unique_and_phrases_present():
    procs = processes.all_processes(catalog())
    assert len({p.id for p in procs}) == len(procs)
    for proc in procs:
        assert proc.phrases, proc.id
        assert all(p.strip() for p in proc.phrases), proc.id
        assert proc.target >= 2


def test_generic_queries_not_used():
    generic = {"printing", "print", "printer", "video"}
    for proc in processes.all_processes(catalog()):
        assert not generic & {p.lower() for p in proc.phrases}, proc.id


def test_suggest_by_name_prefix():
    procs = processes.all_processes(catalog())
    found = processes.suggest(procs, "лами")
    assert {p.id for p in found} == {"lam_pouch", "lam_roll", "lam_wide"}


def test_suggest_by_alias():
    procs = processes.all_processes(catalog())
    ids = [p.id for p in processes.suggest(procs, "брошюровка на пружину")]
    assert ids[:2] == ["wire_binding", "comb_binding"]
    assert processes.suggest(procs, "кбс")[0].id == "perfect_binding"
    assert processes.suggest(procs, "люверс")[0].id == "eyelets"


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
    custom = {"categories": [{"id": "c", "name": "Кат", "processes": [
        {"id": "p", "name": "Услуга", "phrases": ["x y"]}]}]}
    paths.processes_file().write_text(json.dumps(custom, ensure_ascii=False), encoding="utf-8")
    cats = processes.load_catalog()
    assert [p.id for p in processes.all_processes(cats)] == ["p"]
