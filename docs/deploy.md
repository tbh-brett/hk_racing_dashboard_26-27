# Deploying the dashboard

One machine on Fly.io, one volume, one SQLite file, replicated continuously to
object storage. This document is the runbook: first deploy, restoring from
backup, and what to do when a scrape fails.

---

## What you have to sign up for

Three accounts. Two of them are free at this size.

| What | Why | Cost at this size |
|---|---|---|
| **Fly.io** | Runs the container and holds the volume the database sits on. | ~US$5–7/mo for one `shared-cpu-1x` machine with 1 GB RAM, plus ~US$0.15/mo for a 1 GB volume. |
| **Object storage, S3-compatible** — Cloudflare R2, Backblaze B2, or Fly's own Tigris | Litestream streams the SQLite WAL here. This is the copy that survives the machine. | Free. The database is 33 MB; R2's free tier is 10 GB and B2's is 10 GB. |
| **A domain** (optional) | `hkrd.fly.dev` works out of the box and is HTTPS. A domain only buys you a nicer URL. | ~US$10–15/yr, whatever registrar you like. |

**You do not need a database service.** No Supabase, no Neon, no RDS, no
Planetscale. The database is a 33 MB file with 342,065 rows, and every query on
it measures 0.000s locally against 1.09s for a full-table read. Putting it
behind a network would add a round-trip to each of the hundreds of queries a
page makes, in exchange for nothing this workload needs.

**Serverless hosts will not work.** Vercel, Netlify, Cloudflare Pages and AWS
Lambda have no persistent writable filesystem, and SQLite is a file. This needs
a container with a volume attached, which is what Fly, Railway, Render and
Hetzner all provide. Fly is what the config in this repo is written for.

### Which object store

Any of the three works. R2 has no egress charges, which matters if you ever
restore repeatedly; Tigris is one fewer signup because Fly provisions it with
`fly storage create`; B2 is the cheapest per GB, which is irrelevant at 33 MB.
Pick R2 if you have no preference.

---

## First deploy

You need `flyctl` installed and `fly auth login` done.

### 1. The bucket and its keys — Cloudflare R2

In the Cloudflare dashboard:

1. **R2 → Create bucket.** Name it `hkrd-backups`. Location: *Automatic*, or
   *Asia-Pacific* to sit near the machine. **Leave the jurisdiction unset** —
   picking EU or FedRAMP changes the endpoint hostname, and the one below stops
   working.
2. **R2 → Manage API Tokens → Create API Token.**
   - Permission: **Object Read & Write**
   - Scope it to `hkrd-backups` only, not "all buckets". This token is going
     into an environment variable on an internet-facing machine; there is no
     reason for it to reach anything else in the account.
   - TTL: forever, unless you want to diarise a rotation.
3. Copy the three things it shows you **once**: the Access Key ID, the Secret
   Access Key, and the S3 endpoint. Put all three in your password manager
   before closing the page — the secret is not shown again.

The endpoint is `https://<account-id>.r2.cloudflarestorage.com`. The account ID
is also on the R2 overview page if you lose it.

Three settings that matter, all checked against the Litestream 0.3.13 binary
rather than assumed:

- **`LITESTREAM_REGION=auto`.** R2 has no regions and rejects a real one.
- **The endpoint is required.** Left empty, Litestream talks to Amazon and
  fails with a credentials error that names the wrong problem entirely.
- **Path-style addressing**, which Litestream uses automatically once an
  endpoint is set — the request it builds is
  `https://<account-id>.r2.cloudflarestorage.com/hkrd-backups?prefix=hkrd/generations/`,
  with the bucket in the path. R2 accepts this; nothing to configure.

Tigris instead, if you would rather not leave Fly:

```bash
fly storage create        # prints the bucket name and the three credentials
```

### 2. The app and the volume

```bash
fly apps create hkrd
fly volumes create hkrd_data --region hkg --size 1
```

1 GB is roughly thirty times the current database. Fly volumes can be grown
later and cannot be shrunk.

### 3. The secrets

```bash
fly secrets set \
  HKRD_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')" \
  LITESTREAM_REPLICA_URL="s3://hkrd-backups/hkrd" \
  LITESTREAM_ACCESS_KEY_ID="..." \
  LITESTREAM_SECRET_ACCESS_KEY="..." \
  LITESTREAM_ENDPOINT="https://<account-id>.r2.cloudflarestorage.com"
```

Print the generated password and put it in your password manager before you
close the terminal — it is not stored anywhere you can read it back from.

`HKRD_PASSWORD` is not optional. The app refuses to start without it, on
purpose: it serves the whole betting ledger and the blackbook, and a deploy
that has forgotten its secret must fail rather than publish them. Minimum
twelve characters, also enforced.

### 4. Ship it

```bash
fly deploy
```

The first boot finds no database and no replica, so it starts empty. That is
expected — the next step fills it.

### 5. Get the data up there

The database is built from the old repo and pushed once. It is not in git (33
MB of derived data does not belong there, and the old repo's `.git` reached 229
MB doing exactly that).

```bash
# locally, from a checkout with ../hk_race_dashboard beside it
python -m hkrd.jobs.bootstrap --legacy ../hk_race_dashboard

# push it, with the app stopped so nothing writes underneath the copy
fly machine list                      # note the machine id
fly machine stop <id>
fly sftp shell -a hkrd
  put hkrd.db /data/hkrd.db
  exit
fly machine start <id>
```

Within ten seconds Litestream will have replicated it. Confirm:

```bash
fly logs -a hkrd | grep litestream
fly ssh console -a hkrd -C "litestream snapshots -config /app/ops/litestream.yml /data/hkrd.db"
```

### 6. Check it

```bash
fly open                              # the login form
curl https://hkrd.fly.dev/api/health  # {"ok":true} — open, says nothing else
```

Sign in, then `/api/ops/status` shows what is in the database, how old the
latest meeting is, and when the scrape last ran.

---

## Restoring from backup

This is the whole reason Litestream is there. It applies whether the volume was
lost, the machine was destroyed, or the database was corrupted by something you
did on purpose.

```bash
# 1. Stop writing. A restore into a live database races the scraper.
fly machine stop <id>

# 2. Move the bad file aside rather than deleting it — it is evidence, and it
#    is also the only copy of anything written since the last sync.
fly ssh console -a hkrd -C "mv /data/hkrd.db /data/hkrd.db.broken"

# 3. Restore. Litestream reads the replica URL from the environment.
fly ssh console -a hkrd -C \
  "litestream restore -config /app/ops/litestream.yml /data/hkrd.db"

# 4. Start, and check the row counts against what you expect.
fly machine start <id>
curl -b "$COOKIE" https://hkrd.fly.dev/api/ops/status
```

To restore to a moment before a bad write rather than to the latest state:

```bash
litestream restore -config /app/ops/litestream.yml \
  -timestamp 2026-08-27T14:00:00Z -o /data/hkrd.db
```

Retention is 30 days (`ops/litestream.yml`). Beyond that the generation is
gone, and the recovery is a fresh `bootstrap` from the legacy repo plus a
re-scrape of everything since.

**If a restore ever fails, the entrypoint refuses to start the app.** That is
deliberate. A machine that starts with an empty database begins replicating
that emptiness over the backup within ten seconds, which turns a recoverable
problem into an unrecoverable one. If you see `RESTORE FAILED. Refusing to
start.` in the logs, fix the credentials or the bucket — do not work around it
by deleting the replica config.

That guard rests on Litestream distinguishing two cases, which it does —
verified against the 0.3.13 binary, both branches:

| Situation | `restore -if-replica-exists` | What the entrypoint does |
|---|---|---|
| Replica reachable, no backups in it yet (a genuine first boot) | logs `no matching backups found`, **exit 0**, writes no file | starts with an empty database |
| Endpoint unreachable, or the credentials are wrong | logs `cannot fetch generations: ...`, **exit 1**, writes no file | refuses to start |

Without that distinction the flag would swallow an auth failure and the
protection would be decorative.

---

## When a scrape fails

### How you find out

Not from the logs. The dashboard tells you: `/api/ops/status` carries
`scrape_state`, and it has four values because the fix for each is different.

| `scrape_state` | What happened | What to do |
|---|---|---|
| `ok` | The last run finished and reported no errors. | Nothing. |
| `failed` | It ran and said what broke. `last_scrape.detail` names it. | Below. |
| `running` | A row was opened and never closed. Either it is running right now, or the process was killed mid-scrape. | Check `started_at`. More than ten minutes ago means it was killed; just re-run it. |
| `never` | Nothing has ever run on this machine. | The schedule is not wired up — check `fly logs` for the `schedule` line at boot. |

`stale: true` alongside `scrape_state: ok` means the scrapes are working and
there simply has not been a meeting for more than six days. That is a normal
summer. It never fails the health check, and Fly will not restart the machine
for it.

### Run it by hand

```bash
fly ssh console -a hkrd
python -m hkrd.jobs.nightly --db /data/hkrd.db --dry-run   # what would it do?
python -m hkrd.jobs.nightly --db /data/hkrd.db             # do it
```

`--dry-run` makes no requests at all. It reads the database and prints the
window with a reason per date, so you can see what the job thinks is
outstanding before it fetches anything.

### The failures you will actually see

**`the database has this meeting but the scrape returned no races`**
The card was fetched on Tuesday, so we know Wednesday raced, and Wednesday
night came back with nothing. Usually HKJC published results late. Re-run it;
the window reaches four days back, so a meeting missed on the night is picked
up the next morning without any intervention.

**`all N probes returned a page that would not parse`**
Either HKJC changed the race card layout or the site is unreachable from Fly.
Check one date by hand in a browser before assuming the worst — a week with no
racing and a changed layout look identical from inside the job, which is why it
says so out loud rather than exiting quietly on an empty window.

If the layout has changed, the parser will name the column it could not find.
That is by design: every parser here maps columns by header text and raises on
a shape it does not recognise, because the alternative is a trainer's name
landing in the horse column and nothing looking wrong for three days.

**`dividends: ...` or `vet: ...` in the warnings**
Not failures. Dividends and vet records only exist after a race is run, and the
job reports what was absent rather than treating it as an error. They arrive on
the next run.

**`could not read the trial day list`**
The trials job asks HKJC which trial days exist rather than guessing weekdays —
measured over the 2025-26 archive, trials fall on Tue, Thu and Fri equally
(26.4% of 159 days each), Mon 16.4%, and Sat and Wed a handful of times, so a
Tuesday/Thursday schedule would miss 47% of them. If the list itself cannot be
read the job exits 1 rather than reporting "nothing new", because that is the
one failure that would otherwise look like success forever.

### The schedule

`ops/crontab`, in Hong Kong time. Five `nightly` runs a day (07:00, 13:00,
19:00, 22:45, 23:45) and two trial runs (12:00, 20:00). No race calendar is
encoded anywhere: the job reads the database, works out what is outstanding,
and asks HKJC only about dates it cannot already answer. A quiet night is a
handful of requests and no writes, and it does not even run the derive pass.

```bash
fly logs -a hkrd | grep -A20 "window"     # what the last run decided
```

---

## Routine operations

```bash
fly deploy                                   # ship a change
fly logs -a hkrd                             # everything, including the scrapes
fly ssh console -a hkrd                      # a shell on the machine
fly machine restart <id>                     # after a config change

fly secrets set HKRD_PASSWORD="..."          # change the password (restarts)
```

Changing `HKRD_PASSWORD` invalidates every existing session immediately — the
cookie is signed with the password as the key, so a changed password makes
every outstanding cookie fail its signature check. That is the logout-everywhere
button.

### What is not deployed

Live odds capture. `ingest/odds.py` parses a snapshot payload but nothing
fetches one on a schedule yet, so `odds_snapshots` only holds what the legacy
import brought over. Every odds-dependent figure therefore reads the last
snapshot the archive has, not the current market. Worth building before the
season proper; it is the one gap between what is scheduled and what the pages
assume.
