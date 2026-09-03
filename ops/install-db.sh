#!/usr/bin/env bash
# Put an uploaded database in place of the running one.
#
# Run inside the machine, after the file has been sent up beside it:
#
#   fly sftp put hkrd.db /data/hkrd.db.new -a hkrd
#   fly ssh console -a hkrd -C /app/ops/install-db.sh
#   fly machine restart <id> -a hkrd
#
# WHY NOT SEND IT STRAIGHT OVER /data/hkrd.db, WITH THE MACHINE STOPPED. That
# is the obvious sequence and it does not work, for two separate reasons.
#
# SFTP needs the machine RUNNING. hallpass — the SSH server flyctl connects to
# — is a process inside the VM, so a stopped machine has no SSH server at all
# and the transfer has nothing to reach. Stopping first to make the copy safe
# is not an option that exists.
#
# And a running machine has uvicorn and Litestream both holding that file open,
# with a WAL and a shared-memory index beside it that describe the database
# being replaced. Writing over the file in place leaves the three disagreeing:
# SQLite recovers the old WAL onto the new database, which then opens fine and
# is wrong in ways nothing reports.
#
# So: rename over it, which is atomic and leaves the open descriptors pointing
# at the old inode until those processes exit; delete the sidecar files that
# belong to that old inode; then restart, so everything reopens the new one.

set -euo pipefail

DB="${HKRD_DB:-/data/hkrd.db}"
NEW="${DB}.new"

say() { printf '  %-14s %s\n' "$1" "$2"; }

if [[ ! -f "$NEW" ]]; then
  echo "  $NEW is not there. Send it up first:"            >&2
  echo "    fly sftp put hkrd.db ${NEW} -a hkrd"           >&2
  exit 1
fi

# A transfer that stopped halfway leaves a file that still opens and still
# answers some queries. Checked here, where the cost of a bad answer is
# "nothing changed" — after the rename it would be the live database.
if ! check=$(python - "$NEW" <<'PY' 2>&1
import sqlite3, sys
db = sys.argv[1]
try:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        sys.exit("integrity_check did not return ok")
    print(conn.execute("SELECT count(*) FROM runners").fetchone()[0])
except sqlite3.Error as exc:
    # The message, not the traceback. Whoever reads this is mid-deploy and
    # the stack is all inside this file.
    sys.exit(f"{type(exc).__name__}: {exc}")
PY
); then
  echo "  ${NEW} is not a usable database:"                >&2
  echo "  ${check}"                                        >&2
  echo "  Nothing was changed. Send it up again."          >&2
  exit 1
fi

say "uploaded" "${NEW} ($(du -h "$NEW" | cut -f1), ${check} runners)"

# The check above opened a WAL-mode database, and SQLite creates the shared
# memory index for one even on a read-only connection. Left behind, those two
# become orphans named after a file that is about to stop existing.
rm -f "${NEW}-wal" "${NEW}-shm"

mv "$NEW" "$DB"
rm -f "${DB}-wal" "${DB}-shm"

# Litestream's local generation state, which describes the database that was
# just replaced. Cleared, the next start takes a fresh snapshot of what is
# actually there instead of trying to continue a generation that no longer
# matches the file underneath it.
rm -rf "$(dirname "$DB")/.$(basename "$DB")-litestream"

say "installed" "$DB"
say "next" "fly machine restart <id> -a hkrd"
