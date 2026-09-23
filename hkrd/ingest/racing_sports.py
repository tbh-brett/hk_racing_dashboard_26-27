"""Racing & Sports: the one tipster behind two bookmakers' previews.

Ladbrokes' race comment and Sportsbet's "Expert Tips by Racing & Sports" are
the same words and the same four selections in the same order — measured on
all nine races at Happy Valley on 2026-09-23. Sportsbet names the provider
(`tippedSelectionProvider: R_AND_S`); Ladbrokes does not. Counted twice they
would read as two sources agreeing, when it is one opinion carried by two
shops, so both feeds write them under one source, and the latest capture of
either replaces the other.

Three things per race, as plain rows for `jobs/import_tips`:

  the tips          the four selections, first pick first, each with the
                    part of the comment about that horse
  the race comment  the whole paragraph, stored against the race
  runner comments   a line on EVERY runner, which only Sportsbet carries
                    ("Has had one run back from a break …"). Form, not
                    support: they go under their own source so that a
                    Ladbrokes capture, which has none, never withdraws them
"""
from __future__ import annotations

import re
from typing import Any

__all__ = ["SOURCE", "FORM_SOURCE", "TIPSTER", "reasons", "selections",
           "race_comment", "runner_comments"]

SOURCE = "racing_sports"
FORM_SOURCE = "racing_sports_form"
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


def runner_comments(race_no: int, runners: list[dict[str, Any]], url: str,
                    *, extractor: str) -> list[dict[str, Any]]:
    """One quote per runner that has a comment. Each of `runners` is
    {"horse_no", "name", "comment"}."""
    return [{"source": FORM_SOURCE, "role": "analyst", "speaker": TIPSTER,
             "race_no": race_no, "horse_no": r["horse_no"],
             "horse_said": (r["name"] or "").upper(),
             "quote": re.sub(r"\s+", " ", r["comment"]).strip(),
             "url": url, "extracted_by": extractor}
            for r in runners if (r.get("comment") or "").strip()]
