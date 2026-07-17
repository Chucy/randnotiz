"""Konsistenter SQLite-Snapshot + Integritätscheck für den nächtlichen Backup-Cron.

Aufruf (im Container, per VM-Cron VOR dem srv01-Pull):
    python -m app.backup

Schreibt randnotiz-snapshot.db neben die Live-DB (landet damit automatisch im
srv01-rsync mit). Die Online-Backup-API liefert im Gegensatz zu einem rohen Kopieren
der live beschriebenen .db-Datei einen garantiert konsistenten Stand.
Exit-Code != 0, wenn der Snapshot fehlschlägt oder der integrity_check nicht "ok" ist.
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
    src.backup(dst)  # Online-Backup-API: konsistent auch bei parallelen Writes
    dst.close()
    src.close()

    chk = sqlite3.connect(tmp)
    check = chk.execute("PRAGMA integrity_check").fetchone()[0]
    chk.close()  # checkpointet und räumt -wal/-shm des Tmp-Files ab
    if check != "ok":
        print(f"FEHLER: integrity_check = {check!r} — Snapshot NICHT übernommen", file=sys.stderr)
        sys.exit(1)

    os.replace(tmp, snap)  # atomar: srv01 sieht nie einen halben Snapshot
    print(f"Snapshot ok: {snap} ({os.path.getsize(snap)} Bytes)")


if __name__ == "__main__":
    main()
