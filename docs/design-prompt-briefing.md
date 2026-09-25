# Design prompt — the Briefing

Paste the section below the rule into Claude Design. Everything above it is
for us, not for the canvas.

**Attach, in this order:**

1. `web/design-source/Briefing.dc.html` — the current board, the look to keep
2. `web/assets/tokens.css` — every colour, size and step
3. the four samples of `GET /api/briefing/{date}`, one per stage of the days
   before a meeting (real data; each file's `_note` says what, if anything,
   was adjusted). They are **not in the repo**: they quote tipsters and
   bookmakers at length and the repo is public. They live on the PC in
   `Claude outputs/briefing-design/`:
   `briefing-1-two-days-out.json`, `briefing-2-night-before.json`,
   `briefing-3-race-day.json`, `briefing-4-mid-meeting.json`
4. screenshots of the current page, if to hand

**Why this was rewritten (2026-09-25).** The Briefing grew as two tools
stacked on one page — the Screen (who is a chance, computed from the card)
and the board (what tipsters, jockeys and trainers said, and the prices) —
each designed on its own, with its own prompt, each treating the other as an
add-on. Every race was listed twice, 2,700px apart, with two open/close
states. And one prompt read "I start before the odds are out" as "there are
no odds", when the owner means: *I start early, and when odds exist I want
them too.* The owner's answers on 25 Sep settled the rest:

| Question | Answer |
|---|---|
| How is the card laid out? | **Worth studying first** — the two or three races with the most going on at the top, with the reason; the rest follow quietly in race order |
| Is it also the race-day page, on the phone at the course? | **Yes, it follows the day** — prices, late money and time to the off keep updating until the last race |
| Which voices count? | Racing & Sports, Racing To Win (preview and interviews), 賽馬Fact Check, and **神探賽馬 Horse Detective** (Threads). 全方位Bryan is shown, not counted |
| Build the data first? | Yes — `GET /api/briefing/{date}` is built, one answer per race. See `docs/briefing.md` |

Read `docs/briefing.md` for the architecture and `docs/design-loop.md` for how
artboards come back. Artboards land in `web/design-source/*.dc.html` and
`tests/test_conformance.py` reads them. Do not round-trip token values, type
sizes or behaviour — those are cheaper to say in words.

---

## THE BRIEF

You are designing the **Briefing**, the front page of a Hong Kong horse
racing dashboard. It is a private tool with a single reader, an experienced
punter who bets the HKJC tote. It is not a consumer product and nothing on it
is sold.

### What the page is for

The dashboard already knows a great deal about every card — a computed chance
for every runner, the pace, trials, the stewards' reports, the owner's own
saved horses — and outside sources add more: tipsters' picks, what the jockeys
and trainers said in interviews, bookmakers' prices beside the tote. Today the
owner has to go through all of it race by race. **The Briefing does that
screening for him** and puts in front of him, per race, what is worth
reading, with the evidence beside it.

It is a briefing, not a bet slip: nothing on it says what to bet.

### Three kinds of evidence — keep them visibly apart

Every fact on the page is one of three kinds. The design must make the kind
obvious at a glance, and **must never blend them into one score or verdict**:

| Kind | What it is | When it exists |
|---|---|---|
| **Computed** | the dashboard's own reading: each runner's chance to win and to place before any price (the "Screen"), the reasons for and against it, the race's pace shape, trials, blackbook, last start, rematches | as soon as the card is published, two or three days out |
| **Said** | what people said: tipsters' picks and their words, featured segments, **jockey and trainer interviews**, the race comment and a form line per runner from Racing & Sports | on each source's own clock (below) |
| **Priced** | the markets: the HKJC tote, bookmakers' fixed odds (Ladbrokes, Sportsbet), money moving, the gap between markets | the tote from about midday the day before; bookmakers race-day morning; live to the off |

A horse the Screen rates, three sources back and the market makes favourite
is a different finding from one the Screen rates and nobody mentions, or one
three sources back that the Screen ranks ninth. **Seeing where the three
agree and where they disagree, per horse, is the point of putting them on one
page.** Show all three side by side on each runner so that agreement is
visible without reading.

### The days before a meeting — design every stage

The owner starts early and keeps coming back. What exists depends on when:

| Stage (`stage` in the data) | When | What has landed |
|---|---|---|
| `cold` | two days out | the card and the Screen only |
| `voices` | the night before — **the most-read state** | 賽馬Fact Check (posts 20:00 two nights before), Racing To Win's preview and its **jockey and trainer interviews** (about 16:00 the day before) |
| `priced` | from about midday the day before | the tote is open, but **thin**: one bet moves it. Shown, never treated as a reading of the market |
| `race_day` | race-day morning to the last race | Racing & Sports, bookmakers' prices, Horse Detective (race-day morning, when it posts at all), a deep tote, money moving, **races going off one by one** |
| `settled` | after the last race | everything run |

**Odds are shown whenever they exist**, at every stage, with the time they
were captured. Before race day they are marked as thin/early; on race day
they are the live read. What must never happen is a layout that only looks
right once prices arrive: absent material costs almost nothing — no reserved
columns, no "—" grids, no placeholder rails — and the page grows into the
next stage **without the layout jumping**.

The data carries a `clock`: every source with its state — `in` (with when),
`due` (with when it usually lands), `overdue`, or `irregular` — and a
one-line "usually" for each. Something like "Fact Check · due tonight 20:00"
is how absence should read: a normal Tuesday, not an error, and never to be
confused with "nobody backs this horse".

### The layout the owner chose

1. **Worth studying first.** The data gives `order` — races by how many
   different things there are to read in them — and every race's `reasons`.
   The top two or three races come first, each with its reasons in a line or
   two. Every other race follows in race order, quieter. **Each race appears
   once** on the page, with one open/closed state.
2. **One entry per race**, carrying all three kinds of evidence. Closed, it
   is scannable in a second; open, it holds the full field.
3. **It follows the day.** On race day the next race to go is obvious, each
   race shows minutes to the off, prices update in place with their capture
   time, a race past its off with no result yet reads as running, and a race
   with a result collapses to one line with a link to the result. Mid-meeting
   the card is part settled, part live, part upcoming, and that mix should
   look deliberate.
4. **Phone is first-class** (390px): he reads it at the course twenty minutes
   before a race. Desktop 1320–1600px.

### A race, closed

- race number, off time (and minutes to the off on race day), distance,
  class (`C4`, `G3`, `C3 (R)`, `4YO`, `GRIF`), course, field size
- **its reasons**, each marked by kind (computed / said / priced). Real
  examples from 23 Sep: *"#11 PRESTIGE ALWAYS: 3 sources back it, the Screen
  ranks it 5 of 12"*, *"Nichola Yuen (jockey) on #11 PRESTIGE ALWAYS"*, *"The
  Screen's first choice #1 FLYING WROTE is backed by none of the 4 sources
  that tipped this race"*, *"#4 MEGA MASTERMIND is the only habitual leader
  (×1.28 measured)"*, *"#3 SKY CAP: the tote pays 14, +34.0% if the
  bookmakers have its chance right (9.6%)"*
- **pace shape**: how many habitual front-runners, their numbers, and a
  proportional bar of the four running styles (Leader / On-Pace / Midfield /
  Closer)
- **the Screen's shortlist**: its top four by chance to place, each with a
  small bar — and on each, whether the sources back it and where the market
  has it
- the market, when open: the favourite and its price, concentration, win
  pool

### A race, open

**Interviews first**, highlighted in the lime voice colour: role, speaker,
the horse, and what they said. The owner calls these the most important
words on the page; they are never the part folded away to make room.

Then **every runner, one row each**, in the Screen's order, carrying:

- number, name, Chinese name, draw, jockey, trainer
- **computed**: chance to place (and win), shortlist / "also a case" /
  field, the blackbook mark and its FAVOURABLE / NEUTRAL / AGAINST set-up
- **said**: how many sources back it and a mark per source — a *pick* with
  the tipster's rank (`R&S #1`, `RTW #2`, `HD`), a *featured* segment
  (`FC FEAT`, dashed), an *interview* (`INT J` / `INT T`, lime). A pick heard
  through speech-to-text carries a quiet `~`
- **priced**: the tote price, each bookmaker's, the best-paying marked, and
  its move — late money firming in red
- first-time gear, a vet finding, a new trainer — small chips in their
  existing colours

A runner opens to its depth: the measured reasons for and against (×1.28
style chips), its last start in the stewards' words, its latest trial, the
owner's notes, horses in the field it could turn around — **and what each
source said about it, verbatim**: long quotes fold to a line and open on
click, each links to the second it was said (`▸ 2:14`) or the page it came
from, and Chinese shows the original first with any English under it. Racing
& Sports' one-line form comment sits here too; it is form, not support, and
is not counted. Beside it, the price table: every market's win, place, fair
chance, and what the price is worth if the other market is right.

Racing & Sports' race comment (a paragraph) belongs to the race.

### Across the meeting

- **the clock**: which sources are in and which are due, as above
- **where the markets disagree**: the runners one market prices well above
  another's chance, race day only
- total money across all pools, the tote and bookmaker capture times

Keep these light. On a cold or night-before page most of them are small or
absent, and they must not take a rail or a column the triage needs.

### The data

`GET /api/briefing/{date}` — four real samples attached. The shape, briefly:

```
stage, as_of, clock[], order[], order_rule, fit{}, edges[], source_status[]
races[]: race_no, off_time, minutes_to_off, distance, race_class, course,
         field_size, run, pace{leaders[], counts{}}, reasons[{key, kind, text,
         horse_no}], market{favourite, concentration, band, win_pool,
         overround{}} | null, voices{sources[], interviews[], comments[]},
  runners[]: horse_no, horse_name, name_zh, draw, jockey, trainer, style,
             gear_first[], result, blackbook | null,
             screen{rank, tier, win_pct, place_pct, for[], against[], setup,
                    last_start, trial, notes[], reversals[]},
             support{sources, backed_by[{kind, source, who, rank, heard,
                    words[{text, en, t, url}]}]} | null,
             form_line{text, url} | null,
             price{tote{win, place, fair_pct, value_pct}, ladbrokes{…},
                   sportsbet{…}, pays_most} | null,
             market_rank, move{change_pct, rush_pct, rush_direction} | null
```

`reasons[].key` is one of: `lone_leader`, `book`, `trial`, `case` (computed);
`interview`, `consensus`, `talked_up`, `screen_alone` (said); `price_gap`,
`late_money`, `market_apart` (priced — race day only).

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

The three kinds of evidence need telling apart. **Said** already has its
colours (paper, lime). **Priced** stays uncoloured — full text when a value
is +5% or better, dim when negative — because amber is the model's edge and
red is firming. **Computed** is the Screen's existing treatment. If the kinds
cannot be told apart without a new colour, say so in a note and propose at
most one token; do not pick one silently.

Running style is four **distinct hues**, never brightness steps — Leader
`#ff8a5c`, On-Pace `#e8c04a`, Midfield `#9aa7b8`, Closer `#4fb3e0` — always in
that order, front of the field to the back. Race class is a five-step ramp
`#dba07a → #6f8296`, with Group `#e6c260` and Griffin `#c9a888` marked as
categorically different rather than as a sixth step.

Type: **IBM Plex Sans** and **IBM Plex Mono**. Only four sizes — 10 / 12 / 13 /
14px — and the page leans on the small end. Numbers tabular, numeric columns
aligned. Spacing steps: 4 / 6 / 9 / 14 / 16 / 24px.

### Rules

- **Never invent a colour.** Every value above is a named token.
- **Never present a bare number.** Every figure carries context — a rank, a
  percentage, a sample size, a capture time, or a comparison.
- **Three kinds, never one score.** The Screen's chance, the count of
  sources and the price sit side by side; nothing adds them up.
- **Support is a count of sources, never a star rating or a verdict.**
  Racing & Sports is one source however many bookmakers print it.
- **Quotes are verbatim.** Folded, never rewritten; linked to the second.
- **The order is a reading order, not a tip.** "Worth studying first" is a
  count of things to read in a race, and the page should say so once.
- **Nothing on this page re-ranks the main race card.** It is a briefing.
- **Absence reads as absence, not failure** — and never as "no support".

### Not in scope

Do not redesign the top chrome (meeting bar and nav) — shared across the
pages and settled. Do not change colour values, type sizes or spacing steps;
those are one-token changes made in code. Placing bets and editing tips are
out of scope.
