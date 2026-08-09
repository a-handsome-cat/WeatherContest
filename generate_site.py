# -*- coding: utf-8 -*-
"""Render a static HTML report from the accumulated metrics: one page per city plus
a combined overview. No build tooling - plain HTML/CSS/JS, meant to be served straight
from a GitHub Pages `docs/` folder. Also writes a small CSV per table cell (the raw
forecast/observation pairs and formula behind that number) under docs/data/<city>/,
linked from the cell itself, so any number on the site can be downloaded and checked.
"""
from __future__ import annotations

import csv
import html
import io
import re
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
BUCKET_SLUGS = {
    "0-6ч": "0-6h",
    "6-24ч": "6-24h",
    "1-2 дня": "1-2d",
    "2-3 дня": "2-3d",
    "3-5 дней": "3-5d",
    "5-10 дней": "5-10d",
}

# (короткое дружелюбное имя, что это такое) - для показа вместо внутренних id вроде "open-meteo:ecmwf_ifs025"
SOURCE_LABELS: dict[str, tuple[str, str]] = {
    "open-meteo:ecmwf_ifs025": ("ECMWF IFS", "Эталонная глобальная модель Европейского центра среднесрочных прогнозов — считается одной из самых точных в мире."),
    "open-meteo:ecmwf_aifs025_single": ("ECMWF AIFS", "ИИ-модель того же ECMWF: прогноз считает нейросеть, а не физическая симуляция атмосферы."),
    "open-meteo:gfs_seamless": ("GFS (США)", "Основная глобальная модель американской метеослужбы NOAA."),
    "open-meteo:icon_seamless": ("ICON (Германия)", "Модель немецкой метеослужбы DWD, особенно сильна в Европе."),
    "open-meteo:gem_seamless": ("GEM (Канада)", "Глобальная модель канадской метеослужбы."),
    "open-meteo:ukmo_seamless": ("UKMO (Британия)", "Модель британской метеослужбы Met Office."),
    "open-meteo:meteofrance_seamless": ("ARPEGE (Франция)", "Глобальная модель французской Météo-France."),
    "open-meteo:jma_seamless": ("JMA (Япония)", "Глобальная модель японской метеослужбы."),
    "metno": ("Yr (Норвегия)", "Норвежская метеослужба: собственная обработка прогноза поверх нескольких моделей, хорошая репутация в Европе."),
    "openweathermap": ("OpenWeatherMap", "Популярный погодный сервис — то, что часто стоит внутри сторонних приложений."),
    "weatherapi": ("WeatherAPI.com", "Ещё один готовый погодный сервис для приложений."),
    "tomorrow_io": ("Tomorrow.io", "Использует свою сеть радаров и датчиков, а не только публичные метеомодели."),
    "weatherkit": ("Apple Погода", "То самое приложение «Погода» на iPhone — источник самой идеи этого проекта."),
}

GUIDE_HTML = """
<details class="guide">
<summary>Как читать эту страницу (нажмите, чтобы развернуть)</summary>
<h2>Что это за страница</h2>
<p>Идея простая: погодные приложения на телефоне не всегда правы — иногда пишут «облачно», когда за окном уже дождь. Здесь мы автоматически сравниваем прогнозы от полутора десятков разных источников погоды с тем, что реально происходило по данным официальных метеостанций, и считаем, кто предсказывает точнее — отдельно в Нови-Саде и в Архангельске.</p>

<h2>Как это устроено</h2>
<ol>
<li>Несколько раз в день мы забираем свежий прогноз от каждого источника на несколько дней вперёд.</li>
<li>Проходит время. Когда наступает момент, на который был сделан прогноз, мы сверяем предсказанное значение с тем, что реально зафиксировала метеостанция.</li>
<li>Чем больше таких пар «прогноз/факт» накапливается — тем увереннее можно судить, кто точнее.</li>
</ol>

<h2>Как читать таблицы</h2>
<p><strong>Строки</strong> — источники прогноза: от «сырых» метеомоделей (ECMWF, GFS, ICON и так далее) до готовых сервисов (OpenWeatherMap, Apple Погода и другие). Список и краткое описание каждого — в конце страницы.</p>
<p><strong>Столбец «Общий»</strong> — не среднее уже готовых чисел из остальных колонок, а отдельный расчёт: все пары «прогноз/факт» со всех периодов складываются в один общий пул, и skill-score (или CSI для осадков) считается по нему заново с нуля. Это важно, потому что skill-score и CSI — нелинейные величины (отношения), усреднять их напрямую как обычные числа некорректно, и период с 300 парами не должен весить столько же, сколько период с 10. Объединённый пул решает обе проблемы разом.</p>
<p><strong>Остальные столбцы</strong> — на сколько часов или дней вперёд был сделан прогноз (это называется <em>заблаговременностью</em>, lead time). Прогноз «через 2 часа» и прогноз «через 8 дней» — принципиально разные по сложности задачи, поэтому они не смешиваются в одну цифру.</p>
<p>Заголовки столбцов кликабельны — нажмите, чтобы отсортировать таблицу по этому столбцу (например, узнать, кто лучше всего справляется именно с горизонтом 3-5 дней). Повторный клик меняет направление сортировки.</p>
<p>Числа с пунктирным подчёркиванием кликабельны по-другому — по клику скачивается CSV со всеми исходными парами «прогноз/факт», которые легли в основу именно этого числа, вместе с формулой расчёта. Ничего не скрыто — можно проверить любую цифру на сайте вручную.</p>

<h2>Что такое skill-score</h2>
<p>Нас интересует не просто «насколько модель ошиблась», а «насколько она лучше, чем вообще ничего не предсказывать». Поэтому для сравнения берётся наивный прогноз без всякой метеорологии — «будет так же, как было в это же время вчера» (называется прогноз-<em>персистентность</em>).</p>
<p>Важная деталь: обычно это не «температура прямо сейчас», а «температура в это же время суток вчера». Если сравнивать с «сейчас», наивный прогноз на вечер, сделанный утром, будет выглядеть плохо просто потому, что утро и вечер — разная погода по своей природе (день/ночь), а не потому, что «наивно предсказывать плохо». Любая метеомодель тогда будет казаться отличной только за то, что знает про смену дня и ночи — это неинтересное превосходство. «То же время вчера» — гораздо более сложная планка, которая уже учитывает суточный ход, поэтому обыграть её — значит добавить что-то по-настоящему полезное сверх «просто знать, что бывает день и ночь».</p>
<p>Но и «то же время вчера» не всегда честнее: если вчера в это время проходил, скажем, холодный фронт, а сегодня погода спокойная — сравнение с вчера будет случайно и незаслуженно тяжёлым для baseline'а, а значит модель будет казаться лучше, чем есть. Поэтому для каждого периода мы автоматически считаем оба варианта («как сейчас» и «как вчера») по факту и берём тот, что оказался труднее для обгона именно здесь — одинаково для всех источников сразу, чтобы никто не получил случайно более слабого соперника. Какой именно вариант победил в конкретной ячейке — видно во всплывающей подсказке и в CSV.</p>
<p>Дальше по шагам:</p>
<ol>
<li>Считаем ошибку модели — точнее, RMSE (корень из среднего квадрата отклонения предсказанного значения от того, что случилось на самом деле). Квадрат нужен, чтобы сильно наказывать за крупные промахи сильнее, чем за мелкие.</li>
<li>Считаем такую же RMSE для наивного прогноза «как сейчас» за тот же момент времени.</li>
<li>Skill-score = 1 − (RMSE модели)² ⁄ (RMSE наивного прогноза)²</li>
</ol>
<p>Это не то же самое, что MAE (средняя абсолютная ошибка), которая тоже показана в таблице — MAE проще для интуиции, а RMSE участвует именно в формуле skill-score. Наведите курсор на любое число — во всплывающей подсказке видно оба значения и итоговый расчёт, а по клику можно скачать и сами исходные пары.</p>
<p>Как читать результат:</p>
<ul>
<li><strong>+1.0</strong> — модель предсказала идеально, без ошибки.</li>
<li><strong>0</strong> — модель ничем не лучше, чем просто сказать «будет как сейчас». Прогноз не приносит реальной пользы.</li>
<li><strong>меньше 0</strong> — модель хуже, чем вообще не гадать, а взять текущую погоду. Плохой знак для этого источника на этом горизонте.</li>
</ul>
<p>Чем выше число — тем лучше, но интереснее не абсолютное значение, а разница между источниками на одном и том же горизонте.</p>

<h2>Осадки считаются иначе (CSI / POD / FAR)</h2>
<p>Для дождя и снега skill-score не годится: осадки бывают не каждый день, и источник, который всегда отвечает «осадков не будет», по средней ошибке в миллиметрах будет выглядеть отлично, хотя толку от него ноль. Поэтому для осадков считается по-другому — как в задаче «предсказал / не предсказал»:</p>
<ul>
<li><strong>POD</strong> (Probability Of Detection) — из всех случаев, когда дождь реально был, в какой доле источник его предсказал.</li>
<li><strong>FAR</strong> (False Alarm Ratio) — из всех случаев, когда источник предсказывал дождь, в какой доле дождя на самом деле не случилось.</li>
<li><strong>CSI</strong> (Critical Success Index) — общий показатель точности по осадкам от 0 до 1, учитывающий сразу и пропуски, и ложные тревоги. 1.0 — идеально, 0 — провал.</li>
</ul>
<p>В таблице по умолчанию показан CSI, а POD и FAR можно увидеть, наведя курсор на число.</p>

<h2>Насколько можно доверять цифрам</h2>
<p>Чем меньше накопилось пар «прогноз-факт» для конкретной ячейки, тем менее надёжен вывод — один нетипичный день может всё перевернуть. Поэтому цифры показаны с разной степенью уверенности, а не просто скрыты, пока данных мало:</p>
<ul>
<li><span class="confident">обычным жирным текстом</span> — накопилось достаточно данных (от 25 пар), можно доверять.</li>
<li><span class="preliminary">приглушённым текстом</span> — данных пока маловато (10–24 пары), это предварительная оценка, картина может ещё измениться.</li>
<li><span class="insufficient">совсем бледным курсивом</span> — данных почти нет (меньше 10 пар), это скорее шум, чем сигнал.</li>
</ul>
<p>Наведите курсор на любое число — во всплывающей подсказке будет видно, сколько именно пар прогноз/факт легло в основу этой оценки.</p>

<h2>Пометки «сильна на ближайшие сутки» / «на дальнем горизонте»</h2>
<p>Некоторые источники хорошо справляются с прогнозом на завтра, но быстро «теряются» на горизонте в несколько дней — и наоборот. Рядом с названием источника в таблице температуры мы отмечаем, в чём он сейчас сильнее: по средней результативности (skill-score по температуре) в ближних интервалах (до 2 дней) против дальних (от 3 дней). Эта метка основана только на температуре и не показывается в других таблицах, чтобы не выглядеть так, будто она описывает ветер или облачность.</p>
</details>
"""

SORT_JS = r"""
document.querySelectorAll('table').forEach(function (table) {
  var headRow = table.tHead.rows[0];
  Array.from(headRow.cells).forEach(function (th, idx) {
    th.dataset.label = th.textContent;
    th.classList.add('sortable');
    th.addEventListener('click', function () { sortTableByColumn(table, idx); });
  });
});

function sortTableByColumn(table, idx) {
  var headCells = Array.from(table.tHead.rows[0].cells);
  var th = headCells[idx];
  var dir = th.dataset.dir === 'desc' ? 'asc' : 'desc';
  headCells.forEach(function (h) { h.dataset.dir = ''; h.textContent = h.dataset.label; });
  th.dataset.dir = dir;
  th.textContent = th.dataset.label + (dir === 'desc' ? ' ▼' : ' ▲');

  var tbody = table.tBodies[0];
  var rows = Array.from(tbody.rows);
  rows.sort(function (a, b) {
    var va = cellValue(a.cells[idx]);
    var vb = cellValue(b.cells[idx]);
    if (va === null && vb === null) return 0;
    if (va === null) return 1;
    if (vb === null) return -1;
    if (typeof va === 'string') {
      return dir === 'desc' ? String(vb).localeCompare(String(va), 'ru') : String(va).localeCompare(String(vb), 'ru');
    }
    return dir === 'desc' ? vb - va : va - vb;
  });
  rows.forEach(function (r) { tbody.appendChild(r); });
}

function cellValue(cell) {
  var text = cell.textContent.trim();
  if (text === '—' || text === '') return null;
  var m = text.match(/-?\d+\.\d+/);
  if (m) return parseFloat(m[0]);
  return text;
}
"""


_BASELINE_LABELS = {"asof": "как сейчас (на момент забора)", "24h": "как вчера в это же время", "mixed": "смешанный (разный по периодам в пуле)"}


def _fmt_skill(m: dict) -> str:
    conf = m["confidence"]
    skill = m.get("skill_vs_persistence")
    skill_txt = f"{skill:+.2f}" if skill is not None else "—"
    rmse_p = m.get("rmse_persistence")
    baseline_label = _BASELINE_LABELS.get(m.get("persistence_kind"), "?")
    if rmse_p is not None and skill is not None:
        # spells out exactly how skill-score was derived: 1 - (RMSE / RMSE_персистентности)^2
        title = (
            f"n={m['n']}, MAE={m['mae']:.2f}, RMSE={m['rmse']:.2f}, bias={m['bias']:+.2f}. "
            f"Skill = 1 - (RMSE/RMSE_персистентности)^2 = 1 - ({m['rmse']:.2f}/{rmse_p:.2f})^2, "
            f"где RMSE_персистентности={rmse_p:.2f} по {m['n_persistence']} пар(ам). "
            f"Baseline: {baseline_label} (автоматически выбран более сильный из двух по факту)"
        )
    elif rmse_p is not None:
        title = f"n={m['n']}, MAE={m['mae']:.2f}, RMSE={m['rmse']:.2f}, bias={m['bias']:+.2f}. Skill не определён: RMSE_персистентности=0 по {m['n_persistence']} пар(ам) - baseline совпал с фактом точно."
    else:
        title = f"n={m['n']}, MAE={m['mae']:.2f}, RMSE={m['rmse']:.2f}, bias={m['bias']:+.2f}. Недостаточно пар с персистентностью для skill-score."
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


def _source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, (source, ""))[0]


def _source_slug(source: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", source).strip("-")


def _compute_pooled_overall(pairs_grouped: dict) -> dict[tuple[str, str], dict]:
    """The "Общий" column: pools every pair from every lead-time bucket for a given
    (variable, source) into one list and recomputes the metric from scratch on the pool -
    NOT an average of the already-computed per-bucket skill/CSI values. Averaging those
    directly would be wrong twice over: they're nonlinear ratios (averaging ratios isn't the
    same as recomputing on combined data), and a 10-pair bucket would count exactly as much
    as a 300-pair one. Pooling raw pairs fixes both at once and needs no bucket-level
    confidence pre-filtering - a thin bucket just contributes its few pairs to the pool
    instead of being silently dropped."""
    scored_variables = verify.CONTINUOUS_VARS | {"precipitation"}  # excludes wind_direction_10m - see compute_metrics()

    pairs_by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    buckets_by_key: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (variable, bucket, source), plist in pairs_grouped.items():
        if variable not in scored_variables:
            continue
        pairs_by_key[(variable, source)].extend(plist)
        buckets_by_key[(variable, source)].add(bucket)

    result: dict[tuple[str, str], dict] = {}
    for key, pooled_pairs in pairs_by_key.items():
        variable, _source = key
        is_precip = variable == "precipitation"
        agg = verify.aggregate(pooled_pairs, is_precip)
        agg["buckets"] = [b for b in BUCKET_ORDER if b in buckets_by_key[key]]
        result[key] = agg
    return result


def _overall_value(overall_metrics: dict, variable: str, source: str, is_precip: bool) -> str:
    m = overall_metrics.get((variable, source))
    if m is None:
        return "—"
    cell = (_fmt_precip if is_precip else _fmt_skill)(m)
    note = f"Объединённый пул по периодам: {', '.join(m['buckets'])}. "
    return cell.replace('title="', f'title="{html.escape(note)}', 1)


def _csv_rows(pairs: list[dict], is_precip: bool, with_bucket: bool = False) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    header = (["bucket"] if with_bucket else []) + ["fetched_at", "valid_time", "lead_hours", "forecast", "observed"]
    header += ["error"] if is_precip else ["persistence", "error", "abs_error"]
    writer.writerow(header)
    for p in pairs:
        row = ([p["bucket"]] if with_bucket else []) + [
            p["fetched_at"], p["valid_time"], p["lead_hours"],
            round(p["forecast"], 3), round(p["observed"], 3),
        ]
        err = p["forecast"] - p["observed"]
        if is_precip:
            row += [round(err, 3)]
        else:
            row += [round(p["persistence"], 3) if p["persistence"] is not None else "", round(err, 3), round(abs(err), 3)]
        writer.writerow(row)
    return out.getvalue()


def _cell_csv_text(pairs: list[dict], m: dict, is_precip: bool, city_name: str, var_label: str, bucket: str, source_label: str, with_bucket_col: bool = False) -> str:
    lines = [f"# {city_name} / {var_label} / {bucket} / {source_label}"]
    if is_precip:
        lines.append(f"# n={m['n']}, порог дождя = {verify.PRECIP_THRESHOLD_MM} мм")
        lines.append(f"# POD={m['pod']}, FAR={m['far']}, CSI={m['csi']}, MAE={m['mae']:.3f} мм, bias={m['bias']:+.3f} мм")
        lines.append("# CSI = hits / (hits + misses + false_alarms); hit = прогноз>=порога И факт>=порога")
    else:
        lines.append(f"# n={m['n']}, MAE={m['mae']:.3f}, RMSE={m['rmse']:.3f}, bias={m['bias']:+.3f}")
        rmse_p = m.get("rmse_persistence")
        skill = m.get("skill_vs_persistence")
        if rmse_p is not None and skill is not None:
            lines.append(
                f"# skill = 1 - (RMSE/RMSE_персистентности)^2 = 1 - ({m['rmse']:.3f}/{rmse_p:.3f})^2 "
                f"= {skill:.4f}  (RMSE_персистентности по {m['n_persistence']} пар(ам))"
            )
        elif rmse_p is not None:
            # rmse_p == 0 (persistence matched observed exactly every time in this sample) -
            # the model/persistence ratio is undefined rather than computed, not "insufficient data"
            lines.append(f"# skill-score: не определён (RMSE_персистентности=0 по {m['n_persistence']} пар(ам) - baseline совпал с фактом точно)")
        else:
            lines.append("# skill-score: недостаточно пар с персистентностью")
        baseline_label = _BASELINE_LABELS.get(m.get("persistence_kind"), "?")
        lines.append(f"# persistence baseline = {baseline_label} - выбран автоматически как более сильный (ниже RMSE) из двух кандидатов для этого периода, одинаково для всех источников")
    if with_bucket_col:
        lines.append(f"# «Общий» = объединённый пул пар из периодов: {bucket} - метрика посчитана заново по всему пулу, а не усреднена по периодам")
    lines.append("#")
    return "\n".join(lines) + "\n" + _csv_rows(pairs, is_precip, with_bucket=with_bucket_col)


def _write_cell_exports(site_dir: Path, city_id: str, city_name: str, metrics: dict, pairs_grouped: dict, overall_metrics: dict) -> tuple[dict, dict]:
    """Writes one CSV per table cell (raw pairs + formula) under docs/data/<city_id>/,
    returns {(variable, bucket, source): relative_url} and {(variable, source): relative_url}
    for _variable_table to link cells to."""
    data_dir = site_dir / "data" / city_id
    data_dir.mkdir(parents=True, exist_ok=True)
    cell_links: dict[tuple[str, str, str], str] = {}
    overall_links: dict[tuple[str, str], str] = {}

    for (variable, bucket, source), plist in pairs_grouped.items():
        m = metrics.get(variable, {}).get(bucket, {}).get(source)
        if not m or bucket not in BUCKET_SLUGS:
            continue
        is_precip = variable == "precipitation"
        text = _cell_csv_text(plist, m, is_precip, city_name, VARIABLE_LABELS.get(variable, variable), bucket, _source_label(source))
        fname = f"{variable}__{BUCKET_SLUGS[bucket]}__{_source_slug(source)}.csv"
        (data_dir / fname).write_text(text, encoding="utf-8")
        cell_links[(variable, bucket, source)] = f"data/{city_id}/{fname}"

    for (variable, source), m in overall_metrics.items():
        is_precip = variable == "precipitation"
        pooled_pairs = [p for bucket in m["buckets"] for p in pairs_grouped.get((variable, bucket, source), [])]
        bucket_label = ", ".join(m["buckets"])
        text = _cell_csv_text(pooled_pairs, m, is_precip, city_name, VARIABLE_LABELS.get(variable, variable), bucket_label, _source_label(source), with_bucket_col=True)
        fname = f"{variable}__overall__{_source_slug(source)}.csv"
        (data_dir / fname).write_text(text, encoding="utf-8")
        overall_links[(variable, source)] = f"data/{city_id}/{fname}"

    return cell_links, overall_links


def _linkify(cell_html: str, link: str | None) -> str:
    if not link:
        return cell_html
    return f'<a href="{html.escape(link)}" download title="Скачать пары данных и формулу (CSV)">{cell_html}</a>'


def _variable_table(
    var_metrics: dict,
    sources: list[str],
    specialization: dict[str, str],
    is_precip: bool,
    variable: str,
    cell_links: dict,
    overall_links: dict,
    overall_metrics: dict,
) -> str:
    rows = []
    for source in sources:
        cells = []
        for bucket in BUCKET_ORDER:
            m = var_metrics.get(bucket, {}).get(source)
            cell_html = (_fmt_precip if is_precip else _fmt_skill)(m) if m else "—"
            cell_html = _linkify(cell_html, cell_links.get((variable, bucket, source)))
            cells.append(f"<td>{cell_html}</td>")
        overall = _linkify(_overall_value(overall_metrics, variable, source, is_precip), overall_links.get((variable, source)))
        tag = specialization.get(source)
        tag_html = f'<span class="tag">{html.escape(tag)}</span>' if tag else ""
        rows.append(f"<tr><td>{html.escape(_source_label(source))}{tag_html}</td><td>{overall}</td>{''.join(cells)}</tr>")
    header = "".join(f"<th>{b}</th>" for b in BUCKET_ORDER)
    return f'<div class="table-wrap"><table><thead><tr><th>Источник</th><th>Общий</th>{header}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _all_sources(metrics: dict) -> list[str]:
    sources = set()
    for var_metrics in metrics.values():
        for bucket in var_metrics.values():
            sources.update(bucket.keys())
    return sorted(sources)


def _glossary_html(sources: list[str]) -> str:
    items = []
    for source in sources:
        name, desc = SOURCE_LABELS.get(source, (source, ""))
        desc_html = f" — {html.escape(desc)}" if desc else ""
        items.append(f"<li><strong>{html.escape(name)}</strong>{desc_html}</li>")
    if not items:
        return ""
    return f'<h2>Источники на этой странице</h2><ul class="glossary">{"".join(items)}</ul>'


def render_city_page(
    city_id: str,
    display_name: str,
    metrics: dict,
    generated_at: str,
    cell_links: dict,
    overall_links: dict,
    overall_metrics: dict,
) -> str:
    specialization = verify.classify_specialization(metrics)
    sources = _all_sources(metrics)
    body = []
    if not sources:
        body.append('<p class="empty">Пока нет ни одной пары прогноз/факт — таблицы появятся, как только накопятся первые сутки данных.</p>')
    for var, label in VARIABLE_LABELS.items():
        var_metrics = metrics.get(var, {})
        if not var_metrics:
            continue
        body.append(f"<h2>{label}</h2>")
        # specialization is derived from temperature skill only (see verify.classify_specialization) -
        # showing it next to sources in other tables would misleadingly imply it describes that variable
        tags_here = specialization if var == "temperature_2m" else {}
        body.append(_variable_table(var_metrics, sources, tags_here, is_precip=(var == "precipitation"), variable=var, cell_links=cell_links, overall_links=overall_links, overall_metrics=overall_metrics))
    body.append(_glossary_html(sources))
    return _page(
        title=f"Точность прогноза погоды — {display_name}",
        subtitle=f"Сравнение источников прогноза погоды в городе {display_name} с фактическими данными метеостанции.",
        body_parts=body,
        generated_at=generated_at,
        active="city_" + city_id,
    )


def render_combined_page(all_metrics: dict[str, dict], generated_at: str) -> str:
    """Averages skill_vs_persistence per source/variable/bucket across cities (city-average,
    not pooled samples - a city with more accumulated data shouldn't outweigh the other).
    No download links here - these numbers are an average of per-city averages, not backed
    by a single list of raw pairs; see the per-city pages for those."""
    combined: dict = defaultdict(lambda: defaultdict(dict))

    for city_id, metrics in all_metrics.items():
        for var, by_bucket in metrics.items():
            if var == "precipitation":
                continue  # combining CSI across cities isn't meaningful the same way; see per-city pages
            for bucket, by_source in by_bucket.items():
                for source, m in by_source.items():
                    if m["confidence"] == "insufficient" or m["skill_vs_persistence"] is None:
                        continue
                    combined.setdefault(var, {}).setdefault(bucket, {}).setdefault(source, {"sum": 0.0, "n_cities": 0, "n_total": 0})
                    entry = combined[var][bucket][source]
                    entry["sum"] += m["skill_vs_persistence"]
                    entry["n_cities"] += 1
                    entry["n_total"] += m["n"]

    body = []
    any_data = False
    all_sources_seen: set[str] = set()
    for var, label in VARIABLE_LABELS.items():
        if var == "precipitation" or var not in combined:
            continue
        var_metrics = combined[var]
        sources = sorted({s for b in var_metrics.values() for s in b})
        if not sources:
            continue
        any_data = True
        all_sources_seen.update(sources)
        body.append(f"<h2>{label}</h2>")
        rows = []
        for source in sources:
            cells = []
            per_bucket_avgs = []
            overall_n = 0
            for bucket in BUCKET_ORDER:
                entry = var_metrics.get(bucket, {}).get(source)
                if not entry:
                    cells.append("<td>—</td>")
                    continue
                avg_skill = entry["sum"] / entry["n_cities"]
                per_bucket_avgs.append(avg_skill)
                overall_n += entry["n_total"]
                conf = "confident" if entry["n_cities"] == len(config.CITIES) else "preliminary"
                title = f"среднее по {entry['n_cities']} город(ам), всего пар: {entry['n_total']}"
                cells.append(f'<td><span class="{conf}" title="{html.escape(title)}">{avg_skill:+.2f}</span></td>')
            if per_bucket_avgs:
                overall_avg = sum(per_bucket_avgs) / len(per_bucket_avgs)
                overall_conf = verify.confidence(overall_n)
                overall_title = f"среднее по {len(per_bucket_avgs)} период(ам), всего пар: {overall_n}"
                overall_html = f'<span class="{overall_conf}" title="{html.escape(overall_title)}">{overall_avg:+.2f}</span>'
            else:
                overall_html = "—"
            rows.append(f"<tr><td>{html.escape(_source_label(source))}</td><td>{overall_html}</td>{''.join(cells)}</tr>")
        header = "".join(f"<th>{b}</th>" for b in BUCKET_ORDER)
        body.append(f'<div class="table-wrap"><table><thead><tr><th>Источник</th><th>Общий</th>{header}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')

    if not any_data:
        body.append('<p class="empty">Пока недостаточно данных ни по одному городу для сводной таблицы.</p>')
    body.append('<p class="meta">Здесь показано среднее skill-score по обоим городам, где для источника уже есть данные — то есть насколько он в среднем лучше «как сейчас, так и будет», а не рейтинг самих городов. Осадки в сводную таблицу не включены — усреднять CSI по городам с разным климатом некорректно, смотрите их отдельно на страницах городов. Числа здесь не кликабельны для скачивания — это среднее по средним (по городам, а внутри — пока ещё и по периодам), а не отдельный расчёт по объединённому пулу пар, как теперь колонка «Общий» на страницах городов. Смешивать сырые пары двух климатически разных городов в один пул — отдельный вопрос, который здесь пока сознательно не решён; исходные пары и честный «Общий» по каждому городу отдельно смотрите на страницах городов.</p>')
    body.append(_glossary_html(sorted(all_sources_seen)))

    return _page(
        title="Рейтинг источников прогноза погоды",
        subtitle="Сводно по двум городам — Нови-Саду (Сербия) и Архангельску (Россия). Сравниваются источники прогноза, а не сами города.",
        body_parts=body,
        generated_at=generated_at,
        active="index",
    )


def _page(title: str, subtitle: str, body_parts: list[str], generated_at: str, active: str) -> str:
    nav_items = [("index", "index.html", "Сводно")]
    for city_id, city in config.CITIES.items():
        nav_items.append((f"city_{city_id}", f"city_{city_id}.html", city["display_name"]))
    nav_links = []
    for key, href, label in nav_items:
        cls = ' class="active"' if key == active else ""
        nav_links.append(f'<a href="{href}"{cls}>{html.escape(label)}</a>')
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
<p class="subtitle">{html.escape(subtitle)}</p>
{GUIDE_HTML}
<p class="meta">Обновлено: {generated_at} UTC.</p>
{''.join(body_parts)}
<script>{SORT_JS}</script>
</body>
</html>"""


CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; }
h1 { font-size: 1.6rem; margin-bottom: .2rem; }
.subtitle { opacity: .75; margin-top: 0; }
h2 { font-size: 1.15rem; margin-top: 2rem; border-bottom: 1px solid currentColor; padding-bottom: .3rem; }
p, li { font-size: .95rem; }
nav { margin-bottom: 1.5rem; }
nav a { margin-right: 1.2rem; text-decoration: none; opacity: .65; border-bottom: 2px solid transparent; padding-bottom: .2rem; }
nav a.active { opacity: 1; font-weight: 600; border-bottom-color: currentColor; }

.guide { background: rgba(128,128,128,.08); border: 1px solid rgba(128,128,128,.2); border-radius: 10px; padding: .3rem 1.5rem 1rem; margin: 1.5rem 0; }
.guide summary { cursor: pointer; font-weight: 600; font-size: 1.05rem; padding: .8rem 0; }
.guide[open] summary { padding-bottom: .3rem; }
.guide h2 { border-bottom: none; margin-top: 1.5rem; font-size: 1.05rem; }
.guide h2:first-of-type { margin-top: 1rem; }

.table-wrap { overflow-x: auto; margin: 1rem 0 2rem; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { border: 1px solid rgba(128,128,128,.35); padding: .4rem .6rem; text-align: center; white-space: nowrap; }
th { text-align: left; }
td:first-child, th:first-child { text-align: left; }
th.sortable { cursor: pointer; user-select: none; }
th.sortable:hover { background: rgba(128,128,128,.15); }
td:nth-child(2), th:nth-child(2) { border-right: 2px solid rgba(128,128,128,.4); }
tr:nth-child(even) td { background: rgba(128,128,128,.05); }

td a { color: inherit; text-decoration: none; border-bottom: 1px dotted currentColor; cursor: pointer; }
td a:hover { border-bottom-style: solid; }

.tag { font-size: .72rem; opacity: .7; display: block; font-weight: normal; white-space: normal; }
.confident { font-weight: 600; }
.preliminary { opacity: .75; }
.insufficient { opacity: .45; font-style: italic; }
.meta { opacity: .7; font-size: .85rem; }
.empty { opacity: .6; font-style: italic; }

.glossary { padding-left: 1.2rem; }
.glossary li { margin-bottom: .3rem; }
"""


def main() -> None:
    conn = db.connect()
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    site_dir = Path(config.SITE_DIR)
    site_dir.mkdir(parents=True, exist_ok=True)

    all_metrics = {}
    for city_id, city in config.CITIES.items():
        metrics = verify.compute_metrics(conn, city_id)
        all_metrics[city_id] = metrics
        pairs_grouped = verify.pairs_by_cell(conn, city_id)
        overall_metrics = _compute_pooled_overall(pairs_grouped)
        cell_links, overall_links = _write_cell_exports(site_dir, city_id, city["display_name"], metrics, pairs_grouped, overall_metrics)
        page = render_city_page(city_id, city["display_name"], metrics, generated_at, cell_links, overall_links, overall_metrics)
        (site_dir / f"city_{city_id}.html").write_text(page, encoding="utf-8")
        print(f"wrote city_{city_id}.html (+{len(cell_links)} cell CSVs, {len(overall_links)} overall CSVs)")

    index_page = render_combined_page(all_metrics, generated_at)
    (site_dir / "index.html").write_text(index_page, encoding="utf-8")
    print("wrote index.html")

    conn.close()


if __name__ == "__main__":
    main()
