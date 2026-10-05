# The book's tiers: which of the book's horses to back

Asked for on 2026-10-05: *the automatic screen for trials and results, is it
recurring? Look at the wording of the trial and results notes, mine and HKJC's,
for patterns that turn into winners; I wrote a note on CAVAMAZING, it won, and
I forgot to bet it. And races with plenty of book horses are hard to judge: a
better system for that.*

Code: `hkrd/query/book_tier.py` (the rule and its record), read by
`query/screen` (the Briefing), `query/raceday` and `query/meeting` (Race Day's
card and band) and `api/routes/blackbook` (STALE and RENEW).
`web/assets/book-tier.js` draws it, `book-renew.js` is the Blackbook page's
button, and the trial note's two taps are in `review.js`. Tests:
`tests/test_book_tier.py`.

---

## What was measured

On production's database as pulled on 29 Sep 2026 (settled to 27 Sep), each
run scored against the closing tote, de-vigged.

**HKJC's words carry nothing the price lacks.** Every clause seen often enough
was scored on the horse's next start, with meetings split in two halves so a
clause had to hold on both:

| Text | Clauses tested | Clear 1.00 at 95% | By chance |
|---|---|---|---|
| Comments on running (2019-) | 81 | 4 | ~4 |
| Stewards' report keywords (2023-) | 15 | 0 | ~1 |
| Trial comments (2025-) | 70 | 2, both against ("moved better than before", "won cozily") | ~4 |

The market reads the same text the owner does. This is why the automatic
book's race picks do not beat the price either (docs/auto-book.md).

**The owner's trial notes do.** Next start, note written before the race:

| Owner's trial notes | Runs | Won | Tote expected | A/E |
|---|---|---|---|---|
| all | 61 | 12 | 6.3 | 1.91 |
| horse asked and responded ("urged / reminded / whip" + "reacted / accelerated / rapid") | 40 | 9 | 3.6 | 2.47 |
| no such wording | 21 | 3 | 2.7 | 1.13 |
| on a trial it did not win | 42 | 8 | 3.5 | 2.31 |
| on a trial it won | 19 | 4 | 2.8 | 1.42 |
| HKJC rated the trial NEUTRAL | 31 | 5 | 2.3 | 2.13 |

The sub-rows are small and chosen after looking; the top row is the finding.
What the note sees that the price does not is a horse that found more when it
was asked, on a trial it did not win, behind a bland official comment.
CAVAMAZING: 5th of 9 on 19 Sep, HKJC "made progress when hard ridden", the
owner "reacted very well when shown the whip", not booked, ran 1 Oct.

**The rest of the book runs at the price.** First start after booking, owner's
entries since April: 14 won where 17.2 were expected. "High" confidence won
2 of 38; the `improvement` tag 1 of 48. Run notes: 18 runs, 3 won, 2.0
expected — too few to say.

**And the book had stopped filtering.** 20-34 book horses declared at every
meeting of the season; 42 of 54 races with two or more. Nothing closes an
owner's entry, so it only grows.

## The tiers

One per runner, in this order (`book_tier.classify`), as at the off:

| Tier | Rule | Since April: won / runs, A/E | On the page |
|---|---|---|---|
| TRIAL NOTE | the owner's trial note on a trial since the last run, written before the off — **booked or not** | 13 / 66, 1.84 | green: back it to win, flat |
| STANDOUT | in the book, a STANDOUT trial since the last run | 5 / 19, 1.33 | orchid: consider |
| STALE | in the book, 3+ starts since it was booked or renewed | 6 / 75, 0.82 | muted |
| EXCUSE | in the book for traffic, draw, wide trip, slow start, riding error | 25 / 244, 1.12 | copper: watch |
| QUIET | in the book for anything else | 16 / 197, 0.86 | muted |
| RUN NOTE | not in the book, the owner's note on its last start | 0 / 6 | muted |

The record is recomputed from the same rule over every settled race since
April, so the figure on a chip moves with results, and is kept until a result,
a note or the book changes. The tiers were drawn from the same runs they are
scored on: they are a reading order with a record, not a proven edge, and the
record from 7 Oct on is the test.

## The owner's decisions (5 Oct)

- **A trial note counts whether or not the horse is booked.** The note is the
  evidence; the book is not consulted for TRIAL NOTE. A note the system wrote
  ("System: …") never counts.
- **The bet list lives inside each race.** One line under the opened race on
  the Briefing (and at the top of the phone's opened race): *Book 2 here: back
  #7, #10 to win, flat (your trial notes since their last run); 2 quiet.* No
  page-level block (docs: briefing-no-new-panels). The race's `book` reason
  names the trial-note horses once, with the tier's record.
- **An entry with 3 starts since booking is muted as STALE, never closed.**
  The Blackbook list marks it and offers RENEW beside RETIRE; renewing counts
  its starts again from that day (`blackbook.renewed_date`). A system entry is
  adopted before it can be renewed — the system closes its own after their
  test.
- **The trial note has two optional taps:** ASKED (no / yes) and RESPONSE
  (strong / fair / none), so "asked and responded" is read as said rather
  than guessed from wording. NULL is "not said". Adding a tap does not move
  the note's `written_at` — that is what proves the note came before the race
  — new words do.

## How to note and book now

1. Write a trial note on every trial that catches the eye, booked or not. The
   note alone puts the horse on race day.
2. Look for the horse that was asked and found more, especially one that did
   not win and got a bland official comment. An easy trial winner is priced.
   Use the two taps.
3. On race day a TRIAL NOTE horse is a flat WIN bet. Not exotics: the 5 Oct
   betting review found the edge in win, and tickets keyed on the same horses
   lost through the exotics.
4. Book from replays for trouble if useful (EXCUSE, watch), but do not back
   them on that alone; stop booking for "improvement", closing sectionals or
   "outran its price", and do not read "high" confidence as a reason.

## The automatic book is recurring

`jobs/auto_book` runs from `ops/crontab` at 07:40 and 20:30 HKT. On the 29 Sep
copy every scheduled run since it went live on 28 Sep wrote its `job_runs` row
and read what was ready: 23 Sep's results at 07:40 on 29 Sep (the morning
after HKJC's comments on running), trial days 28 and 29 Sep at 20:30 the same
evenings. A meeting waits for its comments on running (5-8 days, backstop 8);
a trial day is read the evening it is finished.
