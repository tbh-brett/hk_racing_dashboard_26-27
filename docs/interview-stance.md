# Interview stance

Brett, 19 Nov 2026: measure whether what jockeys and trainers say in Racing To
Win interviews is worth listening to. Who spoke was too thin to read (80
interviewed runners over 26 speakers, the busiest with 8), and whether they
sounded confident or cautious had never been recorded: `connections_quote.stance`
was NULL on every row.

## How it is read

`derive/stance.py`, rule `rule:stance-1`. No language model (the tips layer
stays deterministic). Each interview answer is read for phrases of two kinds:

- **for**: happy or pleased with him, confident, should run well, trialled
  well, in good order, can win, will be competitive, a good feel, a good draw
  or light weight, more to come, the trip will suit.
- **against**: needs a run, a hard race, up in class, a bad draw, has to carry
  big weight, not sure or time will tell, still raw (work in progress, quirks,
  misses the start), disappointed, the trip is too short or sharp, needs luck
  or pace, not fully fit, a worry or a problem.

An answer's stance is whichever kind it holds more of; a tie or nothing is
neutral. An interview's stance (one speaker on one runner) sums its answers'
cues the same way. Questions the locator left in an answer are not read, and
the draw, weight and class are not read in a sentence about an earlier race.

`jobs/read_stance` writes it for every interview answer. It runs inside each
tips import and when the server starts, and reads everything again each time
(under 300 answers), so a change to the rule reaches the whole archive.

## What it found, and why it is not yet a finding

On the production copy of 16 Nov (results to 14 Nov), by interview:

| Stance | Runners | Won v price expected | Placed v price expected | $10 WIN | $10 PLACE |
|---|---|---|---|---|---|
| Positive | 47 | 9 v 9.4 (0.95) | 22 v 21.4 (1.03) | +6% | −13% |
| Neutral | 17 | 3 v 2.4 (1.28) | 8 v 6.1 (1.32) | +39% | −8% |
| Negative | 15 | 1 v 4.2 (0.24, interval 0.00 to 0.70) | 8 v 8.4 (0.95) | −81% | −30% |

The cautious interviews were mostly about short-priced horses (median 2.9) and
they won once where the price expected four times. That is the one line whose
interval clears 1.00, and it must not be quoted as an edge: the phrases were
written by reading these same 283 answers with each runner's result beside
them. It is a lead to judge **forward only, on interviews from 19 Nov 2026**,
with the rule frozen as `rule:stance-1`. Script:
`claude/tools/interview-lab/speakers.py` (on a copy of the database).

## What it cannot read

Sarcasm, a past race described in the present tense, and an answer about
another horse that the interview locator filed under this one (a jockey asked
about the champion he also rides, for instance). A stance is the lean of the
words, not a judgement of the horse.
