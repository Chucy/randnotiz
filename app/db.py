"""SQLite schema and connection for Randnotiz."""
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
-- Indexes for the filtered admin queries (chapter/reader). Idempotent, run on every
-- executescript() — like idx_blocks_chapter. Not yet performance-critical at beta
-- volumes (~500+ rows), but future-proof for multiple books/revision rounds.
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
    conn.execute("PRAGMA busy_timeout = 5000")  # wait briefly on a concurrent writer instead of failing immediately with "database is locked"
    conn.execute("PRAGMA journal_mode = WAL")   # more robust concurrent reads/writes (persistent, call is idempotent)
    return conn


# Columns added after the first release — CREATE TABLE IF NOT EXISTS
# doesn't apply to existing DBs, so they're added here via ALTER TABLE.
MIGRATIONS = [
    ("chapters", "updated_at", "TEXT"),
    ("comments", "resolved", "INTEGER NOT NULL DEFAULT 0"),
    ("comments", "orphaned", "INTEGER NOT NULL DEFAULT 0"),
    ("comments", "edited_at", "TEXT"),
    ("readers", "book_id", "INTEGER REFERENCES books(id)"),
    # Manual "continue reading" bookmark — exactly one per reader/book (NULL = none).
    ("readers", "bookmark_chapter_id", "INTEGER REFERENCES chapters(id)"),
    ("readers", "bookmark_block_idx", "INTEGER"),
]


def init_db() -> None:
    conn = get_db()
    conn.executescript(SCHEMA)
    for table, col, decl in MIGRATIONS:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    # Multi-book backfill: attach existing readers to the single book — only unambiguous when exactly 1 book exists
    if conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1:
        conn.execute("UPDATE readers SET book_id=(SELECT id FROM books) WHERE book_id IS NULL")
    conn.commit()
    conn.close()
