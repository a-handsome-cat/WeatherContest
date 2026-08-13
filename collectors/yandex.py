# -*- coding: utf-8 -*-
"""Fetch Yandex Pogoda's 10-day forecast. EXPERIMENTAL / test-mode source - Yandex
has no public forecast API, so this scrapes their web page instead.

What we actually parse: not the visual (hashed-CSS-class) forecast widgets, but the
accessibility text Yandex embeds for screen readers - one plain-Russian paragraph per
day, e.g. "Сегодня, 13 августа: утром +21°, ... днём +30° ... вечером +27° ...
ночью +19° ...". This is more resilient than chasing their React component tree:
it's semantic content tied to what the page means, not how it's styled, so it's more
likely to survive a redesign - though the exact wording/keys are still Yandex's to
change, hence still "experimental".

Two deliberate approximations, done openly rather than silently:
- утро/день/вечер/ночь have no exact hour attached - we use representative local
  hours (9/15/21/3) agreed as a reasonable convention, not discovered from Yandex.
  "ночью" is the night AFTER that day's "вечером", i.e. the early hours of the
  *next* calendar day - confirmed by reading the sequence of day blocks in order.
- Local time is converted to UTC using each city's IANA timezone (config.py) -
  confirmed against real sunrise/sunset times in the same text block (matched
  Novi Sad's actual solar geometry to within ~5 minutes), not assumed.

As before, skips precipitation/cloud cover/wind direction entirely - Yandex's text
gives condition words ("ясно", "малооблачно") and compass words ("северный"), not
numeric mm/%/degrees, and inventing a mapping would be fabricating data, not
scraping it.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

URL = "https://yandex.ru/pogoda/ru/details"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

_PUSH_RE = re.compile(r"self\.__next_f\.push\((\[.*?\])\)</script>", re.DOTALL)
_DAY_ID_RE = re.compile(r'"data-id":"d_(\d{1,2})"')
_HTML_MARKER = '"__html":"'

# (keyword as it appears in the text, representative local hour, day offset from the block's date)
# "ночью" is the night following that day's evening - i.e. early hours of the next day.
_PERIODS = [("утром", 9, 0), ("днём", 15, 0), ("вечером", 21, 0), ("ночью", 3, 1)]

_TEMP_RE = re.compile(r"([+\-]?\d+)°")
_WIND_RE = re.compile(r"скорость ветра ([\d,]+)\s*м/с")


def _extract_string_literal(text: str, start_quote_idx: int) -> tuple[str | None, int]:
    """Reads a JSON string literal (with its escapes intact) starting at the opening
    quote, without needing the surrounding JSON to be well-formed on its own -
    the RSC payload is one giant string, not clean per-object JSON."""
    i = start_quote_idx + 1
    out = []
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            out.append(text[i:i + 2])
            i += 2
            continue
        if ch == '"':
            return "".join(out), i
        out.append(ch)
        i += 1
    return None, -1


def _joined_payload(html_text: str) -> str:
    chunks = []
    for raw in _PUSH_RE.findall(html_text):
        try:
            arr = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if len(arr) >= 2 and isinstance(arr[1], str):
            chunks.append(arr[1])
    if not chunks:
        raise ValueError("no self.__next_f.push chunks found - page structure may have changed")
    return "\n".join(chunks)


def _day_blocks(joined: str) -> list[tuple[int, str]]:
    """Returns [(day_of_month, accessible_summary_text), ...] in page order, one per
    forecast day. Pairs each "data-id":"d_NN" marker with the nearest following
    accessibility text block (validated by the presence of "восход", which every
    real day-summary block has) rather than assuming a fixed global order."""
    blocks: list[tuple[int, str]] = []
    for m in _DAY_ID_RE.finditer(joined):
        day_of_month = int(m.group(1))
        window_start = m.end()
        marker_idx = joined.find(_HTML_MARKER, window_start, window_start + 2000)
        if marker_idx == -1:
            continue
        raw, _end = _extract_string_literal(joined, marker_idx + len(_HTML_MARKER) - 1)
        if raw is None:
            continue
        text = json.loads('"' + raw + '"')
        if "восход" not in text:
            continue
        blocks.append((day_of_month, text))
    if not blocks:
        raise ValueError("no day-summary blocks found - page structure may have changed")
    return blocks


def _resolve_date(day_of_month: int, today):
    """Finds the next calendar date (today or later) whose day-of-month matches -
    handles month/year rollover without needing to parse Russian month names."""
    d = today
    for _ in range(40):
        if d.day == day_of_month:
            return d
        d += timedelta(days=1)
    raise ValueError(f"day-of-month {day_of_month} not found within 40 days of {today}")


def _parse_day_block(text: str, block_date, tz: ZoneInfo) -> list[tuple[str, str, int, float]]:
    points: list[tuple[str, str, int, float]] = []
    segments = text.split("<br />")
    for segment in segments[1:]:  # segments[0] is the "<Day>, <date>:" header
        for keyword, hour, day_offset in _PERIODS:
            if not segment.startswith(keyword):
                continue
            local_dt = datetime(
                block_date.year, block_date.month, block_date.day, hour, 0, 0,
                tzinfo=tz,
            ) + timedelta(days=day_offset)
            valid_time = local_dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

            temp_match = _TEMP_RE.search(segment)
            if temp_match:
                points.append((valid_time, "temperature_2m", 1, float(temp_match.group(1))))

            wind_match = _WIND_RE.search(segment)
            if wind_match:
                points.append((valid_time, "wind_speed_10m", 1, float(wind_match.group(1).replace(",", "."))))
            break
    return points


def fetch(lat: float, lon: float, timezone: str) -> list[tuple[str, str, int, float]]:
    """Returns [(valid_time, variable, period_hours, value), ...] - a real multi-day
    forecast (see module docstring), not a current-conditions snapshot."""
    resp = requests.get(URL, params={"lat": lat, "lon": lon}, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    joined = _joined_payload(resp.text)
    blocks = _day_blocks(joined)

    tz = ZoneInfo(timezone)
    today = datetime.now(tz).date()

    points: list[tuple[str, str, int, float]] = []
    for day_of_month, text in blocks:
        block_date = _resolve_date(day_of_month, today)
        points.extend(_parse_day_block(text, block_date, tz))

    if not points:
        raise ValueError("day-summary blocks found but no temperature/wind values could be parsed from them")
    return points
