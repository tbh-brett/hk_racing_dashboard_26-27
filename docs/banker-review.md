# The banker review: why you chose it, and what decided it

Asked for on 2026-11-20, after the desk turned out to learn nothing from what
the owner writes:

> I make mistakes picking bankers sometimes, and there are times circumstances
> affected the outcome. I want a function to keep track of both, going back over
> bankers already run too. I am happy to fill in the reasoning of past bankers.

He also chose, that day, what it is for and what he will give it:

| Asked | His answer |
|---|---|
| The one job | Grade my reasoning: did what I expected happen, and was a miss my reading or circumstances |
| Worth it in three months if | I know which of my reasons to trust |
| What he will give it | One sentence and a confidence tap before the off; no AI reader inside the app (no API access on his side); a bulk review in a Claude Code session about every two weeks |
| Whose bankers | His own small bets are where he experiments; Kelvin's tickets are the decider, often structured before racing starts |

Code: `hkrd/query/banker_review.py` (the rows and the vocabulary),
`hkrd/query/review_record.py` (what it adds up to), `hkrd/jobs/review_banker.py`
(the only write path), `hkrd/store/review.py`, `hkrd/derive/circumstances.py`
(what the record says), `hkrd/api/routes/review.py`. Page: the **Desk** page,
`web/pages/desk.html` and `desk-page.js`, with `review-race.js` (after each race),
`review-rows.js`, `banker-review-form.js`, `banker-review.js` (the backlog),
`banker-review.css`. Tests: `tests/test_banker_review.py`.

---

## The Desk page (20 Nov 2026)

The same day he asked for the desk and the review to leave the Bets page for a
page of their own, and for it to cost less: he means to do it every race night,
"even if it demoralises me when I make constant mistakes". So:

- **One strip of race chips for the night** (TONIGHT). A race still to run is
  the desk: your horses, the ticket, a line on what has to happen, HOW SURE,
  TAKE or PASS. A race that has run shows the result and, under it, **your
  bankers in that race**, each one tap for what decided it. The chip says how
  many are left to judge, then ✓.
- **Every tap saves.** No SAVE button. Text saves when you leave the box. A
  verdict opens the next banker still to judge.
- **Circumstances only when you say so.** The eight causes open when the verdict
  is circumstances, or behind "+ circumstances played a part".
- **The price line on every miss**: "the price gave it 43% to place: read right,
  a horse like this still misses 57 times in 100". Most misses are not
  mistakes, and the page says so where the miss is.
- **What you wrote stays beside it**: your trial note, book entry and desk line
  before the race; your note on the run after it (his 18 Nov reflection was
  filed as run notes, one per horse).
- **A note on each race and on the night** (`race_reflection`): the horse you
  should have banked, why you left a race alone, the structure of the multi.
- **The facts column left the desk table.** Its ▲/▼ marks could not be told
  from noise (measured the same day); the facts stay in the opened row.
- **BACKLOG** holds every banker since April and the record so far. Optional.
- Behind the Bets password, like the Bets page: `/pages/desk.html`, and the
  desk's own routes under `/api/tickets` with it.

## Why it exists

Measured on 20 Nov 2026 on the 19 Nov copy of production
(`claude/tools/desk-lab/`):

- The desk fits nothing to him. Its ▲/▼ marks were indistinguishable from
  noise: 29% of facts on his bankers' briefs carried one, against 20% (11-30%)
  when every fact was made meaningless by construction. Walk-forward over 157
  bankers, the ones with more ▲ placed at 0.97 of his usual, more ▼ at 1.02.
- What held was the one split: horses he had written about before race day
  placed at 1.33 [0.98, 1.67] of the price (125 bankers), without 1.06. All 621
  horses he had evidence on placed at the price (1.03), so whatever he has is
  in which of them he chooses, which the 14 facts cannot see.
- The desk held one decision. Its sentence was a testable claim ("its trial
  showed it can lead; the new tactic might suit"), and the race never tested
  it: INVINCIBLE STEED jumped awkwardly, raced 7th and was held up. Filed as a
  plain miss, it would teach him the wrong thing.

So the review grades the reasoning, keeps luck apart from misjudgement, and
reads the record beside both.

## Which horses

A horse is here when its ticket **cannot pay without it**: the banker of a
banker ticket or leg, or the only horse in a leg or a single-race bet. From every
book: his statements, the April-June log, Kelvin's sheet, and the desk's TAKEN
decisions (which reach the ledger only once a statement does). A box or a pair
has no such horse and is not here. One row per horse in its race, whichever
tickets carried it.

## The two halves of a review

**The claim**: why you chose it, how sure you were (strong, fair, weak), and
for a Kelvin ticket who picked it (you, Kelvin, someone else).

- A claim written **before the off** is a forecast. Once the race has gone it
  stands as written, the rule `trial_notes.written_at` and the desk's decisions
  already live by. What happened goes in the verdict.
- A claim written **after the race** is a recollection. He asked to fill these
  in for past bankers, so they are welcome and editable, but marked, and every
  figure keeps them apart from forecasts. Hindsight bends what one remembers
  thinking; the record should not have to guess which is which.
- `claim_at` is the first save and never moves.
- The desk writes its sentence and confidence here when he TAKEs or PASSes
  before the off. A desk decision from before this existed counts as a claim
  made before the off, read from `ticket_decision`.

**The verdict**: what decided it, once the result is in, and it must agree
with the result:

| Placed | Didn't place | Either |
|---|---|---|
| Read it right, and it placed | Read it right, beaten fairly | Can't tell |
| Read it wrong, placed anyway | Circumstances: never ran its race | |
| | Misread it | |

Then the **circumstances** he blames, from eight: missed the start, held up
or hampered, wide without cover, raced keenly, the ride, the pace, track or
draw bias, gear or health. And a note.

## What the record says

Beside his verdict, never over it, `derive/circumstances` reads the stewards'
report and the comments on running for five of the eight: start, traffic, wide,
keen and health, with the blackbook's own rules (`derive/book_candidates`), plus
"no clear running", which the model and the book leave out as a forecast but
which is traffic all the same. Ride, pace and bias are his to say.

The summary sets them side by side: of the beaten bankers he put down to
**circumstances**, how often the record names one, beside how often it names one
for those he called a **misread** or **beaten fairly**. Blaming luck is the bias a
review like this invites. If the record names something as often for his
misreads as for his circumstances, his calls are not separating the two.

## What it adds up to

At the top of the Desk page's BACKLOG, and `GET /api/bets/review/summary`:

- verdict counts;
- the record check above;
- placed against the price at each confidence, **before the off and after kept
  apart**;
- Kelvin's legs by who picked them;
- every key horse by book (BRETT is his statements and the April-June log);
- by kind of reason, once the fortnightly review has given kinds.

Every line has its count and an interval, and one under 30 is marked thin.

## The fortnightly review

About every two weeks, in a Claude Code session on this machine:

1. Pull a fresh copy of production: `Pull production copy.bat`, or
   `.\ops\pull-db.ps1`. Auto mode refuses to run it for the session; run it
   yourself.
2. In the session: read each claim made since the last review from
   `banker_review` (and `ticket_decision.reason`) **with the result out of
   sight**, and give it a kind (what the reason rests on: the pace, a trial, a
   class drop, the rider, and so on). The kinds are worked out at the first
   review from what he actually writes, not fixed in advance.
3. Then, and only then, read the results, the verdicts and the record, and
   write the fortnight up: which kinds placed above the price, which verdicts
   the record backs, where confidence ran ahead of results.
4. Put the kinds on production, from PowerShell on this PC (in Git Bash, prefix
   the first with `MSYS_NO_PATHCONV=1`):

   ```
   fly ssh sftp put kinds.json /data/kinds.json -a hkrd
   fly ssh console -a hkrd -C "python -m hkrd.jobs.review_banker --kinds /data/kinds.json --db /data/hkrd.db"
   ```

   The job reports what it applied and lists any horse with no review to
   attach a kind to.

## Routes

All under `/api/bets`, so the Bets page's own password guards them.

| Route | Answer |
|---|---|
| `GET /api/bets/review?account=` | every meeting with a key horse, newest first, with how far its review has got, and the form's vocabulary |
| `GET /api/bets/review/summary?account=` | what it adds up to |
| `GET /api/bets/review/{date}?account=` | one meeting's key horses: the run, his notes from before it, his note on the run, the record, his review; and his notes on its races (`notes`, "0" for the night). 404 only when no card is stored; a card with no banker yet is an empty list |
| `POST /api/bets/review/{date}/note` | `{race_no, note}`: his note on a race, or on the night (`race_no` 0); an empty note removes it |
| `POST /api/bets/review/{date}/{race}/{horse}` | any of `reasoning`, `confidence`, `picked_by`, `verdict`, `verdict_note`, `circumstances`; a field left out is kept, an empty one cleared. 404 no such runner, 409 a closed claim or no result yet, 422 a word outside the vocabulary or a verdict the result contradicts |

Measured on the 19 Nov copy (326 key horses since April): the meeting list
29 ms, a meeting 33 ms, the summary 222 ms the first time in a process and
99 ms after (the stewards' text is read once per process).

## To deploy

Nothing to run by hand: `init_db` creates `banker_review`,
`banker_review_circumstance` and `race_reflection` on start-up.

## Known limits

- **The record reads a sentence, not who it is about.** On 11 Nov race 3 the
  stewards wrote that ARGENTO OCEAN was "bumped heavily by PRIVATE WEALTH which
  shifted out when racing keenly", and the review reads "raced keenly" for
  ARGENTO OCEAN. The quote is always shown beside the flag, so the call stays
  his; a flag is a pointer to the text, not a verdict. The same rule is the
  blackbook's, so fixing it is one change for both.
- **"Bumped" is not traffic** here, as in the book: the word is in a fifth of
  all reports, mostly at the start and harmless.

## Not built

- **Kelvin's sheet notes.** The sheet's Note column already holds his post-race
  verdicts ("did not lead despite first one out", "missed the start
  completely"). The import does not keep it per leg; it could seed his
  verdicts.
- **Passes.** A horse he passed on the desk keeps its claim, but the review
  lists only horses a ticket carried.
- **Kinds on the page.** The fortnightly review gives them; the page shows
  their record but offers no way to set one.
