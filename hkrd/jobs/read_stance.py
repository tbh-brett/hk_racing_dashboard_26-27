"""Read the stance of every Racing To Win interview answer.

    python -m hkrd.jobs.read_stance [--db PATH]

`connections_quote.stance` is positive, negative or neutral by
`derive.stance` (`RULE`). Every answer is read again on each run -- under 300
so far, a few milliseconds -- so a change to the rule reaches the whole archive
and not only what arrives after it. Runs inside each tips import and when the
server starts (`jobs/init_store`), so a deploy that changes the rule re-reads
everything without a hand-run job.

Interviews only. The other sources are analysts and presenters, mostly in
Chinese, and the rule reads English answers from the horse's own connections.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from hkrd.derive import stance
from hkrd.store import tips
from hkrd.store.connect import Connection, db_path, get_conn, init_db, transaction

__all__ = ["SOURCE", "fill", "run"]

SOURCE = "rtw_interview"


def fill(conn: Connection) -> dict[str, int]:
    """Read every interview answer and store its stance. The caller owns the
    transaction. Returns how many answers there are, how many changed, and how
    many read each way."""
    rows = tips.stance_texts(conn, SOURCE)
    read = {qid: stance.read(words).stance for qid, words, _ in rows}
    changed = tips.set_stances(conn, read)
    return {"answers": len(rows), "changed": changed, **Counter(read.values())}


def run(db: Path | None = None) -> dict[str, int]:
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        with transaction(conn, immediate=True):
            return fill(conn)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    out = run(ap.parse_args(argv).db)
    print(f"  {stance.RULE}: {out['answers']} answers, {out['changed']} changed · "
          + " · ".join(f"{k} {out.get(k, 0)}" for k in ("positive", "neutral", "negative")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
