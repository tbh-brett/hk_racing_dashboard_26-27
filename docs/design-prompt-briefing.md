# Design prompt — the Briefing

Paste the section below the rule into Claude Design. Everything above it is
for us, not for the canvas.

**Why this exists.** The Briefing grew in two halves designed months apart: the
tips/market board (Sept, artboard `Briefing.dc.html`) and the Screen (24 Sep,
built straight in code with no artboard). Nobody designed the page that holds
both.

**The finding that should drive the redesign.** Brett reads this page **the
night or the day before a meeting**, to work out which two or three races
deserve real study. Measured on the 27 Sep card on the morning of 25 Sep —
two days out, before any source is due:

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

**But "the night before" is not "nothing yet".** The sources publish on a
clock, measured in the tips work (see the header of `ops/tips.ps1`): 賽馬Fact
Check at **20:00 two nights before**, Racing To Win's preview and its jockey
and trainer interviews at **about 16:00 the day before**, and the HKJC tote
opens **around midday the day before** (thin — AGENTS.md). Racing & Sports
arrives with the bookmakers' markets; on 23 Sep it was up the morning of the
meeting, and on the morning of 25 Sep Ladbrokes had not yet listed the 27th.
So by the night before, three of the four tip sources and the interviews are
normally in, and only the bookmakers are missing. The page has three states,
not two, and the pundit half is full in the one Brett reads most.

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
**the night before or the day before**, to triage: *which two or three races
on this card are worth real study, and which horses in them are worth
reading?* He is deciding where to spend his time, not what to bet.

What exists depends on how far out he is, because each source publishes on
its own clock:

| When | What has landed |
|---|---|
| **Two days out** (cold) | nothing priced, no tips — only what the dashboard computes itself |
| **The night before** (the usual read) | 賽馬Fact Check (posts 20:00 two nights before) · Racing To Win's preview and its **jockey and trainer interviews** (about 16:00 the day before) · the HKJC tote, open since about midday but thin |
| **Race day** | everything: Racing & Sports and the bookmakers' fixed odds, a deep tote, money moving |

**Every state must look deliberate.** A page that only looks right once
prices arrive is the wrong page. The market material must be present when it
exists and cost almost nothing — no reserved columns, no placeholder rails, no
"—" grid — when it does not. But the pundit material is normally **there** the
night before, and the interviews in particular are what the owner has called
the most important words on the page: they must never be the part that gets
folded away to make room.

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

**Three states, all drawn:** the **cold** page (two days out — no odds, no
tips, no pools), the **night before** (pundits and interviews in, a thin tote,
no bookmakers — the one read most), and **race day** (everything, money
moving). Show how each grows out of the one before without the layout
jumping.

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

**Pundits, interviews and commentary — usually present from the night
before.** The owner asked for these by name: "the jockey interview is vital
and deserves to be highlighted", and "which race and pick, and the sources
and pundits supporting it", most supporters first. Everything below exists
and is served today (`/api/tips/summary/{date}`); the current artboard
`Briefing.dc.html` draws most of it.

- **Jockey and trainer interviews** (Racing To Win): the highlighted
  treatment at the top of their race, in the lime `--voice` colour — role,
  speaker, which horse, and what they said
- **what each source says about each horse it backs**, verbatim, never
  paraphrased. Long quotes fold to one line and open on click; every quote
  links to the second it was said (`▸ 2:14`) or to the page it came from
- **Chinese quotes show the original first**, with the English line under it
  where there is one, never instead of it
- **three kinds of support, kept visibly different**: a *pick* with the
  tipster's rank (`R&S #1`, `RTW #2`), a *featured* segment (`FC FEAT`,
  dashed), and an *interview* (`INT J` / `INT T`, the voice colour). A pick
  that came through speech-to-text carries a quiet `~` ("heard")
- **the race comment** from Racing & Sports — one paragraph per race
- **a one-line form comment on every runner** from Racing & Sports ("Was a
  first-up winner last start…"). This is form, not support: it is shown
  under the horse and never counted as a source. It comes only from
  Sportsbet, which has refused automated reads since 24 Sep, so design it
  to be absent without a gap
- **each backed horse's price in every market**: the tote and each
  bookmaker (Ladbrokes, Sportsbet, later Unibet) side by side — win, place, each market's fair chance, and what
  the price is worth if the other market is right — with the best-paying
  market marked. When no bookmaker has priced yet, this shrinks to the tote
  alone rather than showing empty columns
- the order within a race is by **number of distinct sources**, most first.
  Racing & Sports is ONE source whichever bookmaker prints it — Ladbrokes'
  comment and Sportsbet's "Expert Tips" are the same words

The open question for the design: how the Screen's shortlist and the
pundits' picks meet. A horse the Screen rates and three sources back is a
different finding from one the Screen rates and nobody mentions, and from one
three sources back that the Screen does not rate. Showing that agreement or
disagreement per race is likely worth more than either list alone. Keep them
visibly different kinds of evidence — the Screen is a computed chance, support
is a count of people — and never merge them into one score.

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
