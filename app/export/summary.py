"""Сводка покрытия (ТЗ, 7.3): что обеспечено роликами, где нужен поиск, какие площадки дали больше,
что добавлено с прошлой сводки."""

import time
from datetime import datetime
from html import escape
from pathlib import Path

from app import __version__
from app import catalog as cat

LAST_SUMMARY = "last_summary_at"


def build(store: cat.Catalog, categories, now=time.time) -> dict:
    processes = [p for c in categories for p in c.processes]
    st = cat.statistics(store, categories)
    coverage = store.coverage()
    entries = [e for e in store.entries() if e.status != cat.REJECTED]
    exact: dict[str, int] = {}
    for e in entries:
        if e.counts_for_coverage:
            for pid, match in e.links.items():
                if match == cat.EXACT:
                    exact[pid] = exact.get(pid, 0) + 1
    since = float(store.get_meta(LAST_SUMMARY) or 0)
    added = [e for e in entries if (e.added_at or 0) > since]
    return {
        "date": datetime.fromtimestamp(now()).strftime("%d.%m.%Y %H:%M"),
        "stats": st,
        "done": [p for p in processes if coverage.get(p.id, 0) >= p.target],
        "two_three_exact": [p for p in processes if 2 <= exact.get(p.id, 0) <= 3],
        "need_search": [(p, coverage.get(p.id, 0)) for p in cat.batch_order(processes, coverage)],
        "since": datetime.fromtimestamp(since).strftime("%d.%m.%Y %H:%M") if since else None,
        "added": added,
        "processes": {p.id: p for p in processes},
    }


def lines(s: dict) -> list[tuple[str, str]]:
    """Сводка строками «показатель — значение» (для листа Excel)."""
    st = s["stats"]
    out = [
        ("Сводка покрытия", s["date"]),
        ("Услуг с выполненной целью", f"{st['services_done']} из {st['services']}"),
        ("Роликов в каталоге", str(st["videos"])),
        ("Засчитано в покрытие", str(st["counted"])),
        ("С точным соответствием", str(st["exact"])),
        ("Услуг без роликов / с 1 / с 2 / с 3 и более",
         " / ".join(str(st["services_by_count"][k]) for k in ("0", "1", "2", "3 и более"))),
        ("Площадки", ", ".join(f"{k} — {v}" for k, v in st["by_source"].items()) or "—"),
        ("Добавлено с прошлой сводки" + (f" ({s['since']})" if s["since"] else ""), str(len(s["added"]))),
        ("", ""),
        ("Нужен дополнительный поиск", "засчитано / цель"),
    ]
    out += [(f"{p.category}: {p.name}", f"{n} / {p.target}") for p, n in s["need_search"]]
    out += [("", ""), ("2–3 точных ролика", "")]
    out += [(f"{p.category}: {p.name}", "") for p in s["two_three_exact"]]
    return out


def write_html(path: Path, s: dict) -> None:
    st = s["stats"]

    def items(values) -> str:
        return "".join(f"<li>{v}</li>" for v in values) or "<li>—</li>"

    need = items(f"{escape(p.category)}: {escape(p.name)} — {n} из {p.target}" for p, n in s["need_search"])
    exact = items(f"{escape(p.category)}: {escape(p.name)}" for p in s["two_three_exact"])
    done = items(f"{escape(p.category)}: {escape(p.name)}" for p in s["done"])
    procs = s["processes"]
    added = items(
        f"№ {e.id} · {escape(e.result.source)} · {escape(e.result.title[:80])}"
        + (" — " + ", ".join(escape(procs[pid].name) for pid in e.links if pid in procs) if e.links else "")
        for e in s["added"])
    sources = items(f"{escape(k)} — {v}" for k, v in st["by_source"].items())
    levels = " · ".join(f"{name}: {st['services_by_count'][key]}" for key, name in
                        (("0", "без роликов"), ("1", "с 1"), ("2", "с 2"), ("3 и более", "с 3 и более")))
    since = f"с прошлой сводки ({s['since']})" if s["since"] else "всего (это первая сводка)"
    html = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Сводка покрытия</title>
<style>
:root {{ --bg:#fff; --fg:#1d1d1f; --muted:#666; --line:#ddd; --ok:#2e7d32; --bad:#c62828; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#1e1e1e; --fg:#eee; --muted:#aaa; --line:#444; --ok:#81c784; --bad:#ef9a9a; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; max-width:900px; margin:24px auto; padding:0 16px; }}
h1 {{ font-size:24px; margin-bottom:4px; }} h2 {{ font-size:18px; margin-top:28px; border-bottom:1px solid var(--line); }}
.muted {{ color:var(--muted); }} .big {{ font-size:32px; font-weight:600; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px; }}
.tile {{ border:1px solid var(--line); border-radius:8px; padding:12px; }}
</style></head><body>
<h1>Сводка покрытия</h1>
<div class="muted">{escape(s['date'])} · программа версии {__version__}</div>
<div class="grid" style="margin-top:16px">
  <div class="tile"><div class="big">{st['services_done']} / {st['services']}</div>услуг с выполненной целью</div>
  <div class="tile"><div class="big">{st['counted']}</div>роликов засчитано в покрытие</div>
  <div class="tile"><div class="big">{st['exact']}</div>с точным соответствием</div>
  <div class="tile"><div class="big">{len(s['added'])}</div>добавлено {since}</div>
</div>
<p>Услуг по числу засчитанных роликов: {levels}.</p>
<h2>Нужен дополнительный поиск ({len(s['need_search'])})</h2><ul>{need}</ul>
<h2>Услуги с 2–3 точными роликами ({len(s['two_three_exact'])})</h2><ul>{exact}</ul>
<h2>Цель выполнена ({len(s['done'])})</h2><ul>{done}</ul>
<h2>Площадки, давшие больше всего роликов</h2><ul>{sources}</ul>
<h2>Добавлено {since}</h2><ul>{added}</ul>
</body></html>
"""
    Path(path).write_text(html, encoding="utf-8")


def mark_done(store: cat.Catalog, now=time.time) -> None:
    """Запомнить дату сводки — следующая покажет, что добавлено после неё."""
    store.set_meta(LAST_SUMMARY, str(now()))
