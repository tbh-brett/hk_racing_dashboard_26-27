"""The stewards' contacts, placed on the course: who moved, into whom, which way (racesim-SPEC §4.2).

Each sentence of a horse's stewards' report that names a contact -- bumped, crowded, shifted out,
lay in -- is placed where the report says ("near the 600 Metres", "on jumping", "in the early stages
of the Home Straight" = 40 m into the straight), with who moved and into whom:

  "X which shifted out"                                  X moved, into this horse
  "bumped by X"                                          X moved, into this horse
  "shifted out and away from A and made contact with B"  this horse moved, into B -- not A
  "lay in and away from X"                               this horse moved, not X

The last three were fixed on 23 Oct: of the 641 contacts placed in 26/27 to 19 Oct, 13 had the
wrong horse moving, 2 the wrong one moved into, and 3 were in the wrong part of the race. A
sentence that says neither who nor where is not placed. Pure functions; `race_trip` stores them
(jobs/rebuild_trip).
"""
from __future__ import annotations

import re

__all__ = ["KIND", "where", "contacts"]

# Stewards' contact: "Near the 900 Metres was bumped by RADIANT PATH which shifted out", "Shifted in shortly
# after the start and made contact with X", "crowded between A and B". Placed where the report says.
KIND = re.compile(r"\b(bump(?:ed|s)?|made contact|contact|crowded|squeezed|checked|steadied|hampered|tightened|inconvenienced"
                  r"|brushed|clipped (?:the )?heels|carried (?:wide|wider|out)|forced wider|taken wider|shift(?:ed|s)? (?:in|out)"
                  r"|lay in|lay out|laid in|laid out|hung (?:in|out))\b", re.I)
SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def where(s: str, D: int, Lh: int) -> int | None:
    """Distance to go (m) that a stewards' sentence places its event at, or None."""
    if re.search(r"\b(at the start|on jumping|jumped (?:awkwardly|out|in|badly)|began awkwardly|leaving the (?:barriers|gates))\b", s, re.I):
        return D - 15
    if re.search(r"\b(?:shortly|soon|just) after (?:the )?(?:start|jumping)\b|\bafter the start\b|\bafter jumping\b", s, re.I):
        return D - 60
    # "in the early stages of the Home Straight" is early in the straight, not early in the race (21 Oct R2)
    if re.search(r"\b(?:early stages|early part) of the (?:home )?straight\b|\bearly in the (?:home )?straight\b", s, re.I):
        return Lh - 40
    if re.search(r"\b(?:in|during) the early stages\b|\bearly in the race\b", s, re.I):
        return D - 150
    m = re.search(r"\bbetween the (\d{2,4}) metres and the (\d{2,4}) metres\b", s, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(near|at|passing|approaching|rounding|inside|after|just after|before|just before|leaving) the (\d{2,4}) metres\b", s, re.I)
    if m:
        w, n = m.group(1).lower(), int(m.group(2))
        return n + (25 if w in ("approaching", "before", "just before") else -25 if w in ("after", "just after", "inside", "leaving") else 0)
    if re.search(r"\b(over the (?:concluding|final|closing) stages|close to the finish|near the finish|approaching the (?:winning )?post|near the winning post)\b", s, re.I):
        return 60
    if re.search(r"\b(?:on|rounding|approaching) the (?:home )?(?:turn|bend)\b", s, re.I):
        return Lh + 80
    if re.search(r"\b(?:turning into|entering|top of) the (?:home )?straight\b", s, re.I):
        return Lh - 20
    return None


def contacts(race_runners: list[dict], D: int, Lh: int) -> list[dict]:
    """Every stewards' contact in a race, placed on the course, nearest the start first.

    `race_runners` are {no, name, inc}: the horse's number, name and its stewards' text. Each
    event is {d: metres to go, a: the horse that moved, b: the one it moved into (or None),
    dir: +1 out / -1 in / 0 not said, push, kind, by: whose report, text}."""
    names = {r["no"]: r["name"].upper().replace("’", "'") for r in race_runners}
    out, seen = [], set()
    for r in race_runners:
        text = r.get("inc") or ""
        for s0 in SENT.split(text):
            s = re.sub(r"\s*\([^)]*\)", "", s0.replace("’", "'"))       # "CLASS (J Orman) which shifted in"
            km = KIND.search(s)
            if not km:
                continue
            d = where(s, D, Lh)
            if d is None or not (0 < d <= D):
                continue
            others = [(s.find(nm), no) for no, nm in names.items() if no != r["no"] and nm and nm in s]
            others = [no for _, no in sorted(others)]
            kind = km.group(1).lower()
            a, b, dirn = None, None, 0
            selfdir = re.search(r"\b(?:shifted|shift|shifts|lay|laid|hung) (in|out)\b", s, re.I)
            # 1. "X which shifted out": X moved, and into this horse (or the other one named)
            sh = re.search(r"\b([A-Z][A-Z'’ \-]+?) (?:which|that) (?:had )?(shifted|lay|hung|laid) (in|out)\b", s)
            if sh:
                who = [no for no, nm in names.items() if nm == sh.group(1).strip()]
                if who:
                    a, dirn = who[0], 1 if sh.group(3) == "out" else -1
                    b = r["no"] if who[0] != r["no"] else (others[0] if others else None)
            # 2. "bumped by X", "a bump from X": X moved into this horse
            if a is None:
                # ("lay in and away from X" is this horse moving, not X: 19 Oct R4 NOT USUAL DOUBLE, 23 Oct)
                by = [no for no in others if re.search(r"(?<!away )\b(?:by|from) " + re.escape(names[no]) + r"\b", s)]
                if by:
                    a, b = by[0], r["no"]
                    if selfdir and re.search(r"\b(?:as a result|after being|when|forcing|and)\b", s, re.I):
                        dirn = 1 if selfdir.group(1).lower() == "out" else -1
            # 3. "shifted out and bumped X", "permitted his mount to shift in": this horse moved, into the one it touched
            # ("shifted out and away from A and made contact with B" moved into B, not A: 23 Oct)
            if a is None and selfdir:
                hit = [no for no in others if re.search(r"\b(?:contact with|bumped|bumping|brushed|crowded|hampered|tightened|inconvenienced) "
                                                        + re.escape(names[no]) + r"\b", s)]
                a, dirn, b = r["no"], 1 if selfdir.group(1).lower() == "out" else -1, (hit[0] if hit else others[0] if others else None)
            # 4. "made contact with X", "crowded between A and B": who moved isn't said
            if a is None and others and not re.search(r"\bbetween\b", s, re.I):
                a, b = r["no"], others[0]
            if a is None:
                a, b = r["no"], None                        # a marker only: "bumped at the start", "checked"
            key = (round(d / 60), frozenset(x for x in (a, b) if x is not None))
            if key in seen:
                continue
            seen.add(key)
            if b is not None and re.search(r"\bto avoid\b|\bavoid(ing)? the heels\b|\baway from\b", s, re.I) and not re.search(r"\b(bump|contact|brush)", s, re.I):
                b = None                                    # shifted to avoid another horse: a move, not a contact
            # steadied or checked "to avoid X" is a check, not a push; a push needs a shift or a contact word
            push = bool(dirn) or bool(re.search(r"\b(bump|contact|crowd|squeez|brush|hamper|tighten|forced wider|carried|taken wider)", kind + " " + s, re.I))
            out.append(dict(d=d, a=a, b=b, dir=dirn, push=int(push and b is not None or bool(dirn)), kind=kind, by=r["no"],
                            text=s0.strip()[:220]))
    return sorted(out, key=lambda e: -e["d"])
