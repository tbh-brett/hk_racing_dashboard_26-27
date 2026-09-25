# Design prompt — the Briefing

Paste the section below the rule into Claude Design. Everything above it is
for us, not for the canvas.

**Why this exists.** The Briefing grew in two halves designed months apart: the
tips/market board (Sept, artboard `Briefing.dc.html`) and the Screen (24 Sep,
built straight in code with no artboard). Nobody designed the page that holds
both.

**The finding that should drive the redesign.** Brett reads this page **the
night or the day before a meeting**, to work out which two or three races
deserve real study. Measured on the 27 Sep card in exactly that state:

| | |
|---|---|
| Page height, one race open | 5,930px — **6.5 screens** |
| The Screen section | 4,600px |
| The tips/market board | 1,228px + a full right rail |
| **Board cells that are empty** | **33 of 44** |
| The "where the markets disagree" rail | one placeholder sentence |
| Sources landed | 0 of 4 |

So about three quarters of the market half is blank at the moment it is read,
and it still takes a quarter of the page and the whole right rail. On top of
that, **every race is listed twice** — once in the Screen and again in the
board 2,700px below — with two independent open/close states.

Read `docs/design-loop.md` first. Artboards land in `web/design-source/*.dc.html`
and `tests/test_conformance.py` reads them. Do not round-trip token values,
type sizes or behaviour — those are cheaper to say in words.

---

## THE BRIEF

You are redesigning the **Briefing** page of a Hong Kong horse racing
dashboard. It is a private tool with a single reader, an experienced punter.
It is not a consumer product and nothing on it is sold.

### When it is read, and what for

Hong Kong races twice a week, 8–11 races a meeting. The owner opens this page
**the night before or the day before** — and at that hour **there are no odds,
no tipster picks and no prices of any kind.** None. The tote has not opened,
no bookmaker has framed a market, and none of the four tip sources have
published.

His job at that moment is **triage**: *which two or three races on this card
are worth real study, and which horses in them are worth reading?* He is
deciding where to spend his time, not what to bet.

**Design for the empty state first.** A page that only looks right once prices
arrive is the wrong page, because he is rarely there when they do. The
market and tips material must be present when it exists and must cost almost
nothing — no reserved columns, no placeholder rails, no "—" grid — when it
does not.

### What it knows before any price exists

This is the whole of what the page has to triage with, and it is a lot:

- a **chance to win and to place** for every runner, computed from the form
  rating, the rider's record, the pace shape, recent barrier trials and the
  horse's circumstances (gate change, class, stable change, trouble last start)
- the **pace shape** of each race — how many habitual front-runners it holds,
  which is the single strongest structural read on a race
- **barrier trials**, rated, since each horse's last run
- the **owner's own saved horses** ("blackbook") with a verdict on whether
  today's conditions suit them
- what the stewards wrote about each horse's **last start**
- which horses in the field have **met each other before**

### What to design

**One page, one entry per race, built for triage.** Beyond that the layout is
yours. Three directions, most promising first — if a fourth is better, show
that instead.

1. **Triage first.** The page opens by answering "these are the races worth
   your time, and here is why" — ranked or marked, not just listed in race
   order. The rest of the card stays available but quiet. Drill into a race
   for full depth.
2. **Overview plus one race in depth.** A compact, always-visible strip of all
   eleven races, scannable in a second, with the selected race shown in full
   below or beside it. You move between races rather than scrolling down a page.
3. **One merged row per race.** A single list of eleven, each row carrying the
   pre-price read, and the market read folded in only once it exists.

**Two states, both drawn:** the **cold** page (night before — no odds, no
tips, no pools; this is the important one) and the **warm** page (race morning,
prices and tips arriving). Show how the second grows out of the first without
the layout jumping.

Widths: **desktop 1320–1600px** and **phone 390px**. The current page has a
separate phone layout, so phone is first-class.

**A question worth answering in the design:** what actually makes a race worth
studying? Candidates the data can support — a lone front-runner in the field,
a clear standout on the shortlist versus a muddle, a horse whose circumstances
disagree sharply with its raw form figures, a blackbook horse whose conditions
are met, a standout barrier trial, a short field. Deciding which of these earn
a mark, and how a race gets ranked or flagged, is part of the design.

### The content — everything below must survive

**Pre-price, per race (the important half):**

- race number, off time, distance, class (`C4`, `G3`, `C3 (R)`, `4YO`, `GRIF`),
  course, field size
- **pace shape**: how many habitual front-runners, their numbers, and a
  proportional bar of the field's four running styles
  (Leader / On-Pace / Midfield / Closer)
- **shortlist**: the four highest-rated horses, each with a percentage chance
  to place and a small bar
- **also a case**: up to three more whose circumstances make a case, each with
  its strongest single reason as a short chip
- **your book**: the owner's saved horses here, each with a
  FAVOURABLE / NEUTRAL / AGAINST verdict on today's set-up, and its rank
- a credibility line: "top 4 held the winner 62% · form alone 59% ·
  closing tote 71%"

**Per race, expanded:** every runner, with the measured reasons for and against
as chips, the last start in the stewards' words, the latest barrier trial, the
owner's own notes, and any horse in the field it has met before.

**Market and tips — present only when they exist:**

- tote and bookmaker price times, or the fact that neither is open
- market concentration, the favourite and its price
- the most-backed horse, how many of the four sources back it, a mark per source
- its price at each market, best-paying one marked
- the biggest disagreement between two markets, as a percentage
- a "where the markets disagree" list (top runners by value)
- a "sources for this meeting" list — what each tipster published and when
- total money across all pools

**A race that has already run** collapses to one line with a link to the
result. Mid-meeting the card is part settled and part upcoming, and that mix
should look deliberate.

### Visual system — follow it exactly, invent no colours

Dark, dense, data-first. Not cards and drop shadows: closer to a terminal or a
Bloomberg screen. Existing pages are tables with hairline rules on near-black.

```
background        #0e1117    page
                  #12151b    panels, nav, table chrome
                  #1a1d23    raised
rules             #1c202a → #262a33 → #2b303a   (subtle → strong)
text              #fafafa primary · #cfd3da · #8b929e workhorse ·
                  #6f757f dim · #4c525c faint
```

One colour, one meaning, across the whole app:

```
#f0a824  amber    the model's own edge
#2ec4b6  teal     the owner's blackbook
#35b37e  green    a win / a positive result
#e0637f  pink     a loss
#e63946  red      alert, odds firming
#c8792f  copper   stewards / trip trouble
#e3d9c2  paper    a tipster's pick           (the Briefing's own)
#c9dc6e  lime     a jockey or trainer spoke  (the Briefing's own)
```

Running style is four **distinct hues**, never brightness steps — Leader
`#ff8a5c`, On-Pace `#e8c04a`, Midfield `#9aa7b8`, Closer `#4fb3e0` — always in
that order, front of the field to the back. Race class is a five-step ramp
`#dba07a → #6f8296`, with Group `#e6c260` and Griffin `#c9a888` marked as
categorically different rather than as a sixth step.

Type: **IBM Plex Sans** and **IBM Plex Mono**. Only four sizes — 10 / 12 / 13 /
14px — and the page leans on the small end. Numbers tabular, numeric columns
aligned. Spacing steps: 4 / 6 / 9 / 14 / 16 / 24px.

### Rules

- **Never invent a colour.** Every value above is a named token. If a new
  meaning genuinely needs one, say so in a note rather than picking it.
- **Never present a bare number.** Every figure carries context — a rank, a
  percentage, a sample size, or a comparison.
- **Support is a count of sources, never a star rating or a verdict.**
- **Nothing on this page re-ranks the main race card.** It is a briefing.
- **Absence must read as absence, not as failure.** "The tote has not opened"
  is a normal Tuesday, not an error, and should not look like one — but it also
  must not be mistaken for "no support for this horse".

### Not in scope

Do not redesign the top chrome (meeting bar and nav) — shared across eight
pages and settled. Do not change colour values, type sizes or spacing steps;
those are one-token changes made in code.
