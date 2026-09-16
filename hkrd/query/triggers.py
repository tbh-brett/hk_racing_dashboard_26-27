"""The conditions a blackbook thesis depends on — and whether a run met them.

`docs/proposal-blackbook.md` §2.1. A blackbook entry says a horse will run
better than its public form suggests, and almost always that claim comes with
circumstances attached: at a trip, on a surface, from a draw, under a rider.
The prose said so and nothing could read it, so every run counted equally
against the thesis. A horse booked for 1200m and beaten four times at 1650m
looked like a failed thesis; it is an untested one.

WHY THIS IS NOT NEW DATA. Every `kind` below is a column the archive already
holds and `query/slices.DIMENSIONS` already groups by. A trigger is a filter
over the run, written in the entry rather than typed into a page — so nothing
has to be derived, scraped or guessed to support it, and a condition means the
same thing here as it does on Lookup.

`pref_distance`, `pref_surface` and `pref_jockey` were the first attempt at
this. They were written by the legacy import and read by NOTHING — the only two
lines in the codebase naming them were the two that wrote them. They migrate
into this table, where something reads them.

DAYS SINCE THE LAST RUN IS NOT HERE. It is a real condition and a common one,
and it is deliberately deferred: it is not a column on the run, so it costs a
correlated lookup per row on a table this is joined against thousands of times.
`query/freshness` is where it would come from. Better absent than quietly slow.
"""
from __future__ import annotations

from typing import Any

from hkrd.store.connect import Connection, get_conn

__all__ = ["KINDS", "OPS", "met_sql", "for_entries", "describe", "validate",
           "NUMERIC_KINDS"]

# What a condition can be ABOUT. The value is the SQL that reads it off a run,
# with `{race}` and `{runner}` filled in by `met_sql` — one place, so a trigger
# on `going` cannot come to mean two things.
KINDS: dict[str, str] = {
    "distance":   "{race}.distance",
    "surface":    "{race}.surface",
    "going":      "{race}.going",
    "course":     "{race}.course",
    "venue":      "{race}.venue",
    "class":      "{race}.race_class",
    "draw":       "{runner}.draw",
    "jockey":     "{runner}.jockey",
    "trainer":    "{runner}.trainer",
    "field_size": "{field_size}",
}

# The ones compared as numbers. Everything else is compared as text, folded to
# lower case — "Turf" typed into a form and "TURF" off the scrape are the same
# condition, and a trigger that failed on capitalisation would be the worst
# kind of bug here: silent, and it makes the horse disappear.
NUMERIC_KINDS = frozenset({"distance", "draw", "field_size"})

OPS = ("is", "in", "<=", ">=", "between")


def validate(kind: str, op: str, value: str) -> None:
    """Refuse a condition that cannot be evaluated, at the point it is written.

    A trigger nothing can check is worse than no trigger: it silently never
    matches, so the horse never appears and the book looks empty rather than
    broken.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if op not in OPS:
        raise ValueError(f"op must be one of {', '.join(OPS)}")
    value = (value or "").strip()
    if not value:
        raise ValueError("a condition needs a value")
    if op in ("<=", ">=") or (op == "between"):
        parts = value.split("-") if op == "between" else [value]
        if op == "between" and len(parts) != 2:
            raise ValueError("a 'between' value looks like '1200-1400'")
        for part in parts:
            try:
                float(part.strip())
            except ValueError:
                raise ValueError(
                    f"'{part.strip()}' is not a number, and '{op}' compares "
                    "numbers") from None
    if op in ("<=", ">=", "between") and kind not in NUMERIC_KINDS:
        raise ValueError(f"'{op}' needs a numeric condition; {kind} is text — "
                         "use 'is' or 'in'")


def met_sql(*, entry: str = "b", runner: str = "r", race: str = "a",
            field_size: str = "NULL", trigger: str = "g") -> str:
    """SQL that is true when a run satisfies EVERY condition on an entry.

    Read as: there is no condition on this entry that this run fails. An entry
    with no conditions is met by every run, which is what the whole book looked
    like before this existed and is the right default — a thesis with no stated
    circumstances is a claim about the horse.

    `NULL` is a FAILURE, not a pass. A race whose distance was never scraped
    cannot be shown to be the 1200m the entry asked for, and treating an
    unknown as a match is how a page quietly credits a thesis with runs it
    never had. `query/gear` draws the same line: a NULL column is "this scrape
    did not carry it", never "there was none".
    """
    actual = "\n             ".join(
        f"WHEN '{kind}' THEN {expr.format(race=race, runner=runner, field_size=field_size)}"
        for kind, expr in KINDS.items())

    # `in` is a csv membership test, done on the value rather than by splitting
    # it: ',1200,1400,' LIKE '%,1200,%'. Both sides are wrapped in commas so
    # '400' cannot match inside '1400'.
    return f"""
    NOT EXISTS (
      SELECT 1 FROM blackbook_trigger {trigger}
       WHERE {trigger}.id = {entry}.id
         AND NOT (
           CASE {trigger}.op
             WHEN 'is' THEN lower(trim({trigger}.value)) = lower(trim(CAST(
               CASE {trigger}.kind
             {actual}
               END AS TEXT)))
             WHEN 'in' THEN
               ',' || replace(lower(trim({trigger}.value)), ', ', ',') || ','
               LIKE '%,' || lower(trim(CAST(
                 CASE {trigger}.kind
             {actual}
                 END AS TEXT))) || ',%'
             WHEN '<=' THEN CAST(
               CASE {trigger}.kind
             {actual}
               END AS REAL) <= CAST({trigger}.value AS REAL)
             WHEN '>=' THEN CAST(
               CASE {trigger}.kind
             {actual}
               END AS REAL) >= CAST({trigger}.value AS REAL)
             WHEN 'between' THEN CAST(
               CASE {trigger}.kind
             {actual}
               END AS REAL) BETWEEN
                 CAST(substr({trigger}.value, 1,
                             instr({trigger}.value, '-') - 1) AS REAL)
                 AND CAST(substr({trigger}.value,
                                 instr({trigger}.value, '-') + 1) AS REAL)
           END IS 1
         )
    )"""


def for_entries(ids: list[str] | None = None, *, conn: Connection | None = None
                ) -> dict[str, list[dict[str, Any]]]:
    """Every entry's conditions, keyed by entry id. One query, not one each."""
    own = conn is None
    conn = conn or get_conn()
    try:
        sql = ("SELECT trigger_id, id, kind, op, value FROM blackbook_trigger")
        params: tuple = ()
        if ids is not None:
            if not ids:
                return {}
            sql += f" WHERE id IN ({','.join('?' * len(ids))})"
            params = tuple(ids)
        sql += " ORDER BY id, trigger_id"
        out: dict[str, list[dict[str, Any]]] = {}
        for row in conn.execute(sql, params):
            out.setdefault(row["id"], []).append(dict(row))
        return out
    finally:
        if own:
            conn.close()


def describe(rows: list[dict[str, Any]]) -> str:
    """The conditions as one line — "1200-1400m · Turf · draw <= 6".

    Written here rather than on the page because the Blackbook row, the Race
    Day band and the Form Guide all need the same sentence, and three copies of
    it is three chances for one of them to describe a condition it is not
    actually filtering on.
    """
    if not rows:
        return ""
    parts = []
    for row in rows:
        kind, op, value = row["kind"], row["op"], (row["value"] or "").strip()
        unit = "m" if kind == "distance" else ""
        if op == "is":
            parts.append(f"{value}{unit}" if kind in ("surface", "going", "course",
                                                      "venue", "class")
                         else f"{kind} {value}{unit}")
        elif op == "in":
            parts.append(f"{kind} {value.replace(',', '/')}{unit}")
        elif op == "between":
            lo, _, hi = value.partition("-")
            parts.append(f"{lo.strip()}-{hi.strip()}{unit}"
                         if kind == "distance" else
                         f"{kind} {lo.strip()}-{hi.strip()}")
        else:
            parts.append(f"{kind} {op} {value}{unit}")
    return " · ".join(parts)
