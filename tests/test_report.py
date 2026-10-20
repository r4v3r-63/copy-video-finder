"""Отчёт для руководства в PDF."""

import io

from PIL import Image

from app import catalog as cat
from app import processes
from app.export import report
from app.sources.base import LicenseInfo, VideoResult


def video(i, title):
    return VideoResult(source="Pixabay", source_id=str(i), title=title, description="", preview_url=f"https://p/{i}.jpg",
                       duration=10, width=1920, height=1080, has_audio=None, page_url=f"https://pixabay.com/v/{i}",
                       license=LicenseInfo("Pixabay Content License", status="Разрешено"))


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 36), (36, 107, 238)).save(buf, "PNG")
    return buf.getvalue()


def make_catalog(tmp_path):
    store = cat.Catalog(tmp_path / "c.sqlite")
    ids = [store.select(video(i, f"Laminator {i}"), "lam_pouch", cat.EXACT) for i in range(4)]
    store.select(video(9, "Product shot"), "guillotine", cat.PRODUCT)  # не засчитывается
    return store, ids


def test_service_videos_counted_only(tmp_path):
    store, ids = make_catalog(tmp_path)
    videos = report.service_videos(store, processes.load_catalog())
    assert [e.id for e in videos["lam_pouch"]] == ids[:3]  # не больше трёх
    assert "guillotine" not in videos


def test_report_pdf(tmp_path):
    store, ids = make_catalog(tmp_path)
    categories = processes.load_catalog()
    thumbs = {ids[0]: png()}
    html = report.report_html(store, categories, thumbs)
    assert "Пакетная ламинация" in html and "Где нужен поиск" in html
    assert f'src="thumb:{ids[0]}"' in html and f'src="thumb:{ids[1]}"' not in html  # без превью — без картинки
    path = report.write_pdf(tmp_path / "r.pdf", html, thumbs)
    data = path.read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 5000
    store.close()


def test_fetch_thumbs_skips_failures(tmp_path):
    store, ids = make_catalog(tmp_path)

    class Session:
        def get(self, url, headers=None, timeout=None):
            class R:
                status_code = 200 if url.endswith("0.jpg") else 404
                content = b"img"
            return R()

    thumbs = report.fetch_thumbs(store.entries(), session=Session())
    assert list(thumbs) == [ids[0]]
    store.close()


def test_service_blocks_not_split_between_pages(tmp_path):
    from PyQt6.QtCore import QSizeF
    from PyQt6.QtGui import QTextDocument

    store, ids = make_catalog(tmp_path)
    for n, pid in enumerate(["photo_id", "guillotine", "laser_engraving", "lam_roll", "wire_binding"]):
        for k in range(3):
            store.select(video(100 + 10 * n + k, f"{pid} {k}"), pid, cat.EXACT)
    entries = store.entries()
    html = report.report_html(store, processes.load_catalog(), {e.id: png() for e in entries})
    doc = QTextDocument()
    image = report.QImage()
    image.loadFromData(png())
    for e in entries:
        doc.addResource(QTextDocument.ResourceType.ImageResource.value, report.QUrl(f"thumb:{e.id}"), image)
    doc.setHtml(html)
    doc.setPageSize(QSizeF(700, 900))
    report.keep_blocks_together(doc)
    layout, page = doc.documentLayout(), 900
    anchors = []
    block = doc.begin()
    while block.isValid():
        if report._anchor(block):
            anchors.append((report._anchor(block), report._top(layout, block)))
        block = block.next()
    services = [(name, top, anchors[i + 1][1]) for i, (name, top) in enumerate(anchors)
                if name.startswith("svc-") and i + 1 < len(anchors)]
    assert len(services) >= 5
    for name, top, bottom in services:
        assert int(top // page) == int((bottom - 1) // page), name
    store.close()
