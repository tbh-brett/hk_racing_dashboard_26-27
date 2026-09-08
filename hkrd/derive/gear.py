"""Gear codes, and the change HKJC already tells you about.

Design note 03 §3 calls first-time gear "one of the more reliable public
signals bettors watch for", and the dashboard has been reconstructing it by
comparing one run's gear string against the run before. That reconstruction was
never necessary, because the fact is IN the string and nothing read it.

HKJC suffixes every code with what is happening to it:

    B      blinkers, worn, no change since last start
    B1     blinkers FIRST TIME
    B2     blinkers second time
    B-     blinkers REMOVED

Counted over the archive's 19,868 runs: 737 `TT1`, 584 `B1`, 472 `CP1`,
366 `B2`, 651 `B-`, 480 `CP-`. Every one of those rendered on screen as the
literal text "B1" in a gear column, with nothing anywhere in the dashboard
saying what the 1 meant.

WHAT THE SUFFIX CANNOT SAY. Re-instatement. A horse that wore blinkers, had
them taken off, and has them back on is a plain `B` again — the 1 means first
time EVER, not first time since. That one still needs the record, so it is
derived in `query/gear.py` where the record is, and it is the only part of this
that is inferred rather than read.

NAMES. Only the codes actually observed in the archive are named, and an
unrecognised code keeps its own token rather than being guessed at: a gear
chip that says "blinkers" about something else is worse than one that says
"BO".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["GearPiece", "parse_pieces", "GEAR_NAMES", "FOCUS_CODES",
           "describe", "pieces_by_code"]

# HKJC's own glossary, restricted to the codes this archive contains. Anything
# else falls through to its raw token — see the module docstring.
GEAR_NAMES: dict[str, str] = {
    "B": "blinkers",
    "BO": "blinkers with one cup",
    "V": "visor",
    "VO": "visor with one cup",
    "XB": "cross-over nose band",
    "TT": "tongue tie",
    "CP": "sheepskin cheek pieces",
    "H": "hood",
    "SR": "shadow roll",
    "P": "pacifiers",
    "PC": "pricker",
    "E": "ear plugs",
    "SB": "sheepskin browband",
    "CC": "Cornell collar",
    "BR": "bar plate",
}

# The headgear that changes how much a horse SEES, which is the family the
# owner watches and the family a barrier trial is used to test. A tongue tie is
# a breathing aid and a shadow roll stops a horse looking down; neither is the
# thing a trainer schools a horse in before committing to it in a race.
FOCUS_CODES: frozenset[str] = frozenset({"B", "BO", "V", "VO", "XB", "H", "CP"})

# A code is letters, then an optional state marker. The marker is a digit (how
# many times it has been worn, 1 being the first) or a hyphen (taken off).
_CODE = re.compile(r"^([A-Za-z]+)\s*([0-9]+|-)?$")

# The card writes these where a horse carries nothing. `store/coerce.parse_gear`
# strips them on the way in, but the legacy migration wrote 2,552 rows raw, so
# they are filtered here too — a piece of gear named "--" would otherwise be
# reported as applied on the run that has none.
_PLACEHOLDER = {"", "-", "--", "---"}

STATES = ("first", "second", "on", "off")


@dataclass(frozen=True)
class GearPiece:
    """One piece of gear and what is happening to it.

    `state` is what HKJC published, never inferred:

        first   worn for the first time on record   (B1)
        second  worn for the second time            (B2)
        on      worn, no change flagged             (B)
        off     taken off for this run              (B-)
    """
    code: str
    name: str
    state: str
    raw: str
    times: int | None = None

    @property
    def worn(self) -> bool:
        """Is it actually on the horse today? `off` means it is not."""
        return self.state != "off"

    @property
    def notable(self) -> bool:
        """Worth a chip on the card. A settled piece worn every start is not."""
        return self.state in ("first", "second", "off")


def _name(code: str) -> str:
    return GEAR_NAMES.get(code.upper(), code.upper())


def parse_pieces(gear: str | None) -> tuple[GearPiece, ...]:
    """Split a gear string into its pieces, with HKJC's own state on each.

    '/'-separated, as published. A token this does not recognise is kept as a
    piece in the `on` state under its own code rather than dropped: a horse
    wearing something unusual must not read as wearing nothing.
    """
    if not gear:
        return ()
    out: list[GearPiece] = []
    for token in str(gear).split("/"):
        raw = token.strip()
        if not raw or raw.upper() in _PLACEHOLDER:
            continue
        found = _CODE.match(raw)
        if not found:
            out.append(GearPiece(raw.upper(), raw.upper(), "on", raw))
            continue
        code, marker = found.group(1).upper(), found.group(2)
        times = None
        if marker == "-":
            state = "off"
        elif marker is None:
            state = "on"
        else:
            times = int(marker)
            state = "first" if times == 1 else "second" if times == 2 else "on"
        out.append(GearPiece(code, _name(code), state, raw, times))
    return tuple(out)


def pieces_by_code(gear: str | None) -> dict[str, GearPiece]:
    """The same, keyed by code. A code repeated in one string keeps the last."""
    return {p.code: p for p in parse_pieces(gear)}


def describe(piece: GearPiece) -> str:
    """One sentence, for the tooltip. The chip is short; this is not."""
    if piece.state == "first":
        return f"{piece.name} for the first time on record"
    if piece.state == "second":
        return f"{piece.name} for the second time"
    if piece.state == "off":
        return f"{piece.name} taken off for this run"
    return f"{piece.name}, worn"
