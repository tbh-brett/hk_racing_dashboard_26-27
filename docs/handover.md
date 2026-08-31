# Handover to the next agent

Read this, then `AGENTS.md`, then `docs/start-here.md`. Then read §1 below
before writing any code, because it names the one thing this session did not
have and you probably need.

---

## 1. Get the design file before you start

The owner has a **handover file and a Claude Design file** that say what is new,
what needed migrating from the old dashboard, and what was meant to be thrown
away. **Neither was ever in the previous session.** The dashboard was therefore
built from the owner's descriptions and from the old repo's behaviour, not from
the specification — and the owner has now compared the result against what they
originally conceptualised and found it short.

So the first thing to do is ask for those files and read them. Everything in §5
is a gap I could verify from the code; the gaps that matter to the owner are
most likely the ones only that document names. Do not guess at them, and do not
treat the current pages as the intent — they are one reading of it.

The owner said this once already, and it is worth quoting because it is easy to
get wrong in the other direction:

> "I am not trying to redesign UX/UI from old dashboard, but to create new and
> improved dashboard, with better architecture and structure, that also happens
> to have improved UX/UI designed with Claude Design."

So: not a port of the old thing, and not a free redesign either. The design file
is the arbiter.

## 2. Where everything is

| | |
|---|---|
| repo | `github.com/tbh-brett/hk_racing_dashboard_26-27`, branch `main` |
| owner's PC | `C:\Users\tbhbr\hk_racing_dashboard_26-27` |
| old repo (the data source) | `C:\Users\tbhbr\OneDrive\Desktop\python\HK races anaylsis` |
| database | `hkrd.db` in the repo root, ~33 MB, not in git |

The owner is **not a developer**. They do not use a terminal comfortably and
have been tripped up by placeholder paths, multi-line pastes and the Windows
execution policy. Every instruction you give them should be one line, tested,
and literal. `ops/*.ps1` exists for exactly this reason — prefer adding to those
over telling them to type a command with flags.

## 3. What is built

`ingest → store → derive → model → query → api → web`, one-directional, enforced
by `tests/test_smoke.py`. 38,600 lines. 689 tests, all passing.

- **9 pages** — raceday, form-guide, lookup, bets, blackbook, results, trials,
  model-analysis, login
- **57 endpoints**
- **21 tables**, SQLite, WAL, foreign keys on
- **Deployment** — Dockerfile, fly.toml, Litestream to Cloudflare R2, a cron
  schedule, `ops/deploy.ps1`. Written and tested; **not yet deployed**.

Hard rules the smoke tests enforce: 600 lines per Python file; no raw hex
outside `tokens.css`; no HTML built in `api/`; no two page stylesheets styling
the same top-level class; no duplicate route paths.

## 4. The central rule

**A number appears in exactly one place and is computed exactly once.**

Race lookup, form guide, horse performance and results are four *views over one
object* — a runner's line in a race. `RunnerLine` (`query/types.py`) is that
object and every query function returns it. If you find yourself computing a
figure a second time somewhere, that is the bug.

## 5. Gaps I could verify from the code

**Live odds are never captured.** `ingest/odds.py` parses a snapshot payload but
nothing fetches one — there is no `fetch` function and no job. `odds_snapshots`
holds only what the legacy import brought over. So every odds-dependent figure
reads a stale snapshot, which sits badly against the project's own rule that
odds-dependent output must use the *latest* snapshot. This is the largest
verified gap and it is squarely in the "additional data" the owner is missing.

**The archive stops.** Races, runners, dividends, comments and bets all end
2026-07-15; trials run to 2026-08-21. Not a bug — the source folder thins out.
`ops/catch-up.ps1` fetches the rest from HKJC. Run `jobs.coverage` to see the
shape. **Bets cannot be scraped** — HKJC does not know what anyone staked, so
those only come from account statements via `jobs.import_statement --src`.

**`model/staking.py` does not exist**, deliberately. See §6.

## 6. Decisions already taken — do not silently reverse these

Each was a real decision. Reopen any of them if the design file or the owner
says so, but say that you are doing it.

- **The seven legacy logic modules were discarded entirely** — `decision_engine`,
  `betting_strategy`, `form_screener`, `horse_cycle`, `backtest_model`,
  `calibration_harness`, `train_gbm`, ~6,800 lines. The owner's words: *"those
  are all vibe-coded without thorough consideration, and plenty of newly
  implemented functions and features replaces them."* Do not port them back.
- **No staking model.** Walk-forward evaluation showed the model does not beat
  the market price, so building bet sizing on top of it would have dressed up a
  negative edge. `MEASURED` in `model/backtest.py` records it.
- **Margin was dropped from the trial-quality score** despite the design
  specifying FINISH + MARGIN + COMMENT, because measurement said it did not
  carry signal. The owner was told.
- **The export/PDF function was removed** at the owner's request.
- **Image scraping and OCR were removed.**
- **Authentication is one shared password** in `HKRD_PASSWORD`, failing closed.

## 7. Findings that cost real work — do not contradict without re-measuring

- **Join history on `horse_name`, never `horse_id`.** `horse_id` is 0% populated
  in July 2026, 54.6% in June, 90% in April. Any join on it silently returns
  partial history.
- **Never parse `lbw` with `pd.to_numeric`** — it drops 79.1% of values across
  21,423 rows. Use the shared parser.
- **Never convert win probability to place probability by linear scaling.**
  `p / sum(p) * 3` overstates the favourite by ~34 points. Harville with the
  Henery discount, in `derive/probability.py`.
- **Running style is field-size scaled**: Closer cutoff is
  `max(8, field_size * 0.7)`, not a fixed `> 7`.
- **Settlement is HKJC tote.** You are paid the final dividend regardless of
  when the bet was struck — so early-price value is not capturable and odds
  drift is a sizing input, never a timing edge.
- **Sectionals are 400m measured from the FINISH backwards**, the opening
  section carrying the remainder. Verified against 21,075 runners, 0 mismatches.
- **Trial days are Tue/Thu/Fri 26.4% each, Mon 16.4%, Sat 3.1%, Wed 1.3%.** A
  Tue/Thu schedule misses 47% of them. Ask HKJC for the list; do not guess.
- **Every parser maps columns by header text** and raises on a shape it does not
  recognise. This is the "corunning lesson": a parser confident about positions
  it never verified put a trainer's name in the horse column and nothing looked
  wrong for three days.

## 8. How to work here

Run the dashboard and look at it. Do not describe a page you have not seen.

```bash
HKRD_ALLOW_NO_AUTH=1 python -m hkrd.serve --port 8000
python -m pytest tests -q          # 689 tests, keep them green
python -m hkrd.jobs.coverage       # what the database actually holds
```

Screenshot pages with Playwright before and after a change — the owner responds
far better to a picture than to a description, and it catches the class of bug
where a page renders but is wrong.

## 9. What not to do

- Do not write `except: pass`. The old `dashboard.py` had 66 of them and that is
  why bugs went unnoticed for months.
- Do not present a bare number. Every figure carries rank, percentile or delta
  vs par, **and a sample size**.
- Do not let a missing minor input void a whole result. Degrade the term and
  label it.
- Do not add a dependency without asking.
- Do not tell the owner something works without having run it. This session
  shipped two PowerShell bugs that only a real run caught — a function calling
  itself, and `Get-Command` returning every match on PATH.
