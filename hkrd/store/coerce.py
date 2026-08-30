"""Type coercion. Every value is coerced HERE, on the way in — never at read time.

The old system scattered `try: float(x) except: pass` across the codebase, so the
same field parsed differently depending on which loader reached it first. One
parser per field, used by every writer, is what makes that class of bug
impossible.

Formats below were enumerated from all 21,423 legacy rows, not assumed.
"""
from __future__ import annotations

import re
from datetime import date, datetime

__all__ = [
    "CoerceError", "parse_lbw", "parse_finish_time", "to_place",
    "to_odds", "to_date", "to_int", "parse_section_times",
    "parse_running_positions",
]


class CoerceError(ValueError):
    """A value could not be parsed and no documented sentinel covers it.

    Raised rather than returning None so a scraper format change surfaces on the
    row that broke, naming the field and value.
    """


# ── beaten lengths ────────────────────────────────────────────────────────────
#
# 79.1% of legacy `lbw` values are NOT plain numbers — 13,126 are mixed
# fractions like '3-1/4', 990 are bare fractions like '1/2', and 780 are
# short-margin codes. `pd.to_numeric` turns every one of them into NaN, which is
# why margin analysis silently biased toward whole numbers and why fitting
# seconds-per-length on the broken parse gave 0.11 instead of 0.139.

# Standard racing margins, in lengths. Below a length the sport names margins
# rather than measuring them.
_MARGIN_CODES: dict[str, float] = {
    "NOSE": 0.05,
    "SH": 0.10,   # short head
    "SHD": 0.10,  # short head, alternate spelling
    "HD": 0.20,   # head
    "N": 0.30,    # neck
    "NK": 0.30,   # neck, alternate spelling
    "SN": 0.30,   # short neck
    "DH": 0.00,   # dead heat with the horse ahead: no margin between them
}

# The winner, or a margin that was never measured.
_NO_MARGIN = {"-", "--", "---", ""}

# 'ML' appears twice in the legacy data, both on last-placed finishers with
# valid times: a real but unmeasured large margin. Recorded as unknown (NULL)
# rather than invented, and kept distinct from a parse failure.
_UNMEASURED = {"ML", "DIST", "DISTANCED"}

_MIXED = re.compile(r"^(\d+)-(\d+)/(\d+)$")   # 3-1/4
_FRACTION = re.compile(r"^(\d+)/(\d+)$")      # 1/2


def parse_lbw(token: object) -> float | None:
    """Beaten lengths. Returns None for winners and unmeasured margins.

    >>> parse_lbw("3-1/4"), parse_lbw("1/2"), parse_lbw("HD"), parse_lbw("-")
    (3.25, 0.5, 0.2, None)
    """
    if token is None:
        return None
    s = str(token).strip()
    if s in _NO_MARGIN:
        return None
    upper = s.upper()
    if upper in _UNMEASURED:
        return None
    if upper in _MARGIN_CODES:
        return _MARGIN_CODES[upper]

    if m := _MIXED.match(s):
        whole, num, den = (int(g) for g in m.groups())
        if den == 0:
            raise CoerceError(f"lbw: zero denominator in {token!r}")
        return whole + num / den
    if m := _FRACTION.match(s):
        num, den = (int(g) for g in m.groups())
        if den == 0:
            raise CoerceError(f"lbw: zero denominator in {token!r}")
        return num / den
    try:
        return float(s)
    except ValueError:
        raise CoerceError(f"lbw: unrecognised margin {token!r}") from None


# ── finish time ───────────────────────────────────────────────────────────────

_MMSS = re.compile(r"^(\d+):(\d{1,2})(?:\.(\d+))?$")          # 1:49.23
_MMSS_DOTS = re.compile(r"^(\d+)\.(\d{2})\.(\d{1,2})$")       # 1.49.23 (trials)


def parse_finish_time(token: object) -> float | None:
    """Race time in seconds. Accepts '1:49.23', '1.49.23' and '109.23'."""
    if token is None:
        return None
    s = str(token).strip()
    if s in _NO_MARGIN:
        return None
    if m := _MMSS.match(s):
        mins, secs, frac = m.group(1), m.group(2), m.group(3) or "0"
        return int(mins) * 60 + int(secs) + float(f"0.{frac}")
    if m := _MMSS_DOTS.match(s):
        mins, secs, frac = m.groups()
        return int(mins) * 60 + int(secs) + float(f"0.{frac}")
    try:
        return float(s)
    except ValueError:
        raise CoerceError(f"finish_time: unrecognised time {token!r}") from None


# ── finishing position ────────────────────────────────────────────────────────
#
# `place` is not an integer column in practice. The legacy data carries 187 'WV'
# (withdrawn/vet), 63 'WV-A', 12 'UR' (unseated rider), 12 'PU' (pulled up), and
# 76 dead heats written as '8 DH'. Coercing this with pd.to_numeric would drop
# every one of them, including the dead heats, which ARE placings.

# Every non-numeric code present in the legacy data, with counts at the time of
# writing. Withdrawn (WV/WX) dominates; the rest are in-running failures.
_NON_FINISHER = {
    "WV",     # 187  withdrawn, veterinary
    "WV-A",   #  63
    "UR",     #  12  unseated rider
    "PU",     #  12  pulled up
    "WX-A",   #   7  withdrawn
    "FE",     #   7  fell
    "DNF",    #   5  did not finish
    "WX",     #   3
    "WXNR",   #   3  withdrawn, not run
    "TNP",    #   2  took no part
    "DISQ",   #   1  disqualified
}
_DEAD_HEAT = re.compile(r"^(\d+)\s*DH$", re.IGNORECASE)


def to_place(token: object) -> tuple[int | None, str | None, bool]:
    """Returns (place, raw_code, dead_heat).

    A non-finisher yields (None, 'WV', False) — the reason is preserved rather
    than flattened to NULL, because "withdrawn" and "ran but unplaced" are
    different facts.
    """
    if token is None:
        return None, None, False
    s = str(token).strip()
    if not s:
        return None, None, False
    if m := _DEAD_HEAT.match(s):
        return int(m.group(1)), s, True
    upper = s.upper()
    if upper in _NON_FINISHER:
        return None, upper, False
    try:
        return int(s), None, False
    except ValueError:
        raise CoerceError(f"place: unrecognised value {token!r}") from None


# ── odds, ints, dates ─────────────────────────────────────────────────────────

def to_odds(token: object) -> float | None:
    """Decimal odds. '---' means no price was offered (scratched, or pre-market)."""
    if token is None:
        return None
    s = str(token).strip()
    if s in _NO_MARGIN:
        return None
    try:
        v = float(s)
    except ValueError:
        raise CoerceError(f"odds: unrecognised value {token!r}") from None
    if v <= 0:
        raise CoerceError(f"odds: non-positive value {token!r}")
    return v


def to_int(token: object, *, field: str = "value") -> int | None:
    if token is None:
        return None
    s = str(token).strip()
    if s in _NO_MARGIN:
        return None
    try:
        return int(float(s))
    except ValueError:
        raise CoerceError(f"{field}: expected an integer, got {token!r}") from None


_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d", "%d %b %Y", "%d/%m/%y")


def to_date(token: object) -> str | None:
    """Normalise to YYYY-MM-DD. Dates are stored as TEXT in that one format."""
    if token is None:
        return None
    if isinstance(token, datetime):
        return token.date().isoformat()
    if isinstance(token, date):
        return token.isoformat()
    s = str(token).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise CoerceError(f"date: unrecognised format {token!r}")


# ── sectionals ────────────────────────────────────────────────────────────────

def parse_section_times(token: object) -> tuple[float, ...]:
    """'24.97; 22.82; 23.62; ; ' -> (24.97, 22.82, 23.62).

    Trailing empty fields are normal — HKJC pads to a fixed column count.
    """
    if token is None:
        return ()
    out: list[float] = []
    for part in str(token).split(";"):
        p = part.strip()
        if not p:
            continue
        try:
            out.append(float(p))
        except ValueError:
            raise CoerceError(f"section_times: bad split {p!r} in {token!r}") from None
    return tuple(out)


def parse_running_positions(token: object) -> tuple[int, ...]:
    """'10 9 6 3 2' or '4; 4; 4; 1' -> (10, 9, 6, 3, 2).

    Both separators appear in the legacy data because the same fact was stored
    twice, in two shapes, and one copy silently died in May 2026. One
    representation is stored from here on.
    """
    if token is None:
        return ()
    out: list[int] = []
    for part in re.split(r"[;\s]+", str(token).strip()):
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            raise CoerceError(
                f"running_positions: bad position {part!r} in {token!r}"
            ) from None
    return tuple(out)
