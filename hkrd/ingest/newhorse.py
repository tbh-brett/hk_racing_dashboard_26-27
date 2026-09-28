"""HKJC's "Intro to New Horses": where each import came from, before Hong Kong.

    https://racing.hkjc.com/en-us/local/page/new-horse?racedate=...&brandNo=...

One profile per horse, written for the race it is first declared for and
never revised: origin and import type, the pedigree and the sire's Hong Kong
record, the sale history, and a paragraph saying who owned and trained it
before and how it trialled or raced. Brett, 2026-09-28: CHIU CHOW GOLF won at
20/1 for David Price's former yard; does the agent, the old trainer or where
it was trained matter?

THE PAGE IS A SHELL. The content comes from one JSON endpoint the page itself
POSTs to: `{lang}` returns the index — every profile by race date, with its
brand number and the path of the profile — and `{lang, detail, path}` returns
the profile as HTML. The index reaches back to September 2025 and no further:
the 2024/25 folders answer empty. So this covers the imports from 2025/26 on,
about 600 at the time of writing and ten to twenty a week after that.

WRITTEN BY A CONTRACTOR, NOT HKJC, and in prose. The page says so. Who owned
and trained the horse is a sentence — "owned by Price Bloodstock Management
Ltd that under Allan & Jason Williams' yard in Australia" — read here by rule,
never by a model (Brett, 2026-09-22). A field the rules cannot find is None;
the paragraph itself is kept, so a reader can always see what was said.

AGENTS are named as an owner ("owned by Price Bloodstock Management Ltd") or a
buyer ("purchased by John Foote Bloodstock"). Every named bloodstock agent is
kept (Brett, 2026-09-28: track every named agent), with the aliases below
folded together so one agent is one name.
"""
from __future__ import annotations

import datetime as dt
import html as _html
import re
from collections.abc import Mapping
from typing import Any

import requests

from ._client import BASE_URL, FetchError, fetch_json

__all__ = ["ENDPOINT", "NewHorseError", "index", "profile", "parse", "agents",
           "page_url", "clean_name", "FX_AUD"]

ENDPOINT = f"{BASE_URL}/contentAsset/api/getNewHorse"
_HK = dt.timezone(dt.timedelta(hours=8))

# Rough Australian dollars per unit, 2024-25. Good for banding a sale price
# and for nothing finer; the currency and amount as sold are kept beside it.
FX_AUD = {"AUD": 1.0, "NZD": 0.91, "GBP": 1.95, "GNS": 2.05, "EUR": 1.65,
          "USD": 1.52, "ZAR": 0.083, "JPY": 0.0101, "HKD": 0.195}

# One agent, one name. The pages write the same operation several ways.
_ALIASES = {"David Price": "Price Bloodstock",
            "Price Bloodstock Management": "Price Bloodstock",
            "John Foote": "John Foote Bloodstock",
            "Andy Williams Bloodstock": "Andrew Williams Bloodstock",
            "Coolmore Upper Bloodstock": "Upper Bloodstock"}
_AGENT = re.compile(
    r"((?:[A-Z][\w'’&]*\s){1,3}?)(Bloodstock|Equine)"
    r"(?:\s(?:Management|Services|Agency|Limited|Ltd|Pty|LLC))*")
_PERSON_AGENTS = re.compile(r"\b(David Price|John Foote)\b")

_COUNTRIES = ("New Zealand", "Australia", "Ireland", "Great Britain", "England",
              "UK", "France", "Germany", "Italy", "USA", "Brazil", "Argentina",
              "Chile", "South Africa", "Japan", "Singapore")
_UKI = {"Great Britain": "UK/Ireland", "England": "UK/Ireland",
        "UK": "UK/Ireland", "Ireland": "UK/Ireland"}
_WORDS = {"no": 0, "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
          "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_N = r"(\d+|no|an?|one|two|three|four|five|six|seven|eight|nine|ten)"


class NewHorseError(FetchError):
    """A profile arrived but is not the page this module reads."""


# ── fetching ────────────────────────────────────────────────────────────────

def index(*, session: requests.Session | None = None) -> list[dict[str, Any]]:
    """Every profile the site lists, oldest race date first."""
    body = fetch_json(ENDPOINT, {"lang": "en-us"}, session=session)
    out = []
    for day in body.get("data") or []:
        for k in day.get("children") or []:
            when = dt.datetime.fromtimestamp(k["raceDate"]["dateValue"] / 1000, _HK)
            out.append({"path": k["path"], "brand_no": k["brandNo"]["value"].strip(),
                        "horse_name": clean_name(k["horseName"]["value"]),
                        "race_date": when.date().isoformat(),
                        "race_no": int(k["raceNo"]["value"]),
                        "horse_no": k["horseNo"]["value"].strip()})
    if not out:
        raise NewHorseError("the new-horse index came back empty — the endpoint "
                            "or its answer has changed")
    return sorted(out, key=lambda e: (e["race_date"], e["race_no"], e["horse_no"]))


def profile(path: str, *, session: requests.Session | None = None) -> str:
    """One profile's HTML."""
    body = fetch_json(ENDPOINT, {"lang": "en-us", "detail": True, "path": path},
                      session=session)
    html = ((body.get("data") or {}).get("contents") or {}).get("rendered") or ""
    if "IMPORT TYPE:" not in html:
        raise NewHorseError(f"{path}: no profile in the answer")
    return html


def page_url(entry: Mapping[str, Any]) -> str:
    return (f"{BASE_URL}/en-us/local/page/new-horse?racedate="
            f"{entry['race_date'].replace('-', '')}&raceNo={entry['race_no']}"
            f"&brandNo={entry['brand_no']}")


def clean_name(name: str) -> str:
    """'STEADFAST FORT ( L286 )' and 'PERFECT ONE ' are the card's names."""
    return re.sub(r"\s+", " ", re.sub(r"\(\s*[A-Z]\d{3}\s*\)", "", name)).strip().upper()


# ── reading ─────────────────────────────────────────────────────────────────

def _text(s: str | None) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _section(html: str, head: str) -> str:
    m = re.search(re.escape(head) + r"\s*</h6>(.*?)(?:<h6>|$)", html, re.S)
    return m.group(1) if m else ""


def _num(tok: str | None) -> int | None:
    if tok is None:
        return None
    return int(tok) if tok.isdigit() else _WORDS.get(tok.lower())


def _sales(text: str) -> list[dict[str, Any]]:
    out = []
    for m in re.finditer(
            r"(Sold|Unsold) as an? (weanling|foal|yearling|2YO|two-year-old|3YO|"
            r"horse in training|ready[ -]to[ -]run)\b(.*?)(?=(?:Sold|Unsold) as|$)",
            text, re.I):
        rest = m.group(3)
        ccy = amount = None
        gns = re.search(r"([\d,]+)\s*(?:gns|guineas)", rest, re.I)
        cash = re.search(r"\b([A-Z]{3})\$?\s*([\d,]+)", rest)
        if gns:
            ccy, amount = "GNS", float(gns.group(1).replace(",", ""))
        elif cash:
            ccy, amount = cash.group(1), float(cash.group(2).replace(",", ""))
        out.append({"sold": m.group(1).lower() == "sold",
                    "kind": m.group(2).lower().replace("two-year-old", "2yo"),
                    "ccy": ccy, "amount": amount,
                    "aud": round(amount * FX_AUD[ccy]) if amount and ccy in FX_AUD else None})
    return out


def _about(comments_html: str, name: str) -> str:
    """The paragraphs about this horse — not the ones about its sire."""
    first = name.split()[0]
    paras = [_text(p) for p in re.split(r"</?p\s*/?>", comments_html)]
    return " ".join(p for p in paras if p and first in p.upper())


def _trainer(s: str) -> str | None:
    m = re.search(
        r"(?:trained by|under(?! a | the | his | her | whip| urge| pressure| hands)) "
        r"([A-Z].+?)(?:[’']s? yard| in (?:South |Western |New South Wales|Queensland|"
        r"Victoria|[A-Z][a-z]+)| with | where |, (?=[a-z])| and (?:was|took|won|"
        r"finished|participat|scheduled|sent|led|dashed|raced)\b|\.(?:\s|$)|$)", s)
    if not m:
        return None
    v = m.group(1).strip(" ,.'’")
    return v if len(v) <= 60 and not v.lower().startswith(("the ", "a ")) else None


def _after(pattern: str, s: str) -> str | None:
    m = re.search(pattern, s)
    if not m:
        return None
    v = re.split(r"\s+(?:that|which|and was|and trained|and under|in [A-Z][a-z]+|"
                 r"with \d|where|at [A-Z]{3}\$)\b", m.group(1))[0]
    v = v.strip(" ,.'’")
    return v if v and not v.startswith("Mr") else None


def agents(text: str) -> list[str]:
    """Every bloodstock agent named in the text, one name each."""
    found = [(_ALIASES.get(m.group(0).strip(), None)
              or _ALIASES.get((m.group(1) + m.group(2)).strip(), None)
              or (m.group(1) + m.group(2)).strip())
             for m in _AGENT.finditer(text)]
    found += [_ALIASES[m.group(1)] for m in _PERSON_AGENTS.finditer(text)]
    return sorted(set(found))


def parse(html: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    """One profile as a row. `entry` is its line in `index`."""
    name = entry["horse_name"]
    imp = re.search(r"IMPORT TYPE:\s*([A-Z]+)", html)
    if not imp:
        raise NewHorseError(f"{name}: profile has no import type")
    about = _about(_section(html, "COMMENTS AND PROSPECT:"), name)
    sales = _sales(_text(_section(html, "SALES HISTORY:")))
    sold = [s for s in sales if s["sold"] and s["aud"]]
    best = max(sold, key=lambda s: s["aud"]) if sold else None
    record = re.search(_N + r" wins?(?:[^.]*?) in " + _N + r" starts?", about)
    starts = re.search(r"(?:unplaced|placed|raced) in " + _N + r" starts?", about)
    raced = bool(record or starts)
    trials = " ".join(s for s in re.split(r"(?<=\.)\s+", about)
                      if re.search(r"trial|jump[- ]?(?:out|put)", s, re.I))
    # Where it was TRAINED: the country in the sentence naming its trainer or
    # yard, and only failing that the first one the paragraph names — which on
    # GLORYSPEED's page is the dam's, not the stable's.
    places = "|".join(_COUNTRIES)
    kept = re.search(rf"(?:trained by|yard)[^.]*?\b({places})\b", about)
    country = kept.group(1) if kept else next(
        (c for c in _COUNTRIES if re.search(rf"\b{c}\b", about)), None)
    sire = re.search(r"PEDIGREE:.*?<td[^>]*>([^<(]+?)\s*\(", html, re.S)
    dam = re.search(r"DAM:\s*<br />\s*([^<]+?)\s*</td>", html)
    starters = re.search(r"NO\. OF STARTERS:\s*<br />\s*(\d+)", html)
    winners = re.search(r"NO\. OF WINNERS:\s*<br />\s*(\d+)", html)
    origin = re.search(r"COUNTRY OF ORIGIN:\s*([A-Z .]+?)\s*<br", html)
    return {
        "horse_name": name, "brand_no": entry["brand_no"],
        "profiled_for": entry["race_date"], "path": entry["path"],
        "url": page_url(entry),
        "origin": origin.group(1).title() if origin else None,
        "import_type": imp.group(1),
        "sire": sire.group(1).strip().title() if sire else None,
        "dam": dam.group(1).strip().title() if dam else None,
        "sire_hk_starters": int(starters.group(1)) if starters else None,
        "sire_hk_winners": int(winners.group(1)) if winners else None,
        "sale_kind": best["kind"] if best else None,
        "sale_ccy": best["ccy"] if best else None,
        "sale_amount": best["amount"] if best else None,
        "sale_aud": best["aud"] if best else None,
        "sales_text": _text(_section(html, "SALES HISTORY:")) or None,
        "prev_name": _after(r"formerly known as ([A-Z][A-Z'’ .-]+)", about),
        "prev_trainer": _trainer(about),
        "prev_owner": _after(r"owned by (?:current syndicate member |the )?"
                             r"([A-Z][\w&.’' ()-]+(?: [A-Z&(][\w&.’' ()-]*)*)", about),
        "buyer": _after(r"purchased by (?:current owners? |trainer |agent )?"
                        r"([A-Z][\w&.’' -]+?) (?:at|for|in|from)\b", about),
        "prev_country": _UKI.get(country, country) if country else None,
        "agents": agents(about),
        "overseas_starts": _num(record.group(2)) if record else
        (_num(starts.group(1)) if starts else None),
        "overseas_wins": _num(record.group(1)) if record else (0 if starts else None),
        # A trial verdict only for a horse that never raced: a raced horse's
        # "to win" is about its races.
        "trial_won": None if raced or not trials else bool(re.search(
            r"\bto win\b|\bwin (?:an?|the) \d|\bwith (?:\d+|one|two|three) wins?\b|\bwon\b",
            trials)),
        "about": about or None,
    }
