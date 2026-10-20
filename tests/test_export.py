import json
import re

import pytest

from app import catalog as cat
from app import licenses, paths, processes
from app.export import html_catalog, registry, summary
from app.sources.base import LicenseInfo, VideoFile, VideoResult
from tests.fakes import Clock


def video(sid, title="Ламинатор в офисе", source="Pixabay", lic="Pixabay Content License", status=licenses.ALLOWED):
    return VideoResult(source=source, source_id=sid, title=title, description="desc; с точкой с запятой", preview_url="https://p/1.jpg",
                       duration=12.4, width=1920, height=1080, has_audio=True, page_url=f"https://page/{sid}",
                       files=[VideoFile("Full HD", f"https://cdn/{sid}.mp4", 1920, 1080, "video/mp4")],
                       author="Анна", author_url="https://a",
                       license=LicenseInfo(lic, "https://lic", status=status, note=""))


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cats():
    return processes.load_catalog(paths.resources_dir() / "processes.json")


@pytest.fixture
def store(tmp_path, clock):
    s = cat.Catalog(tmp_path / "c.sqlite", now=clock)
    a = s.select(video("1"), "lam_pouch", cat.EXACT)
    s.link(a, "lam_roll", cat.RELATED)
    s.set_comment(a, "в холл")
    s.select(video("2", title="Брошюровщик", source="Wikimedia Commons", lic="CC BY-SA 4.0",
                   status=licenses.ATTRIBUTION), "comb_binding", cat.PRODUCT)
    s.add(video("3", title="Без услуги"))
    s.reject(video("4", title="Пляж"))
    yield s
    s.close()


def procs(cats):
    return processes.all_processes(cats)


def test_registry_rows_one_per_link(store, cats):
    rows = registry.rows(store, procs(cats))
    keyed = [(r["id"], r["service_id"], r["match"], r["counted"]) for r in rows]
    assert keyed == [(1, "lam_pouch", cat.EXACT, "да"), (1, "lam_roll", cat.RELATED, "да"),
                     (2, "comb_binding", cat.PRODUCT, "нет"), (3, "", "", "нет"), (4, "", "", "нет")]
    first = rows[0]
    assert first["category"] == "Ламинация" and first["service"] == "Пакетная ламинация"
    assert first["license_confirmed"] == "да" and first["comment"] == "в холл" and first["has_audio"] == "да"
    assert rows[2]["attribution"] == "Анна — Брошюровщик — CC BY-SA 4.0 — https://page/2"


def test_csv_for_russian_excel_and_round_trip(store, cats, tmp_path):
    path = tmp_path / "реестр.csv"
    registry.write_csv(path, registry.rows(store, procs(cats)))
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM — Excel узнаёт UTF-8
    assert raw.decode("utf-8-sig").splitlines()[0].startswith("ID в каталоге;Категория;Услуга")
    # Перенос на «другой компьютер» — в пустой каталог.
    other = cat.Catalog(tmp_path / "other.sqlite")
    videos, links = registry.import_csv(path, other, procs(cats))
    assert (videos, links) == (4, 3)
    e1 = other.find(("Pixabay", "1"))
    assert e1.links == {"lam_pouch": cat.EXACT, "lam_roll": cat.RELATED}
    assert e1.status == cat.SELECTED and e1.license_confirmed and e1.comment == "в холл"
    assert e1.result.files[0].url == "https://cdn/1.mp4" and e1.result.duration == 12.4
    assert e1.result.description == "desc; с точкой с запятой"
    assert other.find(("Pixabay", "4")).status == cat.REJECTED
    assert other.coverage() == store.coverage()
    # Повторный импорт не создаёт дублей.
    registry.import_csv(path, other, procs(cats))
    assert len(other.entries()) == 4
    other.close()


def test_import_recomputes_license_status(tmp_path, cats):
    path = tmp_path / "r.csv"
    path.write_text("ID в каталоге;Площадка;ID на площадке;Лицензия;Статус лицензии;Код услуги\n"
                    "1;Vimeo;9;CC BY-NC 4.0;Разрешено;lam_pouch\n", encoding="utf-8-sig")
    store = cat.Catalog(tmp_path / "c.sqlite")
    registry.import_csv(path, store, procs(cats))
    assert store.find(("Vimeo", "9")).result.license.status == licenses.REJECTED  # из файла не берётся на веру
    store.close()


def test_xlsx(store, cats, tmp_path):
    from openpyxl import load_workbook
    path = tmp_path / "реестр.xlsx"
    s = summary.build(store, cats)
    registry.write_xlsx(path, registry.rows(store, procs(cats)), summary.lines(s))
    wb = load_workbook(path)
    ws = wb["Реестр"]
    assert ws.cell(1, 1).value == "ID в каталоге" and ws.max_row == 6
    page_col = registry.HEADERS.index("Страница ролика") + 1
    assert ws.cell(2, page_col).hyperlink.target == "https://page/1"
    assert wb["Сводка покрытия"].cell(1, 1).value == "Сводка покрытия"


def test_summary_and_added_since_previous(store, cats, clock, tmp_path):
    s = summary.build(store, cats, now=clock)
    # ролик 2 привязан только как «готовая продукция» — в покрытие не засчитан
    assert s["stats"]["counted"] == 1 and len(s["added"]) == 3 and s["since"] is None
    need = [p.id for p, _ in s["need_search"]]
    assert "lam_pouch" in need and need.index("lam_wide") < need.index("lam_pouch")  # без роликов — раньше
    summary.write_html(tmp_path / "сводка.html", s)
    assert "Нужен дополнительный поиск" in (tmp_path / "сводка.html").read_text(encoding="utf-8")
    summary.mark_done(store, now=clock)
    clock.t += 100
    store.select(video("5", title="Новый"), "lam_wide")
    s2 = summary.build(store, cats, now=clock)
    assert [e.result.source_id for e in s2["added"]] == ["5"] and s2["since"]


def test_html_catalog_is_self_contained(store, cats, tmp_path):
    store.select(video("6", title="Опасное </script><b>название</b>"), "lam_pouch")
    path = tmp_path / "каталог.html"
    count = html_catalog.write(path, store, cats)
    html = path.read_text(encoding="utf-8")
    assert count == 4  # отклонённые не выгружаются
    assert "<script src" not in html and "<link" not in html  # без внешних файлов
    data = json.loads(re.search(r"const DATA = (.*?);\n", html).group(1).replace("<\\/", "</"))
    assert {i["id"] for i in data["items"]} == {1, 2, 3, 5}
    assert html.count("</script>") == 1  # опасное название не закрыло тег
    item = next(i for i in data["items"] if i["id"] == 1)
    assert item["services"][0] == {"category": "Ламинация", "name": "Пакетная ламинация", "match": cat.EXACT}
    assert item["files"][0]["url"] == "https://cdn/1.mp4"


def test_formulas_from_sites_stay_text(tmp_path, cats, clock):
    from openpyxl import load_workbook
    s = cat.Catalog(tmp_path / "c.sqlite", now=clock)
    s.select(video("1", title="=1+1"), "lam_pouch", cat.EXACT)
    data = registry.rows(s, procs(cats))
    registry.write_xlsx(tmp_path / "r.xlsx", data)
    cell = load_workbook(tmp_path / "r.xlsx").active["I2"]
    assert cell.value == "=1+1" and cell.data_type == "s"
    registry.write_csv(tmp_path / "r.csv", data)
    assert ";'=1+1;" in (tmp_path / "r.csv").read_text(encoding="utf-8-sig")
    other = cat.Catalog(tmp_path / "o.sqlite", now=clock)
    registry.import_csv(tmp_path / "r.csv", other, procs(cats))
    assert other.entries()[0].result.title == "=1+1"  # апостроф при импорте снимается
    s.close()
    other.close()


def test_import_with_commas_in_data(tmp_path, cats, clock):
    """Теги через запятую в названии и описании не сбивают определение разделителя."""
    s = cat.Catalog(tmp_path / "c.sqlite", now=clock)
    v = video("1", title="laminator, office, laminating, pouch, machine, work, paper, a4, hot")
    v.description = "a, b, c, d, e, f, g, h, i, j, k, l, m, n, o, p"
    s.select(v, "lam_pouch", cat.EXACT)
    registry.write_csv(tmp_path / "r.csv", registry.rows(s, procs(cats)))
    other = cat.Catalog(tmp_path / "o.sqlite", now=clock)
    assert registry.import_csv(tmp_path / "r.csv", other, procs(cats)) == (1, 1)
    (tmp_path / "bad.csv").write_text("a;b\n1;2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="нет столбцов"):
        registry.import_csv(tmp_path / "bad.csv", other, procs(cats))
    s.close()
    other.close()


def test_import_applies_no_and_empty_values(tmp_path, cats, clock):
    s = cat.Catalog(tmp_path / "c.sqlite", now=clock)
    vid = s.select(video("1"), "lam_pouch", cat.EXACT)
    s.set_comment(vid, "старый")
    s.set_downloaded(vid, "old.mp4")
    target = cat.Catalog(tmp_path / "t.sqlite", now=clock)
    registry.write_csv(tmp_path / "r.csv", registry.rows(s, procs(cats)))
    registry.import_csv(tmp_path / "r.csv", target, procs(cats))
    assert target.entries()[0].license_confirmed and target.entries()[0].comment == "старый"
    s.confirm_license(vid, False)
    s.set_comment(vid, "")
    s.set_downloaded(vid, "")
    registry.write_csv(tmp_path / "r.csv", registry.rows(s, procs(cats)))
    registry.import_csv(tmp_path / "r.csv", target, procs(cats))
    e = target.entries()[0]
    assert not e.license_confirmed and e.comment == "" and e.downloaded_path == ""
    s.close()
    target.close()
