"""SQLite-Schema und Verbindung für Randnotiz."""
import os
import sqlite3

DB_PATH = os.environ.get("RANDNOTIZ_DB", os.path.join(os.path.dirname(__file__), "..", "data", "randnotiz.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id),
    num INTEGER NOT NULL,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    updated_at TEXT,
    UNIQUE(book_id, slug)
);
CREATE TABLE IF NOT EXISTS blocks (
    id INTEGER PRIMARY KEY,
    chapter_id INTEGER NOT NULL REFERENCES chapters(id),
    idx INTEGER NOT NULL,
    html TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_blocks_chapter ON blocks(chapter_id, idx);
CREATE TABLE IF NOT EXISTS readers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    token TEXT UNIQUE NOT NULL,
    book_id INTEGER REFERENCES books(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY,
    reader_id INTEGER NOT NULL REFERENCES readers(id),
    chapter_id INTEGER NOT NULL REFERENCES chapters(id),
    block_idx INTEGER NOT NULL,
    quote TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0,
    orphaned INTEGER NOT NULL DEFAULT 0,
    edited_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS reactions (
    id INTEGER PRIMARY KEY,
    reader_id INTEGER NOT NULL REFERENCES readers(id),
    chapter_id INTEGER NOT NULL REFERENCES chapters(id),
    block_idx INTEGER NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(reader_id, chapter_id, block_idx, kind)
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id),
    pos INTEGER NOT NULL,
    text TEXT NOT NULL,
    qtype TEXT NOT NULL CHECK(qtype IN ('scale', 'text'))
);
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY,
    reader_id INTEGER NOT NULL REFERENCES readers(id),
    chapter_id INTEGER NOT NULL REFERENCES chapters(id),
    question_id INTEGER NOT NULL REFERENCES questions(id),
    value TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(reader_id, chapter_id, question_id)
);
CREATE TABLE IF NOT EXISTS reading_progress (
    id INTEGER PRIMARY KEY,
    reader_id INTEGER NOT NULL REFERENCES readers(id),
    chapter_id INTEGER NOT NULL REFERENCES chapters(id),
    max_block_idx INTEGER NOT NULL DEFAULT 0,
    done_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(reader_id, chapter_id)
);
CREATE TABLE IF NOT EXISTS reader_activity (
    reader_id INTEGER PRIMARY KEY REFERENCES readers(id),
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Indizes für die gefilterten Admin-Queries (Kapitel/Leser). Idempotent, laufen bei
-- jedem executescript() mit — wie idx_blocks_chapter. Bei Beta-Mengen (~500+ Zeilen)
-- noch nicht performancekritisch, aber zukunftssicher für mehrere Bücher/Revisionsrunden.
CREATE INDEX IF NOT EXISTS idx_comments_chapter ON comments(chapter_id, block_idx);
CREATE INDEX IF NOT EXISTS idx_comments_reader ON comments(reader_id);
CREATE INDEX IF NOT EXISTS idx_reactions_chapter ON reactions(chapter_id, block_idx);
CREATE INDEX IF NOT EXISTS idx_answers_chapter ON answers(chapter_id, question_id);
"""


def get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")  # bei parallelem Writer kurz warten statt sofort "database is locked"
    conn.execute("PRAGMA journal_mode = WAL")   # robustere gleichzeitige Reads/Writes (persistent, Aufruf ist idempotent)
    return conn


# Spalten, die nach dem ersten Release dazukamen — CREATE TABLE IF NOT EXISTS
# greift bei Bestands-DBs nicht, daher hier per ALTER TABLE nachziehen.
MIGRATIONS = [
    ("chapters", "updated_at", "TEXT"),
    ("comments", "resolved", "INTEGER NOT NULL DEFAULT 0"),
    ("comments", "orphaned", "INTEGER NOT NULL DEFAULT 0"),
    ("comments", "edited_at", "TEXT"),
    ("readers", "book_id", "INTEGER REFERENCES books(id)"),
]


def init_db() -> None:
    conn = get_db()
    conn.executescript(SCHEMA)
    for table, col, decl in MIGRATIONS:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    # Multi-Book-Backfill: Bestandsleser ans einzige Buch hängen — nur eindeutig, wenn genau 1 Buch existiert
    if conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1:
        conn.execute("UPDATE readers SET book_id=(SELECT id FROM books) WHERE book_id IS NULL")
    conn.commit()
    conn.close()
