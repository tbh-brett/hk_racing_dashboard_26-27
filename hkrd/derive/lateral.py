"""Across-the-track trip from HKJC's Comments on Running and stewards' report: lane, cover,
settling and events by phase. THE one comment parser (racesim-SPEC §4.1).

The lab had two. The 16 Oct study's first parser read "very wide" as five wide and a lane given
"into the straight" as the turn's; this one is the replay's, corrected against Brett's video marks:
"1 out 1 back", "rail position", "on outer", "pressed the leader" and plural "outside leaders"
handled; "away from inside fence" is not the rail; "very wide" is four wide; a lane given "into the
straight" is kept for the top of the straight (lane_entry), not the turn; a lane given "from 700M"
or "500m out" is kept at that distance (at), not for the whole phase; a lane given for the early
stages is lane_early, and early_only says the settled lane came only from it (a horse caught wide
early that went on to lead). On 2022/23-2025/26 the two read differently enough to matter -- 3+
wide before the straight 25.0% against 19.7% -- so everything fitted on the trip table is fitted
on this one.

Pure functions, text in, dict out. The rules are literal: they report what the comment
says, by phase, and "not stated" stays not stated (never the rail).

HKJC's placeholder ("No Comments on Running information for this horse.", every runner of a
meeting until the comments are published) is not a comment (`is_placeholder`): read as one, it
is a run where nothing happened.

Lane: 1 rail/inside, 2 two-wide/one-off, 3 three-wide/unqualified wide/outside,
      4 four-wide (very wide), 5 five-wide or wider/widest.
Phase: SETTLE (start to the back straight), MID (back straight / middle stages / >= 650 m
       to go), TURN (home turn, top of the straight, 350-650 m to go), STRAIGHT (< 350 m).
"""
from __future__ import annotations

import re

__all__ = ["DERIVE_VERSION", "PHASES", "straight_length", "is_placeholder", "parse",
           "summarise", "rich", "merge"]

DERIVE_VERSION = "trip-1.0"
PHASES = ("SETTLE", "MID", "TURN", "STRAIGHT")
_ORDER = {p: i for i, p in enumerate(PHASES)}

# Specific before generic: "one off the rail" must not read as the rail.
_LANE = [
    (5, r"\b(5|five|6|six|7|seven)[- ]?(wide|deep|path)\b"),
    (4, r"\b(4|four)[- ]?(very )?(wide|deep|path)\b|\bthree off\b|\b3 off\b"),
    (3, r"\b(3|three)[- ]?(wide|deep|path)\b|\btwo off\b|\b2 off\b"),
    (2, r"\baway from (the )?(inside )?(rail|fence)\b|\b(2|two)[- ]?(wide|deep|path)\b|\bone off\b|\b1 off\b|\b(one|1)[- ]out\b|\boff the (rail|fence)\b"
        r"|\boutside (the |of the )?leader\b(?!s)|\bat the girth\b|\bon the leader'?s girth\b|\bpress(ed|ing)? (the )?leader\b(?!s)"),
    (4, r"\bvery wide\b"),
    (5, r"\bwidest\b|\bextremely wide\b"),
    (1, r"\b(on|along|against|near|hugging|close to|hugged|towards|to) (the )?(far |near |inside )?(rail|fence)\b"
        r"|\bon the inside\b|\b(the |an )?inside (run|passage|trip|position|spot|draw)\b|\bbox[- ]seat\b"
        r"|\bleaders?'? back\b|\bleader'?s back\b|\bon the back of the leader\b|\bsaved ground\b"
        r"|\bfound (the )?(rail|fence)\b|\brailed\b|\brails? (run|position|trip)\b|\brail position\b|\binside\b"
        r"|\btrack(ed|ing) (the )?leader\b(?!s)"),
    (3, r"\b(raced|racing|coursing|travelled|traveled|trapped|sat|caught|ran) deep\b|\bwide\b|\bon the outside\b|\bout wide\b|\bon (the )?outer\b|\bthe outside\b|\bwider\b|\boutside (the )?leaders\b"),
]
# Phrases that use lane words without describing the lane: the gate, and "inside 200M".
_STRIP = re.compile(r"\b(wide|widest|wider|outside|inside|outer|inner|good|bad|awkward|middle)(st)? "
                    r"(draw|gate|barrier|alley|stall|marble)s?\b|\b(draw|gate|barrier) \d+\b"
                    r"|\binside (of )?(the )?(final |last )?\d{2,4} ?(m|metres|meters)\b|\binside (the )?(final|last)\b"
                    r"|\b(to )?avoid(ing)? (being )?(caught |trapped |posted |left )?(wide|three wide|3 wide|out wide)\b"
                    r"|\b(forced|forcing|taken|carried|pushed|shifted|bumped|ran|race|raced) (\w+ ){0,2}wider\b", re.I)
_LANE = [(k, re.compile(p, re.I)) for k, p in _LANE]
_NOT_LANE = re.compile(r"\b(away from|off|left|leave|leaving|came off|came away from) (the )?(inside )?(rail|fence)\b"
                       r"|\bswitch(ed)? (away|out|off|wider)\b", re.I)

_NOCOVER = re.compile(r"\bwithout (any )?cover\b|\bno cover\b|\bpress(ed|ing)? (the )?leader\b(?!s)|\btrapped (wide|three|3|four|4|two|2|out)\b"
                      r"|\bexposed\b|\bwithout a (cover|lead)\b|\bfloated (wide|out)\b", re.I)
_COVER = re.compile(r"\bwith cover\b|\bgot cover\b|\bfound cover\b|\bin behind\b|\b(one|1)[- ]out[- ](one|1)[- ]back\b", re.I)
_THROUGHOUT = re.compile(r"\bthroughout\b|\ball the way\b|\bthe whole way\b|\bfor most of the (race|trip|journey)\b"
                         r"|\bentire (race|trip|journey)\b|\b(for )?the majority( of the (race|event|trip|journey))?\b"
                         r"|\bmost of the (race|way|trip|journey)\b", re.I)

_FIRST_TURN = re.compile(r"\bfirst (turn|bend|corner)\b|\b1st (turn|bend|corner)\b", re.I)
_STRAIGHT = re.compile(r"\blate(ly| stages)?\b|\bthe run home\b|\blast section\b|\bin (the )?(home )?straight\b|\bhome straight\b|\bthe straight\b|\bfinal (furlong|stages|\d+ ?m)\b"
                       r"|\blast (\d+ ?m|furlong)\b|\bclosing stages\b|\b(near|nearing|to|on) the line\b"
                       r"|\bover the (final|last)\b|\bdrift(ed)? (in|out) (in|over|late)\b", re.I)
_TURN = re.compile(r"\b(on|around|rounding|round|approaching|entering|into|at) the (home )?(turn|bend|corner)\b"
                   r"|\bhome (turn|bend)\b|\brounding\b|\bturning (in|for home|into)\b|\bturned? in\b"
                   r"|\btop of (the )?straight\b|\b(into|entering|approaching) (the )?straight\b|\bstraightening\b"
                   r"|\bon turn\b|\bthe turn\b|\bturn\b|\bbend\b|\bcorner(ing|ed)?\b|\bentering (the )?last section\b"
                   r"|\bswinging (wide )?for home\b|\bfor home\b", re.I)
_MID = re.compile(r"\bback straight\b|\bmiddle (stages|part|section)\b|\bmid[- ]?race\b|\bhalf[- ]?way\b"
                  r"|\bdown the back\b|\balong the back\b|\bin the run\b|\bthe back\b", re.I)
_DIST = re.compile(r"(?<![\d./-])(\d{2,4})\s?(m|metres|meters)\b", re.I)
_EARLY = re.compile(r"\bearly( stages| on| in the race)?\b|\b(at|after|shortly after|soon after) the (start|jump)\b"
                    r"|\bon jumping\b|\bleaving the (gates|barriers)\b", re.I)
_ENTRY = re.compile(r"\b(into|entering|approaching|top of|turning into) (the )?(home )?straight\b|\bstraightening\b"
                    r"|\bturning (in|for home)\b|\bfor home\b", re.I)
_AT = re.compile(r"\b(?:from|after|passing|approaching|near|at|around) (?:the )?(\d{3,4}) ?(?:m|metres|meters)\b"
                 r"|\b(\d{3,4}) ?(?:m|metres|meters) (?:out|from home)\b", re.I)

_SLOW_BAD = re.compile(r"\bmade contact (at|shortly after|soon after|after|leaving) (the )?(start|jump|gates?)\b"
                       r"|\b(crowded|bumped|squeezed|hampered|checked|steadied|tightened|interfered with) "
                       r"(start|jump|leaving|on jumping)\b|\b(on|at|after|leaving) (the )?(jump|jumping|start|gates?)\b.*"
                       r"\b(stumbl\w*|lost (ground|lengths?)|bounded|reared|propped|blundered|bumped|crowded|hampered|"
                       r"checked|steadied|squeezed)\b|\b(stumbl\w*|lost (ground|lengths?)|bounded|reared|propped|blundered)"
                       r"\b.*\b(jump|jumping|start|gates?)\b|\bslow(ly)? (away|to begin|out|start|into stride)\b|\bmissed the (start|break|kick|jump)\b"
                       r"|\bawkward(ly)?\b|\bdwelt\b|\bfumbled\b|\bstumbl\w* (at|on|shortly after|leaving)\b"
                       r"|\b(hampered|checked|bumped|crowded|squeezed|tightened) (at|shortly after|soon after|leaving|"
                       r"after) (the )?(start|jump|gates?)\b|\blost (ground|lengths?) at (the )?start\b|\bsluggish\b"
                       r"|\btardy\b|\bblundered\b|\breared\b", re.I)
_SLOW_MILD = re.compile(r"\b(began|jumped|away|broke) (only )?fairly\b|\baverage to begin\b|\bbegan only fair\b", re.I)
_RESTRAINT = re.compile(r"\bheld up (in|at|towards|near) (the )?(rear|back|last|tail)\b"
                        r"|\bheld up (early|to settle|off the pace)\b", re.I)
_TRAFFIC = re.compile(r"\bheld up\b|\bno room\b|\black(ed)? (of )?room\b|\bshort of room\b|\bdenied\b|\bblocked\b"
                      r"|\bchecked\b|\bsteadied\b|\bhampered\b|\bcrowded\b|\btightened\b|\bsqueezed\b"
                      r"|\bcould not (get|find|obtain) (a )?(clear|run|room|out|through)\b|\bnowhere to go\b"
                      r"|\bawaited (a |for )?(run|gap|clear)\b|\bwait(ed|ing)? for (a )?(run|gap)\b|\btrapped behind\b"
                      r"|\bboxed in\b|\bshut (in|out)\b|\bhad to wait\b|\bno clear run\b|\bunable to (get|obtain|find) "
                      r"(a )?clear\b|\bdifficulty (obtaining|finding|getting) (a )?(clear|run)\b|\brestricted room\b"
                      r"|\bclipped heels\b|\bbumped\b|\btight for room\b|\bmade contact\b", re.I)


def _clauses(text: str) -> list[str]:
    parts = re.split(r"[,;]|(?<!\d)\.|\.(?!\d)|\bthen\b|\bbefore\b|\band then\b", text)
    return [p.strip() for p in parts if p and p.strip()]


def _phase_of(clause: str, distance: int | None) -> str | None:
    """The phase a clause speaks about, or None if it gives no cue."""
    if _FIRST_TURN.search(clause):
        return "SETTLE"
    if re.search(r"\bentering (the )?last section\b|\b(into|entering|approaching|top of) (the )?(home )?straight\b"
                 r"|\bstraightening\b|\bturning (in|for home|into)\b", clause, re.I):
        return "TURN"
    if _STRAIGHT.search(clause):
        return "STRAIGHT"
    ds = [int(m.group(1)) for m in _DIST.finditer(clause)]
    ds = [d for d in ds if distance is None or d < distance]
    if ds:
        d = max(ds) if re.search(r"\bfrom\b|\bafter\b|\bpassing\b", clause, re.I) else ds[0]
        return "MID" if d >= 650 else ("TURN" if d >= 350 else "STRAIGHT")
    if _TURN.search(clause):
        return "TURN"
    if _MID.search(clause):
        return "MID"
    return None


def _lane_of(clause: str) -> int | None:
    clause = _STRIP.sub(" ", clause)
    for lane, pat in _LANE:
        m = pat.search(clause)
        if not m:
            continue
        if lane == 1 and _NOT_LANE.search(clause):
            return None            # "switched away from the fence" is not a rail trip
        return lane
    return None


def parse(text: str | None, distance: int | None = None, lh: int | None = None) -> dict:
    """Lane / cover / events by phase for one comment. lh: where the home straight starts (m to go), if known."""
    out = {f"lane_{p.lower()}": None for p in PHASES}
    out.update({f"nocover_{p.lower()}": 0 for p in PHASES})
    out.update(slow_bad=0, slow_mild=0, traffic_pre=0, traffic_straight=0, stated=0,
               lane_early=None, lane_entry=None, at=[], early_only=0)
    if not text or not text.strip():
        return out
    phase = "SETTLE"
    stated: dict[str, int] = {}
    said_last: dict[str, int] = {}                   # the last lane the comment gives in each phase, in its own order
    settle_from_early = None
    for cl in _clauses(text):
        ph = _phase_of(cl, distance)
        if ph is not None and _ORDER[ph] >= _ORDER[phase]:
            phase = ph
        here = ph if ph is not None and _ORDER[ph] < _ORDER[phase] else phase
        lane = _lane_of(cl)
        through = bool(_THROUGHOUT.search(cl))
        targets = ["SETTLE", "MID", "TURN"] if through else [here]
        at = _AT.search(cl)
        n_at = int(at.group(1) or at.group(2)) if at else None
        if n_at is not None and distance is not None and not n_at < distance:
            n_at = None
        elsewhere = False
        if lane is not None and not through and _ENTRY.search(cl):
            out["lane_entry"] = lane                  # the top of the straight, not the middle of the turn
            elsewhere = True
        elif lane is not None and not through and n_at is not None:
            out["at"].append([n_at, lane])            # "from 700M": from there, not the whole phase
            said_last[here] = lane                    # ... and, if nothing later in the comment says otherwise, after it
            elsewhere = True
        elif lane is not None:
            for t in targets:
                stated[t] = lane
                said_last[t] = lane
            if here == "SETTLE" and not through:
                early = bool(_EARLY.search(cl))
                if early:
                    out["lane_early"] = lane
                if settle_from_early is None or not early:
                    settle_from_early = early
        if _NOCOVER.search(cl) and not _COVER.search(cl):
            for t in targets:
                out[f"nocover_{t.lower()}"] = 1
                if t != "STRAIGHT" and stated.get(t) is None and not elsewhere:
                    stated.setdefault(t, 3 if not re.search(r"\b(2|two)[- ]?wide\b|outside (the )?leader", cl, re.I) else 2)
        if _SLOW_BAD.search(cl) and _ORDER[here] <= _ORDER["SETTLE"]:
            out["slow_bad"] = 1
        elif _SLOW_MILD.search(cl) and _ORDER[here] <= _ORDER["SETTLE"]:
            out["slow_mild"] = 1
        if _TRAFFIC.search(_RESTRAINT.sub(" ", cl)) and not _SLOW_BAD.search(cl):
            straight = here == "STRAIGHT"
            if not straight and lh is not None:
                # "held up between the 450 Metres and the 200 Metres", "steadied near the 400 Metres" at Sha Tin: the
                # straight starts at about the 430 there, so these are hold-ups in the straight (20 Oct)
                ds = [int(m.group(1)) for m in _DIST.finditer(cl)]
                ds = [d for d in ds if distance is None or d < distance]
                straight = bool(ds) and min(ds) < lh and max(ds) < lh + 150
            out["traffic_straight" if straight else "traffic_pre"] = 1
    # carry forward before the straight, never into it: the last lane the comment gives in a phase, a lane at a distance
    # included, holds for the phases after it ('raced 2 wide, 3 wide from 650M' is 3 wide on the turn, not 2; 18 Oct)
    last = None
    for p in ("SETTLE", "MID", "TURN"):
        if p in stated:
            last = stated[p]
        out[f"lane_{p.lower()}"] = stated.get(p, last)
        if p in said_last:
            last = said_last[p]
    out["lane_straight"] = stated.get("STRAIGHT")
    out["stated"] = int(bool(stated) or out["lane_entry"] is not None or bool(out["at"]))
    out["early_only"] = int(bool(settle_from_early))
    return out


def summarise(row: dict) -> dict:
    pre = [row.get(f"lane_{p}") for p in ("settle", "mid", "turn")]
    pre = [x for x in pre if x is not None]
    return dict(
        wide3_pre=int(any(x >= 3 for x in pre)),
        nocover_pre=int(any(row.get(f"nocover_{p}") for p in ("settle", "mid", "turn"))),
        rail_pre=int(bool(pre) and max(pre) == 1),
        wide_straight=int((row.get("lane_straight") or 0) >= 3),
        lane_max_pre=max(pre) if pre else None,
    )


# ── the course ──────────────────────────────────────────────────────────────────────────────────
def straight_length(venue: str | None, surface: str | None) -> int:
    """Metres from the top of the home straight to the post (`Lh`): Happy Valley 310, Sha Tin's
    all-weather 380, Sha Tin turf 430."""
    return 310 if venue == "HV" else (380 if surface == "AWT" else 430)


_NOCOM = re.compile(r"^\s*(no comments on running information|no report)", re.I)


def is_placeholder(text: str | None) -> bool:
    """HKJC's "No Comments on Running information for this horse." or "No report": no comment."""
    return bool(text) and bool(_NOCOM.search(text))


# ── the replay's reading: cover, keenness and the lead, by phase ────────────────────────────────
# What counts as cover: a horse directly in front to sit behind (Brett, 16 Oct). It is not ground:
# a horse can be three wide with cover or one out without.
_HAS_COVER = re.compile(r"\bwith cover\b|\b(got|found|obtained|gained|had) (some )?cover\b|\bin behind\b"
                        r"|\bone[- ]out[- ]one[- ]back\b|\b1-out-1-back\b|\bbox[- ]seat\b|\bleader'?s'? back\b"
                        r"|\bbehind (the )?(leaders?|runners|horses|the pace)\b|\btrail(ed|ing)? (the )?(leaders?|pace)\b"
                        r"|\btrailing position\b|\btrack(ed)? (the )?(leaders?|pace)\b|\bon the back of\b|\bcovered up\b|\b(1|one)[- ]out[- ](1|one)[- ]back\b|\bclose up behind\b|\bjust behind (the )?leaders?\b", re.I)
_KEEN = re.compile(r"\bkeen(ly)?\b|\bover-?rac(ed|ing)\b|\bpull(ed|ing)? (hard|for (its|his|her) head)\b"
                   r"|\bfought (for (its|his|her) head|the rider|the bit)\b|\b(refused|hard|difficult|unable) to settle\b"
                   r"|\bdid not settle\b|\bwould not settle\b|\bover-?eager\b|\bhang(ing)? in\b|\bhung (in|out)\b", re.I)
_LED = re.compile(r"\b(led|leading|made (the|all the) running|set the pace|(took|take|takes|taking|taken) (up )?the lead"
                  r"|in front|took up running|to lead)\b", re.I)


def rich(text: str | None, distance: int, lh: int | None = None) -> dict:
    """`parse` plus explicit cover and keenness by phase, and whether the text says it led."""
    out = parse(text, distance, lh)
    for p in PHASES:
        out[f"cover_{p.lower()}"] = 0
        out[f"keen_{p.lower()}"] = 0
    out["led_text"] = 0
    if not text or is_placeholder(text):
        return out
    phase = "SETTLE"
    for cl in _clauses(text):
        ph = _phase_of(cl, distance)
        if ph is not None and _ORDER[ph] >= _ORDER[phase]:
            phase = ph
        here = ph if ph is not None and _ORDER[ph] < _ORDER[phase] else phase
        through = bool(_THROUGHOUT.search(cl))
        targets = ["SETTLE", "MID", "TURN"] if through else [here]
        if _HAS_COVER.search(cl) and not _NOCOVER.search(cl):
            for t in targets:
                out[f"cover_{t.lower()}"] = 1
        if _KEEN.search(cl):
            for t in targets:
                out[f"keen_{t.lower()}"] = 1
        if _LED.search(cl) and _ORDER[here] <= _ORDER["MID"]:
            out["led_text"] = 1
    return out


def merge(a: dict, b: dict) -> dict:
    """One run's reading per phase (the replay's `runner.p`) from the Comments on Running (`a`)
    and the stewards' report (`b`). The comment first; the report fills gaps, and its "very wide"
    (four wide or more) beats the comment's plain "wide" -- Brett's video corrections put those
    horses four wide."""
    m: dict = {}
    for p in PHASES:
        k = p.lower()
        la, lb = a.get(f"lane_{k}"), b.get(f"lane_{k}")
        if la == 3 and lb is not None and lb >= 4:
            la = None
        m[k] = dict(lane=la if la is not None else lb,
                    src="cor" if la is not None else ("inc" if lb is not None else None),
                    nocover=int(a[f"nocover_{k}"] or b[f"nocover_{k}"]),
                    # a phase said to be without cover is not also with cover
                    cover=int((a[f"cover_{k}"] or b[f"cover_{k}"]) and not (a[f"nocover_{k}"] or b[f"nocover_{k}"])),
                    keen=int(a[f"keen_{k}"] or b[f"keen_{k}"]))
    m["slow"] = int(a["slow_bad"] or b["slow_bad"])
    m["traffic_pre"] = int(a["traffic_pre"] or b["traffic_pre"])
    m["traffic_straight"] = int(a["traffic_straight"] or b["traffic_straight"])
    m["led"] = int(a["led_text"] or b["led_text"])
    for key in ("early", "entry"):
        la, lb = a.get(f"lane_{key}"), b.get(f"lane_{key}")
        if la is not None or lb is not None:
            m[key] = dict(lane=la if la is not None else lb, src="cor" if la is not None else "inc")
    at = {n: [n, lane, "cor"] for n, lane in a.get("at", [])}
    for n, lane in b.get("at", []):
        at.setdefault(n, [n, lane, "inc"])
    if at:
        m["at"] = sorted(at.values(), reverse=True)
    # caught wide early, then went on to lead: the wide lane was the start, not where it settled
    if m["led"] and (a.get("early_only") or (a.get("lane_settle") is None and b.get("early_only"))):
        for k in ("settle", "mid", "turn"):
            if m[k]["lane"] is not None and m.get("early") and m[k]["lane"] == m["early"]["lane"]:
                m[k]["lane"], m[k]["src"] = None, None
    return m
