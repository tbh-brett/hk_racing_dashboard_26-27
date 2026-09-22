"""HKJC's Chinese race card, read for the horse's Chinese name and nothing else.

The zh-hk card is the same page as the English one at a sibling URL — same
`table.starter`, same column order, headers in Chinese. Everything else on it
the English card already carries, so this returns three fields a runner:

    {"horse_no": 3, "name_zh": "神駒馬靈", "brand_no": "J162"}

The brand number is the point. It is the horse's permanent identity, printed
on both cards, so `jobs/sync_horse_names` pairs the two languages on it rather
than on the saddle-cloth number — which is a fact about one race, not about a
horse.

Same rules as `ingest.racecard`: columns found by header text, no positional
fallback, a layout change raises naming the race and the column.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from hkrd.ingest._client import NotFound, fetch_html, urls
from hkrd.ingest.racecard import RacecardError

__all__ = ["parse_racecard_zh", "fetch_race_zh"]

# Header text -> field, matched exactly. Every Chinese header on this card is
# short and several share characters — 馬名, 馬齡, 馬主, 馬匹編號 — so a
# substring match is how 馬 binds to the wrong column.
_COLUMNS = {"horse_no": "馬匹編號", "name_zh": "馬名", "brand_no": "烙號"}

_HORSE_NO = re.compile(r"^\d{1,2}$")
_BRAND = re.compile(r"^[A-Z]\d{3}$")
_CJK = re.compile(r"[㐀-鿿]")

# The Chinese site's "there is no such page": a 200 with this panel. Measured
# on 2026-09-22 it is the same body for a race past the end of a card and for
# a card not yet published.
_NO_INFORMATION = re.compile(
    r"""id=["']?errorContainer["']?[^>]*>\s*沒有相關資料""")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_racecard_zh(html: str, race_no: int, *,
                      source: str = "") -> list[dict[str, Any]]:
    """The Chinese name and brand number of every runner on one card."""
    label = source or f"R{race_no}"
    table = BeautifulSoup(html, "html.parser").find("table", class_="starter")
    if table is None:
        raise RacecardError(f"{label}: no Chinese race card table found")

    rows = table.find_all("tr")
    header, columns = None, {}
    for tr in rows[:4]:
        cells = [_clean(c.get_text()) for c in tr.find_all(["th", "td"])]
        found = {f: cells.index(h) for f, h in _COLUMNS.items() if h in cells}
        if len(found) == len(_COLUMNS):
            header, columns = tr, found
            break
    if header is None:
        raise RacecardError(
            f"{label}: Chinese card header has no "
            f"{' / '.join(_COLUMNS.values())} — the layout has changed")

    out: list[dict[str, Any]] = []
    for tr in rows[rows.index(header) + 1:]:
        cells = [_clean(c.get_text()) for c in tr.find_all(["td", "th"])]
        if len(cells) <= max(columns.values()):
            continue
        no, name, brand = (cells[columns[k]]
                           for k in ("horse_no", "name_zh", "brand_no"))
        if not _HORSE_NO.match(no) or not name:
            continue
        out.append({"race_no": race_no, "horse_no": int(no),
                    "name_zh": name, "brand_no": brand or None})

    if not out:
        raise RacecardError(f"{label}: Chinese card table found but no "
                            "runner parsed — the layout has changed")
    # By shape, as the English parser checks: a shifted column puts a brand
    # where a name belongs, or a name where the brand belongs, in EVERY row.
    if not any(_CJK.search(r["name_zh"]) for r in out):
        raise RacecardError(f"{label}: no Chinese name in the 馬名 column — "
                            f"saw {out[0]['name_zh']!r}")
    brands = [r["brand_no"] for r in out if r["brand_no"]]
    if brands and not any(_BRAND.match(b) for b in brands):
        raise RacecardError(f"{label}: the 烙號 column holds no brand numbers "
                            f"— saw {brands[0]!r}")
    return out


def fetch_race_zh(date: str, venue: str, race_no: int, *,
                  session=None) -> list[dict[str, Any]]:
    """One race's Chinese names. NotFound when HKJC says there is no page."""
    html = fetch_html(urls.racecard_zh,
                      {"racedate": date.replace("-", "/"), "Racecourse": venue,
                       "RaceNo": str(race_no)}, session=session)
    label = f"{date} {venue} R{race_no} (zh)"
    if _NO_INFORMATION.search(html):
        raise NotFound(f"{label}: no such page — HKJC answered 沒有相關資料")
    return parse_racecard_zh(html, race_no, source=label)
