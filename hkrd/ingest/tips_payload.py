"""The tips payload — validated and normalised, never trusted.

The PC side (`tools/extract_tips.py`) posts one JSON object per meeting. This
checks its SHAPE and returns plain dicts; it does not know the database
exists, so whether race 4 is on the card is `jobs/import_tips`'s question, and
coercing a value into its column type is `store/tips`'s.

Everything here rejects the WHOLE payload, and says every reason at once. A
payload that breaks the contract is a bug in the extractor, not data, and
storing the parts that happened to be well formed would hide it. The rules
that quarantine one row and keep the rest need the card, so they live in the
job. docs/handover/tips-layer/SPEC.md §3.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["PayloadError", "Payload", "parse", "PAYLOAD_VERSION", "ROLES",
           "CAPTION_KINDS", "STANCES", "REASONS"]

PAYLOAD_VERSION = 1

# Closed vocabularies, where the screen draws something different per value.
# A typo'd caption_kind would silently lose the "names may be wrong" marker
# that every ASR quote has to carry. `topic` and `source` stay open.
ROLES = frozenset({"trainer", "jockey", "presenter", "analyst"})
CAPTION_KINDS = frozenset({"manual", "asr"})
STANCES = frozenset({"positive", "negative", "neutral"})
REASONS = frozenset({"no_runner", "no_race", "name_unknown", "name_mismatch",
                     "low_confidence", "unparsed"})

_TOP = frozenset({"payload_version", "race_date", "generated_at", "extractor",
                  "sources", "quotes", "selections", "quarantine"})
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_QUOTE_FIELDS = ("source", "role", "quote", "url", "extracted_by", "race_no",
                 "horse_no", "horse_said", "speaker", "quote_en", "topic",
                 "stance", "video_id", "t_start", "caption_kind",
                 "confidence", "fetched_at")
_SELECTION_FIELDS = ("source", "tipster", "race_no", "horse_no", "pick_rank",
                     "note", "name_seen", "caption_kind", "url",
                     "published_at", "fetched_at")
_QUARANTINE_FIELDS = ("source", "race_no", "raw", "reason", "url",
                      "fetched_at")


class PayloadError(ValueError):
    """The payload breaks the contract. Carries every reason, not the first."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        shown = "; ".join(self.problems[:12])
        more = len(self.problems) - 12
        super().__init__(shown + (f"; and {more} more" if more > 0 else ""))


@dataclass(frozen=True)
class Payload:
    """`sources` is what this payload is the WHOLE ANSWER for at its meeting:
    anything stored earlier under one of them and absent here is withdrawn.
    Declared by the extractor, or else every source its rows name — so a
    source that failed to harvest, and so sent nothing, is left untouched."""

    race_date: str
    generated_at: str
    extractor: str | None
    sources: list[str]
    quotes: list[dict[str, Any]]
    selections: list[dict[str, Any]]
    quarantine: list[dict[str, Any]]


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _text(row: Mapping, key: str, where: str, bad: list[str], *,
          required: bool = False) -> None:
    v = row.get(key)
    if v is None:
        if required:
            bad.append(f"{where}: missing {key}")
    elif not isinstance(v, str) or (required and not v.strip()):
        bad.append(f"{where}: {key} must be a non-empty string, got {v!r}")


def _optional_int(row: Mapping, key: str, where: str, bad: list[str]) -> None:
    v = row.get(key)
    if v is not None and not _is_int(v):
        bad.append(f"{where}: {key} must be an integer or null, got {v!r}")


def _one_of(row: Mapping, key: str, allowed: frozenset, where: str,
            bad: list[str]) -> None:
    v = row.get(key)
    if v is not None and v not in allowed:
        bad.append(f"{where}: {key} {v!r} is not one of {sorted(allowed)}")


def _rows(body: Mapping, key: str, bad: list[str]) -> list[Mapping]:
    v = body.get(key, [])
    if v is None:
        return []
    if not isinstance(v, list):
        bad.append(f"{key} must be a list, got {type(v).__name__}")
        return []
    out = []
    for i, row in enumerate(v):
        if isinstance(row, Mapping):
            out.append(row)
        else:
            bad.append(f"{key}[{i}] must be an object, got {type(row).__name__}")
    return out


def _same_meeting(row: Mapping, race_date: str, where: str,
                  bad: list[str]) -> None:
    """One payload is one meeting; a row naming another date is a mistake."""
    v = row.get("race_date")
    if v is not None and v != race_date:
        bad.append(f"{where}: race_date {v!r} is not the payload's {race_date}")


def parse(body: object) -> Payload:
    """Validate a posted payload. Raises PayloadError naming every fault."""
    if not isinstance(body, Mapping):
        raise PayloadError([f"payload must be a JSON object, got "
                            f"{type(body).__name__}"])
    bad: list[str] = []
    # A leading underscore is the fixture's own provenance, and ignored. Any
    # other unknown key is a misspelling of a known one until proven not.
    for key in sorted(k for k in body if k not in _TOP
                      and not str(k).startswith("_")):
        bad.append(f"unknown top-level key {key!r}")

    version = body.get("payload_version")
    if not _is_int(version) or version != PAYLOAD_VERSION:
        # Hard reject rather than best effort: a version this code has never
        # seen means fields it does not know the meaning of.
        raise PayloadError([f"payload_version must be {PAYLOAD_VERSION}, "
                            f"got {version!r}", *bad])

    race_date = body.get("race_date")
    try:
        if not (isinstance(race_date, str) and _DATE.match(race_date)):
            raise ValueError
        dt.date.fromisoformat(race_date)
    except ValueError:
        bad.append(f"race_date must be YYYY-MM-DD, got {race_date!r}")
        race_date = ""

    generated_at = body.get("generated_at")
    try:
        # Stored as every row's fetched_at, so a re-push is byte-identical.
        # A clock read here instead would make the same payload twice two
        # different rows' worth of timestamps.
        if not isinstance(generated_at, str):
            raise ValueError
        dt.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        bad.append(f"generated_at must be an ISO-8601 timestamp, got "
                   f"{generated_at!r}")
    _text(body, "extractor", "payload", bad)

    quotes = _rows(body, "quotes", bad)
    selections = _rows(body, "selections", bad)
    quarantine = _rows(body, "quarantine", bad)

    for i, q in enumerate(quotes):
        w = f"quotes[{i}]"
        for key in ("source", "role", "quote", "url", "extracted_by"):
            _text(q, key, w, bad, required=True)
        for key in ("horse_said", "speaker", "quote_en", "topic", "video_id",
                    "fetched_at"):
            _text(q, key, w, bad)
        _optional_int(q, "race_no", w, bad)
        _optional_int(q, "horse_no", w, bad)
        _one_of(q, "role", ROLES, w, bad)
        _one_of(q, "caption_kind", CAPTION_KINDS, w, bad)
        _one_of(q, "stance", STANCES, w, bad)
        conf = q.get("confidence")
        if conf is not None and not (_is_num(conf) and 0 <= conf <= 1):
            bad.append(f"{w}: confidence must be 0..1, got {conf!r}")
        t = q.get("t_start")
        if t is not None and not (_is_num(t) and t >= 0):
            bad.append(f"{w}: t_start must be seconds >= 0, got {t!r}")
        _same_meeting(q, race_date, w, bad)

    for i, s in enumerate(selections):
        w = f"selections[{i}]"
        _text(s, "source", w, bad, required=True)
        _text(s, "tipster", w, bad, required=True)
        for key in ("race_no", "horse_no"):
            if s.get(key) is None:
                bad.append(f"{w}: missing {key}")
            else:
                _optional_int(s, key, w, bad)
        rank = s.get("pick_rank")
        if rank is not None and not (_is_int(rank) and 1 <= rank <= 8):
            bad.append(f"{w}: pick_rank must be 1..8, got {rank!r}")
        # A pick HEARD through speech-to-text is recorded (Brett, 2026-09-22,
        # reversing SPEC §6) and must say so, so the card can mark it.
        _one_of(s, "caption_kind", CAPTION_KINDS, w, bad)
        for key in ("note", "name_seen", "url", "published_at", "fetched_at"):
            _text(s, key, w, bad)
        _same_meeting(s, race_date, w, bad)

    for i, r in enumerate(quarantine):
        w = f"quarantine[{i}]"
        _text(r, "source", w, bad, required=True)
        _text(r, "raw", w, bad, required=True)
        _text(r, "reason", w, bad, required=True)
        _one_of(r, "reason", REASONS, w, bad)
        _optional_int(r, "race_no", w, bad)
        _text(r, "url", w, bad)
        _same_meeting(r, race_date, w, bad)

    rows_name = sorted({r["source"] for r in (*quotes, *selections, *quarantine)
                        if isinstance(r.get("source"), str)})
    declared = body.get("sources")
    if declared is None:
        sources = rows_name
    elif (not isinstance(declared, list)
          or not all(isinstance(x, str) and x.strip() for x in declared)):
        bad.append(f"sources must be a list of source names, got {declared!r}")
        sources = rows_name
    else:
        sources = sorted(set(declared))
        # A row from a source the payload does not claim to be complete for
        # is a payload that disagrees with itself about what it holds.
        stray = sorted(set(rows_name) - set(sources))
        if stray:
            bad.append(f"rows name sources not in `sources`: {stray}")

    if bad:
        raise PayloadError(bad)

    def normal(row: Mapping, fields: tuple[str, ...]) -> dict[str, Any]:
        out = {k: row.get(k) for k in fields}
        out["race_date"] = race_date
        out["fetched_at"] = row.get("fetched_at") or generated_at
        return out

    return Payload(
        race_date=race_date, generated_at=generated_at,
        extractor=body.get("extractor"), sources=sources,
        quotes=[normal(q, _QUOTE_FIELDS) for q in quotes],
        selections=[normal(s, _SELECTION_FIELDS) for s in selections],
        quarantine=[normal(r, _QUARANTINE_FIELDS) for r in quarantine])
