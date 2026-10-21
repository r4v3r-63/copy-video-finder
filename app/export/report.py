"""Отчёт для руководства в PDF: сколько услуг обеспечено роликами, где нужен поиск, превью роликов.

Собирается средствами Qt (QTextDocument → QPdfWriter), без лишних библиотек. Превью роликов
скачиваются заранее (`fetch_thumbs`, в фоне) и встраиваются в документ картинками.
"""

import io
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import escape
from pathlib import Path

import requests
from PIL import Image, ImageOps
from PyQt6.QtCore import QMarginsF, QSizeF, QUrl
from PyQt6.QtGui import QImage, QPageLayout, QPageSize, QPdfWriter, QTextCursor, QTextDocument

from app import __version__, paths
from app.core import design
from app.sources.http import USER_AGENT
from app.storage import catalog as cat

VIDEOS_PER_SERVICE = 3
THUMB_SIZE = (340, 192)  # 16:9, в отчёте — 170 точек шириной
_MATCH_ORDER = {cat.EXACT: 0, cat.PARTIAL: 1, cat.RELATED: 2}


def service_videos(store: cat.Catalog, categories) -> dict[str, list[cat.Entry]]:
    """Засчитанные в покрытие ролики каждой услуги, точные — первыми (не больше трёх)."""
    found: dict[str, list[tuple[int, cat.Entry]]] = {}
    for e in store.entries():
        if not e.counts_for_coverage:
            continue
        for pid, match in e.links.items():
            if match != cat.PRODUCT:
                found.setdefault(pid, []).append((_MATCH_ORDER.get(match, 3), e))
    return {pid: [e for _, e in sorted(items, key=lambda x: (x[0], x[1].id))][:VIDEOS_PER_SERVICE]
            for pid, items in found.items()}


def thumb_jpeg(data: bytes) -> bytes | None:
    """Превью для отчёта: JPEG 16:9 шириной THUMB_SIZE[0] (обрезка по центру).

    Через Pillow — Qt не читает AVIF (Coverr), а вертикальные превью растянули бы блок услуги.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            fitted = ImageOps.fit(image.convert("RGB"), THUMB_SIZE, Image.Resampling.LANCZOS)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    out = io.BytesIO()
    fitted.save(out, "JPEG", quality=85)
    return out.getvalue()


def fetch_thumbs(entries: list[cat.Entry], session=None, cancel: threading.Event | None = None) -> dict[int, bytes]:
    """Превью роликов для отчёта: номер в каталоге → картинка. Не скачалось — без превью."""
    session = session or requests.Session()

    def one(e: cat.Entry):
        if cancel and cancel.is_set() or not e.result.preview_url:
            return e.id, None
        try:
            resp = session.get(e.result.preview_url, headers={"User-Agent": USER_AGENT}, timeout=15)
            return e.id, thumb_jpeg(resp.content) if resp.status_code == 200 else None
        except requests.RequestException:
            return e.id, None

    with ThreadPoolExecutor(max_workers=6) as pool:
        return {vid: data for vid, data in pool.map(one, entries) if data}


def report_html(store: cat.Catalog, categories, thumbs: dict[int, bytes], now=None) -> str:
    """Отчёт в HTML для QTextDocument (простая вёрстка: таблицы, цвета ячеек, картинки thumb:N)."""
    p = design.PALETTES[design.LIGHT]  # печать — всегда светлая тема
    stats = cat.statistics(store, categories)
    coverage = store.coverage()
    videos = service_videos(store, categories)
    date = (now or datetime.now()).strftime("%d.%m.%Y")
    processes = [proc for c in categories for proc in c.processes]
    need = [proc for proc in cat.batch_order(processes, coverage)]

    def tile(value, label):
        return (f'<td width="25%" style="background:{p.bg_secondary}; padding:10px">'
                f'<span style="font-size:20pt; font-weight:bold; color:{p.accent_text}">{value}</span><br>'
                f'<span style="color:{p.text_secondary}">{escape(label)}</span></td>')

    out = [f'<html><body style="font-family:{design.FONT_FAMILY}; color:{p.text_primary}; font-size:10pt">',
           '<table width="100%"><tr><td width="56"><img src="logo" width="48"></td><td>'
           '<span style="font-size:18pt; font-weight:bold">Видеоролики для копицентров Copy.ru</span><br>'
           f'<span style="color:{p.text_secondary}">Отчёт о покрытии услуг · {date} · программа {__version__}'
           '</span></td></tr></table><br>',
           '<table width="100%" cellspacing="6"><tr>',
           tile(f"{stats['services_done']} из {stats['services']}", "услуг с выполненной целью"),
           tile(stats["counted"], "роликов засчитано в покрытие"),
           tile(stats["exact"], "из них с точным соответствием"),
           tile(len(need), "услуг, где нужен поиск"),
           "</tr></table>"]
    sources = ", ".join(f"{escape(k)} — {v}" for k, v in stats["by_source"].items()) or "—"
    out.append(f'<p style="color:{p.text_secondary}">Роликов в каталоге: {stats["videos"]}. '
               f'По площадкам: {sources}.</p>')

    out.append(f'<h2 style="font-size:13pt; border-bottom:2px solid {p.accent_secondary}">Покрытие услуг</h2>')
    out.append(f'<table width="100%" cellspacing="0" cellpadding="5" style="border-collapse:collapse">'
               f'<tr style="color:{p.text_secondary}; font-weight:bold"><td>Категория</td><td>Услуга</td>'
               '<td align="center">Роликов</td><td>Состояние</td></tr>')
    for c in categories:
        for proc in c.processes:
            n = coverage.get(proc.id, 0)
            level = cat.coverage_level(n, proc.target)
            state = ["нет роликов", "1 ролик", "2 ролика", "цель выполнена"][level]
            out.append(f'<tr style="background:{p.coverage[level]}"><td>{escape(c.name)}</td>'
                       f'<td>{escape(proc.name)}</td><td align="center">{n} из {proc.target}</td>'
                       f'<td>{state}</td></tr>')
    out.append("</table>")

    out.append(f'<h2 style="font-size:13pt; border-bottom:2px solid {p.accent_secondary}">'
               '<a name="videos"></a>Ролики по услугам</h2>')
    any_videos = False
    for c in categories:
        rows = [(proc, videos[proc.id]) for proc in c.processes if videos.get(proc.id)]
        if not rows:
            continue
        any_videos = True
        out.append(f'<h3 style="font-size:11pt"><a name="cat-{escape(c.id)}"></a>{escape(c.name)}</h3>')
        for proc, entries in rows:
            out.append(f'<p style="font-weight:bold; margin-bottom:2px"><a name="svc-{escape(proc.id)}"></a>'
                       f'{escape(proc.name)}</p>'
                       '<table cellspacing="6"><tr>')
            for e in entries:
                r = e.result
                picture = f'<img src="thumb:{e.id}" width="170"><br>' if e.id in thumbs else ""
                out.append(f'<td width="190" valign="top">{picture}'
                           f'<span style="font-size:8pt">№ {e.id} · {escape(r.title[:60])}<br>'
                           f'<span style="color:{p.text_secondary}">{escape(r.source)} · '
                           f'{escape(r.license.name if r.license else "")} · {escape(e.links.get(proc.id, ""))}'
                           f'</span><br><a href="{escape(r.page_url)}">страница ролика</a></span></td>')
            out.append("</tr></table>")
    if not any_videos:
        out.append(f'<p style="color:{p.text_secondary}">В покрытие пока не засчитано ни одного ролика.</p>')

    if need:
        out.append(f'<h2 style="font-size:13pt; border-bottom:2px solid {p.accent_secondary}">'
                   '<a name="end-need"></a>Где нужен поиск</h2>'
                   '<p>' + "; ".join(f"{escape(proc.name)} ({coverage.get(proc.id, 0)} из {proc.target})"
                                     for proc in need) + ".</p>")
    out.append(f'<p style="color:{p.text_secondary}; font-size:8pt">В покрытие засчитываются ролики со свободной '
               "лицензией, подтверждённой при отборе, кроме степени «только готовая продукция».</p>")
    out.append("</body></html>")
    return "".join(out)


def write_pdf(path: Path, html: str, thumbs: dict[int, bytes]) -> Path:
    """Сохранить отчёт в PDF (A4)."""
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Unit.Millimeter)
    writer.setResolution(150)
    writer.setTitle("Видеоролики для копицентров Copy.ru — отчёт о покрытии")
    writer.setCreator(f"Поиск видео Copy.ru {__version__}")
    doc = QTextDocument()
    logo = QImage(str(paths.resources_dir() / "logo.svg"))
    doc.addResource(QTextDocument.ResourceType.ImageResource.value, QUrl("logo"), logo)
    for vid, data in thumbs.items():
        image = QImage()
        if image.loadFromData(data):
            doc.addResource(QTextDocument.ResourceType.ImageResource.value, QUrl(f"thumb:{vid}"), image)
    doc.setHtml(html)
    doc.setPageSize(writer.pageLayout().paintRectPixels(writer.resolution()).size().toSizeF())
    keep_blocks_together(doc)
    doc.print(writer)
    return Path(path)


def _anchor(block) -> str:
    it = block.begin()
    while not it.atEnd():
        names = it.fragment().charFormat().anchorNames()
        if names:
            return names[0]
        it += 1
    return ""


def _relayout(doc: QTextDocument) -> None:
    """Раскладка после правки формата досчитывается лениво — пересчитать её целиком сейчас."""
    size = doc.pageSize()
    doc.setPageSize(size + QSizeF(0, 1))
    doc.setPageSize(size)


def _top(layout, block) -> float:
    """Верх блока вместе с его верхним отступом (blockBoundingRect отступ не включает)."""
    return layout.blockBoundingRect(block).top() - block.blockFormat().topMargin()


def keep_blocks_together(doc: QTextDocument) -> None:
    """Не разрывать блок услуги (название и ролики) между страницами: перенести его целиком.

    Блок — от якоря «svc-…» до следующего якоря. Если он пересекает границу страницы, верхний
    отступ (его или заголовков прямо над ним) доводит его до начала следующей страницы.
    Разрыв страницы (PageBreak_AlwaysBefore) не годится: при печати Qt с ним теряет конец документа.
    """
    page_height = doc.pageSize().height()
    layout = doc.documentLayout()
    anchors = []
    block = doc.begin()
    while block.isValid():
        name = _anchor(block)
        if name:
            anchors.append((name, block))
        block = block.next()
    _relayout(doc)
    for i, (name, block) in enumerate(anchors):  # сверху вниз: сдвиг блока двигает только нижние
        if not name.startswith("svc-"):
            continue
        top = layout.blockBoundingRect(block).top()
        bottom = _top(layout, anchors[i + 1][1]) if i + 1 < len(anchors) else layout.documentSize().height()
        if int(top // page_height) == int((bottom - 1) // page_height) or bottom - top > page_height:
            continue
        target, j = block, i
        while j > 0 and not anchors[j - 1][0].startswith("svc-") and anchors[j - 1][1].next() == target:
            j -= 1
            target = anchors[j][1]  # заголовки прямо над услугой (категория, раздел) — вместе с ней
        rect = layout.blockBoundingRect(target)
        fmt = target.blockFormat()
        fmt.setTopMargin(fmt.topMargin() + page_height - rect.top() % page_height + 2)
        QTextCursor(target).setBlockFormat(fmt)
        _relayout(doc)
