# The banker card: your record beside every fact

Asked for on 2026-11-17: *the banker is what I need most help with — I pick
on intuition after plenty of deliberation and seldom keep track of why. I want
to know whether my preferences pay, if they don't, and how to get the most out
of the quinella.*

Three pieces, all on the Bets page:

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
A race not kept yet is worked out on the spot. What's kept is the slow half,
held until a bet, a result, a note or the book changes. Your decisions and taps
are the quick half, read on every request, so deciding never triggers the slow
read. Measured on the copy: 212 ms cold, about 30 ms warm.

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
- **Your verdict after the result** (why it paid or failed, and whose fault).
  The record names what went wrong; there is no table for your own verdict yet.
