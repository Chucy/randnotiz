"""Ingest: Markdown chapters → SQLite.

Invocation (chapter order from the book Makefile, source of truth):
    python -m app.ingest --makefile /path/to/book/Makefile \
        --slug my-book --title "My Book"

Alternatively without a Makefile (alphabetical by filename):
    python -m app.ingest --book-dir <dir> --slug ... --title ...

Re-ingest is idempotent: chapters are upserted by slug (IDs stay stable,
comments survive), blocks are rewritten. When blocks change, comments/reactions/
progress are remapped to the new block indices via text matching (comments that
can't be matched are marked orphaned), and the chapter is stamped as
revised. `--dry-run` shows the report without writing anything.
"""
import argparse
import difflib
import glob
import os
import re
import sys

import markdown

from .db import get_db, init_db

MD = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])

# Default questions per chapter (non-fiction) — only set on the first ingest.
DEFAULT_QUESTIONS = [
    ("Wie verständlich war das Kapitel? (1 = unverständlich, 5 = glasklar)", "scale"),
    ("Wie nützlich war das Kapitel für dich? (1 = nutzlos, 5 = sehr nützlich)", "scale"),
    ("Was war unklar oder zu kompliziert?", "text"),
    ("Was hat dir gefehlt oder was hättest du dir mehr gewünscht?", "text"),
]


def clean_markdown(md_text: str, book_slug: str) -> str:
    """Remove/adjust Pandoc-specific syntax that python-markdown doesn't know."""
    # Strip heading attributes: "# Vorwort {.unnumbered}" / "{-}" / "{#id}"
    md_text = re.sub(r"^(#{1,6} .*?)\s*\{[^}]*\}\s*$", r"\1", md_text, flags=re.MULTILINE)
    # Rewrite image paths into the book namespace: ](assets/... → ](/assets/<slug>/...
    # (the app serves per book from <BOOKS_DIR>/<slug>/assets/ — otherwise identical filenames from two books would collide)
    md_text = re.sub(r"\]\(assets/", f"](/assets/{book_slug}/", md_text)
    return md_text


def split_blocks(md_text: str) -> list[str]:
    """Split markdown into blocks (blank-line separated), keeping code fences intact."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in md_text.splitlines():
        if re.match(r"^(```|~~~)", line.strip()):
            in_fence = not in_fence
            current.append(line)
            continue
        if not line.strip() and not in_fence:
            if current:
                blocks.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def chapter_title(md_text: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.+)$", md_text, re.MULTILINE)
    return m.group(1).strip() if m else fallback


def files_from_makefile(makefile: str) -> list[str]:
    """Parse the KAPITEL variable from the book Makefile → file list in book order."""
    makefile = os.path.expanduser(makefile)
    base = os.path.dirname(makefile)
    with open(makefile, encoding="utf-8") as f:
        content = f.read()
    m = re.search(r"^KAPITEL\s*=\s*((?:.*\\\n)*.*)$", content, re.MULTILINE)
    if not m:
        sys.exit("Keine KAPITEL-Variable im Makefile gefunden")
    files = [os.path.join(base, tok) for tok in re.findall(r"(\S+\.md)", m.group(1))]
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        sys.exit(f"Im Makefile referenziert, aber nicht gefunden: {missing}")
    return files


def remap_block(old_text: str, old_idx: int, new_blocks: list[tuple[int, str]]) -> int | None:
    """Find the best new block for an old one: exact text first, otherwise fuzzy (difflib).

    None = no sufficiently similar block remains (paragraph deleted/heavily rewritten).
    """
    exact = [i for i, t in new_blocks if t == old_text]
    if exact:
        return min(exact, key=lambda i: abs(i - old_idx))  # on duplicates: pick the closest one
    best_idx, best_ratio = None, 0.0
    for i, t in new_blocks:
        r = difflib.SequenceMatcher(None, old_text, t).ratio()
        if r > best_ratio:
            best_idx, best_ratio = i, r
    # 0.75: minor revisions land around ~0.85+, coincidentally similar short paragraphs ~0.6 —
    # when in doubt, prefer orphaning (visible) over a wrong match (silently wrong).
    return best_idx if best_ratio >= 0.75 else None


def remap_feedback(cur, ch_id: int, old_texts: dict[int, str], new_blocks: list[tuple[int, str]]) -> dict:
    """Adjust comments/reactions/progress after block renumbering.

    Comments without a target are marked orphaned (text+quote are preserved),
    reactions without a target are deleted (mere one-tap markers with no content of their own).
    """
    mapping: dict[int, int | None] = {}

    def target(old_idx: int) -> int | None:
        if old_idx not in mapping:
            old_text = old_texts.get(old_idx)
            mapping[old_idx] = remap_block(old_text, old_idx, new_blocks) if old_text is not None else None
        return mapping[old_idx]

    stats = {"c_moved": 0, "c_orphaned": 0, "r_moved": 0, "r_deleted": 0, "p_adjusted": 0}
    for c in cur.execute("SELECT id, block_idx FROM comments WHERE chapter_id=? AND orphaned=0", (ch_id,)).fetchall():
        t = target(c["block_idx"])
        if t is None:
            cur.execute("UPDATE comments SET orphaned=1 WHERE id=?", (c["id"],))
            stats["c_orphaned"] += 1
        elif t != c["block_idx"]:
            cur.execute("UPDATE comments SET block_idx=? WHERE id=?", (t, c["id"]))
            stats["c_moved"] += 1
    for r in cur.execute("SELECT id, block_idx FROM reactions WHERE chapter_id=?", (ch_id,)).fetchall():
        t = target(r["block_idx"])
        if t is None:
            cur.execute("DELETE FROM reactions WHERE id=?", (r["id"],))
            stats["r_deleted"] += 1
        elif t != r["block_idx"]:
            # OR IGNORE: if the reader already has the same reaction on the target block (UNIQUE), delete the duplicate
            cur.execute("UPDATE OR IGNORE reactions SET block_idx=? WHERE id=?", (t, r["id"]))
            if cur.rowcount:
                stats["r_moved"] += 1
            else:
                cur.execute("DELETE FROM reactions WHERE id=?", (r["id"],))
                stats["r_deleted"] += 1
    max_new = len(new_blocks) - 1
    for p in cur.execute("SELECT id, max_block_idx FROM reading_progress WHERE chapter_id=?", (ch_id,)).fetchall():
        t = target(p["max_block_idx"])
        if t is None:
            t = min(p["max_block_idx"], max_new)  # rough: keep the position, but clamp into the new chapter
        if t != p["max_block_idx"]:
            cur.execute("UPDATE reading_progress SET max_block_idx=? WHERE id=?", (t, p["id"]))
            stats["p_adjusted"] += 1
    return stats


def ingest(files: list[str], slug: str, title: str, dry_run: bool = False, allow_new: bool = False) -> None:
    init_db()
    conn = get_db()
    cur = conn.cursor()

    # Guard: an unknown slug only creates a book with --new (catches typos
    # that would otherwise silently create an empty second book)
    if not cur.execute("SELECT 1 FROM books WHERE slug=?", (slug,)).fetchone() and not allow_new:
        existing = [r["slug"] for r in cur.execute("SELECT slug FROM books ORDER BY slug")]
        sys.exit(f"Buch-Slug '{slug}' existiert nicht. Vorhandene Bücher: {existing or 'keine'}. "
                 f"Neues Buch anlegen: --new anhängen.")

    cur.execute("INSERT INTO books(slug, title) VALUES(?, ?) ON CONFLICT(slug) DO UPDATE SET title=excluded.title", (slug, title))
    book_id = cur.execute("SELECT id FROM books WHERE slug=?", (slug,)).fetchone()["id"]

    # Only set default questions if none exist yet
    if not cur.execute("SELECT 1 FROM questions WHERE book_id=?", (book_id,)).fetchone():
        for pos, (qtext, qtype) in enumerate(DEFAULT_QUESTIONS):
            cur.execute("INSERT INTO questions(book_id, pos, text, qtype) VALUES(?,?,?,?)", (book_id, pos, qtext, qtype))

    for num, path in enumerate(files):
        ch_slug = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as f:
            md_text = clean_markdown(f.read(), slug)
        ch_title = chapter_title(md_text, ch_slug)

        cur.execute(
            "INSERT INTO chapters(book_id, num, slug, title) VALUES(?,?,?,?) "
            "ON CONFLICT(book_id, slug) DO UPDATE SET num=excluded.num, title=excluded.title",
            (book_id, num, ch_slug, ch_title),
        )
        ch_id = cur.execute("SELECT id FROM chapters WHERE book_id=? AND slug=?", (book_id, ch_slug)).fetchone()["id"]

        # Save old blocks before they're renumbered — anchor for comment remapping
        old_texts = {row["idx"]: row["text"] for row in cur.execute(
            "SELECT idx, text FROM blocks WHERE chapter_id=? ORDER BY idx", (ch_id,)).fetchall()}

        cur.execute("DELETE FROM blocks WHERE chapter_id=?", (ch_id,))
        new_blocks: list[tuple[int, str]] = []
        for idx, block in enumerate(split_blocks(md_text)):
            MD.reset()
            html = MD.convert(block)
            cur.execute("INSERT INTO blocks(chapter_id, idx, html, text) VALUES(?,?,?,?)", (ch_id, idx, html, block))
            new_blocks.append((idx, block))

        print(f"  K{num:02d} {ch_title} ({ch_slug}) — {idx + 1} Blöcke")

        # On changed content: adjust feedback + stamp chapter as revised
        if old_texts and [t for _, t in new_blocks] != [old_texts[i] for i in sorted(old_texts)]:
            cur.execute("UPDATE chapters SET updated_at=datetime('now') WHERE id=?", (ch_id,))
            s = remap_feedback(cur, ch_id, old_texts, new_blocks)
            if any(s.values()):
                print(f"       ↳ geändert — Kommentare: {s['c_moved']} verschoben, {s['c_orphaned']} verwaist · "
                      f"Reactions: {s['r_moved']} verschoben, {s['r_deleted']} entfernt · "
                      f"Fortschritt: {s['p_adjusted']} angepasst")
            else:
                print("       ↳ geändert (kein Feedback betroffen)")

    # Remove chapters that are no longer in the source (incl. dependent data)
    keep = tuple(os.path.splitext(os.path.basename(p))[0] for p in files)
    stale = cur.execute(
        f"SELECT id, slug FROM chapters WHERE book_id=? AND slug NOT IN ({','.join('?' * len(keep))})",
        (book_id, *keep),
    ).fetchall()
    for row in stale:
        for table in ("comments", "reactions", "answers", "blocks", "reading_progress"):
            cur.execute(f"DELETE FROM {table} WHERE chapter_id=?", (row["id"],))
        cur.execute("DELETE FROM chapters WHERE id=?", (row["id"],))
        print(f"  entfernt (nicht mehr in Quelle): {row['slug']}")

    if dry_run:
        conn.rollback()
        conn.close()
        print(f"DRY-RUN: nichts geschrieben — obiger Report zeigt, was ein echter Ingest von {len(files)} Kapiteln täte.")
        return
    conn.commit()
    conn.close()
    print(f"Ingest fertig: {len(files)} Kapitel in Buch '{title}'.")


def delete_book(slug: str, yes: bool = False) -> None:
    """Remove a book along with all feedback. Without yes, only a report (dry-run default)."""
    init_db()
    conn = get_db()
    cur = conn.cursor()
    b = cur.execute("SELECT * FROM books WHERE slug=?", (slug,)).fetchone()
    if not b:
        existing = [r["slug"] for r in cur.execute("SELECT slug FROM books ORDER BY slug")]
        sys.exit(f"Buch '{slug}' nicht gefunden. Vorhandene Bücher: {existing or 'keine'}")
    bid = b["id"]
    in_ch = "IN (SELECT id FROM chapters WHERE book_id=?)"
    counts = {
        "Kapitel": cur.execute("SELECT COUNT(*) FROM chapters WHERE book_id=?", (bid,)).fetchone()[0],
        "Leser (Magic-Links werden ungültig!)": cur.execute("SELECT COUNT(*) FROM readers WHERE book_id=?", (bid,)).fetchone()[0],
        "Kommentare": cur.execute(f"SELECT COUNT(*) FROM comments WHERE chapter_id {in_ch}", (bid,)).fetchone()[0],
        "Reaktionen": cur.execute(f"SELECT COUNT(*) FROM reactions WHERE chapter_id {in_ch}", (bid,)).fetchone()[0],
        "Fragebogen-Antworten": cur.execute(f"SELECT COUNT(*) FROM answers WHERE chapter_id {in_ch}", (bid,)).fetchone()[0],
    }
    print(f"Buch '{b['title']}' ({slug}) — würde löschen:")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    if not yes:
        conn.close()
        print("Nur Report, nichts gelöscht. Wirklich löschen: --yes anhängen.")
        return
    for table in ("comments", "reactions", "answers", "reading_progress", "blocks"):
        cur.execute(f"DELETE FROM {table} WHERE chapter_id {in_ch}", (bid,))
    cur.execute("DELETE FROM reader_activity WHERE reader_id IN (SELECT id FROM readers WHERE book_id=?)", (bid,))
    cur.execute("DELETE FROM readers WHERE book_id=?", (bid,))
    cur.execute("DELETE FROM questions WHERE book_id=?", (bid,))
    cur.execute("DELETE FROM chapters WHERE book_id=?", (bid,))
    cur.execute("DELETE FROM books WHERE id=?", (bid,))
    conn.commit()
    conn.close()
    print(f"Buch '{slug}' komplett gelöscht.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=False)
    src.add_argument("--makefile", help="Buch-Makefile mit KAPITEL-Variable (Reihenfolge = Buchreihenfolge)")
    src.add_argument("--book-dir", help="Verzeichnis mit *.md (alphabetische Reihenfolge)")
    ap.add_argument("--slug", help="Buch-Slug (taucht öffentlich in Asset-URLs auf — keine internen Codenamen)")
    ap.add_argument("--title", help="Buchtitel")
    ap.add_argument("--new", action="store_true", help="Neues Buch anlegen (Pflicht bei unbekanntem Slug — Tippfehler-Guard)")
    ap.add_argument("--exclude", default="", help="Kommagetrennte Kapitel-Slugs, die nicht ins Testleser-Exemplar sollen")
    ap.add_argument("--dry-run", action="store_true", help="Nur Diff-Report zeigen (Kommentar-Auswirkungen), nichts schreiben")
    ap.add_argument("--delete-book", metavar="SLUG", help="Buch samt Feedback + Lesern löschen (ohne --yes nur Report)")
    ap.add_argument("--yes", action="store_true", help="Löschen mit --delete-book wirklich ausführen")
    args = ap.parse_args()
    if args.delete_book:
        delete_book(args.delete_book, yes=args.yes)
        sys.exit(0)
    if not (args.makefile or args.book_dir):
        ap.error("--makefile oder --book-dir erforderlich (außer bei --delete-book)")
    if not args.slug or not args.title:
        ap.error("--slug und --title erforderlich")
    if args.makefile:
        file_list = files_from_makefile(args.makefile)
    else:
        file_list = sorted(glob.glob(os.path.join(os.path.expanduser(args.book_dir), "*.md")))
        if not file_list:
            sys.exit(f"Keine Kapitel-Dateien in {args.book_dir} gefunden")
    excluded = {s.strip() for s in args.exclude.split(",") if s.strip()}
    if excluded:
        file_list = [f for f in file_list if os.path.splitext(os.path.basename(f))[0] not in excluded]
        print(f"Ausgeschlossen: {', '.join(sorted(excluded))}")
    ingest(file_list, args.slug, args.title, dry_run=args.dry_run, allow_new=args.new)
