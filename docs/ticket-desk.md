# The ticket desk: your banker, the engine's partners

Asked for on 2026-11-16: *a model that goes through the Briefing, the form guide,
the trials and the book and suggests Quinella + Quinella Place banker bets; I
accept or reject; the bets are recorded, reviewed and scored after the results;
I can say why one worked or failed, and whether that was the screen's fault.*

This is the backend of the first part: the ticket, the decision and the record.
There is no page yet, no tags or comments, and nothing learned.

Code: `hkrd/query/ticket_build.py` (the ticket), `hkrd/query/suggest.py` (the candidates),
`hkrd/jobs/decide_ticket.py` (accept, edit, reject), `hkrd/jobs/record_tickets.py`
(the paper record), `hkrd/query/ticket_record.py` (the scoring),
`hkrd/query/ticket_ledger.py` (the ledger's side), `hkrd/store/tickets.py` and
four tables at the end of `schema.sql`, `hkrd/api/routes/tickets.py`.
Tests: `tests/test_ticket_desk.py`.

---

## What was measured, and what it decided

On production's database as pulled on 17 Nov 2026 (settled to 14 Nov). The
ledger is 838 bets from April to June (`personal`) and 553 from this season
(`brett`); Kelvin's sheet is left out.

**The banker is the bet.** This season's 153 QPL-banker tickets: the banker out
of the frame 90, the banker placed and no partner did 9, paid 54. April to June
was the same shape (71, 9, 47).

**No machine banker beats the price.** Over 4,307 races, each season scored by a
model fitted only on the seasons before it, with the market's next three as
partners:

| Banker | Placed against the price | QPL | QIN |
|---|---|---|---|
| The favourite | 0.97 | −14.0% | −12.5% |
| The model's top pick | 0.98 | −14.4% | −15.4% |
| The model's top pick of the market's first four | 0.98 | −13.3% | −14.2% |
| The model's biggest disagreement with the market | 1.01 | −16.5% | −19.4% |

(3,102 of those races have dividends. LightGBM 4.6.0 was used where the project
pins 4.7.0.)

**Your bankers have placed above it, and it is not yet proven.** This season,
against the place pool's last capture, by `query/book_tier` as at the race:

| Bankers | n | Placed | Pool expected | A/E |
|---|---|---|---|---|
| Every banker | 147 | 62 | 51.7 | 1.20 [0.90, 1.50] |
| Your own evidence before race day | 83 | 37 | 28.3 | 1.31 [0.89, 1.73] |
| In the system's book only | 9 | 4 | 3.1 | 1.30 |
| Nothing of yours on record | 55 | 21 | 20.3 | 1.04 |

The interval is the one this repo uses (Poisson on the count), and by it none of
these is clear of 1.00. A binomial reading of the same bankers puts the second
row at about two standard errors. One season, 19 meetings.

**A candidate is not a tip.** The paper record rebuilt over the same 20 meetings
holds every runner you had a tier on: 621 with your own evidence placed 187
where the pool expected 181.1 (1.03), and their tickets returned −18.3% (730
tickets, interval −34 to 0) — the takeout. Whatever your bankers have is in
which of those horses you chose.

**Your partners are level with the market's.** Same banker, same number of
partners, same stakes:

| Partners taken from | Over | Against yours |
|---|---|---|
| Closing win order | 281 tickets, both seasons | +14.0 pts [+4, +26] |
| Closing win order | 158, this season | +8.7 pts [−4, +23] |
| Win order five minutes before the close | 158 | +5.2 pts [−7, +17] |
| The QPL pool's order five minutes before (the engine) | 158 | +2.7 pts [−8, +14] |

The first row is the only one clear of nothing, and it reads prices nobody can
bet on. At prices you could see, the engine's partners and yours are not
distinguishable.

**No rule for passing a race was found.** The favourite with the market's next
three returned −12.9% over 4,730 races (interval −16 to −9). Only fields of 7 to
9 were consistently worse (−23.7%, both halves). Whether the model agrees with
the favourite changes nothing. You bet 337 of the 384 races on your meetings.

**So:** you pick the banker; the candidates are the horses you have a tier on;
the engine picks the partners by the pool that pays them; a swap is recorded as
an edit, so the last table keeps being measured; and nothing here is a reason to
bet.

---

## How it works

**The ticket** (`ticket_build.build`). A QIN + QPL banker ticket at $10 a line with
four partners by default; a field under seven is a quinella only. Partners are
ranked by the QPL pool's own price of each pair with the banker, the QIN pool
where there is no QPL, and the win market only where no pair pool was captured —
it says which. The banker's chance of placing is the place pool's, with
Harville-Henery beside it; its chance of the first two is the QIN pool's. Each
line says what it would return alone, and whether that covers the ticket.

**Flags warn and never block.** `no_own_evidence`, `small_field`,
`line_under_cost`, `qin_only`, `no_market`, `banker_unpriced`; each carries the
measurement behind it, dated. What is refused is a ticket that describes
nothing: a banker not in the race, a partner twice, no partner.

**The decision** (`decide_ticket.decide`). `accept` keeps the engine's partners;
`accept` with partners of your own is stored as `edited`; `reject` is a pass.
The ticket you saw is kept with it. You may change it until the pool shuts. One
made after that is stored `late` and is in no figure; one made in time cannot be
replaced afterwards. **It writes no bet** — every bet this season came from a
statement, and the ledger is matched to the decision when the record is read.

**The paper record** (`record_tickets`). Once a race's pool has shut, the ticket
the engine would have built for every runner you had a tier on, and every banker
you decided on, read from the last capture at least five minutes before the
close. Written once and never rewritten. Because captures are never deleted it
comes out the same whenever it runs, so it is three cron lines and not a hook in
the capture.

**The record** (`ticket_record.record`). Every paper ticket and decision scored
at the final dividend: the outcome (`banker_out`, `partner_out`, `paid_qpl`,
`paid`), whether it paid less than it cost, your partners against the engine's
on every edit, bankers placed against the stored place chance by evidence and by
what you did, and whether a matching bet is in the ledger. Races before
17 Nov 2026 are `backfilled` — the rules were written after looking at them —
and are aggregated only when asked for.

## Routes

| Route | Answer |
|---|---|
| `GET /api/tickets/{date}` | each race's candidate bankers with their tickets |
| `GET /api/tickets/{date}/{race}/{banker}?partners=1,2&k=&unit_stake=` | the ticket for any runner as banker |
| `POST /api/tickets/{date}/{race}/{banker}/decision` | `{decision: accept or reject, partners?, unit_stake?, account?, reason?}` |
| `GET /api/tickets/record?date_from=&date_to=&scope=&account=` | the scored record |
| `GET /api/tickets/bankers?account=&since=` | the ledger's bankers by evidence, live |

Measured on the 17 Nov copy: a card 13 ms before any price, 53 tickets over 11
priced races in 182 ms, a season's record 58 ms, the ledger's bankers 88 ms.

## To deploy

Nothing to run by hand: `init_db` creates the tables, and `ops/crontab` carries
the three `record_tickets --pending` lines. Optionally, on the server after the
deploy, to give the record this season's races as `backfilled` (733 bankers
over 197 races in 16 s on a copy). From PowerShell on this PC:

```
fly ssh console -a hkrd -C "nice -n 19 python -m hkrd.jobs.record_tickets --pending --since 2026-09-06 --db /data/hkrd.db"
```

This and the facts backfill in `docs/banker-card.md` are one run if both flags
are given: `--since 2026-09-06 --facts-since 2026-04-01`.

## Not built

- ~~**The page.**~~ **Built 17 Nov**, as the DESK and BANKERS views of the Bets
  page, in the dashboard's own style with no artboard yet. Each candidate opens
  to a brief of your own record, the ticket and TAKE or PASS. See
  `docs/banker-card.md`.
- **Your verdict after the result ("whose fault").** The record names what went
  wrong with a ticket, and your taps say why you took it; your own verdict
  after the result has no table yet.
- **Anything learned.** Nothing here is fitted. The first things worth fitting
  are which evidence carries the banker edge and whether your edits pay, and
  both need more than one season.
- **Two data gaps found on the way.** QIN and QPL dividends are missing from
  September 2024 to December 2025 (6 races held). And 150 of this season's bets
  carry a return and no `hit` — deliberate for an apportioned credit
  (`import_statement`), but `query/bets.py`'s summary counts them as misses.
