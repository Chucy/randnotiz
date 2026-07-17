# Randnotiz

[🇩🇪 Deutsch](#deutsch) · [🇬🇧 English](#english)

<a id="deutsch"></a>

Selbstgehostete Plattform, um strukturiertes **Testleser-Feedback** zu einem Manuskript zu sammeln — eine schlanke, datensparsame Alternative zu SaaS-Tools wie BetaReader.io oder BetaBooks.

Testleser öffnen einen persönlichen Magic-Link (kein Konto, kein Passwort), lesen das Buch Kapitel für Kapitel und hinterlassen **Inline-Kommentare pro Absatz**, schnelle **Reaktionen** (❤️ / ❓ / 😴) und Antworten auf **Kapitel-Fragebögen**. Als Autor:in bekommst du ein Admin-Dashboard, das von wenigen Notizen bis zu hunderten Kommentaren skaliert, plus einen JSON-Export zur weiteren Auswertung.

> **Status:** im privaten Beta-Einsatz. Funktional fertig für seinen Zweck; bewusst klein gehalten.

<!-- TODO: Screenshots hier einfügen — Leseransicht (Kommentar-Overlay pro Absatz) + Admin-Dashboard (Kapitel-Kacheln + Reaktions-Heatmap). Größter Wirkung-pro-Aufwand-Hebel. -->

## Funktionen

- **Magic-Link-Leser** — ein Link = eine Leser-Identität, keine Registrierung.
- **Inline-Absatz-Kommentare** — Absatz antippen, Notiz hinterlassen; Leser können eigene bearbeiten/löschen.
- **Reaktionen** pro Absatz (❤️ gefällt · ❓ verwirrt · 😴 gelangweilt) — als Heatmap sichtbar gemacht.
- **Kapitel-Fragebögen** — Skala- und Freitextfragen, im Admin editierbar.
- **Admin-Dashboard** — serverseitige Filter (Kapitel/Status/Leser), KPI-Leiste, Kapitel-Kacheln, Reaktions-Heatmap, verdichtete Kommentar-Cards, Lesefortschritt. Bleibt auch bei 500+ Kommentaren übersichtlich.
- **Fortschritts-Tracking** — wie weit jeder Leser gekommen ist, pro Kapitel (datensparsam: keine Verweildauer-Erfassung).
- **Multi-Book** — eine Instanz kann mehrere Manuskripte bedienen; jeder Leser-Link gehört zu genau einem Buch.
- **JSON-Export** für die Offline- / KI-gestützte Auswertung.
- **Ingest** von Markdown-Kapiteln → SQLite; ein Re-Ingest bildet vorhandene Kommentare per Text-Matching auf geänderte Absätze ab.

## Stack

FastAPI · SQLite · Jinja2 · Vanilla JS/CSS. Ein Docker-Container. **Kein Frontend-Framework, kein Build-Step** — bewusst so gewählt, damit das Projekt leicht zu betreiben und zu warten bleibt.

## Schnellstart

```bash
git clone <this-repo> randnotiz
cd randnotiz
cp .env.example .env        # dann RANDNOTIZ_ADMIN_KEY setzen
docker compose up -d --build
```

Der Container lauscht standardmäßig auf `127.0.0.1:8300` und erwartet einen TLS-terminierenden Reverse-Proxy (z. B. nginx) davor. Health-Check: `GET /healthz`. Siehe [`deploy/`](deploy/) für eine Beispiel-nginx-Site + Let's-Encrypt-Setup.

### Konfiguration (Umgebungsvariablen)

| Variable | Zweck | Default |
|---|---|---|
| `RANDNOTIZ_ADMIN_KEY` | Admin-Login-Key (langen Zufallswert setzen). **Pflicht.** | — |
| `RANDNOTIZ_BOOKS` | Verzeichnis mit einem Ordner pro Buch (`<slug>/` mit Kapiteln, `Makefile`, `assets/`). | — |
| `RANDNOTIZ_DB` | Pfad zur SQLite-Datenbank. | `data/randnotiz.db` |

### Ein Buch hinzufügen

```bash
# Kapitelreihenfolge stammt aus dem Makefile des Buchs (Quelle der Wahrheit):
python -m app.ingest --makefile /path/to/book/Makefile \
    --slug my-book --title "My Book" --new

# Re-Ingest nach Änderungen — erst die Feedback-Auswirkung ansehen, dann echt ausführen:
python -m app.ingest --makefile /path/to/book/Makefile --slug my-book --dry-run
python -m app.ingest --makefile /path/to/book/Makefile --slug my-book
```

`--dry-run` zeigt, wie vorhandene Kommentare neu zugeordnet würden, bevor etwas geschrieben wird. `python -m app.ingest -h` listet alle Optionen (`--book-dir`, `--exclude`, `--delete-book`, …).

### Admin & Leser

- Admin: `/admin` aufrufen, mit `RANDNOTIZ_ADMIN_KEY` anmelden. Dort Leser-Links anlegen und Feedback ansehen.
- Leser: den Magic-Link (`/r/<token>`) teilen. Jeder Link ist eine Identität — wer sich ein Gerät teilt, braucht je einen eigenen Link.
- Export: `/admin` → Export, oder `GET /api/export?book=<slug>`.

## Backup

`app/backup.py` schreibt einen konsistenten Snapshot (`data/randnotiz-snapshot.db`) über die Online-Backup-API von SQLite plus einen `integrity_check`. Per Cron einplanen und **immer aus dem Snapshot wiederherstellen**, nie aus der Live-`.db` (die ein offenes WAL haben kann). SQLite auf einer lokalen Platte halten — nicht auf einem NFS/CIFS-Mount (File-Locking).

```bash
python -m app.backup
```

## Architektur

- **Schema & Migrationen** liegen in `app/db.py`: ein Basis-Schema plus additive `ALTER TABLE ADD COLUMN` / `CREATE INDEX IF NOT EXISTS`-Migrationen, die bei jedem Start laufen — kein separates Migrations-Tool.
- **Sicherheit:** Admin-Auth per HttpOnly/Secure/SameSite-Cookie und `secrets.compare_digest`; Security-Header (CSP, `X-Frame-Options`, nosniff) auf jeder Antwort; Path-Traversal-geschütztes Ausliefern von Assets; Non-Root-Container (UID 10001); Text-Längenlimit gegen Storage-Missbrauch.
- **Zuverlässigkeit:** WAL-Modus + Busy-Timeout; ein Catch-all-Exception-Handler, der loggt und einen generischen 500 zurückgibt; `/healthz` als Basis für den Docker-`HEALTHCHECK`.

## Lizenz

[MIT](LICENSE)

---

<a id="english"></a>

[🇩🇪 Deutsch](#deutsch) · 🇬🇧 English

Self-hosted platform for collecting structured **beta-reader feedback** on a manuscript — a lightweight, privacy-friendly alternative to SaaS tools like BetaReader.io or BetaBooks.

Readers open a personal magic link (no account, no password), read the book chapter by chapter, and leave **inline comments per paragraph**, quick **reactions** (❤️ / ❓ / 😴), and answers to **per-chapter questionnaires**. As the author you get an admin dashboard that scales from a handful of notes to hundreds of comments, plus a JSON export for further analysis.

> **Status:** used in a live private beta. Functionally complete for its purpose; deliberately small.

## Features

- **Magic-link readers** — one link = one reader identity, no signup.
- **Inline paragraph comments** — tap a paragraph, leave a note; readers can edit/delete their own.
- **Reactions** per paragraph (❤️ liked · ❓ confused · 😴 bored) — surfaced as a heatmap.
- **Per-chapter questionnaires** — scale and free-text questions, editable in the admin.
- **Admin dashboard** — server-side filtering (chapter/status/reader), KPI bar, chapter tiles, reaction heatmap, condensed comment cards, reader progress. Built to stay readable at 500+ comments.
- **Reading-progress tracking** — how far each reader got, per chapter (privacy-conscious: no dwell-time tracking).
- **Multi-book** — one instance can serve several manuscripts; each reader link is bound to one book.
- **JSON export** for offline / AI-assisted analysis.
- **Ingest** Markdown chapters → SQLite; re-ingest remaps existing comments onto changed paragraphs by text-matching.

## Stack

FastAPI · SQLite · Jinja2 · vanilla JS/CSS. One Docker container. **No frontend framework, no build step** — a deliberate choice to keep the project easy to run and maintain.

## Quick start

```bash
git clone <this-repo> randnotiz
cd randnotiz
cp .env.example .env        # then set RANDNOTIZ_ADMIN_KEY
docker compose up -d --build
```

The container listens on `127.0.0.1:8300` by default and expects a TLS-terminating reverse proxy (e.g. nginx) in front of it. Health check: `GET /healthz`. See [`deploy/`](deploy/) for an example nginx site + Let's Encrypt setup.

### Configuration (environment variables)

| Variable | Purpose | Default |
|---|---|---|
| `RANDNOTIZ_ADMIN_KEY` | Admin login key (set a long random value). **Required.** | — |
| `RANDNOTIZ_BOOKS` | Directory holding one folder per book (`<slug>/` with chapters, `Makefile`, `assets/`). | — |
| `RANDNOTIZ_DB` | SQLite database path. | `data/randnotiz.db` |

### Adding a book

```bash
# Chapter order comes from the book's Makefile (the source of truth):
python -m app.ingest --makefile /path/to/book/Makefile \
    --slug my-book --title "My Book" --new

# Re-ingest after edits — preview feedback impact first, then run for real:
python -m app.ingest --makefile /path/to/book/Makefile --slug my-book --dry-run
python -m app.ingest --makefile /path/to/book/Makefile --slug my-book
```

`--dry-run` shows how existing comments would be remapped before anything is written. Run `python -m app.ingest -h` for all options (`--book-dir`, `--exclude`, `--delete-book`, …).

### Admin & readers

- Admin: browse to `/admin`, log in with `RANDNOTIZ_ADMIN_KEY`. Create reader links and view feedback there.
- Readers: share the magic link (`/r/<token>`). Each link is one identity — people sharing a device need one link each.
- Export: `/admin` → export, or `GET /api/export?book=<slug>`.

## Backup

`app/backup.py` writes a consistent snapshot (`data/randnotiz-snapshot.db`) using SQLite's online-backup API plus an `integrity_check`. Schedule it via cron and **restore from the snapshot**, never from the live `.db` file (which may have an open WAL). Keep SQLite on a local disk — not on an NFS/CIFS mount (file locking).

```bash
python -m app.backup
```

## Architecture notes

- **Schema & migrations** live in `app/db.py`: a base schema plus additive `ALTER TABLE ADD COLUMN` / `CREATE INDEX IF NOT EXISTS` migrations that run on every startup — no separate migration tool.
- **Security:** admin auth via HttpOnly/Secure/SameSite cookie and `secrets.compare_digest`; security headers (CSP, `X-Frame-Options`, nosniff) on every response; path-traversal-guarded asset serving; non-root container (UID 10001); a text-length cap against storage abuse.
- **Reliability:** WAL mode + busy timeout; a catch-all exception handler that logs and returns a generic 500; `/healthz` backs the Docker `HEALTHCHECK`.

## License

[MIT](LICENSE)
