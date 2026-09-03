#!/usr/bin/env bash
# What happens when the machine starts.
#
#   1. If there is no database on the volume, restore it from object storage.
#   2. Start the schedule.
#   3. Hand the process over to Litestream, which runs the web server.
#
# Order matters. Litestream must own the web server rather than run beside it:
# as the parent it starts replicating BEFORE the first write and flushes the
# WAL after the last one, so a deploy that replaces the machine mid-write does
# not lose the tail. Started as a sibling, there is a window at each end where
# writes are unreplicated and nothing says so.

set -euo pipefail

DB="${HKRD_DB:-/data/hkrd.db}"
PORT="${HKRD_PORT:-8080}"
HOST="${HKRD_HOST:-0.0.0.0}"
CONFIG=/app/ops/litestream.yml

say() { printf '  %-14s %s\n' "$1" "$2"; }

# ── 0. what the container actually received ──────────────────────────────────
# Printed BEFORE the restore, because the restore's own failure names the
# symptom and never the cause: R2 answers a bad signature with a 403 that says
# "check your secret access key", which is true of four different mistakes.
# Endpoint doubled with the bucket, region not "auto", the wrong key pair, or
# a value that never reached the container at all -- the Dockerfile declares
# LITESTREAM_ENDPOINT and LITESTREAM_REGION as empty image defaults, and if
# those ever won over the Fly secrets the request would go out unsigned for R2
# and look exactly like a wrong password.
#
# Everything here is safe in a log. The endpoint, region and bucket are not
# secrets. The access key id is an identifier, not a credential -- it is the
# username half. The secret appears ONLY as a character count, which separates
# "arrived mangled" from "arrived intact but wrong" and tells you nothing else.
_ep="${LITESTREAM_ENDPOINT:-}"
_rg="${LITESTREAM_REGION:-}"
_ak="${LITESTREAM_ACCESS_KEY_ID:-}"
_sk="${LITESTREAM_SECRET_ACCESS_KEY:-}"

say "replica url" "${LITESTREAM_REPLICA_URL:-<unset>}"
say "endpoint"    "${_ep:-<EMPTY - would talk to AWS, not R2>}"
say "region"      "${_rg:-<EMPTY - R2 needs auto>}"
say "access key"  "${_ak:-<unset>} (${#_ak} chars, R2 uses 32)"
say "secret key"  "${#_sk} chars (R2 uses 64) - value never printed"

# ── 1. the database ──────────────────────────────────────────────────────────
mkdir -p "$(dirname "$DB")"

if [[ -f "$DB" ]]; then
  say "database" "$DB (present, $(du -h "$DB" | cut -f1))"

elif [[ -n "${LITESTREAM_REPLICA_URL:-}" ]]; then
  say "database" "$DB missing — restoring from ${LITESTREAM_REPLICA_URL}"
  # -if-replica-exists so a genuinely first-ever boot starts empty instead of
  # failing. It does NOT cover the dangerous case, which is a replica that
  # exists but is unreachable because the credentials are wrong: that must
  # stop here rather than start an empty dashboard that then replicates its
  # own emptiness over the backup.
  if litestream restore -if-replica-exists -config "$CONFIG" "$DB"; then
    if [[ -f "$DB" ]]; then
      say "restored" "$(du -h "$DB" | cut -f1)"
    else
      say "restored" "no replica found — starting from an empty database"
    fi
  else
    echo "  RESTORE FAILED. Refusing to start."                            >&2
    echo "  A machine that starts empty here will replicate that emptiness"  >&2
    echo "  over the backup within ten seconds. Check the bucket, the URL"   >&2
    echo "  and the credentials before restarting. See docs/deploy.md."      >&2
    exit 1
  fi

else
  say "database" "$DB missing and no LITESTREAM_REPLICA_URL — starting empty"
  say "warning" "nothing is being backed up. See docs/deploy.md."
fi

# ── 2. the schedule ──────────────────────────────────────────────────────────
# Backgrounded. It has no state of its own: every job it runs is idempotent and
# safe to interrupt, so losing it on shutdown costs nothing. Litestream in the
# foreground is what keeps the container alive.
if [[ -f /app/ops/crontab ]]; then
  say "schedule" "$(grep -cv '^\s*\(#\|$\)' /app/ops/crontab) entries, TZ=${TZ:-UTC}"
  supercronic -passthrough-logs /app/ops/crontab &
else
  say "schedule" "no crontab found — nothing will be scraped"
fi

# ── 3. the server ────────────────────────────────────────────────────────────
say "serving" "http://${HOST}:${PORT}"

if [[ -n "${LITESTREAM_REPLICA_URL:-}" ]]; then
  exec litestream replicate -config "$CONFIG" \
    -exec "uvicorn hkrd.api.app:app --host ${HOST} --port ${PORT}"
fi

# No replica configured. Say so once, loudly, rather than looking identical to
# a replicated deploy — this is the state where a lost volume is a lost ledger.
echo "  NOT REPLICATED. LITESTREAM_REPLICA_URL is unset; the only copy of the" >&2
echo "  betting ledger is this volume."                                        >&2
exec uvicorn hkrd.api.app:app --host "${HOST}" --port "${PORT}"
