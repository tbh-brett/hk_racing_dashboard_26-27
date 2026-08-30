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

## 2. Getting it onto your PC the first time

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

## 3. How to open it after that

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

## 4. What hosting is for, and why you do not need it yet

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

## 5. If you do want to host it

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

## 6. What happens on a race day

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

## 7. Things worth knowing

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

## 8. If something goes wrong

Stop the dashboard (Ctrl+C) and start it again. That fixes most things.

To rebuild the database from scratch:

```powershell
.\ops\start.ps1 -Rebuild
```

If a page is empty or a number looks wrong, that is a bug worth reporting — the
whole point of this rebuild is that an empty column should never be something
you have to notice yourself.
