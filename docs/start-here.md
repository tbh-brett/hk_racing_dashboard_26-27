# Start here

Written for the person who owns this, not for a developer. If something below
assumes knowledge you do not have, that is a fault in this document.

---

## 1. What you have

A finished dashboard. It runs on your computer and it holds everything from
the old system:

| | |
|---|---|
| races | 1,712 |
| runners | 21,280 |
| barrier trials | 7,750 |
| your bets | 1,078 |
| blackbook entries | 196 |

Nine pages: **Race Day**, **Form Guide**, **Lookup**, **Bets**, **Blackbook**,
**Results**, **Trials**, **Model Analysis**, and the sign-in page.

It replaces the old Streamlit app — 135 files, ~120,000 lines, with a
22,898-line file at the centre of it. Nothing from that file survived. The
things that were wrong in it were found by measurement and fixed; the things
that were guesses were deleted.

## 2. The three files you double-click

Once it is on your PC, you never need PowerShell again. In the project folder:

| Double-click | What it does |
|---|---|
| **Start dashboard** | Opens the dashboard in your browser |
| **Repair data** | Fixes pace figures, comments on running and tags. Shows what is wrong first and asks before fetching |
| **Update data** | Fetches race meetings and trials the database is missing |

They are `.bat` files, which is deliberate: Windows refuses to run `.ps1`
scripts by default, and a `.bat` can lift that for its own run without changing
anything about the machine.

Leave the black window open while any of them is working. Closing it stops
whatever is running.

## 3. Getting it onto your PC the first time

Open **PowerShell** (press Start, type PowerShell, Enter) and paste this one
line:

```powershell
iwr -useb https://raw.githubusercontent.com/tbh-brett/hk_racing_dashboard_26-27/main/ops/first-run.ps1 | iex
```

It finds your old dashboard folder, downloads this one next to it, and starts
it. Nothing else to type.

It has to be one line, and it has to arrive this way rather than as a file, for
two reasons that both bite otherwise:

- **Pasting several lines into PowerShell does not work.** The console reads
  one line at a time, so a block with `if {...}` on one line and `else {...}`
  on the next runs the first half and then errors on the orphan.
- **Windows refuses to run `.ps1` files by default.** A script that arrives as
  text is not a file, so it runs — and the first thing it does is lift that
  restriction for that window only, so `.\ops\start.ps1` works afterwards.

### Updating it later, when a new version is published

Not the same thing as **Update data**, and the difference is worth holding on
to. *Update data* fetches race meetings and trials the database is missing —
new *results*. This fetches new *dashboard*: the pages, the figures and the
fixes themselves.

```powershell
.\ops\update.ps1
```

It downloads the latest changes from the online branch this copy follows, stops
the running dashboard, updates the Python packages, and opens the new version.
You do not need to visit GitHub or wait for someone to send you a file.

It stops and explains if this folder has dashboard code you have edited, so it
cannot overwrite work in progress. Your database, your workspace settings and
anything else of your own are left alone.

## 4. How to open it from PowerShell

Open **PowerShell**, go to this folder, and run one command:

```powershell
.\ops\start.ps1
```

That is all. It installs what Python needs, builds the database the first time,
starts the dashboard and opens your browser. The first run takes a few minutes;
every run after that takes seconds.

Leave the PowerShell window open while you use it. **Ctrl+C** in that window
stops it.

If it cannot find the old repo, tell it where that is:

```powershell
.\ops\start.ps1 -LegacyRepo C:\wherever\hk_race_dashboard
```

**No accounts are involved.** No Fly.io, no Cloudflare, no password. This runs
entirely on your machine.

## 5. What hosting is for, and why you do not need it yet

You asked what platforms you would need to *host* this. Hosting means putting
it on the internet, and it buys exactly two things:

1. **You can open it on your phone**, at the track or anywhere else.
2. **The scraper runs by itself.** Twice a week, without your laptop being on,
   new race results appear in the dashboard on their own.

That is the whole list. Everything the dashboard shows, every page and every
number, works on your laptop without any of it.

So this is a decision you can take later, once you have used it a few times and
know whether you want it on your phone.

### What you have signed up for

| | What it does | Cost | Needed to use the dashboard? |
|---|---|---|---|
| **Fly.io** | Runs the dashboard on the internet | ~US$6/month | No |
| **Cloudflare R2** | Keeps a backup of the database | Free | No |

If you decide not to host it, cancel Fly and delete the R2 bucket. You lose
nothing — the dashboard keeps working on your laptop exactly as it does now.

## 6. If you do want to host it

One command, from PowerShell in this folder:

```powershell
.\ops\deploy.ps1
```

It installs the Fly command-line tool, signs you in, creates everything, asks
for your two Cloudflare values, generates the dashboard password, uploads the
database and then checks that it actually worked. Safe to re-run if it stops
partway.

`docs/deploy.md` is the detail: what to do if a scrape fails, how to restore
from backup, and what each error means.

## 7. What happens on a race day

**On your laptop.** Nothing is automatic. Start the dashboard, then run this
when you want the latest meeting:

```powershell
.\.venv\Scripts\python -m hkrd.jobs.nightly
```

It works out which meetings are missing results, fetches them, and recomputes
everything downstream. A few seconds when there is nothing new.

**Hosted.** Nothing to do. It scrapes five times a day and recalculates by
itself. The **Model Analysis** page tells you whether that is working — if the
last scrape failed, it says so there rather than quietly showing you last
week's card.

## 8. Things worth knowing

**Every view is a URL.** `?date=2026-07-15&race=4` — a page can be bookmarked
or shared exactly as you left it.

**⌘K or `/`** opens the command palette. It reaches every meeting, race, horse
and page, and it is the only place the meeting is chosen.

**Your bets are attached to the horses.** The Blackbook shows what you actually
staked on a booked horse and what came back — not a theoretical return from
betting every run.

**The model does not beat the market.** This was measured, honestly, and it is
written on the Model Analysis page rather than hidden. The dashboard is for
finding horses and understanding races; the staking advice that the old system
implied was never supported by its own numbers.

## 9. If something goes wrong

Stop the dashboard (Ctrl+C) and start it again. That fixes most things.

To rebuild the database from scratch:

```powershell
.\ops\start.ps1 -Rebuild
```

If a page is empty or a number looks wrong, that is a bug worth reporting — the
whole point of this rebuild is that an empty column should never be something
you have to notice yourself.

---

## 10. Filling in missing data

The database was built from your old archive folder, and that archive stops at
some point — the dashboard is not broken, it simply has not been told about
anything after that date. To see where it stops:

```powershell
.\ops\catch-up.ps1 -ShowOnly
```

It prints a month-by-month table of every source and says which ones have gone
quiet. To actually fetch what is missing:

```powershell
.\ops\catch-up.ps1              # the last 60 days
.\ops\catch-up.ps1 -Days 120    # further back
```

Races, results, dividends, comments on running and barrier trials all come from
HKJC and this fetches them. It asks one question every 1.2 seconds and only
about dates it cannot already answer, so a wide catch-up takes a while. Leave it
running. Re-running is always safe — every write replaces the same row rather
than adding a second one.

**Bets are the exception.** HKJC does not know what you staked, so no scrape can
recover them. They come from your account statements:

```powershell
.venv\Scripts\python -m hkrd.jobs.import_statement --src "C:\folder\statement.txt"
```

## 11. Making changes to the dashboard

Install **Claude Code** on your PC and open it in this folder. It can then read
the code, make the change, run the tests and show you the result — which is a
much shorter loop than describing a page to someone who cannot see it.

```powershell
npm install -g @anthropic-ai/claude-code
cd C:\Users\tbhbr\hk_racing_dashboard_26-27
claude
```

(That needs Node.js from https://nodejs.org first.)

Then just describe what you want in plain English. Useful things to say:

- "read AGENTS.md and docs/start-here.md first" — the working rules are written
  down, and they are what keeps the rebuild from turning back into the old one
- "run the tests before you tell me it works"
- "show me the page before and after"

---

## 12. Missing pace figures, incidents or tags on Results

Different from section 9. That one is about data the archive never had; this is
about data an earlier scrape got wrong or skipped.

```powershell
.\ops\repair.ps1          # what is damaged — fetches nothing
.\ops\repair.ps1 -Fix     # repair it
```

Three problems, three costs, and the job separates them so you are not waiting
on a fetch you do not need:

| | What went wrong | Cost |
|---|---|---|
| **pace** | A horse that pulled up carries a short list of section times, and that used to void the pace figure for its **entire field** — 30 races, 377 runners. Fixed in the code; the figures just need recomputing. | instant, no fetching |
| **comments on running** | 89 meetings have none, because the archive's incident reports only begin September 2025. Fetching the comments page alone is ~11 requests a meeting, not the ~30 a full re-scrape costs. **Tags come free** — they are read back out of the comments. | ~20 minutes |
| **race headers** | Five races lost their distance and class, so no pace could be computed for them either. | seconds |

Run `-Fix -Only pace` first if you want the instant half immediately. Repairing
is always safe to re-run — every write replaces the same row rather than adding
another.
