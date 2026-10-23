"""Which way a horse made its run from the home turn, from the Comments on Running (lab, 18 Oct).

Brett: from the turn, a midfielder or closer with momentum goes round for a clear run and covers more ground ('round');
one settled on the rail or one out may save ground and wait for a gap between runners, at the risk of traffic
('through'). A horse that saves ground on the turn and then switches out in the straight is the second kind: it waited.

The first version (a single regex) counted the stewards' contact language 'shifted out' as going round, matched the
'out' in '300m out' and 'taken back from the widest draw', and so named most of this season's round runs wrongly.
This one reads clause by clause:

  round    a move out or wide on the turn: 'angled out after passing 500M', 'commenced move out wide 700m out',
           'switched out rounding the Turn', 'swung wide for home', 'very wide into straight', '4 wide on the turn',
           'circled the field'
  through  saving ground into the straight or coming between runners: 'saved ground on turn', 'between runners',
           'found a gap', 'along the rail in the straight', or a switch out once in the straight ('angled out 350M'
           at Sha Tin, 'switched out for a run upon straightening')
  neither  the start and settling ('raced wide without cover', 'taken back from a wide draw', 'positioned 2 wide'),
           contacts ('shifted out and bumped'), behaviour ('hung out'), or nothing said

A clause's place comes from its distance ('500M', '600m out') or its words (turn, bend, straight, turning in). A move
out counts as on the turn from the top of the straight plus 30 m (Sha Tin turf 460 m, all-weather 410 m, Happy Valley
340 m) to the top of the straight plus 450 m (650 m at 2000 m and up); further out it is mid-race, not the run. A move
out with no place counts only if it says it was for the run ('for a run', 'to challenge'); a keen horse shifting out
is not making its run. If a comment
has both ways, the first round clause wins unless a switch in the straight came before it; 'saved ground on turn,
angled out 500M' is round (the run started on the turn). 'at' is the distance of the deciding clause, when it gives one.

    run_way(text, distance, Lh) -> {"how": "round"|"through", "at": metres or None, "why": "..."} or None

`run_of` is what the trip table and the replay store: the way, and where the run started -- the
deciding clause's distance, else the comment's "improved from 600M". The Comments on Running only:
the stewards' "shifted out" is a contact (derive/contacts), not a run round. Pure functions.
"""
from __future__ import annotations

import re

__all__ = ["run_way", "run_of", "runs_from_behind"]

CLAUSE = re.compile(r"[,;.]\s*|\s+(?:and|but|then|before)\s+(?=(?:was|had|being|angled|switched|swung|came|went|brought|moved|saved|found|"
                    r"commenced|improved|eased|cornering|making)\b)", re.I)
NOCOM = re.compile(r"^\s*(no comments on running information|no report)", re.I)

DIST = re.compile(r"(?<![\d.])(\d{3,4})\s?(?:m|metres|meters)\b", re.I)
TURN_W = re.compile(r"\b(?:turn|bend|corner|cornering|for home|turning for home)\b", re.I)
INTO_ST = re.compile(r"\b(?:into|entering|top of)\b[^,;.]{0,12}\bstraight\b|\bturning in\b|\bturned in\b|\bstraightening\b", re.I)
IN_ST = re.compile(r"\b(?:in|early in|inside|within|during|halfway up|up|down) (?:the )?(?:home )?straight\b|\b(?:after|upon|on) straightening\b|"
                   r"\bin the run home\b|\b(?:last|final) (?:section|furlong|\d{3} ?m)\b", re.I)

# never about the run: the start, the first turn, a whole-race description
SKIP = re.compile(r"\b(?:draw|barrier|gate|stalls?|jump(?:ed|ing)?|start|began|first (?:turn|bend|corner)|back straight|throughout|"
                  r"after the start|early (?:stages|on)|settling)\b", re.I)
# not a run round: a settled lane, a contact, behaviour
NOT_ROUND = re.compile(r"\bwithout cover\b|\bno cover\b|\btrapped\b|\bcaught\b|"
                       r"\b(?:race|raced|racing|positioned|travelled|traveled|settled|sat|kept|remained|stayed|taken back|tracked|chased|"
                       r"held up|in midfield|in rear|towards rear|at rear)\b[^,;.]{0,20}\bwide\b|"
                       r"\bhung\b|\blugg|\blaid out\b|\blay out\b|\bdrifted\b|\bcarried\b|\bbumped\b|\bhampered\b|\bcrowded\b|\bawkward|"
                       r"\binconvenienced\b|\bchecked\b|\bsteadied\b|\binto the (?:running|path)\b|\bacross\b|\bcontact\b|\btightened\b|\bimpeded\b|"
                       r"\bkeen(?:ly)?\b|\bover-?rac|\bpull(?:ed|ing)\b", re.I)

_W = r"(?:(?!\d)\w+\s+){0,2}?"                     # up to two words between, none a distance ('eased 200m out' is not a move out)
OUT_MOVE = re.compile(r"\b(?:angled|switched|shifted|moved|came|went|eased|fanned|pulled|edged|steered|brought|swung|taken)\s+" + _W +
                      r"(?:out|wide|wider|widest|to the outside|outside(?: runners| rivals)?|(?:three|four|five|six|3|4|5|6)[- ]wide)\b", re.I)
WIDE_RUN = re.compile(r"\b(?:swung|came|went|brought|ridden|pushed(?: along)?|moved|improved|commenced(?: (?:a |his |her |its )?(?:move|run|bid))?|"
                      r"made (?:ground|progress|headway|a move|its move|his move|her move)|ran on|stayed on|closed|finished(?: off)?|challenged)\b"
                      r"[^,;.]{0,30}?\b(?:wide|widest|(?:three|four|five|six|3|4|5|6)[- ]wide|on the (?:outside|outer)|out wide)\b", re.I)
WIDE_TURN = re.compile(r"\b(?:very wide|widest|(?:three|four|five|six|3|4|5|6)[- ]wide|wide|wider)\b[^,;.]{0,20}?\b(?:on|around|rounding|approaching|"
                       r"making|at|into|turning|entering)\b[^,;.]{0,12}?\b(?:turn|bend|straight|home)\b|\bwide(?:r|st)? turning in\b|"
                       r"\bwide(?:r|st)? into (?:the )?(?:home )?straight\b|\bwide(?:r|st)? (?:on |upon |when |after )?straightening\b|\bwide(?:r|st)? for home\b", re.I)
ROUND_FIELD = re.compile(r"\b(?:a)?round (?:the )?(?:field|runners|horses|rivals|them)\b|\bcircl(?:ed|ing)\b|\bon the outside of (?:the )?(?:field|runners)\b", re.I)
RUN_CTX = re.compile(r"\bfor (?:a |the |his |her |its )?(?:run|clear run|room|challenge|daylight)\b|\bto (?:challenge|improve|mount)\b|"
                     r"\bto make (?:a |its |his |her )?(?:run|move|challenge)\b|\bfor home\b|\bmaking (?:a |its |his |her )?(?:run|move)\b", re.I)

THROUGH = re.compile(r"\bbetween (?:runners|horses|rivals|them|two)\b|\bsplit (?:runners|horses|rivals|the field|them)\b|"
                     r"\bthrough (?:a |the )?(?:gap|narrow gap|small gap|tight gap|runners|horses|field|on the inside)\b|\bfound (?:a |the )?gap\b|"
                     r"\bsqueezed (?:through|between)\b|\bshot through\b|\bup the (?:inside|rail)\b|\binside (?:runners|rivals|horses)\b|"
                     r"\b(?:along|on|hugging|down|up|near|kept to) the (?:rail|inside|fence|inner)\b[^,;.]{0,25}\b(?:straight|final|run home|home|turn|bend)\b|"
                     r"\bkept to (?:the )?inside\b|\bsaved ground\b[^,;.]{0,25}\b(?:turn|bend|home|straight|corner)\b|\brails? run\b", re.I)


def _where(clause: str, Lh: int) -> float | None:
    """Metres to go the clause is about, from a distance or the course words; None if it doesn't say."""
    m = DIST.search(clause)
    if m:
        return float(m.group(1))
    if IN_ST.search(clause):
        return Lh - 120.0
    if INTO_ST.search(clause):
        return Lh - 10.0
    if TURN_W.search(clause):
        return Lh + 80.0
    return None


def run_way(text: str | None, distance: int, Lh: int) -> dict | None:
    if not text or NOCOM.search(text):
        return None
    on_turn_from = Lh + 30
    run_from = Lh + (650 if distance >= 2000 else 450)    # a move out further from home is mid-race, not the run
    first_round, first_through = None, None
    for i, cl in enumerate(c.strip() for c in CLAUSE.split(text) if c and c.strip()):
        if SKIP.search(cl):
            continue
        where = _where(cl, Lh)
        if where is not None and (where >= distance - 150 or where > run_from):
            continue                                     # the start or mid-race, not the run
        at = float(DIST.search(cl).group(1)) if DIST.search(cl) else None
        rnd = None
        if ROUND_FIELD.search(cl):
            rnd = "round the field"
        elif not NOT_ROUND.search(cl):
            if WIDE_TURN.search(cl):
                rnd = "wide on the turn or into the straight"
            elif OUT_MOVE.search(cl) or WIDE_RUN.search(cl):
                if where is None:
                    if RUN_CTX.search(cl):
                        rnd = "out or wide for its run"
                elif where >= on_turn_from:
                    rnd = "out or wide on the turn"
                else:
                    if first_through is None:
                        first_through = dict(how="through", at=at, why="switched out in the straight", k=i)
                    continue
        if rnd:
            if first_round is None:
                first_round = dict(how="round", at=at, why=rnd, k=i)
            continue
        if THROUGH.search(cl) and first_through is None:
            first_through = dict(how="through", at=at, why="saved ground or between runners", k=i)
    if first_round and (first_through is None or first_through["why"] != "switched out in the straight" or first_round["k"] < first_through["k"]):
        out = first_round
    elif first_through:
        out = first_through
    else:
        return None
    return {k: v for k, v in out.items() if k != "k"}


MOVE = re.compile(r"\b(?:improved|commenced|began|started|made (?:a )?(?:move|run|ground|progress|headway)|ranged up|moved (?:up|closer)|quickened)\b"
                  r"[^.;]{0,40}?\b(?:from|at|near|approaching|passing|before|after)?\s*(?:the\s*)?(\d{3,4})\s*m", re.I)


def runs_from_behind(positions: list[int]) -> bool:
    """Not up with the pace before the run: further back than third at the call before the last
    two. A leader's "on the rail in the straight" is not a run through the field."""
    return len(positions) >= 2 and positions[max(0, len(positions) - 3)] > 3


def run_of(text: str | None, distance: int, Lh: int) -> dict | None:
    """The run from the turn as the replay stores it: {how, src, why, from?}, or None."""
    w = run_way(text, distance, Lh)
    if not w:
        return None
    at = w["at"]
    if at is None:
        m = MOVE.search(text)
        at = int(m.group(1)) if m else None
    at = int(at) if at and 100 <= at < distance - 150 else None
    return dict(how=w["how"], src="cor", why=w["why"], **({"from": at} if at else {}))
