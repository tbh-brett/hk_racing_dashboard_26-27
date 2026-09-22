"""Reads for the tips layer.

One so far: the ROSTER, which is what `tools/extract_tips.py` resolves
against. The extractor runs on the PC and must pick runners from the card
this database actually holds — the same card `jobs/import_tips` will check its
numbers against — rather than from a card it fetched for itself, or from
nothing at all. The two panel reads (SPEC §8) join this module in phase 4.
"""
from __future__ import annotations

from typing import Any

from hkrd.store.connect import Connection, get_conn

__all__ = ["roster"]


def roster(date: str, *, conn: Connection | None = None) -> dict[str, Any]:
    """Every declared runner at one meeting, with its Chinese name if known.

    `named` against `runners` says how much of the card the Chinese name
    sync has reached: a Chinese source can only be resolved against names
    this table holds, so a card with none is worth knowing about before
    extracting from it rather than after.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute(
            "SELECT r.race_no, r.venue, u.horse_no, u.horse_name, u.jockey, "
            "       u.trainer, z.name_zh "
            "  FROM races r "
            "  LEFT JOIN runners u ON u.race_date = r.race_date "
            "       AND u.race_no = r.race_no "
            "  LEFT JOIN horse_name_zh z ON z.horse_name = u.horse_name "
            " WHERE r.race_date = ? ORDER BY r.race_no, u.horse_no",
            (date,)).fetchall()
    finally:
        if own:
            conn.close()

    races: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        field = races.setdefault(r["race_no"], [])
        if r["horse_no"] is not None:
            field.append({"horse_no": r["horse_no"],
                          "horse_name": r["horse_name"],
                          "name_zh": r["name_zh"], "jockey": r["jockey"],
                          "trainer": r["trainer"]})
    runners = [x for field in races.values() for x in field]
    return {"race_date": date,
            "venue": next((r["venue"] for r in rows if r["venue"]), None),
            "races": [{"race_no": n, "runners": field}
                      for n, field in sorted(races.items())],
            "runners": len(runners),
            "named": sum(1 for x in runners if x["name_zh"])}
