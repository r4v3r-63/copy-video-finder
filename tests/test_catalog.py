from pathlib import Path

import pytest

from app import licenses
from app.catalog import EXACT, PARTIAL, PRODUCT, REJECTED, RELATED, SELECTED, Catalog
from app.sources.base import LicenseInfo, VideoFile, VideoResult
from app.sources.web import parse_page
from tests.fakes import Clock, fixture_text


def video(sid="1", source="Pixabay", status=licenses.ALLOWED, title="laminator, office"):
    return VideoResult(source=source, source_id=sid, title=title, description="desc", preview_url="https://p/1.jpg",
                       duration=12.5, width=1920, height=1080, has_audio=False, page_url=f"https://page/{sid}",
                       files=[VideoFile("Full HD", f"https://cdn/{sid}.mp4", 1920, 1080, "video/mp4")],
                       author="Anna", author_url="https://a", license=LicenseInfo("Pixabay Content License", "https://lic",
                                                                                  status=status, note="n"))


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cat(tmp_path, clock):
    c = Catalog(tmp_path / "catalog.sqlite", now=clock)
    yield c
    c.close()


def test_add_is_idempotent_by_source_and_id(cat):
    a = cat.add(video("1"))
    b = cat.add(video("1"))
    c = cat.add(video("1", source="Coverr"))
    assert a == b != c
    assert len(cat.entries()) == 2


def test_removed_translation_links_are_dropped_but_videos_and_other_links_survive(tmp_path):
    path = tmp_path / "catalog.sqlite"
    store = Catalog(path)
    result = parse_page(fixture_text("manual_video.html"), "https://example.org/video/1")
    vid = store.select(result, "translation")
    store.set_comment(vid, "Сохранить ролик")
    shared = store.select(video("shared"), "translation")
    store.link(shared, "scanning", PARTIAL)
    before = store.get(vid)
    store.close()

    for _ in range(2):
        store = Catalog(path)
        entry = store.get(vid)
        assert entry.links == {}
        assert entry.result == before.result
        assert (entry.status, entry.license_confirmed, entry.comment) == (SELECTED, True, "Сохранить ролик")
        assert store.get(shared).links == {"scanning": PARTIAL}
        assert len(store.entries()) == 2
        assert store.entries(process_id="translation") == []
        store.close()


def test_round_trip_keeps_all_fields(cat):
    vid = cat.add(video("7"))
    e = cat.get(vid)
    r = e.result
    assert (r.source, r.source_id, r.title, r.duration, r.width, r.height, r.has_audio) == \
        ("Pixabay", "7", "laminator, office", 12.5, 1920, 1080, False)
    assert r.files[0].url == "https://cdn/7.mp4" and r.files[0].quality == "Full HD"
    assert r.license.name == "Pixabay Content License" and r.license.url == "https://lic"
    assert e.added_at == 1_800_000_000.0 and e.status == "" and not e.license_confirmed


def test_select_confirms_license_and_links_service(cat, clock):
    vid = cat.select(video("1"), "lam_pouch", EXACT)
    e = cat.get(vid)
    assert e.status == SELECTED and e.license_confirmed and e.checked_at == clock.t
    assert e.links == {"lam_pouch": EXACT}
    cat.link(vid, "lam_roll", RELATED)
    cat.link(vid, "lam_pouch", PARTIAL)  # смена степени
    assert cat.get(vid).links == {"lam_pouch": PARTIAL, "lam_roll": RELATED}
    cat.unlink(vid, "lam_roll")
    assert cat.get(vid).links == {"lam_pouch": PARTIAL}


def test_user_marks_survive_re_adding(cat):
    vid = cat.select(video("1"), "lam_pouch")
    cat.set_comment(vid, "хорошо на экран")
    cat.set_title(vid, "Пакетный ламинатор")
    cat.set_downloaded(vid, "C:/v.mp4")
    cat.add(video("1", title="new title from api"))
    e = cat.get(vid)
    assert e.status == SELECTED and e.license_confirmed and e.links == {"lam_pouch": EXACT}
    assert e.comment == "хорошо на экран" and e.result.title == "Пакетный ламинатор" and e.downloaded_path == "C:/v.mp4"


def test_reject_and_statuses(cat):
    cat.reject(video("1"))
    cat.select(video("2"))
    assert cat.statuses() == {("Pixabay", "1"): REJECTED, ("Pixabay", "2"): SELECTED}
    assert cat.find(("Pixabay", "1")).status == REJECTED
    assert cat.find(("Pixabay", "x")) is None


def test_filters(cat):
    a = cat.select(video("1", title="Пакетный ламинатор"), "lam_pouch")
    cat.select(video("2", title="Брошюровщик"), "comb_binding")
    cat.reject(video("3", title="Пляж"))
    cat.set_comment(a, "для экрана в холле")
    assert [e.result.source_id for e in cat.entries(process_id="lam_pouch")] == ["1"]
    assert [e.result.source_id for e in cat.entries(status=REJECTED)] == ["3"]
    assert [e.result.source_id for e in cat.entries(text="ламин")] == ["1"]
    assert [e.result.source_id for e in cat.entries(text="холле")] == ["1"]
    assert len(cat.entries(text="pixabay")) == 3


def test_coverage_rules(cat):
    cat.select(video("1"), "lam_pouch", EXACT)  # засчитан
    cat.select(video("2"), "lam_pouch", PRODUCT)  # только готовая продукция — нет
    cat.select(video("3", status=licenses.REVIEW), "lam_pouch")  # лицензия не подходит — нет
    unconfirmed = cat.add(video("4"))
    cat.link(unconfirmed, "lam_pouch")  # лицензия не подтверждена — нет
    rejected = cat.select(video("5"), "lam_pouch")
    cat.set_status(rejected, REJECTED)  # отклонён — нет
    multi = cat.select(video("6"), "lam_roll", PARTIAL)
    cat.link(multi, "lam_wide", RELATED)  # один ролик в двух услугах
    assert cat.coverage() == {"lam_pouch": 1, "lam_roll": 1, "lam_wide": 1}
    cat.confirm_license(unconfirmed)
    assert cat.coverage()["lam_pouch"] == 2


def test_delete_removes_links(cat):
    vid = cat.select(video("1"), "lam_pouch")
    cat.delete(vid)
    assert cat.get(vid) is None and cat.coverage() == {}


def test_persists_between_sessions(tmp_path):
    c = Catalog(tmp_path / "c.sqlite")
    vid = c.select(video("1"), "lam_pouch")
    c.close()
    c = Catalog(tmp_path / "c.sqlite")
    assert c.get(vid).links == {"lam_pouch": EXACT}
    c.close()


def test_record_search_and_new_since_previous(cat, clock):
    assert cat.new_since_previous_search() == 0
    new = cat.record_search("ламинация", [("Pixabay", "1"), ("Pixabay", "2"), ("Pixabay", "1")])
    assert new == {("Pixabay", "1"), ("Pixabay", "2")}
    assert cat.new_since_previous_search() == 2
    clock.t += 60
    new = cat.record_search("ламинация", [("Pixabay", "2"), ("Coverr", "9")])
    assert new == {("Coverr", "9")}
    assert cat.new_since_previous_search() == 1


def test_coverage_level_and_batch_order():
    from app.catalog import batch_order, coverage_level
    from app.processes import Process
    assert [coverage_level(n, 3) for n in (0, 1, 2, 3, 5)] == [0, 1, 2, 3, 3]
    assert coverage_level(2, 2) == 3
    procs = [Process(id=i, name=i, category="c", phrases_ru=[], phrases_en=[], target=3) for i in "abcde"]
    coverage = {"a": 2, "b": 0, "c": 3, "d": 1, "e": 0}
    assert [p.id for p in batch_order(procs, coverage)] == ["b", "e", "d", "a"]  # «c» — цель выполнена


def test_statistics(cat):
    from app.catalog import statistics
    from app.processes import Category, Process
    procs = [Process(id=i, name=i, category="Кат", phrases_ru=[], phrases_en=[], target=3) for i in ("p1", "p2")]
    cats = [Category(id="c", name="Кат", processes=procs)]
    for sid in ("1", "2", "3"):
        cat.select(video(sid), "p1", EXACT)
    cat.select(video("4", source="Coverr"), "p2", PARTIAL)
    cat.reject(video("5"))
    st = statistics(cat, cats)
    assert (st["categories"], st["services"], st["videos"], st["counted"], st["exact"]) == (1, 2, 4, 4, 3)
    assert st["services_by_count"] == {"0": 0, "1": 1, "2": 0, "3 и более": 1}
    assert st["services_done"] == 1
    assert st["by_source"] == {"Pixabay": 3, "Coverr": 1}


def test_has_history(cat):
    assert not cat.has_history()
    cat.record_search("x", [])
    assert cat.has_history()


def test_downloaded_path_survives_moving_program_folder(tmp_path, monkeypatch):
    """Файлы внутри папки программы хранятся относительными: папку можно перенести целиком."""
    from app import paths
    old = tmp_path / "old" / "Данные"
    monkeypatch.setenv("COPYVF_DATA_DIR", str(old))
    video_file = old / "Видео" / "1.mp4"
    video_file.parent.mkdir(parents=True)
    video_file.write_bytes(b"x")
    store = Catalog(old / "catalog.sqlite")
    vid = store.add(VideoResult(source="Pixabay", source_id="1", title="x", description="", preview_url=None,
                                duration=10, width=1920, height=1080, has_audio=None, page_url=""))
    store.set_downloaded(vid, str(video_file))
    assert store._db.execute("SELECT downloaded_path FROM videos").fetchone()[0] == "Данные/Видео/1.mp4"
    store.set_downloaded(vid, "D:/Ролики/2.mp4")  # вне папки программы — как есть
    assert store.get(vid).downloaded_path == "D:/Ролики/2.mp4"
    store.set_downloaded(vid, str(video_file))
    store.close()
    new = tmp_path / "new"
    (tmp_path / "old").rename(new)
    monkeypatch.setenv("COPYVF_DATA_DIR", str(new / "Данные"))
    moved = Catalog(new / "Данные" / "catalog.sqlite")
    e = moved.get(vid)
    assert Path(e.downloaded_path) == new / "Данные" / "Видео" / "1.mp4" and e.has_file
    assert paths.portable_path(e.downloaded_path) == "Данные/Видео/1.mp4"
    moved.close()


def test_old_absolute_paths_become_portable(tmp_path, monkeypatch):
    data = tmp_path / "Данные"
    monkeypatch.setenv("COPYVF_DATA_DIR", str(data))
    data.mkdir()
    store = Catalog(data / "catalog.sqlite")
    vid = store.add(VideoResult(source="Pixabay", source_id="1", title="x", description="", preview_url=None,
                                duration=10, width=1920, height=1080, has_audio=None, page_url=""))
    store._db.execute("UPDATE videos SET downloaded_path = ?", (str(data / "Видео" / "a.mp4"),))
    store._db.commit()
    store.close()
    store = Catalog(data / "catalog.sqlite")
    assert store._db.execute("SELECT downloaded_path FROM videos").fetchone()[0] == "Данные/Видео/a.mp4"
    assert store.get(vid).downloaded_path == str(data / "Видео" / "a.mp4")
    store.close()


def test_links_of_retired_processes_move_to_groups(tmp_path):
    """Справочник v5: ролики убранных техпроцессов (люверсы, бейджи…) — у новых групп, один раз."""
    path = tmp_path / "c.sqlite"
    store = Catalog(path)
    a = store.add(VideoResult(source="Pixabay", source_id="1", title="x", description="", preview_url=None,
                              duration=10, width=1920, height=1080, has_audio=None, page_url=""))
    store.link(a, "eyelets", EXACT)
    store.link(a, "wf_ecosolvent", RELATED)  # уже у группы — остаётся более точная степень
    store.link(a, "badge_punch", PARTIAL)
    store._db.execute("DELETE FROM meta WHERE key = 'directory_version'")
    store._db.commit()
    store.close()
    store = Catalog(path)
    assert store.get(a).links == {"wf_ecosolvent": EXACT, "badges": PARTIAL}
    store.link(a, "eyelets", EXACT)  # после переноса — не трогается повторно
    store.close()
    assert Catalog(path).get(a).links["eyelets"] == EXACT
