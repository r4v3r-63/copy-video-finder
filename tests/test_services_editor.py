"""Редактор справочника услуг: сохранение, загрузка правок, коды услуг."""

import json

import pytest

from app import paths, processes
from app.processes import Level, Process


def test_save_roundtrip_matches_builtin(tmp_path):
    categories = processes.load_catalog(processes.builtin_file())
    out = processes.save_catalog(categories, tmp_path / "p.json")
    assert json.loads(out.read_text(encoding="utf-8")) == json.loads(processes.builtin_file().read_text(encoding="utf-8"))


def test_user_copy_is_used_and_keeps_ids(data_dir):
    categories = processes.load_catalog()
    lam = next(p for p in processes.all_processes(categories) if p.id == "lam_pouch")
    lam.name = "Ламинирование в пакет"
    lam.phrases_en.append("pouch laminating at office")
    categories[0].processes.append(Process(id=processes.new_id("Печать на холсте", set()), name="Печать на холсте",
                                           category=categories[0].name, phrases_ru=["печать на холсте"],
                                           phrases_en=["canvas printing"]))
    processes.save_catalog(categories)
    assert paths.processes_file().exists()
    loaded = {p.id: p for p in processes.all_processes(processes.load_catalog())}
    assert loaded["lam_pouch"].name == "Ламинирование в пакет"  # код тот же — привязки каталога целы
    assert "pouch laminating at office" in loaded["lam_pouch"].phrases_en
    assert loaded["pechat_na_holste"].phrases_en == ["canvas printing"]


def test_missing_fields_come_from_builtin(data_dir):
    """Старая копия без уровней поиска получает их из встроенного справочника новой версии."""
    raw = json.loads(processes.builtin_file().read_text(encoding="utf-8"))
    for c in raw["categories"]:
        for p in c["processes"]:
            p.pop("levels")
    paths.processes_file().write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    loaded = {p.id: p for p in processes.all_processes(processes.load_catalog())}
    assert loaded["wire_binding"].levels  # подтянулись


def test_removed_levels_stay_removed(data_dir):
    categories = processes.load_catalog()
    proc = next(p for p in processes.all_processes(categories) if p.id == "creasing")
    proc.levels = []
    processes.save_catalog(categories)
    loaded = {p.id: p for p in processes.all_processes(processes.load_catalog())}
    assert loaded["creasing"].levels == []


def test_new_id_unique():
    assert processes.new_id("Печать на холсте", set()) == "pechat_na_holste"
    assert processes.new_id("Печать на холсте", {"pechat_na_holste"}) == "pechat_na_holste_2"
    assert processes.new_id("???", set()) == "service"


def test_dialog_edit_and_save(data_dir):
    from app.ui.services_dialog import ServicesDialog

    dialog = ServicesDialog(processes.load_catalog())
    service_item = dialog.tree.topLevelItem(0).child(0)
    dialog.tree.setCurrentItem(service_item)
    dialog.name.setText("Фото на паспорт")
    dialog.phrases_en.setPlainText("passport photo studio\nid photo")
    dialog._add_level_row(Level("Ещё", "смежный процесс", {"en": ["photo booth"]}))
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(1))  # смена выбора сохраняет форму
    dialog._save()
    loaded = processes.all_processes(processes.load_catalog())
    first = loaded[0]
    assert first.name == "Фото на паспорт" and first.phrases_en == ["passport photo studio", "id photo"]
    assert first.levels[-1].name == "Ещё" and first.levels[-1].match == "смежный процесс"


def test_dialog_refuses_service_without_phrases(data_dir):
    from app.ui.services_dialog import ServicesDialog

    dialog = ServicesDialog(processes.load_catalog())
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    dialog._add_service()
    dialog._commit()
    assert any("нет поисковых фраз" in p for p in dialog.problems())
