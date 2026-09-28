# The automatic blackbook

Asked for on 2026-09-28, after a morning spent going through the previous
day's results by hand: *an automatic blackbook screen for results and trials,
in a different colour, with the reasoning and the date added, run once after
the full results are in, without loading the dashboard with formulas.*

Code: `hkrd/derive/book_candidates.py` (the rules and the wording),
`hkrd/query/auto_book.py` (which days are ready, and their runs),
`hkrd/jobs/auto_book.py` (the pass), `hkrd/query/blackbook_origin.py` (whose
record a figure is), `web/assets/book-origin.js` (the colour). Runs from
`ops/crontab` at 07:40 and 20:30.

---

## What it does

- **Once per meeting**, when HKJC's comments on running are in. They arrive
  five to eight days after a meeting; some trouble is only in them (two of the
  owner's traffic entries have nothing in the stewards' report). Horses run a
  median 24 days apart and 4.6% run again within ten, so waiting costs almost
  nothing. At **eight days** without them it reads the stewards' report and the
  sectionals alone, and every entry it writes says so.
- **Once per trial day**, when HKJC has finished publishing it.
- Books the top **six** runs of a meeting, and every trial rated **STANDOUT**
  (at most six a day; it averages 1.6). Never a horse the book already follows,
  never a winner, never a run with a veterinary finding.
- Writes them into the blackbook as **system** entries, in the owner's own tag
  vocabulary, dated the day after the meeting, with the reason written out —
  the finish, then each reason, quoting HKJC where HKJC said it:

  > 6th of 12, beaten 2L at 3.0, drawn 9. Fastest closing section in the race,
  > from 10th at the turn. Stewards: "Over the concluding stages was unable to
  > be ridden out when held up behind BEAUTY SHOW."

- **Closes a system entry after its next three starts** — RETIRED, dated the
  day after the third, with its record in the reason — unless the owner adopted
  it. Nothing else here ever closes an entry, and this never touches one the
  owner booked or adopted (`write_notes.close_tested` refuses).

On the page a system entry is orchid (`--book-sys`) everywhere a booked horse
shows: the Blackbook list (with its reason under the row, and ADOPT / DISMISS),
the Briefing (SYS tag, SYSTEM BOOK note), the Race Day band and card, and the
Results panel. Adopting makes it the owner's; it reads teal from then on.

## The reasons

| Reason | Read from | Tag | Rule |
|---|---|---|---|
| Held up, checked, crowded, hampered, short of room, steadied | stewards' report, comments on running | `traffic` | beaten 6L or less; "restrained" and "taken back" alone do not count |
| Wide without cover, three or four wide | the same | `bad_run` | beaten 5L or less |
| Fastest or second-fastest closing section | `runner_pace.late_dev` | `final_sectional` | beaten 6L or less |
| Far back at the turn, passed 4+ in the straight | running positions | `final_sectional` | beaten 4L or less |
| Slow away | stewards' report | `slow_start` | beaten 4L or less |
| Ran well above its price | the closing tote | `exceptional_performer` | 20.0+, placed or within 2.5L |
| Drawn 9 or wider, with either trip above | the draw | `bad_draw` | — |
| Trial rated STANDOUT | `derive/trial_quality` | `trial` | — |

Ranked for the cap by how many separate reasons, then trouble in the straight,
then the longer price — the order the owner's own choices follow (below).

## What was measured, and what it means

On the local archive (607 meetings to 2026-09-09, the owner's 155 entries to
2026-09-03):

**None of the race reasons beats the price.** Next start, against the closing
tote, over every pick the shipped rules make on 159 meetings with a stewards'
report:

| Picks | Runs | Win A/E | Place A/E |
|---|---|---|---|
| all | 912 | 0.91 ±0.21 | 0.94 |
| with a closing reason | 726 | 0.92 | 0.95 |
| with traffic | 694 | 0.95 | 0.92 |
| with a wide trip | 145 | 1.09 ±0.50 | 1.04 |
| outran its price | 372 | 0.67 ±0.37 | 0.92 |

The market reads the same stewards' report, and a hard-luck story it can see
is, if anything, overbet. The owner's own book over the same period: win A/E
1.14, place 1.00, 148 horses, 16 winners — not distinguishable from 1.00 yet.

**Trials are the exception.** STANDOUT trials won 1.36x what the tote expected
next start (n=225, ±0.35); POSITIVE 1.13, NEUTRAL 1.19, NEGATIVE 0.88.

**It is not a copy of the owner's eye.** Over April to July 2026 the rules
flagged about 38 runs a meeting and the owner booked about 1 in 10 of them;
with the cap at six, the system's picks included 25 of the owner's 149
race-sourced entries (17%). Among flagged runs the owner booked 7% with one
reason, 14% with two, 27% with three; 14% with trouble in the straight against
8% without; 20% at 50.0 or longer against 6-9% below. The margin made no
difference. Re-ordering by those moved the overlap from 22 to 25 — the owner's
picks are spread wider than any six.

**So it is for three things, none of them a tip:**

1. **Time** — the trawl through the results is done.
2. **Coverage** — a run that fits a reason is never missed.
3. **A control group.** Every system entry is booked by rule, in the owner's
   vocabulary, tested over the same three starts. The Blackbook's analysis view
   switches between YOURS and SYSTEM, so the owner's `traffic` row sits beside
   the rule's `traffic` row. That answers the question 155 entries cannot:
   *does my eye beat the rule, reason by reason?*

## Whose record is whose

`query/blackbook_origin`: the owner's record is what the owner booked plus the
system entries they adopted; the system's record is everything the system
booked, adopted or not, so it stays whole as a control. The Blackbook summary,
BY TAG and BACKED vs MISSED read the owner's by default, and a system horse the
owner never backed is never counted as the owner's miss (Blackbook, Results).

## Load, and the knob

Simulated from 2026-06-01 on the local archive, the book carried about 75 live
system entries by September, and the 9 September card had 14 system horses
declared beside the owner's 17. `RESULTS_CAP` (6) and `TESTED_RUNS` (3) are the
two numbers that set that; lowering the cap to 4 is the obvious lever if the
card reads crowded.

## Running it by hand

```
python -m hkrd.jobs.auto_book --date 2026-09-27 --dry-run   # what it would book
python -m hkrd.jobs.auto_book --pending                      # what the cron runs
python -m hkrd.jobs.auto_book --pending --since 2026-09-06   # this season so far
```

It starts at 2026-09-27 (`FIRST_DAY`), the meeting the owner reviewed by hand
the day it was asked for.
