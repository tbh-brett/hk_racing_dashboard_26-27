"""Racing & Sports: the one tipster behind two bookmakers' previews.

Ladbrokes' race comment is Racing & Sports' preview: the same words and the
same four selections Sportsbet printed under that name — measured on all nine
races at Happy Valley on 2026-09-23. Sportsbet was read from the PC until it
refused it on 24 Sep, and was taken out on 30 Sep (Brett: one source, not
two); Ladbrokes answers the server, so Racing & Sports comes from there.

Two things per race, as plain rows for `jobs/import_tips`:

  the tips          the four selections, first pick first, each with the
                    part of the comment about that horse
  the race comment  the whole paragraph, stored against the race

Sportsbet alone also carried a line on every runner; Ladbrokes' feed has the
field (`form_comment`) and leaves it empty for Hong Kong, so that line is gone.
"""
from __future__ import annotations

import re
from typing import Any

__all__ = ["SOURCE", "TIPSTER", "reasons", "selections", "race_comment"]

SOURCE = "racing_sports"
TIPSTER = "Racing & Sports"
_NAMED = re.compile(r"([A-Z][A-Z0-9'’&.\- ]{1,40}?)\s*\((\d{1,2})\)")


def reasons(comment: str) -> dict[int, dict[str, str]]:
    """horse number -> {"name", "text"}: each horse the comment names, and
    what it says about it up to the next horse named."""
    marks = list(_NAMED.finditer(comment or ""))
    out: dict[int, dict[str, str]] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(comment)
        no = int(m.group(2))
        if no not in out:
            out[no] = {"name": m.group(1).strip(),
                       "text": re.sub(r"\s+", " ", comment[m.start():end]).strip()}
    return out


def selections(race_no: int, tipped: list[dict[str, Any]], comment: str,
               url: str) -> list[dict[str, Any]]:
    """Payload selections from the tipped runners, in order. Each of
    `tipped` is {"horse_no", "name"} as the bookmaker numbers and spells
    it; the name goes along as the checksum `import_tips` tests."""
    said = reasons(comment)
    return [{"source": SOURCE, "tipster": TIPSTER, "race_no": race_no,
             "horse_no": t["horse_no"], "pick_rank": rank,
             "name_seen": (said.get(t["horse_no"], {}).get("name")
                           or t["name"] or "").upper(),
             "note": said.get(t["horse_no"], {}).get("text"), "url": url}
            for rank, t in enumerate(tipped, start=1)]


def race_comment(race_no: int, comment: str, url: str, *,
                 extractor: str) -> list[dict[str, Any]]:
    """The race's whole comment as one quote on the race, or nothing."""
    text = re.sub(r"\s+", " ", comment or "").strip()
    if not text:
        return []
    return [{"source": SOURCE, "role": "analyst", "speaker": TIPSTER,
             "race_no": race_no, "horse_no": None, "quote": text,
             "url": url, "extracted_by": extractor}]
