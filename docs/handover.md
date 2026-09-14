# Handover to the next agent

**2026-09-14.** Written on the owner's first PC for an agent on a different
device. You get none of the sessions that came before you, so everything they
learned that is not already in the code or `docs/decisions.md` is here.

The previous version of this file (2026-08-31, `git show 8ae6903:docs/handover.md`)
described a dashboard that was not deployed and captured no odds. Both have been
untrue for two weeks, so it was rewritten rather than amended.

Read this, then `AGENTS.md`, then the last few entries of `docs/decisions.md`
(newest last).

---

## 0. First ten minutes

```bash
git pull                                    # or clone: github.com/tbh-brett/hk_racing_dashboard_26-27
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest tests -q     # 1344 passed, 2 skipped on 2026-09-14
```

If that count has moved and no commit says why, stop and find out before
building on it. Python 3.11+; the first PC runs 3.13. One test drives
`web/assets/vocab.js` through Node, so install Node.js too.

## 1. Where everything is

| | |
|---|---|
| repo | `github.com/tbh-brett/hk_racing_dashboard_26-27`, branch `main`. **Public.** |
| production | `https://hkrd.fly.dev` — Fly app `hkrd`, one machine in `sin`, SQLite at `/data/hkrd.db` on a volume, Litestream to Cloudflare R2 every 10 s |
| deploying | **manual**, from a Windows checkout: `Deploy to Fly.bat` → `ops/deploy.ps1` → `fly deploy`. There is no CI. Pushing to `main` deploys nothing |
| schedule | `ops/crontab` on the machine: `nightly` five times a day, `project_card --pending` three, `scrape_trials` two, `scrape_odds` every minute |
| design | `web/design-source/*.dc.html`, never hand-edited; the round trip is `docs/design-loop.md`. Speed Map has no export |
| runbook | `docs/deploy.md` — restore, logs, what each error means |
| first PC | `C:\Users\tbhbr\hk_racing_dashboard_26-27` — **not on `main`**, see §6 |

**The owner is not a developer.** They do not use a terminal comfortably and
have been tripped up by placeholder paths, multi-line pastes and the Windows
execution policy. Every instruction you give them should be one line, tested,
and literal. `ops/*.ps1` and the `.bat` files exist for exactly this — prefer
adding to those over handing them a command with flags. If you have not run a
command, say so; do not describe it as working.

## 2. What is built

`ingest → store → derive → model → query → api → web`, one direction, enforced
by `tests/test_smoke.py`. `hkrd/` is 25,391 lines of Python, 30 tables, ~85
routes, ten pages: Race Day, Speed Map, Form Guide, Bets, Blackbook, Results,
Lookup, Trials, Model Analysis, and sign-in.

Since the last handover, roughly in order of how much each changed what the
dashboard can say:

- **Live odds** from HKJC's JSON endpoint every minute — WIN, PLA, QIN, QPL,
  doubles and pool turnover. `docs/handover-pools.md`.
- **Deployed**, 2026-09-03. Six things had to be fixed first; `docs/status.md`.
- **Bet entry**, with ticket pricing checked against the account statement.
- **SARR rebuilt term by term**: the draw (fitted per venue and distance,
  walk-forward), trust in a run the vet found something on, a class step, a
  recency-weighted place rate, habitual style, and clearing rows for horses
  that were scratched. Each is measured where it is defined, in
  `hkrd/model/sarr.py`. `sarr.DERIVE_VERSION` is `sarr-1.1`.
- **A pre-race speed map.** `derive/settle.py` projects first-call position
  from the ESZ trait, the gate and style — in-race rho 0.622, MAE 0.198, about
  ±2.4 places in a 13-runner field, and the page says so.
- **Upcoming cards are scored by the schedule** (`3b67054`). Until 2026-09-14
  only the Card button did it, and a blank rank now says whether it is a
  debutant or a card nobody scored.
- **The blend keeps the model it has** (`107c53b`, `7f71228`). One unrated
  runner used to blank the fundamental stream for the whole field — two races
  in three — so the Model Analysis weight control did nothing.

## 3. The central rule

**A number appears in exactly one place and is computed exactly once.** Race
Day, Form Guide, Lookup and Results are views over one object, a runner's line
in a race — `RunnerLine` in `query/types.py`. If you are computing a figure a
second time somewhere, that is the bug. Thresholds count: `sarr.MIN_PRIOR`
exists because three places each carried a literal 2.

## 4. Open work, most urgent first

### 4.1 Deploy `main`, then check production's derived tables — not done

`main` now carries the scheduled card scoring and the blend fix; neither is on
production unless someone has deployed since 2026-09-14. **Deploy from a clean
checkout of `main`** (`git status` empty): `fly deploy` builds from the WORKING
TREE, which is how production has run code that was never committed (§5).

Then check which model wrote production's SARR rows. Nothing was able to check
this from the first PC:

```bash
fly ssh console -a hkrd -C "python -c \"import sqlite3;print(sqlite3.connect('/data/hkrd.db').execute('select derive_version,count(*),max(race_date) from runner_sarr group by 1').fetchall())\""
```

If anything is not `sarr-1.1`, rebuild it on the machine — minutes, not
seconds:

```bash
fly ssh console -a hkrd -C "python -m hkrd.jobs.rebuild_sarr --db /data/hkrd.db"
```

Why it matters, measured on the first PC's archive: its table was `sarr-1.0`,
and rebuilding under `sarr-1.1` changed **99.8% of scores, 39.5% of ranks and
the top-rated horse in 19.5% of races**. The owner reported SARR results that
still looked wrong while every fix was committed — that is the likely reason.

### 4.2 Re-derive the blend calibration on production's data

`blend.CALIBRATION` was fitted on the first PC's archive, which ends at
2026-07-15. Production holds two more months.

```bash
fly ssh console -a hkrd -C "python -m hkrd.jobs.fit_blend --db /data/hkrd.db"
```

It prints and writes nothing. If the figures differ materially from
`hkrd/model/blend.py`, update the constants **and** the docstring beside them,
which quotes them. Run it only after 4.1 — it reads `runner_sarr`, and the
constants must describe the model the page shows. Expect the fitted weight to
stay 0.00: it has on three generations of SARR and on both populations.

### 4.3 The backtest section still drops every race with an unrated runner

`model/backtest.py` skips a race if any runner has no SARR score — the same
selection just removed from `fit_blend`, where it meant the weight was chosen
on 660 races of 1,712. Widening it changes every number in the DOES IT BEAT
THE PRICE table, so it is the owner's call rather than a quiet fix.

### 4.4 Two literals `3b67054` did not reach

- `query/speedmap.py` returns the reason `"fewer than two prior runs"`, and
  `tests/test_speedmap.py` asserts the string. It should read `sarr.MIN_PRIOR`.
- The blend footer names unrated runners without a reason, deliberately: a
  blank is too little history (a rule) or a card nobody scored (a fault), and
  only Race Day tells them apart (`raceday._unrated`). Surfacing it on Model
  Analysis needs the prior-run count shared rather than copied, and
  `query/raceday.py` is at 568 of 600 lines — propose a split first.

## 5. Traps this repo has already sprung

- **Production can run code that is not on `main`.** Deploys have gone out
  from a checkout with uncommitted work. Before reasoning about production from
  the repo, ask which machine deployed last.
- **`python script.py` can import the wrong copy of `hkrd`.** The first PC's
  `.venv` holds an editable install pointing at the main checkout, and a
  script's `sys.path[0]` is the script's own directory, so run from a worktree
  it silently imports the other branch. `python -m` and `pytest` put the cwd
  first and are fine. It produced a false "no difference" on 2026-09-13; assert
  on `module.__file__` or set `PYTHONPATH`.
- **A derived table can be a model generation behind the code.** The
  `derive_version` column is the cheap tell. Check it before blaming the model.
- **The repo is public and `hkrd.db` holds the owner's bets.** Never commit a
  database, a WAL, a statement or `.env`. `.gitignore` covers the known shapes;
  a backup named `hkrd.db.pre-draw` once was not.
- **Line endings.** The Windows working tree is CRLF and `.gitattributes` pins
  everything the container reads to LF. `sed -i` or a bash heredoc rewrites a
  file LF; git normalises it on commit, but keep a file one or the other.
- **Agent permissions.** On the first PC the permission classifier blocked
  `fly ssh console -a hkrd -C ...`. If it happens to you, give the owner the
  command rather than routing around it. `flyctl` is signed in there.
- **Production's API needs a session.** `/api/health` is the only open route.
- **`gh` is not installed on the first PC**, so a pull request there means the
  GitHub web page.

## 6. The first PC — only if anyone works on it again

`C:\Users\tbhbr\hk_racing_dashboard_26-27` sits on
`claude/handover-docs-review-qgvvfk`, dozens of commits behind `main`, with
uncommitted edits that are an EARLIER DRAFT of work now on `main`. Checked file
by file on 2026-09-14: everything in it either landed on `main` in a later form
or is identical to `main`, and the one untracked document,
`docs/handover-draw-and-speedmap.md`, specifies two pieces of work that were
both since built (`aa4dd2f`, `b026827`). Nothing there is unique.

It matters because everything on that PC binds to that checkout: `Start
dashboard.bat`, the `.venv`, and `hkrd.db`, whose SARR rows are `sarr-1.0`.
And `ops/update.ps1` pulls the upstream of the CURRENT branch — a dead one — so
Update there reports "already up to date" and never delivers `main`.

To put it on `main` without losing anything (the stash is recoverable, and the
database is gitignored so neither step touches it):

```powershell
git -C C:\Users\tbhbr\hk_racing_dashboard_26-27 stash push -u -m "pre-main draft 2026-09-14"
git -C C:\Users\tbhbr\hk_racing_dashboard_26-27 checkout main
git -C C:\Users\tbhbr\hk_racing_dashboard_26-27 pull --ff-only
```

then `.\ops\update.ps1` works as documented, and
`.venv\Scripts\python -m hkrd.jobs.rebuild_sarr` brings the local table to
`sarr-1.1`. Not run — it is the owner's working copy.

Also on that PC, outside the repo: `C:\Users\tbhbr\hkrd-deploy-assets\` holds
`hkrd.db.pre-draw` (a 30 MB database from before the draw term — it contains
bets, never put it anywhere public) and `draw-cache.zip`, the input to a gate
backfill that is on `main` as `13e143e`. Neither is needed on a new device.
The worktree `.claude/worktrees/quinella-doubles-odds-scraping-b2147c` is
merged and can be removed.

## 7. Decisions already taken — do not silently reverse these

Each was a real decision. Reopen one if the owner asks, but say you are doing it.

- **The seven legacy logic modules were discarded** — `decision_engine`,
  `betting_strategy`, `form_screener`, `horse_cycle`, `backtest_model`,
  `calibration_harness`, `train_gbm`. The owner's words: *"those are all
  vibe-coded without thorough consideration, and plenty of newly implemented
  functions and features replaces them."* Do not port them back.
- **No staking model.** The model does not beat the market price, so bet sizing
  on top of it would dress up a negative edge. `MEASURED` in
  `model/backtest.py`.
- **The blend's weight on the fundamental stream is 0.00**, fitted, not a
  placeholder. Every positive weight scores worse out of sample, on 660 fully
  rated races and on all 1,617 with a complete book. The page shows the
  alternatives beside it rather than asserting it. `docs/decisions.md`.
- **Margin was dropped from trial quality** despite the design specifying it,
  because it carried no signal. The owner was told.
- **Export/PDF, image scraping and OCR were removed** at the owner's request.
- **Authentication is one shared password** in `HKRD_PASSWORD`, failing closed.

## 8. How to work here

Run the dashboard and look at it. Do not describe a page you have not seen.

```bash
HKRD_ALLOW_NO_AUTH=1 python -m hkrd.serve --port 8000
python -m pytest tests -q
python -m hkrd.jobs.coverage       # what the database actually holds
```

A local database comes from `ops/start.ps1` (bootstraps from the legacy repo,
which only the first PC has) or from production. `docs/deploy.md` restores
from R2. To copy production down without catching SQLite mid-write — **not
run from the first PC**, check the volume has room for a second copy first:

```bash
fly ssh console -a hkrd -C "python -c \"import sqlite3;sqlite3.connect('/data/hkrd.db').backup(sqlite3.connect('/data/copy.db'))\""
fly ssh sftp get /data/copy.db hkrd.db -a hkrd
fly ssh console -a hkrd -C "rm /data/copy.db"
```

Commit messages here are a sentence saying what was wrong, then prose with the
measured numbers — read `git log` before writing one. The owner responds far
better to a screenshot than to a description.

## 9. What not to do

- Do not write `except: pass`. The old `dashboard.py` had 66 of them, and that
  is why bugs went unnoticed for months.
- Do not present a bare number. Every figure carries rank, percentile or delta
  vs par, **and a sample size**.
- Do not let a missing minor input void a whole result. Degrade the term and
  label it — the blend fix in §2 is that rule, applied late.
- Do not add a dependency without asking.
- Do not evaluate a model change on data it has seen. Walk-forward or nothing.

## 10. What you cannot see

Claude sessions do not travel between devices. These artifacts do, for the
same account:

- ESZ Speed Map design canvas —
  https://claude.ai/code/artifact/e45df474-af3c-433c-9a88-be760ef07ac4
- SARR draw handover (historical; both parts are built) —
  https://claude.ai/code/artifact/84698c8c-ef67-401a-aa5c-88b7dc22dfbd
