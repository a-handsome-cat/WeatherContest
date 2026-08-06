"""Render a static HTML report from the accumulated metrics: one page per city plus
a combined overview. No build tooling - plain HTML/CSS, meant to be served straight
from a GitHub Pages `docs/` folder.
"""
from __future__ import annotations

import html
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import config
import db
import verify

VARIABLE_LABELS = {
    "temperature_2m": "Температура",
    "wind_speed_10m": "Ветер",
    "cloud_cover": "Облачность",
    "precipitation": "Осадки",
}
BUCKET_ORDER = [label for _, _, label in verify.LEAD_BUCKETS]

CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, system-ui, sans-serif; max-width: 1000px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }
h1 { font-size: 1.5rem; }
h2 { font-size: 1.2rem; margin-top: 2.5rem; border-bottom: 1px solid currentColor; padding-bottom: .3rem; }
nav a { margin-right: 1rem; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0 2rem; font-size: .9rem; }
th, td { border: 1px solid rgba(128,128,128,.35); padding: .4rem .6rem; text-align: center; }
th { text-align: left; }
td:first-child, th:first-child { text-align: left; white-space: nowrap; }
.tag { font-size: .75rem; opacity: .75; display: block; }
.confident { font-weight: 600; }
.preliminary { opacity: .75; }
.insufficient { opacity: .4; font-style: italic; }
.meta { opacity: .7; font-size: .85rem; }
.empty { opacity: .6; font-style: italic; }
"""


def _fmt_skill(m: dict) -> str:
    conf = m["confidence"]
    skill = m.get("skill_vs_persistence")
    skill_txt = f"{skill:+.2f}" if skill is not None else "—"
    title = f"n={m['n']}, MAE={m['mae']:.2f}, bias={m['bias']:+.2f}"
    return f'<span class="{conf}" title="{html.escape(title)}">{skill_txt}</span>'


def _fmt_precip(m: dict) -> str:
    conf = m["confidence"]
    csi = m.get("csi")
    csi_txt = f"CSI {csi:.2f}" if csi is not None else "—"
    pod = m.get("pod")
    far = m.get("far")
    pod_txt = f"{pod:.2f}" if pod is not None else "—"
    far_txt = f"{far:.2f}" if far is not None else "—"
    title = f"n={m['n']}, POD={pod_txt}, FAR={far_txt}, MAE={m['mae']:.2f}mm"
    return f'<span class="{conf}" title="{html.escape(title)}">{csi_txt}</span>'


def _variable_table(var_metrics: dict, sources: list[str], specialization: dict[str, str], is_precip: bool) -> str:
    rows = []
    for source in sources:
        cells = []
        for bucket in BUCKET_ORDER:
            m = var_metrics.get(bucket, {}).get(source)
            cells.append(f"<td>{(_fmt_precip if is_precip else _fmt_skill)(m)}</td>" if m else "<td>—</td>")
        tag = specialization.get(source)
        tag_html = f'<span class="tag">{html.escape(tag)}</span>' if tag else ""
        rows.append(f"<tr><td>{html.escape(source)}{tag_html}</td>{''.join(cells)}</tr>")
    header = "".join(f"<th>{b}</th>" for b in BUCKET_ORDER)
    return f"<table><tr><th>Источник</th>{header}</tr>{''.join(rows)}</table>"


def _all_sources(metrics: dict) -> list[str]:
    sources = set()
    for var_metrics in metrics.values():
        for bucket in var_metrics.values():
            sources.update(bucket.keys())
    return sorted(sources)


def render_city_page(city_id: str, display_name: str, metrics: dict, generated_at: str) -> str:
    specialization = verify.classify_specialization(metrics)
    sources = _all_sources(metrics)
    body = []
    if not sources:
        body.append('<p class="empty">Пока нет ни одной пары прогноз/факт — рейтинг появится, как только накопятся первые сутки данных.</p>')
    for var, label in VARIABLE_LABELS.items():
        var_metrics = metrics.get(var, {})
        if not var_metrics:
            continue
        body.append(f"<h2>{label}</h2>")
        body.append(_variable_table(var_metrics, sources, specialization, is_precip=(var == "precipitation")))
    return _page(f"{display_name} — рейтинг источников погоды", body, generated_at, city_id)


def render_combined_page(all_metrics: dict[str, dict], generated_at: str) -> str:
    """Averages skill_vs_persistence per source/variable/bucket across cities (city-average,
    not pooled samples - a city with more accumulated data shouldn't outweigh the other)."""
    combined: dict = defaultdict(lambda: defaultdict(dict))
    counts: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

    for city_id, metrics in all_metrics.items():
        for var, by_bucket in metrics.items():
            if var == "precipitation":
                continue  # combining CSI across cities isn't meaningful the same way; see per-city pages
            for bucket, by_source in by_bucket.items():
                for source, m in by_source.items():
                    if m["confidence"] == "insufficient" or m["skill_vs_persistence"] is None:
                        continue
                    key = (var, bucket, source)
                    combined.setdefault(var, {}).setdefault(bucket, {}).setdefault(source, {"sum": 0.0, "n_cities": 0, "n_total": 0})
                    entry = combined[var][bucket][source]
                    entry["sum"] += m["skill_vs_persistence"]
                    entry["n_cities"] += 1
                    entry["n_total"] += m["n"]

    body = []
    any_data = False
    for var, label in VARIABLE_LABELS.items():
        if var == "precipitation" or var not in combined:
            continue
        var_metrics = combined[var]
        sources = sorted({s for b in var_metrics.values() for s in b})
        if not sources:
            continue
        any_data = True
        body.append(f"<h2>{label}</h2>")
        rows = []
        for source in sources:
            cells = []
            for bucket in BUCKET_ORDER:
                entry = var_metrics.get(bucket, {}).get(source)
                if not entry:
                    cells.append("<td>—</td>")
                    continue
                avg_skill = entry["sum"] / entry["n_cities"]
                conf = "confident" if entry["n_cities"] == len(config.CITIES) else "preliminary"
                title = f"среднее по {entry['n_cities']} город(ам), всего пар: {entry['n_total']}"
                cells.append(f'<td><span class="{conf}" title="{html.escape(title)}">{avg_skill:+.2f}</span></td>')
            rows.append(f"<tr><td>{html.escape(source)}</td>{''.join(cells)}</tr>")
        header = "".join(f"<th>{b}</th>" for b in BUCKET_ORDER)
        body.append(f"<table><tr><th>Источник</th>{header}</tr>{''.join(rows)}</table>")

    if not any_data:
        body.append('<p class="empty">Пока недостаточно данных ни по одному городу для сводного рейтинга.</p>')
    body.append('<p class="meta">Сводная таблица — среднее skill-score (относительно наивного прогноза "как сейчас") по городам, где есть данные. Осадки в сводную таблицу не включены — CSI по городам с разным климатом складывать некорректно, смотрите их отдельно на страницах городов.</p>')

    return _page("Novi Sad vs Arkhangelsk — сводный рейтинг источников погоды", body, generated_at, None)


def _page(title: str, body_parts: list[str], generated_at: str, active_city: str | None) -> str:
    nav_links = ['<a href="index.html">Сводно</a>']
    for city_id, city in config.CITIES.items():
        nav_links.append(f'<a href="city_{city_id}.html">{html.escape(city["display_name"])}</a>')
    nav = f"<nav>{''.join(nav_links)}</nav>"
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
{nav}
<h1>{html.escape(title)}</h1>
<p class="meta">Skill-score &gt; 0 значит модель точнее наивного прогноза "как сейчас есть, так и будет". Полупрозрачные значения — предварительные (мало пар), совсем блёклые — данных пока недостаточно для выводов. Обновлено: {generated_at} UTC.</p>
{''.join(body_parts)}
</body>
</html>"""


def main() -> None:
    conn = db.connect()
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    site_dir = Path(config.SITE_DIR)
    site_dir.mkdir(parents=True, exist_ok=True)

    all_metrics = {}
    for city_id, city in config.CITIES.items():
        metrics = verify.compute_metrics(conn, city_id)
        all_metrics[city_id] = metrics
        page = render_city_page(city_id, city["display_name"], metrics, generated_at)
        (site_dir / f"city_{city_id}.html").write_text(page, encoding="utf-8")
        print(f"wrote city_{city_id}.html")

    index_page = render_combined_page(all_metrics, generated_at)
    (site_dir / "index.html").write_text(index_page, encoding="utf-8")
    print("wrote index.html")

    conn.close()


if __name__ == "__main__":
    main()
