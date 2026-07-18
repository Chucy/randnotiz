"""Randnotiz — self-hosted beta-reader platform (BetaBooks-style)."""
import logging
import mimetypes
import os
import secrets
from collections import defaultdict
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .db import get_db, init_db

ADMIN_KEY = os.environ.get("RANDNOTIZ_ADMIN_KEY", "")
REACTION_KINDS = {"herz", "frage", "gaehn"}
MAX_TEXT = 5000  # upper bound for free-text input (storage-DoS protection)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("randnotiz")

app = FastAPI(title="Randnotiz")
BASE = os.path.dirname(__file__)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    return resp


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    """Last resort: unexpected errors land structured in the log instead of as a raw traceback."""
    logger.exception("Unhandled error at %s %s", request.method, request.url.path)
    return JSONResponse({"error": "interner Serverfehler"}, status_code=500)


app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")
# Book storage: one directory per book — <BOOKS_DIR>/<slug>/ with chapters, Makefile, assets/
BOOKS_DIR = os.path.expanduser(os.environ.get("RANDNOTIZ_BOOKS", ""))
templates = Jinja2Templates(directory=os.path.join(BASE, "templates"))

init_db()


@app.get("/healthz")
def healthz():
    """Liveness for Docker HEALTHCHECK: checks that the process is running AND the DB responds."""
    try:
        conn = get_db()
        conn.execute("SELECT 1")
        conn.close()
    except Exception:
        logger.exception("Healthcheck failed")
        raise HTTPException(503, "database unavailable")
    return {"status": "ok"}


# ---------- Helpers ----------

def reader_or_404(conn, token: str):
    r = conn.execute("SELECT * FROM readers WHERE token=?", (token,)).fetchone()
    if not r:
        raise HTTPException(404, "Unbekannter Leser-Link")
    return r


def touch_reader(conn, reader_id: int) -> None:
    """Records that the magic link was just used (no commit — that's the caller's job)."""
    conn.execute(
        "INSERT INTO reader_activity(reader_id) VALUES(?) "
        "ON CONFLICT(reader_id) DO UPDATE SET last_seen_at=datetime('now')",
        (reader_id,),
    )


def fmt_activity(mins: int | None) -> str:
    """Relative activity display for the dashboard (minutes since last_seen_at)."""
    if mins is None:
        return "— nie geöffnet"
    if mins < 5:
        return "🟢 gerade aktiv"
    if mins < 60:
        return f"vor {mins} Min"
    if mins < 48 * 60:
        return f"vor {mins // 60} Std"
    return f"vor {mins // 1440} Tagen"


def admin_ok(request: Request) -> bool:
    """Admin auth via HttpOnly cookie (no longer via URL query — no leak into logs/history)."""
    key = request.cookies.get("br_admin", "")
    return bool(ADMIN_KEY) and secrets.compare_digest(key, ADMIN_KEY)


def require_admin(request: Request):
    if not admin_ok(request):
        raise HTTPException(403, "Nicht angemeldet")


LOGIN_HTML = (
    "<!DOCTYPE html><html lang='de'><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
    "<title>Admin-Login</title><link rel='stylesheet' href='/static/style.css'></head>"
    "<body><main class='container' style='max-width:24rem; padding-top:4rem; font-family:system-ui,sans-serif'>"
    "<h1>🔒 Admin-Login</h1><!--ERR-->"
    "<form method='post' action='/admin/login'>"
    "<input type='password' name='key' placeholder='Admin-Key' autofocus required "
    "style='width:100%; padding:.6rem; margin:.5rem 0'>"
    "<button class='primary' type='submit' style='width:100%'>Anmelden</button>"
    "</form></main></body></html>"
)


def book_of_reader(conn, reader):
    """The book this magic link belongs to (1 link = 1 book)."""
    b = conn.execute("SELECT * FROM books WHERE id=?", (reader["book_id"],)).fetchone()
    if not b:
        raise HTTPException(404, "Diesem Link ist kein Buch zugeordnet")
    return b


def check_chapter_in_book(conn, chapter_id: int, reader) -> None:
    """Write APIs: chapter must belong to the reader's book (no cross-book feedback).

    Call before touch_reader — reads only, so a 404 doesn't leave a write lock open.
    """
    if not conn.execute("SELECT 1 FROM chapters WHERE id=? AND book_id=?", (chapter_id, reader["book_id"])).fetchone():
        raise HTTPException(404, "Kapitel nicht gefunden")


# ---------- Landing ----------

@app.get("/", response_class=HTMLResponse)
def landing():
    return (
        "<!DOCTYPE html><html lang='de'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Testleser-Bereich</title><link rel='stylesheet' href='/static/style.css'></head>"
        "<body><main class='container' style='text-align:center; padding-top:4rem; font-family:system-ui,sans-serif'>"
        "<h1>📖 Testleser-Bereich</h1>"
        "<p>Hier liest du als Testleser ein Buch vor der Veröffentlichung.</p>"
        "<p>Du brauchst dafür deinen <strong>persönlichen Link</strong> — den bekommst du direkt vom Autor.<br>"
        "Link verloren? Einfach beim Autor melden. 🙂</p>"
        "</main></body></html>"
    )


@app.get("/assets/{book_slug}/{rest:path}")
def book_asset(book_slug: str, rest: str):
    """Serve book images from <BOOKS_DIR>/<slug>/assets/.

    Deliberately ONLY the assets/ subdirectory — the manuscript markdown files in the
    same book folder must never be publicly reachable. Triple protection:
    slug whitelist against the DB, plus a realpath fence around both slug AND rest path.
    """
    if not BOOKS_DIR:
        raise HTTPException(404, "Keine Buch-Ablage konfiguriert")
    conn = get_db()
    known = conn.execute("SELECT 1 FROM books WHERE slug=?", (book_slug,)).fetchone()
    conn.close()
    if not known:
        raise HTTPException(404, "Asset nicht gefunden")
    root = os.path.realpath(BOOKS_DIR)
    base = os.path.realpath(os.path.join(root, book_slug, "assets"))
    full = os.path.realpath(os.path.join(base, rest))
    if not base.startswith(root + os.sep) or not full.startswith(base + os.sep) or not os.path.isfile(full):
        raise HTTPException(404, "Asset nicht gefunden")  # also cleanly covers "book without an assets/ folder"
    media_type, _ = mimetypes.guess_type(full)
    resp = FileResponse(full, media_type=media_type)
    resp.headers["Cache-Control"] = "public, max-age=86400"  # mobile-first: don't re-fetch images on every chapter load
    return resp


# ---------- Reader Views ----------

@app.get("/r/{token}", response_class=HTMLResponse)
def index(request: Request, token: str):
    conn = get_db()
    reader = reader_or_404(conn, token)
    touch_reader(conn, reader["id"])
    conn.commit()
    book = book_of_reader(conn, reader)
    chapters = [dict(c) for c in conn.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM comments cm WHERE cm.chapter_id=c.id AND cm.reader_id=?) AS my_comments, "
        "(SELECT COALESCE(SUM(LENGTH(b.text)), 0) FROM blocks b WHERE b.chapter_id=c.id) AS chars "
        "FROM chapters c WHERE c.book_id=? ORDER BY c.num",
        (reader["id"], book["id"]),
    ).fetchall()]
    for c in chapters:
        c["minutes"] = max(1, round(c["chars"] / 6 / 220))  # ~6 chars/word, 220 words/min
    # Server-side reading state → localStorage seed (device switch: read status + continue-reading card)
    done_ids = [row["chapter_id"] for row in conn.execute(
        "SELECT chapter_id FROM reading_progress WHERE reader_id=? AND done_at IS NOT NULL", (reader["id"],))]
    last = conn.execute(
        "SELECT c.slug, c.title FROM reading_progress p JOIN chapters c ON c.id=p.chapter_id "
        "WHERE p.reader_id=? ORDER BY p.updated_at DESC LIMIT 1", (reader["id"],)).fetchone()
    server_progress = {"done": done_ids, "last": dict(last) if last else None}
    # Manual bookmark → cross-chapter "continue reading" card (takes precedence over last-read)
    bookmark = None
    if reader["bookmark_chapter_id"] is not None:
        bc = conn.execute(
            "SELECT slug, title FROM chapters WHERE id=?", (reader["bookmark_chapter_id"],)).fetchone()
        if bc:
            bookmark = {"slug": bc["slug"], "title": bc["title"], "block_idx": reader["bookmark_block_idx"]}
    conn.close()
    return templates.TemplateResponse(request, "index.html", {
        "reader": reader, "book": book, "chapters": chapters, "token": token,
        "server_progress": server_progress, "bookmark": bookmark,
    })


@app.get("/r/{token}/k/{ch_slug}", response_class=HTMLResponse)
def chapter(request: Request, token: str, ch_slug: str):
    conn = get_db()
    reader = reader_or_404(conn, token)
    touch_reader(conn, reader["id"])
    conn.commit()
    book = book_of_reader(conn, reader)
    ch = conn.execute("SELECT * FROM chapters WHERE book_id=? AND slug=?", (book["id"], ch_slug)).fetchone()
    if not ch:
        raise HTTPException(404, "Kapitel nicht gefunden")
    blocks = conn.execute("SELECT idx, html FROM blocks WHERE chapter_id=? ORDER BY idx", (ch["id"],)).fetchall()
    prev_ch = conn.execute("SELECT slug, title FROM chapters WHERE book_id=? AND num<? ORDER BY num DESC LIMIT 1", (book["id"], ch["num"])).fetchone()
    next_ch = conn.execute("SELECT slug, title FROM chapters WHERE book_id=? AND num>? ORDER BY num LIMIT 1", (book["id"], ch["num"])).fetchone()
    questions = conn.execute("SELECT * FROM questions WHERE book_id=? ORDER BY pos", (book["id"],)).fetchall()
    my_comments = conn.execute("SELECT id, block_idx, text FROM comments WHERE chapter_id=? AND reader_id=? AND orphaned=0", (ch["id"], reader["id"])).fetchall()
    my_reactions = conn.execute("SELECT block_idx, kind FROM reactions WHERE chapter_id=? AND reader_id=?", (ch["id"], reader["id"])).fetchall()
    my_answers = {row["question_id"]: row["value"] for row in conn.execute(
        "SELECT question_id, value FROM answers WHERE chapter_id=? AND reader_id=?", (ch["id"], reader["id"]))}
    total = conn.execute("SELECT COUNT(*) AS n FROM chapters WHERE book_id=?", (book["id"],)).fetchone()["n"]
    pos = conn.execute("SELECT COUNT(*) AS n FROM chapters WHERE book_id=? AND num<=?", (book["id"], ch["num"])).fetchone()["n"]
    # Server-side reading state as fallback for scroll restore (device switch)
    prog = conn.execute("SELECT max_block_idx FROM reading_progress WHERE reader_id=? AND chapter_id=?", (reader["id"], ch["id"])).fetchone()
    server_pos = prog["max_block_idx"] if prog else 0
    # "Revised" note only for readers who already knew the chapter before the change
    first_seen = conn.execute("SELECT first_seen_at FROM reader_activity WHERE reader_id=?", (reader["id"],)).fetchone()
    show_update_note = bool(ch["updated_at"] and first_seen and first_seen["first_seen_at"] < ch["updated_at"])
    # Manual bookmark: block idx only if the bookmark sits in THIS chapter (else empty → no marker here)
    bookmark_idx = reader["bookmark_block_idx"] if reader["bookmark_chapter_id"] == ch["id"] else ""
    conn.close()
    return templates.TemplateResponse(request, "chapter.html", {
        "reader": reader, "book": book, "ch": ch, "blocks": blocks,
        "prev_ch": prev_ch, "next_ch": next_ch, "questions": questions,
        "my_comments": [dict(r) for r in my_comments],
        "my_reactions": [dict(r) for r in my_reactions],
        "my_answers": my_answers, "token": token, "pos": pos, "total": total,
        "server_pos": server_pos, "show_update_note": show_update_note,
        "bookmark_idx": bookmark_idx,
    })


# ---------- Reader API ----------

class CommentIn(BaseModel):
    chapter_id: int
    block_idx: int
    text: str = Field(max_length=MAX_TEXT)


class ReactionIn(BaseModel):
    chapter_id: int
    block_idx: int
    kind: str


class AnswersIn(BaseModel):
    chapter_id: int
    answers: dict[int, str]


class ProgressIn(BaseModel):
    chapter_id: int
    max_block_idx: int = Field(ge=0)
    done: bool = False


class BookmarkIn(BaseModel):
    chapter_id: int
    block_idx: int = Field(ge=0)


@app.post("/api/r/{token}/comment")
def add_comment(token: str, body: CommentIn):
    if not body.text.strip():
        raise HTTPException(400, "Leerer Kommentar")
    conn = get_db()
    reader = reader_or_404(conn, token)
    check_chapter_in_book(conn, body.chapter_id, reader)
    touch_reader(conn, reader["id"])
    quote_row = conn.execute("SELECT text FROM blocks WHERE chapter_id=? AND idx=?", (body.chapter_id, body.block_idx)).fetchone()
    quote = (quote_row["text"][:300] if quote_row else "")
    cur = conn.execute(
        "INSERT INTO comments(reader_id, chapter_id, block_idx, quote, text) VALUES(?,?,?,?,?)",
        (reader["id"], body.chapter_id, body.block_idx, quote, body.text.strip()),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"ok": True, "id": new_id}


class CommentUpdateIn(BaseModel):
    text: str = Field(max_length=MAX_TEXT)


@app.post("/api/r/{token}/comment/{comment_id}/update")
def update_comment(token: str, comment_id: int, body: CommentUpdateIn):
    if not body.text.strip():
        raise HTTPException(400, "Leerer Kommentar")
    conn = get_db()
    reader = reader_or_404(conn, token)
    touch_reader(conn, reader["id"])
    cur = conn.execute(
        "UPDATE comments SET text=?, edited_at=datetime('now') WHERE id=? AND reader_id=?",
        (body.text.strip(), comment_id, reader["id"]),  # reader_id check: only own comments
    )
    ok = cur.rowcount > 0
    conn.commit()  # even on 404: commit touch_reader and release the write lock
    conn.close()
    if not ok:
        raise HTTPException(404, "Kommentar nicht gefunden")
    return {"ok": True}


@app.post("/api/r/{token}/comment/{comment_id}/delete")
def delete_comment(token: str, comment_id: int):
    conn = get_db()
    reader = reader_or_404(conn, token)
    touch_reader(conn, reader["id"])
    cur = conn.execute("DELETE FROM comments WHERE id=? AND reader_id=?", (comment_id, reader["id"]))
    ok = cur.rowcount > 0
    conn.commit()  # even on 404: commit touch_reader and release the write lock
    conn.close()
    if not ok:
        raise HTTPException(404, "Kommentar nicht gefunden")
    return {"ok": True}


@app.post("/api/r/{token}/reaction")
def toggle_reaction(token: str, body: ReactionIn):
    if body.kind not in REACTION_KINDS:
        raise HTTPException(400, "Unbekannte Reaktion")
    conn = get_db()
    reader = reader_or_404(conn, token)
    check_chapter_in_book(conn, body.chapter_id, reader)
    touch_reader(conn, reader["id"])
    existing = conn.execute(
        "SELECT id FROM reactions WHERE reader_id=? AND chapter_id=? AND block_idx=? AND kind=?",
        (reader["id"], body.chapter_id, body.block_idx, body.kind),
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM reactions WHERE id=?", (existing["id"],))
        active = False
    else:
        conn.execute(
            "INSERT INTO reactions(reader_id, chapter_id, block_idx, kind) VALUES(?,?,?,?)",
            (reader["id"], body.chapter_id, body.block_idx, body.kind),
        )
        active = True
    conn.commit()
    conn.close()
    return {"ok": True, "active": active}


@app.post("/api/r/{token}/answers")
def save_answers(token: str, body: AnswersIn):
    conn = get_db()
    reader = reader_or_404(conn, token)
    check_chapter_in_book(conn, body.chapter_id, reader)
    touch_reader(conn, reader["id"])
    for qid, value in body.answers.items():
        value = str(value).strip()[:MAX_TEXT]
        if not value:
            continue
        conn.execute(
            "INSERT INTO answers(reader_id, chapter_id, question_id, value) VALUES(?,?,?,?) "
            "ON CONFLICT(reader_id, chapter_id, question_id) DO UPDATE SET value=excluded.value, created_at=datetime('now')",
            (reader["id"], body.chapter_id, qid, value),
        )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/r/{token}/progress")
def save_progress(token: str, body: ProgressIn):
    conn = get_db()
    reader = reader_or_404(conn, token)
    check_chapter_in_book(conn, body.chapter_id, reader)
    touch_reader(conn, reader["id"])
    conn.execute(
        "INSERT INTO reading_progress(reader_id, chapter_id, max_block_idx, done_at) "
        "VALUES(?,?,?, CASE WHEN ? THEN datetime('now') ELSE NULL END) "
        "ON CONFLICT(reader_id, chapter_id) DO UPDATE SET "
        "max_block_idx=MAX(max_block_idx, excluded.max_block_idx), "
        "done_at=COALESCE(done_at, excluded.done_at), "
        "updated_at=datetime('now')",
        (reader["id"], body.chapter_id, body.max_block_idx, 1 if body.done else 0),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/r/{token}/bookmark")
def set_bookmark(token: str, body: BookmarkIn):
    # Set/move the single "continue reading" bookmark. Because it's one column pair
    # on readers, writing a new position implicitly clears the old one.
    conn = get_db()
    reader = reader_or_404(conn, token)
    check_chapter_in_book(conn, body.chapter_id, reader)
    touch_reader(conn, reader["id"])
    conn.execute(
        "UPDATE readers SET bookmark_chapter_id=?, bookmark_block_idx=? WHERE id=?",
        (body.chapter_id, body.block_idx, reader["id"]),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/r/{token}/bookmark/clear")
def clear_bookmark(token: str):
    conn = get_db()
    reader = reader_or_404(conn, token)
    touch_reader(conn, reader["id"])
    conn.execute(
        "UPDATE readers SET bookmark_chapter_id=NULL, bookmark_block_idx=NULL WHERE id=?",
        (reader["id"],),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


# ---------- Admin ----------

@app.post("/admin/login")
async def admin_login(request: Request):
    form = await request.form()
    key = str(form.get("key", ""))
    if not ADMIN_KEY or not secrets.compare_digest(key, ADMIN_KEY):
        err = "<p style='color:#c0392b'>Falscher Key.</p>"
        return HTMLResponse(LOGIN_HTML.replace("<!--ERR-->", err), status_code=403)
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie("br_admin", key, httponly=True, secure=True,
                    samesite="strict", max_age=60 * 60 * 24 * 30, path="/")
    return resp


@app.post("/admin/logout")
def admin_logout():
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie("br_admin", path="/")
    return resp


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, book: str = "", chapter: int = 0, status: str = "open", reader: int = 0):
    if not admin_ok(request):
        return HTMLResponse(LOGIN_HTML.replace("<!--ERR-->", ""))
    if status not in ("open", "resolved", "all"):
        status = "open"
    conn = get_db()
    books = conn.execute("SELECT * FROM books ORDER BY title").fetchall()
    # Active book: ?book=<slug>, otherwise the first one — all sections are filtered by it
    active = next((b for b in books if b["slug"] == book), books[0] if books else None)
    bid = active["id"] if active else -1
    readers = [dict(r) for r in conn.execute(
        "SELECT r.*, (SELECT COUNT(*) FROM comments c WHERE c.reader_id=r.id) AS n_comments, "
        "(SELECT COUNT(DISTINCT chapter_id) FROM answers a WHERE a.reader_id=r.id) AS n_chapters_answered, "
        "(SELECT COUNT(*) FROM reading_progress p WHERE p.reader_id=r.id AND p.done_at IS NOT NULL) AS n_chapters_done, "
        "(SELECT MAX(ch.num) FROM reading_progress p JOIN chapters ch ON ch.id=p.chapter_id WHERE p.reader_id=r.id) AS last_chapter_num, "
        "(SELECT CAST((julianday('now') - julianday(act.last_seen_at)) * 1440 AS INTEGER) "
        " FROM reader_activity act WHERE act.reader_id=r.id) AS mins_ago "
        "FROM readers r WHERE r.book_id=? ORDER BY r.name", (bid,)).fetchall()]
    for r in readers:
        r["activity"] = fmt_activity(r["mins_ago"])
    total_chapters = conn.execute(
        "SELECT COUNT(*) AS n FROM chapters WHERE book_id=?", (bid,)).fetchone()["n"]
    # Filter comments server-side (chapter/status/reader) instead of rendering everything and hiding client-side —
    # with 500+ comments, this keeps only the actually needed subset in the DOM.
    cwhere = ["c.book_id=?"]
    cargs: list = [bid]
    if chapter:
        cwhere.append("c.num=?"); cargs.append(chapter)
    if status == "open":
        cwhere.append("cm.resolved=0")
    elif status == "resolved":
        cwhere.append("cm.resolved=1")
    if reader:
        cwhere.append("cm.reader_id=?"); cargs.append(reader)
    comments = conn.execute(
        "SELECT cm.*, r.name AS reader_name, c.title AS chapter_title, c.num AS chapter_num "
        "FROM comments cm JOIN readers r ON r.id=cm.reader_id JOIN chapters c ON c.id=cm.chapter_id "
        f"WHERE {' AND '.join(cwhere)} ORDER BY c.num, cm.block_idx, cm.created_at", cargs).fetchall()
    # Book-wide counters (independent of the filter) — keeps the header honest even when filtered.
    crow = conn.execute(
        "SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN cm.resolved=0 THEN 1 ELSE 0 END), 0) AS n_open "
        "FROM comments cm JOIN chapters c ON c.id=cm.chapter_id WHERE c.book_id=?", (bid,)).fetchone()
    chapter_list = conn.execute(
        "SELECT num, title FROM chapters WHERE book_id=? ORDER BY num", (bid,)).fetchall()
    # Aggregate overview (tier 2): metrics per chapter — size scales with the book structure,
    # not with the amount of feedback. LEFT JOIN so chapters without comments still show up as a tile.
    chapter_stats = conn.execute(
        "SELECT c.num, c.title, COUNT(cm.id) AS n_total, "
        "COALESCE(SUM(CASE WHEN cm.resolved=0 THEN 1 ELSE 0 END), 0) AS n_open "
        "FROM chapters c LEFT JOIN comments cm ON cm.chapter_id=c.id "
        "WHERE c.book_id=? GROUP BY c.id ORDER BY c.num", (bid,)).fetchall()
    # KPI bar: overall average of scale ratings + weakest chapter
    overall_avg = conn.execute(
        "SELECT ROUND(AVG(CAST(a.value AS REAL)), 2) AS avg FROM answers a "
        "JOIN questions q ON q.id=a.question_id JOIN chapters c ON c.id=a.chapter_id "
        "WHERE q.qtype='scale' AND c.book_id=?", (bid,)).fetchone()["avg"]
    weak = conn.execute(
        "SELECT c.num, ROUND(AVG(CAST(a.value AS REAL)), 2) AS avg FROM answers a "
        "JOIN questions q ON q.id=a.question_id JOIN chapters c ON c.id=a.chapter_id "
        "WHERE q.qtype='scale' AND c.book_id=? GROUP BY a.chapter_id ORDER BY avg ASC LIMIT 1", (bid,)).fetchone()
    readers_done = sum(1 for r in readers if total_chapters and r["n_chapters_done"] == total_chapters)
    # Reaction heatmap: one cell per paragraph block per chapter, colored by the dominant reaction,
    # opacity by count. Block count comes from blocks (book structure) so clusters stay position-accurate.
    hmap_rows = conn.execute(
        "SELECT c.num AS chapter_num, c.title AS chapter_title, re.block_idx, re.kind, COUNT(*) AS n "
        "FROM reactions re JOIN chapters c ON c.id=re.chapter_id WHERE c.book_id=? "
        "GROUP BY re.chapter_id, re.block_idx, re.kind", (bid,)).fetchall()
    block_counts = {r["num"]: r["n"] for r in conn.execute(
        "SELECT c.num, COUNT(b.id) AS n FROM chapters c LEFT JOIN blocks b ON b.chapter_id=c.id "
        "WHERE c.book_id=? GROUP BY c.id", (bid,)).fetchall()}
    scale_stats = conn.execute(
        "SELECT c.num AS chapter_num, c.title AS chapter_title, q.text AS question, "
        "ROUND(AVG(CAST(a.value AS REAL)), 2) AS avg_val, COUNT(*) AS n "
        "FROM answers a JOIN questions q ON q.id=a.question_id JOIN chapters c ON c.id=a.chapter_id "
        "WHERE q.qtype='scale' AND c.book_id=? GROUP BY a.chapter_id, a.question_id ORDER BY c.num, q.pos", (bid,)).fetchall()
    text_answers = conn.execute(
        "SELECT c.num AS chapter_num, c.title AS chapter_title, q.text AS question, a.value, r.name AS reader_name "
        "FROM answers a JOIN questions q ON q.id=a.question_id JOIN chapters c ON c.id=a.chapter_id "
        "JOIN readers r ON r.id=a.reader_id WHERE q.qtype='text' AND c.book_id=? ORDER BY c.num, q.pos", (bid,)).fetchall()
    questions = conn.execute(
        "SELECT q.*, (SELECT COUNT(*) FROM answers a WHERE a.question_id=q.id) AS n_answers "
        "FROM questions q WHERE q.book_id=? ORDER BY q.pos", (bid,)).fetchall()
    conn.close()
    # Build heatmap rows: for each chapter with reactions, one cell per block (0..max), dominant reaction + opacity.
    kind_emoji = {"herz": "❤️", "frage": "❓", "gaehn": "😴"}
    agg: dict = defaultdict(lambda: defaultdict(dict))
    titles: dict = {}
    for row in hmap_rows:
        agg[row["chapter_num"]][row["block_idx"]][row["kind"]] = row["n"]
        titles[row["chapter_num"]] = row["chapter_title"]
    heatmap = []
    for num in sorted(agg):
        span = max(block_counts.get(num, 0), max(agg[num]) + 1)
        cells = []
        for b in range(span):
            kinds = agg[num].get(b)
            if not kinds:
                cells.append(None)
                continue
            dom = max(kinds, key=kinds.get)
            total = sum(kinds.values())
            cells.append({
                "kind": dom, "op": round(min(1.0, 0.4 + 0.2 * total), 2),
                "title": f"Absatz {b}: " + ", ".join(f"{c}× {kind_emoji[k]}" for k, c in kinds.items()),
            })
        heatmap.append({"num": num, "title": titles[num], "cells": cells})
    # Site is https-only (nginx enforces redirect + HSTS) — always show magic links as https,
    # even if request.base_url arrives as http behind the proxy.
    base_url = str(request.base_url).rstrip("/")
    if base_url.startswith("http://"):
        base_url = "https://" + base_url[len("http://"):]
    return templates.TemplateResponse(request, "admin.html", {
        "book": active, "books": books, "readers": readers, "comments": comments, "heatmap": heatmap,
        "scale_stats": scale_stats, "text_answers": text_answers,
        "base_url": base_url, "total_chapters": total_chapters, "questions": questions,
        "chapter_list": chapter_list, "n_comments_total": crow["total"], "n_comments_open": crow["n_open"],
        "filter_chapter": chapter, "filter_status": status, "filter_reader": reader,
        "chapter_stats": chapter_stats, "overall_avg": overall_avg, "weak": weak, "readers_done": readers_done,
    })


def admin_redirect(book_slug: str, anchor: str = "", **params) -> RedirectResponse:
    """After admin actions, go back to the correct book tab — pass optional filters (chapter/status/reader)
    through as query params so a POST doesn't bounce you out of the filtered view."""
    q = []
    if book_slug:
        q.append(f"book={quote(book_slug)}")
    for k, v in params.items():
        if v:  # skip 0 / "" / None — then the route default kicks in
            q.append(f"{k}={quote(str(v))}")
    url = "/admin" + ("?" + "&".join(q) if q else "")
    if anchor:
        url += f"#{anchor}"
    return RedirectResponse(url, status_code=303)


def book_by_slug_or_400(conn, slug: str):
    b = conn.execute("SELECT * FROM books WHERE slug=?", (slug,)).fetchone()
    if not b:
        raise HTTPException(400, "Unbekanntes Buch")
    return b


@app.post("/admin/comment/{comment_id}/toggle")
def toggle_comment_resolved(request: Request, comment_id: int, book: str = "",
                            chapter: int = 0, status: str = "", reader: int = 0):
    require_admin(request)
    conn = get_db()
    if not conn.execute("SELECT 1 FROM comments WHERE id=?", (comment_id,)).fetchone():
        raise HTTPException(404, "Kommentar nicht gefunden")
    conn.execute("UPDATE comments SET resolved = 1 - resolved WHERE id=?", (comment_id,))
    conn.commit()
    conn.close()
    return admin_redirect(book, "comments", chapter=chapter, status=status, reader=reader)


@app.post("/admin/question")
async def create_question(request: Request, book: str = ""):
    require_admin(request)
    form = await request.form()
    text = str(form.get("text", "")).strip()[:MAX_TEXT]
    qtype = str(form.get("qtype", ""))
    if not text or qtype not in ("scale", "text"):
        raise HTTPException(400, "Fragetext fehlt oder Typ ungültig")
    conn = get_db()
    b = book_by_slug_or_400(conn, book)
    pos = conn.execute("SELECT COALESCE(MAX(pos), -1) + 1 AS p FROM questions WHERE book_id=?", (b["id"],)).fetchone()["p"]
    conn.execute("INSERT INTO questions(book_id, pos, text, qtype) VALUES(?,?,?,?)", (b["id"], pos, text, qtype))
    conn.commit()
    conn.close()
    return admin_redirect(book, "questions")


@app.post("/admin/question/{question_id}/update")
async def update_question(request: Request, question_id: int, book: str = ""):
    require_admin(request)
    form = await request.form()
    text = str(form.get("text", "")).strip()[:MAX_TEXT]
    try:
        pos = int(form.get("pos", 0))
    except ValueError:
        raise HTTPException(400, "Position muss eine Zahl sein")
    if not text:
        raise HTTPException(400, "Fragetext fehlt")
    conn = get_db()
    if not conn.execute("SELECT 1 FROM questions WHERE id=?", (question_id,)).fetchone():
        raise HTTPException(404, "Frage nicht gefunden")
    conn.execute("UPDATE questions SET text=?, pos=? WHERE id=?", (text, pos, question_id))
    conn.commit()
    conn.close()
    return admin_redirect(book, "questions")


@app.post("/admin/question/{question_id}/delete")
def delete_question(request: Request, question_id: int, book: str = ""):
    require_admin(request)
    conn = get_db()
    if not conn.execute("SELECT 1 FROM questions WHERE id=?", (question_id,)).fetchone():
        raise HTTPException(404, "Frage nicht gefunden")
    conn.execute("DELETE FROM answers WHERE question_id=?", (question_id,))
    conn.execute("DELETE FROM questions WHERE id=?", (question_id,))
    conn.commit()
    conn.close()
    return admin_redirect(book, "questions")


@app.post("/admin/reader")
async def create_reader(request: Request, book: str = ""):
    require_admin(request)
    form = await request.form()
    name = str(form.get("name", "")).strip()[:120]
    if not name:
        raise HTTPException(400, "Name fehlt")
    token = secrets.token_urlsafe(12)
    conn = get_db()
    b = book_by_slug_or_400(conn, book)
    conn.execute("INSERT INTO readers(name, token, book_id) VALUES(?,?,?)", (name, token, b["id"]))
    conn.commit()
    conn.close()
    return admin_redirect(book)


@app.post("/admin/reader/{reader_id}/rotate")
def rotate_token(request: Request, reader_id: int, book: str = ""):
    require_admin(request)
    conn = get_db()
    if not conn.execute("SELECT 1 FROM readers WHERE id=?", (reader_id,)).fetchone():
        raise HTTPException(404, "Leser nicht gefunden")
    conn.execute("UPDATE readers SET token=? WHERE id=?", (secrets.token_urlsafe(12), reader_id))
    conn.commit()
    conn.close()
    return admin_redirect(book)


@app.post("/admin/reader/{reader_id}/delete")
def delete_reader(request: Request, reader_id: int, book: str = ""):
    require_admin(request)
    conn = get_db()
    if not conn.execute("SELECT 1 FROM readers WHERE id=?", (reader_id,)).fetchone():
        raise HTTPException(404, "Leser nicht gefunden")
    for table in ("comments", "reactions", "answers", "reading_progress", "reader_activity"):
        conn.execute(f"DELETE FROM {table} WHERE reader_id=?", (reader_id,))
    conn.execute("DELETE FROM readers WHERE id=?", (reader_id,))
    conn.commit()
    conn.close()
    return admin_redirect(book)


@app.get("/api/export")
def export(request: Request, book: str = ""):
    """JSON export — complete, or filtered to one book with ?book=<slug>."""
    require_admin(request)
    conn = get_db()
    out = {}
    if book:
        b = book_by_slug_or_400(conn, book)
        bid = b["id"]
        in_chapters = "chapter_id IN (SELECT id FROM chapters WHERE book_id=?)"
        filters = {
            "books": ("id=?", (bid,)),
            "chapters": ("book_id=?", (bid,)),
            "readers": ("book_id=?", (bid,)),
            "questions": ("book_id=?", (bid,)),
            "comments": (in_chapters, (bid,)),
            "reactions": (in_chapters, (bid,)),
            "answers": (in_chapters, (bid,)),
            "reading_progress": (in_chapters, (bid,)),
            "reader_activity": ("reader_id IN (SELECT id FROM readers WHERE book_id=?)", (bid,)),
        }
        for table, (where, params) in filters.items():
            out[table] = [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE {where}", params).fetchall()]
    else:
        for table in ("books", "chapters", "readers", "comments", "reactions", "questions", "answers", "reading_progress", "reader_activity"):
            out[table] = [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
    conn.close()
    return JSONResponse(out)
