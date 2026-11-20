"""The banker review: every horse he backed to carry a ticket, with what he said
before the race, what happened in it, and what he says decided it.

Asked for on 2026-11-20 (docs/banker-review.md): grade his reasoning, keep track
of the times he picked wrong apart from the times circumstances decided it, and
go back over the bankers already run -- his own and the legs of Kelvin's
tickets, which are the money that decides.

WHICH HORSES. A horse carries a ticket when the ticket cannot pay without it:
the banker of a banker ticket or leg, or the only horse in a leg or a
single-race bet. Read off `bet_selections` for every book -- Brett's statements,
the April-June log, Kelvin's sheet -- and from the desk's TAKEN decisions, which
reach the ledger only once a statement does. A box or a pair has no horse it
cannot do without, and is not here.

ONE ROW PER HORSE IN ITS RACE, whichever tickets it was on: his judgement of a
horse in a race is one judgement, though Kelvin's 3x4 and his own QPL may both
have carried it.

BESIDE HIS ACCOUNT, all read fresh, never stored with it:
  the run       where it finished, beaten how far, at what price, whether it
                placed, and its chance of placing at the price
                (`ticket_ledger.place_chances`: the place pool where captured)
  his evidence  the book tier as at the race (`book_tier.tiers_for`): the trial
                note, run note or book entry he wrote BEFORE it ran
  the record    what the stewards' report and the comments on running say
                happened to it (`derive/circumstances`), quoted
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from hkrd.derive import circumstances
from hkrd.query import book_tier, ticket_ledger
from hkrd.store.connect import Connection, get_conn
from hkrd.store.review import effective_claim, reflections

__all__ = ["SINCE", "ACCOUNTS", "VERDICTS", "VERDICT_LABEL", "CONFIDENCE", "PICKED_BY",
           "vocabulary", "key_horses", "rows", "meetings", "meeting", "horse"]

SINCE = "2026-04-01"           # the ledger, and the book, begin here
# The Bets page's account picker: BRETT is his statements and the legacy log.
ACCOUNTS: dict[str, tuple[str, ...]] = {"brett": ("brett", "personal"),
                                        "kelvin": ("kelvin",)}

# What decided it: (key, label, needs it to have placed -- True, False or
# either). The two halves of the list are the two halves of every result: his
# reading was right or wrong, and the horse placed or did not. Circumstances
# is the one a beaten horse's owner reaches for first, which is why the
# summary sets it beside what the stewards' report says (`query/review_record`).
VERDICTS: tuple[tuple[str, str, bool | None], ...] = (
    ("right_paid", "Read it right, and it placed", True),
    ("lucky", "Read it wrong, placed anyway", True),
    ("right_beaten", "Read it right, beaten fairly", False),
    ("circumstances", "Circumstances: never ran its race", False),
    ("mistake", "Misread it", False),
    ("unsure", "Can't tell", None),
)
VERDICT_LABEL = {k: label for k, label, _ in VERDICTS}
CONFIDENCE: tuple[tuple[str, str], ...] = (("strong", "Strong"), ("fair", "Fair"),
                                          ("weak", "Weak"))
PICKED_BY: tuple[tuple[str, str], ...] = (("me", "Me"), ("kelvin", "Kelvin"),
                                         ("other", "Someone else"))

_KEY_SQL = """
WITH sel AS (
  SELECT b.bet_id, lower(b.account) AS account, b.race_date, b.bet_type,
         b.all_up_formula, b.stake, b.returned, b.status, s.race_no, s.horse_no,
         s.is_banker, sum(s.is_banker) OVER w AS bankers, count(*) OVER w AS picks
    FROM bets b JOIN bet_selections s ON s.bet_id = b.bet_id
   WHERE b.race_date >= ? AND b.race_date <= ?
  WINDOW w AS (PARTITION BY s.bet_id, s.leg_no, s.race_no)
)
SELECT * FROM sel WHERE (bankers > 0 AND is_banker = 1) OR (bankers = 0 AND picks = 1)
"""


def _marks(n: int) -> str:
    return ",".join("?" * n)


def _accounts(account: str | None) -> tuple[str, ...] | None:
    if not account:
        return None
    return ACCOUNTS.get(account.lower(), (account.lower(),))


def vocabulary() -> dict[str, Any]:
    """Every word the review's form offers, in the order it offers them."""
    return {
        "verdicts": [{"key": k, "label": label, "needs_placed": needs}
                     for k, label, needs in VERDICTS],
        "confidence": [{"key": k, "label": v} for k, v in CONFIDENCE],
        "picked_by": [{"key": k, "label": v} for k, v in PICKED_BY],
        "circumstances": [{"key": k, "label": v, "from_record": k in circumstances.FROM_RECORD}
                          for k, v in circumstances.CIRCUMSTANCES]}


def key_horses(conn: Connection, date_from: str, date_to: str, *,
               account: str | None = None) -> dict[tuple, dict[str, Any]]:
    """{(race_date, race_no, horse_no): {"tickets": [...], "desk": {...} or None}}
    for every horse a ticket in the window could not pay without."""
    books = _accounts(account)
    out: dict[tuple, dict[str, Any]] = defaultdict(lambda: {"tickets": [], "desk": None})
    for r in conn.execute(_KEY_SQL, (date_from, date_to)):
        if books and r["account"] not in books:
            continue
        out[(r["race_date"], r["race_no"], r["horse_no"])]["tickets"].append({
            "bet_id": r["bet_id"], "account": r["account"], "bet_type": r["bet_type"],
            "formula": r["all_up_formula"], "stake": r["stake"], "returned": r["returned"],
            "status": r["status"], "role": "banker" if r["is_banker"] else "single"})
    for r in conn.execute(
            "SELECT race_date, race_no, banker_no, decision, account, reason, decided_at "
            "FROM ticket_decision WHERE race_date >= ? AND race_date <= ? AND late = 0 "
            "AND decision IN ('accepted', 'edited')", (date_from, date_to)):
        acct = (r["account"] or "brett").lower()
        if books and acct not in books:
            continue
        out[(r["race_date"], r["race_no"], r["banker_no"])]["desk"] = {
            "decision": r["decision"], "account": acct, "reason": r["reason"],
            "decided_at": r["decided_at"]}
    return dict(out)


def _by_race(conn: Connection, sql: str, dates: list[str], args: tuple = ()) -> list[Any]:
    out: list[Any] = []
    for i in range(0, len(dates), 400):
        chunk = dates[i:i + 400]
        out += conn.execute(sql.format(marks=_marks(len(chunk))), [*chunk, *args]).fetchall()
    return out


def rows(conn: Connection, keys: dict[tuple, dict[str, Any]], *,
         evidence: bool = True) -> list[dict[str, Any]]:
    """Each key horse with its run, his evidence, the record and his review.
    `evidence=False` leaves out his notes, which the summary does not read."""
    if not keys:
        return []
    dates = sorted({k[0] for k in keys})
    races = {k[:2] for k in keys}
    field: dict[tuple, int] = defaultdict(int)
    run: dict[tuple, dict] = {}
    for r in _by_race(conn, "SELECT race_date, race_no, horse_no, horse_name, place, "
                      "place_code, lengths_behind, win_odds, running_positions, jockey, draw "
                      "FROM runners WHERE race_date IN ({marks})", dates):
        k = (r["race_date"], r["race_no"])
        if k not in races:
            continue
        if not (r["place_code"] or "").upper().startswith("W"):
            field[k] += 1
        if (*k, r["horse_no"]) in keys:
            run[(*k, r["horse_no"])] = dict(r)
    result = {tuple(r) for r in _by_race(
        conn, "SELECT DISTINCT race_date, race_no FROM runners WHERE race_date IN ({marks}) "
        "AND place IS NOT NULL", dates)}
    info = {(r["race_date"], r["race_no"]): dict(r) for r in _by_race(
        conn, "SELECT race_date, race_no, venue, distance, race_class, off_time FROM races "
        "WHERE race_date IN ({marks})", dates)}
    chances = ticket_ledger.place_chances(conn, dates[0], races)
    tiers = book_tier.tiers_for(
        [{"race_date": k[0], "race_no": k[1], "horse_no": k[2],
          "horse_name": run[k]["horse_name"], "off_time": info.get(k[:2], {}).get("off_time")}
         for k in keys if k in run], conn=conn) if evidence else {}
    texts: dict[tuple, dict[str, str]] = defaultdict(dict)
    for r in _by_race(conn, "SELECT race_date, race_no, horse_no, source, comment_text FROM "
                      "runner_comments WHERE race_date IN ({marks}) AND source IN "
                      "('incident', 'corunning')", dates):
        if (r["race_date"], r["race_no"], r["horse_no"]) in keys:
            texts[(r["race_date"], r["race_no"], r["horse_no"])][r["source"]] = r["comment_text"]
    stored = {(r["race_date"], r["race_no"], r["horse_no"]): {**dict(r), "circumstances": []}
              for r in _by_race(conn, "SELECT * FROM banker_review WHERE race_date IN "
                                "({marks})", dates)}
    for r in _by_race(conn, "SELECT race_date, race_no, horse_no, circumstance FROM "
                      "banker_review_circumstance WHERE race_date IN ({marks})", dates):
        if (r[0], r[1], r[2]) in stored:
            stored[(r[0], r[1], r[2])]["circumstances"].append(r[3])
    desk = {(r["race_date"], r["race_no"], r["banker_no"]): dict(r) for r in _by_race(
        conn, "SELECT race_date, race_no, banker_no, reason, decided_at FROM ticket_decision "
        "WHERE race_date IN ({marks}) AND late = 0", dates)}
    # His note on THIS run, written after it -- the run note that becomes the
    # horse's evidence for its next start. His 18 Nov 2026 reflection on the
    # night was filed that way, one note per horse.
    names = {(k[0], k[1], run[k]["horse_name"]): k for k in keys if k in run}
    after: dict[tuple, dict] = {}
    for r in _by_race(conn, "SELECT horse_name, race_date, race_no, note, written_at FROM "
                      "run_notes WHERE race_date IN ({marks})", dates):
        k = names.get((r["race_date"], r["race_no"], r["horse_name"]))
        if k:
            after[k] = {"text": r["note"], "written_at": r["written_at"]}

    out = []
    for k, held in keys.items():
        me = run.get(k)
        if me is None:
            continue                  # a horse no card stored: nothing to show
        source, chance, places = chances.get(k[:2], (None, {}, None))
        size = field.get(k[:2], 0)
        withdrawn = (me["place_code"] or "").upper().startswith("W")
        has_result = k[:2] in result
        placed = (None if not has_result or withdrawn
                  else me["place"] is not None and me["place"] <= (places or (3 if size >= 7 else 2)))
        mine = stored.get(k)
        out.append({
            "race_date": k[0], "race_no": k[1], "horse_no": k[2],
            "horse_name": me["horse_name"],
            "race": {**{c: info.get(k[:2], {}).get(c) for c in
                        ("venue", "distance", "race_class", "off_time")},
                     "field_size": size, "result": has_result},
            "tickets": held["tickets"], "desk": held["desk"],
            "accounts": sorted({t["account"] for t in held["tickets"]}
                               | ({held["desk"]["account"]} if held["desk"] else set())),
            "run": {"place": me["place"], "place_code": me["place_code"],
                    "withdrawn": withdrawn, "lengths_behind": me["lengths_behind"],
                    "win_odds": me["win_odds"], "running_positions": me["running_positions"],
                    "jockey": me["jockey"], "draw": me["draw"], "placed": placed,
                    "chance": round(chance[k[2]], 3) if k[2] in chance else None,
                    "chance_source": source},
            "evidence": _evidence(tiers.get(k)),
            "record": circumstances.read(texts[k].get("incident"), texts[k].get("corunning")),
            "stewards": texts[k].get("incident"),
            "run_note": after.get(k),
            "review": _review(mine, desk.get(k))})
    out.sort(key=lambda r: (r["race_date"], r["race_no"], r["horse_no"]))
    return out


def _evidence(tier: dict[str, Any] | None) -> dict[str, Any] | None:
    """What he had written on the horse before it ran, in his words."""
    if not tier:
        return None
    note, run_note, booking = tier.get("note"), tier.get("run_note"), tier.get("booking")
    return {"tier": tier["tier"], "label": tier.get("label"),
            "note": ({"text": note.get("note"), "trial_date": note.get("trial_date"),
                      "written_at": note.get("written_at"), "response": note.get("response")}
                     if note else None),
            "run_note": ({"text": run_note.get("note"), "race_date": run_note.get("race_date"),
                          "written_at": run_note.get("written_at")} if run_note else None),
            "booking": ({"reasoning": booking.get("reasoning"), "origin": booking.get("origin"),
                         "added_date": booking.get("added_date"), "tags": booking.get("tags")}
                        if booking else None)}


def _review(stored: dict[str, Any] | None, desk: dict[str, Any] | None) -> dict[str, Any]:
    c = effective_claim(stored, desk)
    s = stored or {}
    return {"reasoning": c["reasoning"], "confidence": c["confidence"],
            "picked_by": c["picked_by"], "claim_at": c["claim_at"],
            "claim_edited_at": c["claim_edited_at"], "claim_source": c["source"],
            # Before the off, after it, or nothing claimed yet.
            "claim_timing": (None if not c["claim_at"] else
                             "after" if c["claim_late"] else "before"),
            "claim_kind": s.get("claim_kind"), "verdict": s.get("verdict"),
            "verdict_note": s.get("verdict_note"), "verdict_at": s.get("verdict_at"),
            "circumstances": sorted(s.get("circumstances") or [])}


def meetings(*, account: str | None = None, conn: Connection | None = None
             ) -> list[dict[str, Any]]:
    """Every meeting with a key horse, newest first, with how far its review
    has got: horses, how many have run, have a claim, have a verdict."""
    own = conn is None
    conn = conn or get_conn()
    try:
        keys = key_horses(conn, SINCE, "9999-12-31", account=account)
        if not keys:
            return []
        dates = sorted({k[0] for k in keys})
        run = {tuple(r) for r in _by_race(
            conn, "SELECT DISTINCT race_date, race_no FROM runners WHERE race_date IN "
            "({marks}) AND place IS NOT NULL", dates)}
        venue = {r[0]: r[1] for r in _by_race(
            conn, "SELECT race_date, max(venue) FROM races WHERE race_date IN ({marks}) "
            "GROUP BY race_date", dates)}
        stored = {(r[0], r[1], r[2]): (r[3], r[4]) for r in _by_race(
            conn, "SELECT race_date, race_no, horse_no, claim_at, verdict FROM banker_review "
            "WHERE race_date IN ({marks})", dates)}
        desk = {(r[0], r[1], r[2]) for r in _by_race(
            conn, "SELECT race_date, race_no, banker_no FROM ticket_decision WHERE race_date "
            "IN ({marks}) AND late = 0 AND trim(coalesce(reason, '')) <> ''", dates)}
        by: dict[str, dict[str, Any]] = {}
        for k, held in keys.items():
            m = by.setdefault(k[0], {"race_date": k[0], "venue": venue.get(k[0]), "horses": 0,
                                     "run": 0, "claimed": 0, "judged": 0, "accounts": set()})
            claim_at, verdict = stored.get(k, (None, None))
            m["horses"] += 1
            m["run"] += k[:2] in run
            m["claimed"] += bool(claim_at) or k in desk
            m["judged"] += bool(verdict)
            m["accounts"] |= {t["account"] for t in held["tickets"]}
            if held["desk"]:
                m["accounts"].add(held["desk"]["account"])
        return [{**m, "accounts": sorted(m["accounts"])}
                for m in sorted(by.values(), key=lambda m: m["race_date"], reverse=True)]
    finally:
        if own:
            conn.close()


def meeting(date: str, *, account: str | None = None,
            conn: Connection | None = None) -> dict[str, Any]:
    """One meeting's key horses, each with everything the review shows, and
    his notes on its races and on the night (key "0"). `card` is False when no
    card is stored for the date, which the route answers with a 404; a card
    with no banker on it yet -- the statement comes the next day -- is an
    empty list, not an error."""
    own = conn is None
    conn = conn or get_conn()
    try:
        horses = rows(conn, key_horses(conn, date, date, account=account))
        venue, races = conn.execute("SELECT max(venue), count(*) FROM races WHERE "
                                    "race_date = ?", (date,)).fetchone()
        return {"race_date": date, "venue": venue, "account": account, "card": bool(races),
                "horses": horses,
                "notes": {str(k): v for k, v in reflections(conn, date).items()},
                "vocabulary": vocabulary()}
    finally:
        if own:
            conn.close()


def horse(date: str, race_no: int, horse_no: int, *,
          conn: Connection | None = None) -> dict[str, Any] | None:
    """One horse's row, as `meeting` gives it -- what the page redraws after a
    save. A runner no ticket carried still has one, with no tickets."""
    own = conn is None
    conn = conn or get_conn()
    try:
        keys = key_horses(conn, date, date)
        k = (date, race_no, horse_no)
        got = rows(conn, {k: keys.get(k, {"tickets": [], "desk": None})})
        return got[0] if got else None
    finally:
        if own:
            conn.close()
