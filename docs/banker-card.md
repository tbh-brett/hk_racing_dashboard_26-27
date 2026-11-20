# The banker card: your record beside every fact

Asked for on 2026-11-17: *the banker is what I need most help with — I pick
on intuition after plenty of deliberation and seldom keep track of why. I want
to know whether my preferences pay, if they don't, and how to get the most out
of the quinella.*

Three pieces. Since 20 Nov 2026 the brief and the taps are on the Desk page
(`pages/desk.html`) and the scorecard stays on the Bets page:

- **The brief** (DESK view): every candidate banker's facts, each beside how
  *your* bankers with that fact have placed against the price.
- **Reason taps** (DESK view): when you TAKE or PASS, a tap or two for what the
  database can't see, plus a line of your own.
- **The scorecard** (BANKERS view): what you lean towards and which of it has
  paid, kept live.

Code: `hkrd/query/banker_facts.py` (the fourteen facts, one definition),
`hkrd/query/banker_record.py` (the scorecard and the tap vocabulary),
`hkrd/query/banker_brief.py` (the brief), `jobs/decide_ticket` (the taps),
`jobs/record_tickets` (keeps the facts once per race), `runner_fact` and
`ticket_reason` in `schema.sql`. Page: `web/assets/desk.js`,
`web/assets/banker-card.js`, `web/assets/desk.css`. Route:
`GET /api/tickets/scorecard`. Tests: `tests/test_banker_card.py`.

---

## What it was built on

Measured on production's database as pulled on 17 Nov 2026 (settled to
14 Nov): 255 of your bankers since April, 224 races. "A/E" is how often a
banker placed divided by how often the price said it would. The price is the
place pool where it was captured, else the starting prices. 1.00 is the market.

| | Bankers | A/E |
|---|---|---|
| Every banker | 255 | 1.18 [0.96, 1.40] |
| With your own evidence before race day | 120 | 1.30 |
| Without it | 126 | 1.07 |
| Not placed before at this course and trip | 90 | 1.62 |
| Placed there before, no evidence of yours | 97 | 0.91 |
| Model's top three (October on) | 59 | 1.52 |
| Model 4th or worse, no evidence of yours | 26 | 0.41 |

Your own evidence and "not placed at this course and trip" held in both halves
of the season. The model line is one season. Fourteen facts are read, so some
lines will clear 1.00 by luck. The scorecard is there to show which ones keep
clearing.

**The line to clear is not 1.00.** A quinella or quinella place ticket gives up
the 17.5% takeout, so a banker has to land roughly 1/(1 − 0.175) ≈ **1.21**
times as often as the pools expect before its tickets pay
(`banker_record.BREAKEVEN`).

## The facts

`banker_facts.FACTS`, as at the race: runs, trials and notes from strictly
before the race day, a book entry live on it, a model score from before the
off. A fact that was not kept then is *unknown*, never "no": tips from 6 Sep
2026, the model from 1 Oct 2026.

Your own evidence · the model's rank · placed before at this course and trip ·
place in the market · last start · beaten narrowly last start · since its last
run · class against its last start · draw · how it usually runs · rider's last
365 days · trialled since its last run · gear on for the first time · tipped.

The market is the starting price for a run with a result, and the capture the
ticket was priced on for a card.

## The desk page, rebuilt 19 Nov

The first DESK view (17 Nov) put all nine races on one long page, one click per
candidate, with a fourteen-fact brief on each. Brett did not use it: "not at all
intuitive (infinite scroll)", and its purpose was unclear. He wrote his banker
reasoning for 18 Nov in a text file instead. Rebuilt the next day around how he
actually decides, race by race, comparing his own horses (`web/assets/desk.js`,
`desk-detail.js`):

- **Race chips.** One per race, showing how many of his horses run and a ✓
  once he has decided. The next race to run opens first.
- **One table per race.** His candidates side by side, with:
  - how strong his evidence is (the trial-note response he tapped, or the
    book entry and its excuse) and how many days before the race he wrote it;
  - the place pool's chance;
  - the pace model's chance of leading (`runner_shape`, as the Speed Map has
    it), with the race's three likeliest leaders listed above the table;
  - the model's rank;
  - the facts his record has gone better (▲) or worse (▼) with, by name.

  On 18 Nov his R7 case rested on RIDING TOGETHER leading, which the pace
  model gave 13%.
- **One row opens.** His words, his record, the ticket, then TAKE or PASS.
- **The multi.** How often the bankers he has taken would all place, beside
  the likeliest of his own horses in the same races. On 18 Nov his three were
  about 5% at five minutes out; PRESTIGE GOOD and VIRTUS GLORY, both his own
  book horses, would have made it about 10%.
- **Races that have gone off stay on the page** with the result, the pair
  dividends and where each of his horses finished, so a night can be reviewed
  race by race.

The BANKERS view opens on two lists, what has paid for him (at break-even or
better) and what has not (below the price), each line with its count and
interval. A side of his evidence is listed only where it says something the
fact alone does not. His reasons follow, and every fact in full is folded below.

## The brief

Everything is read on one side of your evidence. Each side has a usual: 1.30
with your evidence, 1.07 without. **Each fact is marked against your usual on
its side, not against break-even.** The first build marked against break-even,
and every horse you had written about inherited 1.30. Ten facts of ten read FOR
on a real card, which says nothing about which of your horses to choose.

| Mark | Means |
|---|---|
| ▲ STRONGER | your bankers with this fact did 1.15× your usual or better |
| ▼ WEAKER | 1/1.15 of it or worse |
| AS USUAL | between |
| THIN | under 30 bankers on your side, and under 30 over all your bankers |
| — | not kept, or not known, for this race |

The number's colour is the money: green at break-even or past it, red below
the market. The evidence fact itself is read against the other side. A side
with fewer than 30 bankers on a fact falls back to all your bankers, and the
tooltip says so.

On 14 Nov race 1, read with the results removed: I AM THE WALRUS (your trial
note) was 3 ▲ · 1 ▼. ▲ for not having placed at the course and trip (1.61 over
52) and for racing on the pace (1.52 over 32), ▼ for having trialled since its
last run (1.13 over 51). MAJESTICUS in the same race was 2 ▲ · 4 ▼.

## The taps

`banker_record.REASONS`: what I saw in a trial · in a run · the pace and the
map · the rider · the stable or connections · market support · a weak race ·
the draw · class or weight · gut. Optional, on a pass as well as a take, and
replaced along with the decision. A tap outside the list is refused, because
nothing could score it. The facts the database can see are not asked for:
they are in the brief, and the brief is kept with the ticket you saw
(`ticket_decision.seen_json`).

The BANKERS view scores each tap for the bankers you took and the ones you
passed. A passed banker that placed less often than the price expected was a
good pass. Only decisions made before the off count.

## The scorecard

For each fact and value:

- **LEAN:** how much more often your banker had it than the field did.
- **YOURS:** your bankers with it, against the price.
- **WITH / WITHOUT YOUR EVIDENCE:** the same, split.
- **FIELD:** the runners with it you did *not* bank, in the same races. Your
  number above the field's is your choosing; both above 1.00 means it's the
  fact, not you.

Your books only: `brett` and the legacy `personal` log. Kelvin's sheet is his.

**It is cheap because the facts are kept.** Working fourteen facts out for
every runner of every race you banked in took 535 ms of a 640 ms read.
`jobs/record_tickets` now keeps them once per race with a result
(`runner_fact`, versioned, never rewritten), and the scorecard reads them back.
A race not kept yet is worked out on the spot. What's kept in memory is the slow
half, held until a bet is added, edited or removed, or a result lands, and a
day at most. Your decisions and taps are the quick half, read on every request,
so deciding never triggers the slow read.

**When it is slow, measured on the server (17 Nov).** The first version also
worked the slow half out again after every note or book entry and every hour.
That made the next DESK or BANKERS open the slow one several times a day (6
trial notes, 7 run notes and 7 book entries on 16 Nov alone) without changing
any answer: a note only counts for a race it was written before. The desk also
worked out the book tiers' own record, which it never shows: 155 ms, or 405 ms
the first time. Both are gone, and the API works the scorecard out on a thread
as it starts, so a deploy does not leave the cost to your first open. On a fully
priced eleven-race card (14 Nov, 53 candidates, on the copy), the DESK view's
work now takes:

| When | Before | After |
|---|---|---|
| The first open in a fresh process | 469 ms | 240 ms (and warmed at start-up) |
| The first open after a new result or bet | 391 ms | 241 ms |
| Any other open | 112 ms | 118 ms |

The BANKERS view alone: about 165 ms after a result, 26 ms otherwise.

## To deploy

`init_db` creates `runner_fact` and `ticket_reason`. The cron lines already
carry `record_tickets --pending`, which keeps each new meeting's facts.

**On the server, once, after this is deployed**, to give the scorecard the
season so far (about 6,200 runners over 50 meetings, 5 s on the copy). From
PowerShell on this PC, which hands the command to the Fly machine:

```
fly ssh console -a hkrd -C "nice -n 19 python -m hkrd.jobs.record_tickets --pending --facts-since 2026-04-01 --db /data/hkrd.db"
```

Not on the PC itself: `nice` and `/data/hkrd.db` exist only on the machine,
and the job is not there until the deploy. Until it has run, the scorecard
works the missing facts out itself each time its kept half refreshes (after a
new result, bet or note, and at most hourly): about 0.8 s once each time, and
correct, only slower.

## Not built

- **Ticket shape** (three partners by default, a flag on long-shot pair lines,
  weighted stakes past three). Measured on 17 Nov, not built.
- ~~**Your verdict after the result**~~ **Built 20 Nov**: the REVIEW view
  (`docs/banker-review.md`). The desk's TAKE or PASS now also takes how sure
  you are, and writes it with your sentence as the horse's claim there.
