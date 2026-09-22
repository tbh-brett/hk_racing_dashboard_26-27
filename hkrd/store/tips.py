"""The tips layer's write path, and the lookups its import resolves against.

Four tables: `horse_name_zh`, `connections_quote`, `tipster_selection` and
`tips_quarantine`. Types are coerced here, on the way in, as everywhere else.

THE LATEST PUSH IS THE ANSWER, which is the opposite of `upsert._upsert`. That
writer keeps a stored value when the incoming row has a NULL, because a
narrower scrape arriving later means "this source does not carry the field".
Here a NULL arriving later means the extractor looked again and could not pin
the quote to a runner — and keeping the old number would be keeping exactly
the guess the NULL withdrew. So every column is overwritten on conflict, and
whatever a source said about a meeting and no longer says — left out of its
latest push, or quarantined by it — is removed (`replace_absent`).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any

from . import coerce

__all__ = [
    "quote_id", "quarantine_id", "canonical_raw",
    "upsert_horse_names", "upsert_quotes", "upsert_selections",
    "upsert_quarantine", "quarantine_row_id", "replace_absent",
    "meeting_card", "meeting_venue", "meeting_names_zh",
    "dates_missing_names",
]

Row = dict[str, Any]

QUOTE_COLS = ("quote_id", "race_date", "race_no", "horse_no", "horse_said",
              "speaker", "role", "quote", "quote_en", "topic", "stance",
              "source", "video_id", "t_start", "caption_kind", "confidence",
              "extracted_by", "url", "fetched_at")
SELECTION_KEY = ("source", "tipster", "race_date", "race_no", "horse_no")
SELECTION_COLS = (*SELECTION_KEY, "pick_rank", "note", "name_seen",
                  "caption_kind", "url", "published_at", "fetched_at")
QUARANTINE_COLS = ("quarantine_id", "source", "race_date", "race_no", "raw",
                   "reason", "url", "fetched_at")


# ── keys ─────────────────────────────────────────────────────────────────────

def quote_id(row: Row) -> str:
    """'<video_id>:<int(t_start)>', so a re-push lands on the same row.

    A quote with no timestamp has no place in its video to be keyed by, and
    falls back to a hash of what it says and where it came from.
    """
    if row.get("video_id") and row.get("t_start") is not None:
        return f"{row['video_id']}:{int(float(row['t_start']))}"
    digest = hashlib.sha1("\x00".join(
        str(row.get(k) or "") for k in ("source", "url", "quote")
    ).encode("utf-8")).hexdigest()
    return f"h:{digest[:16]}"


def canonical_raw(row: Row) -> str:
    """One text per row, whatever order its keys arrived in."""
    return json.dumps(row, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def quarantine_id(source: str, raw: str) -> str:
    return hashlib.sha1(f"{source}\x00{raw}".encode("utf-8")).hexdigest()


# ── coercion ─────────────────────────────────────────────────────────────────

def _text(value: object) -> str | None:
    s = str(value).strip() if value is not None else ""
    return s or None


def _real(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise coerce.CoerceError(
            f"{field}: expected a number, got {value!r}") from None


def _date(value: object) -> str:
    out = coerce.to_date(value)
    if out is None:
        raise coerce.CoerceError("race_date: missing")
    return out


# ── writes ───────────────────────────────────────────────────────────────────

def _overwrite(conn: sqlite3.Connection, table: str, cols: Sequence[str],
               keys: Sequence[str], rows: Sequence[tuple]) -> int:
    if not rows:
        return 0
    sets = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in keys)
    conn.executemany(
        f"INSERT INTO {table} ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)}) "
        f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {sets}", rows)
    return len(rows)


def upsert_horse_names(conn: sqlite3.Connection, rows: Sequence[Row]) -> int:
    """English -> Chinese pairs. `seen_at` only ever moves forward, so a
    re-read of an older card cannot make a name look older than it is."""
    prepared = [(
        (r.get("horse_name") or "").strip().upper(),
        _text(r.get("name_zh")),
        _text(r.get("brand_no")),
        _text(r.get("source")),
        _date(r.get("seen_at")),
    ) for r in rows]
    if not prepared:
        return 0
    conn.executemany(
        "INSERT INTO horse_name_zh (horse_name, name_zh, brand_no, source, "
        "  seen_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (horse_name) DO UPDATE SET "
        "  name_zh = excluded.name_zh, brand_no = excluded.brand_no, "
        "  source = excluded.source, "
        "  seen_at = max(excluded.seen_at, horse_name_zh.seen_at)", prepared)
    return len(prepared)


def upsert_quotes(conn: sqlite3.Connection, rows: Sequence[Row]) -> int:
    prepared = [(
        quote_id(r), _date(r.get("race_date")),
        coerce.to_int(r.get("race_no"), field="race_no"),
        coerce.to_int(r.get("horse_no"), field="horse_no"),
        _text(r.get("horse_said")), _text(r.get("speaker")),
        _text(r.get("role")), _text(r.get("quote")), _text(r.get("quote_en")),
        _text(r.get("topic")), _text(r.get("stance")), _text(r.get("source")),
        _text(r.get("video_id")), _real(r.get("t_start"), field="t_start"),
        _text(r.get("caption_kind")),
        _real(r.get("confidence"), field="confidence"),
        _text(r.get("extracted_by")), _text(r.get("url")),
        _text(r.get("fetched_at")),
    ) for r in rows]
    return _overwrite(conn, "connections_quote", QUOTE_COLS, ("quote_id",),
                      prepared)


def upsert_selections(conn: sqlite3.Connection, rows: Sequence[Row]) -> int:
    prepared = [(
        _text(r.get("source")), _text(r.get("tipster")),
        _date(r.get("race_date")),
        coerce.to_int(r.get("race_no"), field="race_no"),
        coerce.to_int(r.get("horse_no"), field="horse_no"),
        coerce.to_int(r.get("pick_rank"), field="pick_rank"),
        _text(r.get("note")), _text(r.get("name_seen")),
        _text(r.get("caption_kind")), _text(r.get("url")),
        _text(r.get("published_at")), _text(r.get("fetched_at")),
    ) for r in rows]
    return _overwrite(conn, "tipster_selection", SELECTION_COLS,
                      SELECTION_KEY, prepared)


def quarantine_row_id(row: Row) -> str:
    """The same failure pushed twice is one row.

    The id hashes `identity` where the row has one, else `raw`. A row the
    extractor quarantined is its own text, so the text is its identity. A
    quote or selection the IMPORT quarantined is identified by its key
    instead: its raw JSON carries the extractor's timestamp and confidence,
    and hashing those would mint a new row for the same failure on every
    re-extraction — a count that climbs with nothing new going wrong.
    """
    return quarantine_id(_text(row.get("source")) or "",
                         str(row.get("identity") or row.get("raw") or ""))


def upsert_quarantine(conn: sqlite3.Connection, rows: Sequence[Row]) -> int:
    prepared = [(
        quarantine_row_id(r), _text(r.get("source")),
        coerce.to_date(r.get("race_date")),
        coerce.to_int(r.get("race_no"), field="race_no"),
        str(r.get("raw") or ""), _text(r.get("reason")), _text(r.get("url")),
        _text(r.get("fetched_at"))) for r in rows]
    return _overwrite(conn, "tips_quarantine", QUARANTINE_COLS,
                      ("quarantine_id",), prepared)


def replace_absent(conn: sqlite3.Connection, date: str,
                   sources: Sequence[str], *, quote_ids: Iterable[str],
                   selection_keys: Iterable[Sequence[Any]],
                   quarantine_ids: Iterable[str]) -> int:
    """Remove what these sources said about this meeting and no longer say.

    The latest push for a source is its whole answer (Brett, 2026-09-22): a
    quote left out of it, or quarantined by it, goes; so does a quarantine
    row for a failure that is fixed, or that the source no longer produces.
    Other sources, and other meetings, are not touched. Returns how many
    quotes and selections went — the quarantine is bookkeeping, not tips.
    """
    if not sources:
        return 0
    marks = ", ".join("?" for _ in sources)
    keep_q = set(quote_ids)
    keep_s = {tuple(k) for k in selection_keys}
    keep_x = set(quarantine_ids)
    args = (date, *sources)

    gone_q = [(r[0],) for r in conn.execute(
        "SELECT quote_id FROM connections_quote WHERE race_date = ? "
        f"AND source IN ({marks})", args) if r[0] not in keep_q]
    gone_s = [tuple(r) for r in conn.execute(
        f"SELECT {', '.join(SELECTION_KEY)} FROM tipster_selection "
        f"WHERE race_date = ? AND source IN ({marks})", args)
        if tuple(r) not in keep_s]
    gone_x = [(r[0],) for r in conn.execute(
        "SELECT quarantine_id FROM tips_quarantine WHERE race_date = ? "
        f"AND source IN ({marks})", args) if r[0] not in keep_x]

    conn.executemany("DELETE FROM connections_quote WHERE quote_id = ?", gone_q)
    conn.executemany(
        "DELETE FROM tipster_selection WHERE "
        + " AND ".join(f"{k} = ?" for k in SELECTION_KEY), gone_s)
    conn.executemany("DELETE FROM tips_quarantine WHERE quarantine_id = ?",
                     gone_x)
    return len(gone_q) + len(gone_s)


# ── what an import resolves against ──────────────────────────────────────────

def meeting_card(conn: sqlite3.Connection, date: str
                 ) -> dict[int, dict[int, str]]:
    """race_no -> {horse_no: horse_name} for one meeting. A race with no
    runners stored is present with an empty field, not missing."""
    card: dict[int, dict[int, str]] = {}
    for r in conn.execute(
            "SELECT r.race_no, u.horse_no, u.horse_name FROM races r "
            "LEFT JOIN runners u ON u.race_date = r.race_date "
            "  AND u.race_no = r.race_no WHERE r.race_date = ?", (date,)):
        field = card.setdefault(r["race_no"], {})
        if r["horse_no"] is not None:
            field[r["horse_no"]] = r["horse_name"]
    return card


def meeting_venue(conn: sqlite3.Connection, date: str) -> str | None:
    row = conn.execute("SELECT venue FROM races WHERE race_date = ? "
                       "AND venue IS NOT NULL LIMIT 1", (date,)).fetchone()
    return row["venue"] if row else None


def meeting_names_zh(conn: sqlite3.Connection, date: str
                     ) -> dict[tuple[int, int], str | None]:
    """(race_no, horse_no) -> the runner's Chinese name, None where unknown."""
    return {(r["race_no"], r["horse_no"]): r["name_zh"] for r in conn.execute(
        "SELECT u.race_no, u.horse_no, z.name_zh FROM runners u "
        "LEFT JOIN horse_name_zh z ON z.horse_name = u.horse_name "
        "WHERE u.race_date = ?", (date,))}


def dates_missing_names(conn: sqlite3.Connection, since: str) -> list[str]:
    """Meetings from `since` on with a runner whose Chinese name is unknown."""
    return [r["race_date"] for r in conn.execute(
        "SELECT DISTINCT u.race_date FROM runners u "
        "LEFT JOIN horse_name_zh z ON z.horse_name = u.horse_name "
        "WHERE u.race_date >= ? AND z.horse_name IS NULL "
        "ORDER BY u.race_date", (since,))]
