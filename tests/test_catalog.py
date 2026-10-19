import pytest

from app import licenses
from app.catalog import EXACT, PARTIAL, PRODUCT, REJECTED, RELATED, SELECTED, Catalog
from app.sources.base import LicenseInfo, VideoFile, VideoResult
from tests.fakes import Clock


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
