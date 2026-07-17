"""Consistent SQLite snapshot + integrity check for the nightly backup cron.

Invocation (in the container, via VM cron BEFORE the srv01 pull):
    python -m app.backup

Writes randnotiz-snapshot.db next to the live DB (so it's automatically included in
the srv01 rsync). Unlike a raw copy of the live .db file being written to, the online
backup API guarantees a consistent state.
Exit code != 0 if the snapshot fails or the integrity_check is not "ok".
"""
import os
import sqlite3
import sys

from .db import DB_PATH


def main() -> None:
    db = os.path.abspath(DB_PATH)
    snap = os.path.join(os.path.dirname(db), "randnotiz-snapshot.db")
    tmp = snap + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)

    src = sqlite3.connect(db)
    src.execute("PRAGMA busy_timeout = 10000")
    dst = sqlite3.connect(tmp)
    src.backup(dst)  # online backup API: consistent even with concurrent writes
    dst.close()
    src.close()

    chk = sqlite3.connect(tmp)
    check = chk.execute("PRAGMA integrity_check").fetchone()[0]
    chk.close()  # checkpoints and cleans up the -wal/-shm of the tmp file
    if check != "ok":
        print(f"ERROR: integrity_check = {check!r} — snapshot NOT applied", file=sys.stderr)
        sys.exit(1)

    os.replace(tmp, snap)  # atomic: srv01 never sees a half-written snapshot
    print(f"Snapshot ok: {snap} ({os.path.getsize(snap)} bytes)")


if __name__ == "__main__":
    main()
