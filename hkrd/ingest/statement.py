"""HKJC account statements — the record of what was actually bet.

    from hkrd.ingest import statement
    bets = statement.parse(Path("acctstmt (22 April).txt"))

Adapted from the old `parse_acct_statement.py`, which the handoff lists among
the best code in the repo. The parsing logic is carried across faithfully; what
is dropped is everything around it — the module wrote to a JSONL, read that
file back to de-duplicate, and mutated it in place. Ingest returns plain dicts
and does not know the database exists; `jobs/import_statement.py` writes them.

One public function, one shape out. The statement is the ONLY record of a bet
that exists before settlement, so a block this cannot read is reported rather
than skipped — a bet silently missing from the ledger reads as a bet never
placed, and the Blackbook's backed-versus-missed would then call it missed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["StatementError", "parse", "parse_text", "ParseReport"]


class StatementError(ValueError):
    """A statement could not be read at all — not one block within it."""


SEP = "*" * 40

VENUE_MAP = {"happy valley": "HV", "sha tin": "ST"}

# "3 CONCORDE STAR +" or "11 SMILING EMPEROR" — the trailing + marks a
# combining selection and is not part of the name.
SELECTION_RE = re.compile(r"^(\d{1,2})\s+([^+]+?)\s*\+?\s*$")
MONEY_RE = re.compile(r"^\$([\d,]+(?:\.\d+)?)$")
# Flexi bet: "$1.5625/192" is a unit stake across 192 combinations. The dollar
# value is the per-combination stake, not the total.
FLEXI_MONEY_RE = re.compile(r"^\$([\d,]+(?:\.\d+)?)\s*/\s*\d+$")
DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})\s+(\d{2}:\d{2})$")
RACE_RE = re.compile(r"^Race\s+(\d+)$", re.IGNORECASE)
ALL_UP_FORMULA_RE = re.compile(r"^(\d+)\s*[xX]\s*(\d+)$")

# Longest first: "quinella - quinella place" must not match as "quinella".
BET_TYPE_KEYWORDS = (
    "quinella - quinella place", "quinella-quinella place", "quinella place",
    "quinella", "first 4", "first four", "quartet", "qtt", "trio",
    "win-place", "win - place", "place", "win", "tierce", "trifecta",
    "double trio", "six up", "all up",
)
SUBTYPE_KEYWORDS = ("multi-banker", "multi banker")


@dataclass
class ParseReport:
    """What the file contained, so a block that did not parse is visible."""

    blocks: int = 0
    bets: int = 0
    records: int = 0
    cash_movements: int = 0
    unparsed: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"  blocks             {self.blocks:>6}",
                 f"  bet blocks         {self.bets:>6}",
                 f"  bet records        {self.records:>6}",
                 f"  cash movements     {self.cash_movements:>6}"]
        if self.unparsed:
            lines.append(f"  UNPARSED           {len(self.unparsed):>6}")
            lines += [f"    {u}" for u in self.unparsed[:10]]
        return "\n".join(lines)


# ── small readers ────────────────────────────────────────────────────────────

def _money(text: str) -> float | None:
    """A plain ($60.00) or flexi ($1.5625/192) amount."""
    s = text.strip()
    for pattern in (MONEY_RE, FLEXI_MONEY_RE):
        m = pattern.match(s)
        if m:
            return float(m.group(1).replace(",", ""))
    return None


def _is_money(text: str) -> bool:
    return _money(text) is not None


def _split_blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip() == SEP:
            if current:
                blocks.append(current)
            current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _classify(block: list[str]) -> str:
    """bet | header | balance | footer | cash | unknown.

    Cash movements carry a date line but are not wagers, so they are named
    rather than counted as a failure to parse.
    """
    joined = "\n".join(block)
    if "Account Records" in joined:
        return "header"
    if "Account Balance as of" in joined:
        return "balance"
    if "- End -" in joined:
        return "footer"
    low = joined.lower()
    if any(w in low for w in ("deposit", "withdrawal", "withdraw", "transfer")):
        return "cash"
    if any(DATE_RE.match(ln.strip()) for ln in block):
        return "bet"
    # A block with a reference number but nothing this can read is NOT nothing.
    # Returning "unknown" here would drop it without a word, and a bet missing
    # from the ledger reads as a bet never placed.
    if any(ln.strip().isdigit() for ln in block[:2]):
        return "bet"
    return "unknown"


# "All Up" names the shape of the ticket, not the pool it is into. The pool can
# follow on its own line, and the legacy parser stopped at the first keyword it
# saw -- so five real all-ups came through as ALLUP_OTHER when their own notes
# show three-leg banker structures that are plainly quinellas. The pool line is
# read here instead of being lost.
_POOL_KEYWORDS = tuple(k for k in BET_TYPE_KEYWORDS if k not in ("all up", "six up"))


def _bet_type_line(lines: list[str]) -> tuple[int | None, str, str]:
    """(index, bet type, sub type). Sub type is 'Multi-Banker' when present."""
    for idx, raw in enumerate(lines):
        s = raw.strip().lower()
        if not s or not any(kw in s for kw in BET_TYPE_KEYWORDS):
            continue
        text = lines[idx].strip()
        j = idx + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        nxt = lines[j].strip() if j < len(lines) else ""
        sub = nxt if any(k in nxt.lower() for k in SUBTYPE_KEYWORDS) else ""
        if ("all up" in s and not sub
                and any(k in nxt.lower() for k in _POOL_KEYWORDS)):
            text = f"{text} {nxt}"
        return idx, text, sub
    return None, "", ""


# ── blocks ───────────────────────────────────────────────────────────────────

def _parse_all_up(lines: list[str], ref: str, placed_at: str, date: str,
                  venue: str, bet_type: str) -> dict | None:
    """A cross-race parlay: one `Race N` section per leg, each optionally
    carrying a banker. Parsed as ONE ticket, never as several single-race bets
    — the legs settle together."""
    formula, formula_idx = "", None
    for i, ln in enumerate(lines):
        if ALL_UP_FORMULA_RE.match(ln.strip()):
            formula = ln.strip().upper().replace(" ", "")
            formula_idx = i
            break

    raw_legs: list[dict] = []
    current: dict | None = None
    stakes: list[float] = []
    for line in lines[(formula_idx + 1) if formula_idx is not None else 0:]:
        s = line.strip()
        if not s:
            continue
        race = RACE_RE.match(s)
        if race:
            if current is not None:
                raw_legs.append(current)
            current = {"race_number": int(race.group(1)), "pre": [], "post": [],
                       "banker_seen": False}
            continue
        if _is_money(s):
            stakes.append(_money(s))
            continue
        if s.lower().startswith("banker with"):
            if current is not None:
                current["banker_seen"] = True
            continue
        sel = SELECTION_RE.match(s)
        if sel and current is not None:
            key = "post" if current["banker_seen"] else "pre"
            current[key].append(int(sel.group(1)))
    if current is not None:
        raw_legs.append(current)

    legs = []
    for leg in raw_legs:
        if leg["banker_seen"] and leg["pre"] and leg["post"]:
            banker, picks = leg["pre"][-1], leg["post"]
        else:
            banker, picks = None, leg["pre"] + leg["post"]
        if picks:
            legs.append({"race_number": leg["race_number"], "banker": banker,
                         "selections": picks})

    if len(legs) < 2 or len(stakes) < 2:
        return None

    flat: list[int] = []
    for leg in legs:
        if leg["banker"] is not None:
            flat.append(leg["banker"])
        flat.extend(leg["selections"])

    return {
        "bookie_ref": ref, "placed_at": placed_at, "meeting_date": date,
        "venue": venue, "race_number": legs[0]["race_number"],
        "bet_type_text": bet_type, "is_all_up": True, "all_up_formula": formula,
        "all_up_legs": legs, "banker": None, "selections": flat,
        "multi_legs": None, "per_combo_stake": stakes[0],
        "total_debit": stakes[1],
        "total_credit": stakes[2] if len(stakes) >= 3 else 0.0,
    }


def _parse_bet(block: list[str]) -> dict | None:
    """One bet block. Scans by content rather than fixed line positions, so a
    statement variant with an extra header line still reads."""
    lines = [ln.rstrip() for ln in block]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) < 5:
        return None

    ref = lines[0].strip()
    if not ref.isdigit():
        ref = next((ln.strip() for ln in lines[:4] if ln.strip().isdigit()), ref)

    dt_idx = next((i for i, ln in enumerate(lines[:6])
                   if DATE_RE.match(ln.strip())), None)
    if dt_idx is None:
        return None
    dd, mm, yyyy, hhmm = DATE_RE.match(lines[dt_idx].strip()).groups()
    date = f"{yyyy}{mm}{dd}"
    placed_at = f"{yyyy}-{mm}-{dd}T{hhmm}:00"

    venue = None
    for ln in lines[dt_idx + 1: dt_idx + 4]:
        venue = VENUE_MAP.get(ln.strip().lower())
        if venue:
            break
    if not venue:
        return None

    bt_idx, bet_type, sub_type = _bet_type_line(lines)
    if bt_idx is None:
        return None
    multi_banker = bool(sub_type)
    if multi_banker:
        bet_type = f"{bet_type} {sub_type}".strip()

    if "all up" in bet_type.lower() or "all-up" in bet_type.lower():
        return _parse_all_up(lines, ref, placed_at, date, venue, bet_type)

    start = bt_idx + (2 if multi_banker else 1)
    race_idx = next((i for i in range(start, len(lines))
                     if RACE_RE.match(lines[i].strip())), None)
    if race_idx is None:
        return None
    race_number = int(RACE_RE.match(lines[race_idx].strip()).group(1))

    multi_legs: list[list[int]] = []
    banker: int | None = None
    picks: list[int] = []
    i = race_idx + 1

    if multi_banker:
        # A Quartet multi-banker lists one leg per finishing POSITION, all in
        # the same race. They are positions, not races.
        leg: list[int] = []
        while i < len(lines):
            s = lines[i].strip()
            if not s:
                i += 1
                continue
            if _is_money(s):
                break
            if s.lower().startswith("banker with"):
                if leg:
                    multi_legs.append(leg)
                leg = []
                i += 1
                continue
            sel = SELECTION_RE.match(s)
            if sel:
                leg.append(int(sel.group(1)))
            i += 1
        if leg:
            multi_legs.append(leg)
        seen: set[int] = set()
        for group in multi_legs:
            for horse in group:
                if horse not in seen:
                    picks.append(horse)
                    seen.add(horse)
        if not picks or not multi_legs:
            return None
    else:
        pre: list[int] = []
        post: list[int] = []
        banker_seen = False
        while i < len(lines):
            s = lines[i].strip()
            if not s:
                i += 1
                continue
            if s.lower().startswith("banker with"):
                banker_seen = True
                i += 1
                continue
            if _is_money(s):
                break
            sel = SELECTION_RE.match(s)
            if sel:
                (post if banker_seen else pre).append(int(sel.group(1)))
            i += 1
        if banker_seen:
            if not pre or not post:
                return None
            banker, picks = pre[-1], post
        else:
            picks = pre
        if not picks:
            return None

    stakes = [_money(lines[j].strip()) for j in range(i, len(lines))
              if lines[j].strip() and _is_money(lines[j].strip())]
    if len(stakes) < 2:
        return None

    return {
        "bookie_ref": ref, "placed_at": placed_at, "meeting_date": date,
        "venue": venue, "race_number": race_number, "bet_type_text": bet_type,
        "banker": banker, "selections": picks,
        "multi_legs": multi_legs if multi_banker else None,
        "per_combo_stake": stakes[0], "total_debit": stakes[1],
        "total_credit": stakes[2] if len(stakes) >= 3 else 0.0,
    }


# ── one block to one or more ledger records ──────────────────────────────────

def _expand(parsed: dict) -> list[dict[str, Any]]:
    """A "Quinella - Quinella Place" block is TWO bets sharing one debit, so it
    becomes two records at half the stake each. An All-Up is one ticket at the
    full debit and must never be split — HKJC publishes only per-leg dividends,
    so its settlement comes from the bookie credit."""
    text = parsed["bet_type_text"].lower()
    banker = parsed.get("banker")

    if parsed.get("is_all_up"):
        code = ("ALLUP_WP" if "win" in text and "place" in text
                else "ALLUP_QQP" if "quinella" in text
                else "ALLUP_WIN" if "win" in text
                else "ALLUP_PLACE" if "place" in text
                else "ALLUP_OTHER")
        return [{
            "meeting_date": parsed["meeting_date"], "venue": parsed["venue"],
            "race_number": parsed["race_number"], "bet_type": code,
            "selections": parsed["selections"], "banker": None,
            "legs": parsed.get("all_up_legs") or [],
            "all_up_formula": parsed.get("all_up_formula", ""),
            "stake_hkd": parsed["total_debit"],
            "bookie_ref": parsed["bookie_ref"],
            "placed_at": parsed["placed_at"],
            "total_credit": parsed["total_credit"],
        }]

    multi = parsed.get("multi_legs")
    if multi:
        code = "QTT_MB"
    elif "quinella - quinella place" in text or "quinella-quinella place" in text:
        code = None                      # the bundle, split below
    elif "quinella place" in text:
        code = "QPL_BANKER" if banker else "QPL"
    elif "quinella" in text:
        code = "QIN_BANKER" if banker else "QIN"
    elif "quartet" in text or "qtt" in text:
        code = "QTT_BOX"
    elif "tierce" in text or "trifecta" in text:
        code = "TCE_BOX"
    elif "place" in text:
        code = "PLACE"
    elif "win" in text:
        code = "WIN"
    else:
        code = "OTHER"

    base = {
        "meeting_date": parsed["meeting_date"], "venue": parsed["venue"],
        "race_number": parsed["race_number"],
        "selections": parsed["selections"], "banker": banker,
        "legs": multi or [], "bookie_ref": parsed["bookie_ref"],
        "placed_at": parsed["placed_at"],
        "total_credit": parsed["total_credit"],
    }

    if code is None:
        # One debit covering two pools, and ONE credit covering both. Half the
        # stake each, so the ledger's turnover is the money that actually left
        # the account -- and half the credit each, flagged, because the
        # statement does not say how a $129.50 return split between the win
        # pool and the place pool. The block total stays exact, which is what
        # P&L and ROI are read off; only the QIN-versus-QPL share is an
        # apportionment, and `credit_apportioned` says so rather than letting
        # it pass as settled fact. A zero credit is not ambiguous: neither
        # pool paid, so it is not flagged.
        half_stake = round(parsed["total_debit"] / 2, 2)
        credit = parsed["total_credit"] or 0.0
        half_credit = round(credit / 2, 2)
        split = {**base, "total_credit": half_credit,
                 "credit_apportioned": credit > 0}
        return [
            {**split, "bet_type": "QIN_BANKER" if banker else "QIN",
             "stake_hkd": half_stake},
            {**split, "bet_type": "QPL_BANKER" if banker else "QPL",
             "stake_hkd": half_stake},
        ]
    return [{**base, "bet_type": code, "stake_hkd": parsed["total_debit"]}]


# ── public ───────────────────────────────────────────────────────────────────

def parse_text(text: str, *, source: str = "") -> tuple[list[dict], ParseReport]:
    """Statement text to ledger records, with a report of what did not read."""
    if not text or not text.strip():
        raise StatementError(f"empty statement{f' ({source})' if source else ''}")

    report = ParseReport()
    records: list[dict[str, Any]] = []
    for block in _split_blocks(text):
        report.blocks += 1
        kind = _classify(block)
        if kind == "cash":
            report.cash_movements += 1
            continue
        if kind != "bet":
            continue
        report.bets += 1
        parsed = _parse_bet(block)
        if parsed is None:
            head = next((ln.strip() for ln in block if ln.strip()), "?")
            report.unparsed.append(f"block starting {head!r}")
            continue
        records.extend(_expand(parsed))

    if report.bets and not records:
        raise StatementError(
            f"{report.bets} bet blocks found but none parsed"
            f"{f' ({source})' if source else ''} — the statement format may have changed")
    report.records = len(records)
    return records, report


def parse(path: Path) -> tuple[list[dict], ParseReport]:
    return parse_text(Path(path).read_text(encoding="utf-8", errors="replace"),
                      source=str(path))
