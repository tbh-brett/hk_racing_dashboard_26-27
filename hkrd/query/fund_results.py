"""What the fund app may read once betting opens: prices, results, later runs.

The meetings, the card and the Six Up pools are in `query/fund`, with the
envelope every answer here is wrapped in.

Two rules from AGENTS.md shape this file. 999.0 is HKJC saying "no price" and
is never one, on read as on write. And a price is the latest capture, never a
morning one: `odds_at` takes the newest capture at or before the time asked
for, per race, because races are captured on their own ladders and a meeting
has no single moment at which every race was read.
"""
from __future__ import annotations

import re
from typing import Any

from hkrd.query.fund import (RESULT_JOBS, SCRATCHED, NotHeld, check_meeting,
                             class_text, envelope, hk_time, last_read,
                             parse_at, parse_day, reading)
from hkrd.query.market import NO_PRICE
from hkrd.store import coerce
from hkrd.store.connect import Connection

__all__ = ["INCIDENT_TAGS", "incident_tags", "closing_place", "odds_at", "results",
           "horse_runs"]

# The stewards' report as a few fixed tags, for a blackbook follow-up line.
# DESCRIPTIVE ONLY: measured 5 Oct 2026, stewards' keywords clear the price no
# more often than chance. Order is the order a run is told in.
INCIDENT_TAGS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (tag, re.compile(rx, re.IGNORECASE)) for tag, rx in (
        ("slow away", r"slow to begin|began slowly|missed the (?:start|jump)"
                      r"|jumped awkwardly|jumped only fairly|\bdwelt\b"),
        ("checked", r"\bchecked\b|\bsteadied\b|clipped (?:the )?heels"
                    r"|restrained off the heels"),
        ("bumped", r"\bbumped\b|\bbrushed\b|\bcrowded\b|\bhampered\b"
                   r"|\bsqueezed\b|made (?:\w+ )?contact"),
        ("held up", r"(?:failed to obtain|unable to obtain|lacked|without|denied)"
                    r" clear running|\bheld up\b|lacked room|\bshut in\b"),
        ("wide", r"(?:raced|forced|carried|taken|went|travelled) (?:very |extremely )?wide"
                 r"|\b(?:three|four|five) wide\b|without cover"),
        ("over-raced", r"over-rac|overrac|raced keenly|pulled hard|travelled keenly"),
        ("hung", r"\bhung\b|\blay(?:ing)? (?:in|out)\b|\blugged\b"),
        ("tailed off", r"tailed (?:off|out)"),
        ("eased", r"\beased (?:down|up|right up)\b|not persevered with"),
        # Never "did not show any significant findings", and never a history
        # ("had previously been diagnosed with an irregular heart rhythm"):
        # every phrase here is a finding about THIS race. Blood in the mouth
        # is usually a bitten tongue and is left out; blood in the trachea is
        # bleeding. The stored apostrophe in "horse's" is damaged, hence \S+.
        ("vet finding", r"found to be lame|\blame (?:in|on) the\b|bled from"
                        r"|to have bled|amount of blood|blood in \S+(?: \S+)? trachea"
                        r"|revealed [^.]{0,80}(?:irregular heart rhythm"
                        r"|lame|bled|heat stress|respiratory|injur)"),
        ("lost a shoe", r"(?:lost|dislodged|spread) (?:a|its|the|both) (?:\w+ ){0,2}"
                        r"(?:shoe|plate)s?\b"),
        ("performance unacceptable", r"considered unacceptable"),
        ("barrier trial ordered", r"satisfaction of the stewards in a barrier trial"),
    ))


def incident_tags(text: str | None) -> list[str]:
    """The tags a stewards' report earns, in a fixed order; [] for none."""
    if not text:
        return []
    return [tag for tag, rx in INCIDENT_TAGS if rx.search(text)]


def _price(value: float | None) -> float | None:
    return value if value is not None and 1 <= value < NO_PRICE else None


def odds_at(date: str, venue: str, at: str, *,
            conn: Connection | None = None) -> dict[str, Any]:
    """Every race's win odds as they stood at `at`: its latest capture at or
    before then that holds a price.

    Each race says when its own capture was taken. The label is `close` once
    every race returned had stopped selling by `at` -- the settled price --
    and `snapshot` otherwise.
    """
    date, cut = parse_day(date, "date"), parse_at(at)
    with reading(conn) as c:
        check_meeting(c, date, venue)
        race_nos = [r[0] for r in c.execute(
            "SELECT race_no FROM races WHERE race_date = ? ORDER BY race_no", (date,))]
        closes = {r[0]: r[1] for r in c.execute(
            "SELECT race_no, closed_at FROM market_close WHERE race_date = ?", (date,))}
        taken: dict[int, str] = {}
        win: dict[int, dict[str, float]] = {}
        for race_no in race_nos:
            # Newest first down ix_snap_race, stopping at the first capture
            # with a real price in it.
            row = c.execute(
                "SELECT captured_at FROM odds_snapshots WHERE race_date = ? "
                "AND race_no = ? AND captured_at <= ? AND win_odds < ? "
                "ORDER BY captured_at DESC LIMIT 1",
                (date, race_no, cut, NO_PRICE)).fetchone()
            if row is None:
                continue
            prices = {str(h): p for h, w in c.execute(
                "SELECT horse_no, win_odds FROM odds_snapshots WHERE race_date = ? "
                "AND race_no = ? AND captured_at = ? ORDER BY horse_no",
                (date, race_no, row[0])) if (p := _price(w)) is not None}
            if prices:
                taken[race_no], win[race_no] = row[0], prices
    if not taken:
        raise NotHeld(f"no win odds taken at or before {at} for {date} {venue}")
    closed = all(closes.get(n) and closes[n] <= cut for n in taken)
    newest = hk_time(max(taken.values()))
    return envelope("odds", newest, {
        "meeting": {"date": date, "venue": venue},
        "odds": {"label": "close" if closed else "snapshot", "taken_at": newest,
                 "races": [{"race_no": n, "taken_at": hk_time(taken[n]),
                            "win": win[n]} for n in sorted(taken)]}})


def closing_place(c: Connection, date: str, race_no: int) -> dict[int, float]:
    """Each runner's last captured place odds: the tote's closing place price.

    `runners` keeps the closing WIN odds and nothing beside them, so the place
    price lives only in the capture, and the last capture is the settled one
    now that capturing stops when HKJC shuts the pool. The fund needs it to
    measure a place leg that did not place against the tote.
    """
    row = c.execute(
        "SELECT captured_at FROM odds_snapshots WHERE race_date = ? AND race_no = ? "
        "AND place_odds < ? ORDER BY captured_at DESC LIMIT 1",
        (date, race_no, NO_PRICE)).fetchone()
    if row is None:
        return {}
    return {h: p for h, v in c.execute(
        "SELECT horse_no, place_odds FROM odds_snapshots WHERE race_date = ? "
        "AND race_no = ? AND captured_at = ?", (date, race_no, row[0]))
        if (p := _price(v)) is not None}


def _finish(r: Any, place_close: float | None) -> dict[str, Any]:
    """One runner's line in a result, or in a run."""
    place = r["place"]
    item: dict[str, Any] = {"pos": place}
    if place == 1:
        item["margin"] = ""
    elif place is not None and (m := coerce.format_lbw(r["lengths_behind"])):
        item["margin"] = m
    if (p := _price(r["win_odds"])) is not None:
        item["win_odds_close"] = p
    if place_close is not None:
        item["place_odds_close"] = place_close
    if tags := incident_tags(r["comment_text"]):
        item["incidents"] = tags
    return item


def results(date: str, venue: str, *,
            conn: Connection | None = None) -> dict[str, Any]:
    """Every race run so far: finishing order, margins, closing win and place
    odds, stewards' tags, and the win and place dividends per HK$10.

    The closing odds are there for the fund's Classic, struck at FIXED prices
    on Australian books: a leg is measured against the tote's dividend when it
    paid and against the tote's closing price when it did not.

    A withdrawn runner is listed with no position and `scratched`, so a void
    leg can be settled from this alone. Six Up dividends are not captured by
    hkrd, so `pool_dividends` is left out rather than sent empty.
    """
    date = parse_day(date, "date")
    with reading(conn) as c:
        check_meeting(c, date, venue)
        rows = c.execute("""
            WITH run AS (SELECT DISTINCT race_no FROM runners
                          WHERE race_date = ? AND place IS NOT NULL)
            SELECT r.race_no, r.horse_no, r.place, r.place_code,
                   r.lengths_behind, r.win_odds, k.comment_text
              FROM runners r
              JOIN run ON run.race_no = r.race_no
              LEFT JOIN runner_comments k
                ON k.race_date = r.race_date AND k.race_no = r.race_no
               AND k.horse_no = r.horse_no AND k.source = 'incident'
             WHERE r.race_date = ?
             ORDER BY r.race_no, r.place IS NULL, r.place, r.horse_no""",
            (date, date)).fetchall()
        paid = c.execute(
            "SELECT race_no, pool, combination, dividend_per_10 FROM dividends "
            "WHERE race_date = ? AND pool IN ('WIN', 'PLACE') "
            "ORDER BY race_no, pool, dividend_per_10 DESC", (date,)).fetchall()
        places = {n: closing_place(c, date, n) for n in {r["race_no"] for r in rows}}
        updated = last_read(c, RESULT_JOBS)
    if not rows:
        raise NotHeld(f"no results yet for {date} {venue}")

    races: dict[int, dict[str, Any]] = {}
    for r in rows:
        race = races.setdefault(r["race_no"], {"race_no": r["race_no"], "finish": [],
                                               "win_dividends": [], "place_dividends": []})
        line = {"horse_no": r["horse_no"],
                **_finish(r, places[r["race_no"]].get(r["horse_no"]))}
        if r["place_code"] in SCRATCHED:
            line["scratched"] = True
        race["finish"].append(line)
    for d in paid:
        if d["race_no"] in races and d["dividend_per_10"] is not None:
            key = "win_dividends" if d["pool"] == "WIN" else "place_dividends"
            races[d["race_no"]][key].append({"horse_no": int(d["combination"]),
                                             "per_10_hkd": d["dividend_per_10"]})
    return envelope("results", updated, {"meeting": {"date": date, "venue": venue},
                                         "results": list(races.values())})


def horse_runs(brand_no: str, since: str, *,
               conn: Connection | None = None) -> dict[str, Any]:
    """A horse's runs on or after `since`, found by brand number.

    The brand number names the horse; the runs are read by NAME, the one join
    key that holds across the whole archive. A withdrawal is not a run. Field
    size is the race's starters, grouped once rather than counted per row.
    """
    since = parse_day(since, "since")
    with reading(conn) as c:
        names = [r[0] for r in c.execute(
            "SELECT horse_name FROM horse_name_zh WHERE brand_no = ? "
            "UNION SELECT horse_name FROM horse_background WHERE brand_no = ?",
            (brand_no, brand_no))]
        if not names:
            raise NotHeld(f"no horse with brand number {brand_no}")
        who = ",".join("?" * len(names))
        gone = ",".join("?" * len(SCRATCHED))
        rows = c.execute(f"""
            WITH mine AS (
              SELECT race_date, race_no, horse_no, draw, place, place_code,
                     lengths_behind, win_odds
                FROM runners
               WHERE horse_name IN ({who}) AND race_date >= ?
                 AND (place IS NOT NULL
                      OR (place_code IS NOT NULL AND place_code NOT IN ({gone})))),
            field AS (
              SELECT r.race_date, r.race_no, count(*) AS n
                FROM runners r
                JOIN mine m ON m.race_date = r.race_date AND m.race_no = r.race_no
               WHERE r.place_code IS NULL OR r.place_code NOT IN ({gone})
               GROUP BY r.race_date, r.race_no)
            SELECT m.*, a.venue, a.distance, a.race_class, f.n AS field_size,
                   k.comment_text
              FROM mine m
              JOIN races a ON a.race_date = m.race_date AND a.race_no = m.race_no
              JOIN field f ON f.race_date = m.race_date AND f.race_no = m.race_no
              LEFT JOIN runner_comments k
                ON k.race_date = m.race_date AND k.race_no = m.race_no
               AND k.horse_no = m.horse_no AND k.source = 'incident'
             ORDER BY m.race_date, m.race_no""",
            (*names, since, *SCRATCHED, *SCRATCHED)).fetchall()
        places = {(r["race_date"], r["race_no"]): closing_place(c, r["race_date"], r["race_no"])
                  for r in rows}
        updated = last_read(c, RESULT_JOBS)

    runs = []
    for r in rows:
        run: dict[str, Any] = {"date": r["race_date"], "venue": r["venue"],
                               "race_no": r["race_no"], "draw": r["draw"],
                               "field_size": r["field_size"]}
        if r["distance"] is not None:
            run["distance"] = r["distance"]
        if cls := class_text(r["race_class"]):
            run["class"] = cls
        close = places[(r["race_date"], r["race_no"])].get(r["horse_no"])
        runs.append({**run, **_finish(r, close)})
    return envelope("runs", updated, {"brand_no": brand_no, "runs": runs})
