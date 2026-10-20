"""Автономный HTML-каталог (ТЗ, 7.3): один файл, открывается в любом браузере без сервера.

Данные встроены в страницу; фильтры, поиск и выбор работают без интернета
(из сети подгружаются только картинки превью). Выбранные ролики выгружаются в CSV.
"""

import json
import time
from datetime import datetime
from pathlib import Path

from app import __version__, design, licenses
from app import catalog as cat


def _items(store: cat.Catalog, processes) -> list[dict]:
    by_id = {p.id: p for p in processes}
    items = []
    for e in sorted(store.entries(), key=lambda e: e.id):
        if e.status == cat.REJECTED:
            continue
        r, lic = e.result, e.result.license
        services = [{"category": by_id[pid].category, "name": by_id[pid].name, "match": match}
                    for pid, match in sorted(e.links.items()) if pid in by_id]
        items.append({
            "id": e.id, "title": r.title, "description": r.description[:300], "source": r.source,
            "sourceId": r.source_id, "duration": round(r.duration) if r.duration else None,
            "size": f"{r.width}×{r.height}" if r.width and r.height else "", "preview": r.preview_url or "",
            "page": r.page_url, "author": r.author, "authorUrl": r.author_url or "",
            "license": lic.name, "licenseUrl": lic.url or "", "licenseStatus": lic.status,
            "licenseConfirmed": e.license_confirmed, "note": lic.note,
            "attribution": licenses.attribution_text(r.author, r.title, lic.name, r.page_url)
            if lic.status == licenses.ATTRIBUTION else "",
            "files": [{"quality": f.quality, "url": f.url, "size": f"{f.width}×{f.height}" if f.width else ""}
                      for f in r.files],
            "local": e.downloaded_path, "comment": e.comment, "counted": e.counts_for_coverage,
            "services": services,
        })
    return items


def write(path: Path, store: cat.Catalog, categories, now=time.time) -> int:
    processes = [p for c in categories for p in c.processes]
    items = _items(store, processes)
    data = {
        "date": datetime.fromtimestamp(now()).strftime("%d.%m.%Y %H:%M"),
        "version": __version__,
        "categories": [{"name": c.name, "services": [p.name for p in c.processes]} for c in categories],
        "matches": list(cat.MATCHES),
        "items": items,
    }
    # «</» внутри JSON закрыл бы тег script — экранируем.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.replace("/*TOKENS*/", design.css_variables()).replace("/*DATA*/null", payload)
    Path(path).write_text(html, encoding="utf-8")
    return len(items)


TEMPLATE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Каталог роликов Copy.ru</title>
<style>
/*TOKENS*/
* { box-sizing:border-box; }
body { margin:0; background:var(--bg-secondary); color:var(--text-primary); font:var(--font-body)/1.45 var(--font-family); }
header { position:sticky; top:0; z-index:1; background:var(--bg-primary); border-bottom:1px solid var(--border-color); padding:12px 16px; }
h1 { font-size:var(--font-display); font-weight:700; margin:0 0 8px; }
.filters { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
input[type=search], select { background:var(--bg-surface); color:var(--text-primary); border:1px solid var(--border-color); border-radius:var(--radius-input); padding:7px 10px; font:inherit; max-width:100%; transition:border-color .15s; }
input[type=search]:focus, select:focus { outline:none; border-color:var(--accent-primary); box-shadow:0 0 0 1px var(--accent-primary); }
input[type=search] { flex:1 1 240px; }
input[type=checkbox] { accent-color:var(--accent-primary); }
button { background:var(--accent-primary); color:var(--text-inverse); border:1px solid var(--accent-primary); border-radius:var(--radius-button); padding:8px 16px; font:inherit; font-weight:500; cursor:pointer; transition:background .15s; }
button:hover { background:var(--accent-hover); }
button:focus-visible { outline:2px solid var(--accent-secondary); outline-offset:2px; }
button:disabled { background:var(--border-color); border-color:var(--border-color); color:var(--text-secondary); cursor:default; }
.muted { color:var(--text-secondary); }
main { padding:16px; display:grid; grid-template-columns:repeat(auto-fill, minmax(min(300px, 100%), 1fr)); gap:16px; }
.card { background:var(--bg-surface); border:1px solid var(--border-color); border-radius:var(--radius-card); overflow:hidden; display:flex; flex-direction:column; transition:box-shadow .15s; }
.card:hover { box-shadow:0 2px 12px rgba(17,17,17,.08); }
.thumb { aspect-ratio:16/9; background:var(--border-color); display:block; width:100%; object-fit:cover; }
.body { padding:12px 14px; display:flex; flex-direction:column; gap:6px; flex:1; }
.title { font-weight:600; overflow-wrap:anywhere; }
.tags span { display:inline-block; border:1px solid var(--border-color); border-radius:12px; padding:1px 8px; margin:2px 4px 0 0; font-size:var(--font-caption); }
.ok { color:var(--ok); } .warn { color:var(--warn); }
a { color:var(--accent-text); }
a:hover { text-decoration:none; }
.links a { margin-right:10px; }
label.pick { display:flex; gap:6px; align-items:center; margin-top:auto; padding-top:8px; border-top:1px solid var(--border-color); }
.empty { padding:40px; text-align:center; grid-column:1/-1; }
</style></head><body>
<header>
  <h1>Каталог роликов для копицентров Copy.ru</h1>
  <div class="muted" id="meta"></div>
  <div class="filters" style="margin-top:8px">
    <input type="search" id="q" placeholder="Поиск по названию, описанию, услуге, площадке, автору">
    <select id="category"><option value="">Все категории</option></select>
    <select id="service"><option value="">Все услуги</option></select>
    <select id="source"><option value="">Все площадки</option></select>
    <select id="license"><option value="">Любая лицензия</option></select>
    <select id="match"><option value="">Любое соответствие</option></select>
    <label><input type="checkbox" id="counted"> только засчитанные</label>
    <label><input type="checkbox" id="local"> только скачанные</label>
  </div>
  <div class="filters" style="margin-top:8px">
    <span id="count"></span>
    <button id="selectShown">Выбрать показанные</button>
    <button id="clear">Снять выбор</button>
    <button id="export" disabled>Скачать выбранное в CSV</button>
  </div>
</header>
<main id="list"></main>
<script>
const DATA = /*DATA*/null;
const $ = id => document.getElementById(id);
const picked = new Set();
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const dur = s => s == null ? "?" : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
const fileUrl = p => "file:///" + p.replace(/\\\\/g, "/").replace(/^\\/+/, "");

function fill(select, values) { for (const v of values) select.insertAdjacentHTML("beforeend", `<option>${esc(v)}</option>`); }
fill($("category"), DATA.categories.map(c => c.name));
fill($("source"), [...new Set(DATA.items.map(i => i.source))].sort());
fill($("license"), [...new Set(DATA.items.map(i => i.licenseStatus))].sort());
fill($("match"), DATA.matches);
function fillServices() {
  const cat = $("category").value, sel = $("service"), prev = sel.value;
  sel.length = 1;
  fill(sel, DATA.categories.filter(c => !cat || c.name === cat).flatMap(c => c.services));
  sel.value = [...sel.options].some(o => o.value === prev) ? prev : "";
}
fillServices();
$("meta").textContent = `Выгружено ${DATA.date} · роликов: ${DATA.items.length} · программа версии ${DATA.version}`;

function shown() {
  const q = $("q").value.trim().toLowerCase(), cat = $("category").value, svc = $("service").value;
  const src = $("source").value, lic = $("license").value, match = $("match").value;
  return DATA.items.filter(i => {
    const s = i.services.filter(x => (!cat || x.category === cat) && (!svc || x.name === svc) && (!match || x.match === match));
    if ((cat || svc || match) && !s.length) return false;
    if (src && i.source !== src) return false;
    if (lic && i.licenseStatus !== lic) return false;
    if ($("counted").checked && !i.counted) return false;
    if ($("local").checked && !i.local) return false;
    if (q) {
      const hay = [i.title, i.description, i.source, i.author, i.comment, ...i.services.map(x => x.name)].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function card(i) {
  const lic = i.licenseUrl ? `<a href="${esc(i.licenseUrl)}" target="_blank">${esc(i.license)}</a>` : esc(i.license);
  const files = i.files.map(f => `<a href="${esc(f.url)}" target="_blank">${esc(f.quality)}${f.size ? " " + esc(f.size) : ""}</a>`).join(" ");
  return `<article class="card">
    ${i.preview ? `<img class="thumb" loading="lazy" src="${esc(i.preview)}" alt="">` : `<div class="thumb"></div>`}
    <div class="body">
      <div class="title">№ ${i.id} · ${esc(i.title)}</div>
      <div class="muted">${esc(i.source)} · ${dur(i.duration)}${i.size ? " · " + esc(i.size) : ""}${i.author ? " · " + (i.authorUrl ? `<a href="${esc(i.authorUrl)}" target="_blank">${esc(i.author)}</a>` : esc(i.author)) : ""}</div>
      <div>Лицензия: ${lic} — <span class="${i.licenseConfirmed ? "ok" : "warn"}">${esc(i.licenseStatus)}${i.licenseConfirmed ? "" : " (не проверена)"}</span></div>
      ${i.attribution ? `<div class="muted">Атрибуция: ${esc(i.attribution)}</div>` : ""}
      ${i.note ? `<div class="muted">${esc(i.note)}</div>` : ""}
      <div class="tags">${i.services.map(s => `<span>${esc(s.name)} · ${esc(s.match)}</span>`).join("") || '<span class="muted">без услуги</span>'}</div>
      ${i.comment ? `<div>💬 ${esc(i.comment)}</div>` : ""}
      <div class="links"><a href="${esc(i.page)}" target="_blank">Страница ролика</a>${files ? " Скачать: " + files : ""}${i.local ? ` <a href="${esc(fileUrl(i.local))}">Скачанный файл</a>` : ""}</div>
      <label class="pick"><input type="checkbox" data-id="${i.id}" ${picked.has(i.id) ? "checked" : ""}> выбрать</label>
    </div></article>`;
}

function render() {
  const list = shown();
  $("list").innerHTML = list.map(card).join("") || '<div class="empty muted">Ничего не найдено</div>';
  $("count").textContent = `Показано: ${list.length} · выбрано: ${picked.size}`;
  $("export").disabled = !picked.size;
}

$("list").addEventListener("change", e => {
  if (!e.target.dataset.id) return;
  const id = Number(e.target.dataset.id);
  e.target.checked ? picked.add(id) : picked.delete(id);
  $("count").textContent = `Показано: ${shown().length} · выбрано: ${picked.size}`;
  $("export").disabled = !picked.size;
});
for (const id of ["q", "service", "source", "license", "match", "counted", "local"]) $(id).addEventListener("input", render);
$("category").addEventListener("input", () => { fillServices(); render(); });
$("selectShown").addEventListener("click", () => { shown().forEach(i => picked.add(i.id)); render(); });
$("clear").addEventListener("click", () => { picked.clear(); render(); });
$("export").addEventListener("click", () => {
  const cols = [["ID в каталоге", i => i.id], ["Название", i => i.title], ["Площадка", i => i.source], ["ID на площадке", i => i.sourceId],
    ["Услуги", i => i.services.map(s => `${s.name} (${s.match})`).join("; ")], ["Длительность, с", i => i.duration ?? ""],
    ["Лицензия", i => i.license], ["Статус лицензии", i => i.licenseStatus], ["Автор", i => i.author],
    ["Атрибуция", i => i.attribution], ["Страница ролика", i => i.page], ["Файл (лучшее качество)", i => i.files[0]?.url ?? ""],
    ["Скачан в", i => i.local], ["Комментарий", i => i.comment]];
  // Как в реестре: строка с «= + - @» в Excel — формула, поэтому перед ней апостроф.
  const cell = v => { const s = String(v); return `"${(/^[=+\\-@\\t\\r]/.test(s) ? "'" + s : s).replace(/"/g, '""')}"`; };
  const rows = DATA.items.filter(i => picked.has(i.id)).map(i => cols.map(([, f]) => cell(f(i))).join(";"));
  const csv = "\\ufeff" + [cols.map(([h]) => cell(h)).join(";"), ...rows].join("\\r\\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], {type: "text/csv;charset=utf-8"}));
  a.download = "выбранные-ролики.csv";
  a.click();
});
render();
</script>
</body></html>
"""
