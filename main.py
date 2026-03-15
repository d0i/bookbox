import json
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from db import init_db, get_db
from suggest import suggest_box
from isbn_lookup import lookup_isbn

app = FastAPI(title="BookBox")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
def startup():
    init_db()


# ── Helpers ──────────────────────────────────────────

def _active_boxes_with_counts(conn):
    return conn.execute(
        "SELECT b.*, COUNT(bk.id) AS book_count "
        "FROM boxes b LEFT JOIN books bk ON bk.box_id = b.id "
        "WHERE b.archived = 0 GROUP BY b.id ORDER BY b.id"
    ).fetchall()


def _genres(conn):
    rows = conn.execute(
        "SELECT DISTINCT bk.genre FROM books bk "
        "JOIN boxes b ON b.id = bk.box_id "
        "WHERE bk.genre != '' AND b.archived = 0 ORDER BY bk.genre"
    ).fetchall()
    return [r["genre"] for r in rows]


def _add_ctx(conn, author="", genre=""):
    boxes = _active_boxes_with_counts(conn)
    genres = _genres(conn)
    suggested_box, reason = suggest_box(conn, author, genre)
    if not suggested_box and boxes:
        suggested_box = boxes[0]["id"]
    return {
        "boxes": boxes,
        "genres": genres,
        "suggested_box": suggested_box,
        "suggestion_reason": reason,
    }


def _from_box_ctx(conn, box_id: str) -> dict:
    if not box_id:
        return {"from_box": None, "from_box_label": None}
    box = conn.execute("SELECT * FROM boxes WHERE id = ?", (box_id,)).fetchone()
    if box:
        return {"from_box": box["id"], "from_box_label": box["label"]}
    return {"from_box": None, "from_box_label": None}


# ── Home / Index ───────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    conn = get_db()
    boxes = _active_boxes_with_counts(conn)
    archived_count = conn.execute("SELECT COUNT(*) FROM boxes WHERE archived = 1").fetchone()[0]
    conn.close()
    return templates.TemplateResponse("index.html", {
        "request": request, "boxes": boxes, "archived_count": archived_count,
    })


# ── Add / Rename Box ─────────────────────────────

class AddBoxRequest(BaseModel):
    id: str
    label: str = ""


@app.post("/api/boxes")
def add_box(req: AddBoxRequest):
    box_id = req.id.strip()
    if not box_id:
        return JSONResponse({"ok": False, "error": "Box ID is required"}, status_code=400)
    label = req.label.strip() or box_id
    conn = get_db()
    existing = conn.execute("SELECT id FROM boxes WHERE id = ?", (box_id,)).fetchone()
    if existing:
        conn.close()
        return JSONResponse({"ok": False, "error": "A box with this ID already exists"}, status_code=409)
    conn.execute("INSERT INTO boxes (id, label) VALUES (?, ?)", (box_id, label))
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True, "id": box_id, "label": label})


class RenameBoxRequest(BaseModel):
    label: str


@app.patch("/api/box/{box_id}")
def rename_box(box_id: str, req: RenameBoxRequest):
    label = req.label.strip()
    if not label:
        return JSONResponse({"ok": False, "error": "Label is required"}, status_code=400)
    conn = get_db()
    box = conn.execute("SELECT id FROM boxes WHERE id = ?", (box_id,)).fetchone()
    if not box:
        conn.close()
        return JSONResponse({"ok": False, "error": "Box not found"}, status_code=404)
    conn.execute("UPDATE boxes SET label = ? WHERE id = ?", (label, box_id))
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True, "label": label})


# ── Box View ──────────────────────────────────────

@app.get("/box/{box_id}", response_class=HTMLResponse)
def box_view(request: Request, box_id: str):
    conn = get_db()
    box = conn.execute("SELECT * FROM boxes WHERE id = ?", (box_id,)).fetchone()
    if not box:
        conn.close()
        return HTMLResponse(f"<h1>Box '{box_id}' not found</h1>", status_code=404)
    books = conn.execute(
        "SELECT * FROM books WHERE box_id = ? ORDER BY title", (box_id,)
    ).fetchall()
    # Active boxes for move-to dropdown (exclude self and archived)
    move_targets = conn.execute(
        "SELECT id, label FROM boxes WHERE archived = 0 AND id != ? ORDER BY id", (box_id,)
    ).fetchall()
    conn.close()
    return templates.TemplateResponse("box.html", {
        "request": request, "box": box, "books": books,
        "book_count": len(books), "move_targets": move_targets,
    })


# ── Book Detail View ─────────────────────────────

@app.get("/book/{book_id}", response_class=HTMLResponse)
def book_view(request: Request, book_id: int):
    conn = get_db()
    book = conn.execute(
        "SELECT bk.*, b.label AS box_label FROM books bk "
        "JOIN boxes b ON b.id = bk.box_id WHERE bk.id = ?", (book_id,)
    ).fetchone()
    if not book:
        conn.close()
        return HTMLResponse("<h1>Book not found</h1>", status_code=404)
    # Active boxes for move dropdown
    boxes = conn.execute(
        "SELECT id, label FROM boxes WHERE archived = 0 ORDER BY id"
    ).fetchall()
    conn.close()
    return templates.TemplateResponse("book.html", {
        "request": request, "book": book, "boxes": boxes,
    })


# ── Book / Box Memo APIs ─────────────────────────

class UpdateBookRequest(BaseModel):
    memo: str | None = None
    box_id: str | None = None


@app.patch("/api/book/{book_id}")
def update_book(book_id: int, req: UpdateBookRequest):
    conn = get_db()
    book = conn.execute("SELECT id FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        conn.close()
        return JSONResponse({"ok": False, "error": "Book not found"}, status_code=404)
    if req.memo is not None:
        conn.execute("UPDATE books SET memo = ? WHERE id = ?", (req.memo, book_id))
    if req.box_id is not None:
        target = conn.execute(
            "SELECT * FROM boxes WHERE id = ? AND archived = 0", (req.box_id,)
        ).fetchone()
        if not target:
            conn.close()
            return JSONResponse({"ok": False, "error": "Target box not found or archived"}, status_code=400)
        conn.execute("UPDATE books SET box_id = ? WHERE id = ?", (req.box_id, book_id))
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True})


class MemoRequest(BaseModel):
    memo: str


@app.patch("/api/box/{box_id}/memo")
def update_box_memo(box_id: str, req: MemoRequest):
    conn = get_db()
    box = conn.execute("SELECT id FROM boxes WHERE id = ?", (box_id,)).fetchone()
    if not box:
        conn.close()
        return JSONResponse({"ok": False, "error": "Box not found"}, status_code=404)
    conn.execute("UPDATE boxes SET memo = ? WHERE id = ?", (req.memo, box_id))
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True})


# ── Archive / Restore / Delete box ───────────────

@app.post("/api/box/{box_id}/archive")
def archive_box(box_id: str):
    conn = get_db()
    conn.execute("UPDATE boxes SET archived = 1 WHERE id = ?", (box_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True})


@app.post("/api/box/{box_id}/restore")
def restore_box(box_id: str):
    conn = get_db()
    conn.execute("UPDATE boxes SET archived = 0 WHERE id = ?", (box_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True})


@app.delete("/api/box/{box_id}")
def delete_box(box_id: str):
    conn = get_db()
    box = conn.execute("SELECT * FROM boxes WHERE id = ? AND archived = 1", (box_id,)).fetchone()
    if not box:
        conn.close()
        return JSONResponse({"ok": False, "error": "Only archived boxes can be deleted"}, status_code=400)
    conn.execute("DELETE FROM books WHERE box_id = ?", (box_id,))
    conn.execute("DELETE FROM boxes WHERE id = ?", (box_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True})


# ── Archived boxes view ──────────────────────────

@app.get("/archived", response_class=HTMLResponse)
def archived_view(request: Request):
    conn = get_db()
    boxes = conn.execute(
        "SELECT b.*, COUNT(bk.id) AS book_count "
        "FROM boxes b LEFT JOIN books bk ON bk.box_id = b.id "
        "WHERE b.archived = 1 GROUP BY b.id ORDER BY b.id"
    ).fetchall()
    conn.close()
    return templates.TemplateResponse("archived.html", {"request": request, "boxes": boxes})


# ── Move books ───────────────────────────────────

class MoveRequest(BaseModel):
    book_ids: list[int]
    target_box_id: str


@app.post("/api/books/move")
def move_books(req: MoveRequest):
    conn = get_db()
    # Only allow moving to active boxes
    target = conn.execute(
        "SELECT * FROM boxes WHERE id = ? AND archived = 0", (req.target_box_id,)
    ).fetchone()
    if not target:
        conn.close()
        return JSONResponse({"ok": False, "error": "Target box not found or is archived"}, status_code=400)
    placeholders = ",".join("?" for _ in req.book_ids)
    conn.execute(
        f"UPDATE books SET box_id = ? WHERE id IN ({placeholders})",
        [req.target_box_id] + req.book_ids,
    )
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True, "moved": len(req.book_ids)})


# ── Search API ───────────────────────────────────

@app.get("/api/search")
def api_search(q: str = ""):
    q = q.strip()
    if not q:
        return JSONResponse({"results": []})
    conn = get_db()
    like = f"%{q}%"
    rows = conn.execute(
        "SELECT bk.id, bk.title, bk.author, bk.genre, bk.isbn, bk.box_id, b.label AS box_label "
        "FROM books bk JOIN boxes b ON b.id = bk.box_id "
        "WHERE b.archived = 0 AND (bk.title LIKE ? OR bk.author LIKE ? OR bk.genre LIKE ? OR bk.isbn LIKE ?) "
        "ORDER BY bk.title LIMIT 30",
        (like, like, like, like),
    ).fetchall()
    conn.close()
    results = [{"id": r["id"], "title": r["title"], "author": r["author"],
                "genre": r["genre"], "isbn": r["isbn"], "box_id": r["box_id"],
                "box_label": r["box_label"]}
               for r in rows]
    return JSONResponse({"results": results})


# ── Add Book ─────────────────────────────────────

@app.get("/add", response_class=HTMLResponse)
def add_form(request: Request, box_id: str = ""):
    conn = get_db()
    fb = _from_box_ctx(conn, box_id)
    ctx = _add_ctx(conn)
    if fb["from_box"]:
        ctx["suggested_box"] = fb["from_box"]
        ctx["suggestion_reason"] = ""
    conn.close()
    return templates.TemplateResponse("add.html", {"request": request, "form": {}, "success": None, **fb, **ctx})


@app.post("/add", response_class=HTMLResponse)
def add_book(request: Request, title: str = Form(...), author: str = Form(...),
             genre: str = Form(""), box_id: str = Form(...), from_box: str = Form(""),
             isbn: str = Form(""), publisher: str = Form(""),
             published_year: str = Form(""), page_count: str = Form(""),
             language: str = Form(""), thumbnail_url: str = Form("")):
    conn = get_db()
    fb = _from_box_ctx(conn, from_box)
    box = conn.execute("SELECT * FROM boxes WHERE id = ?", (box_id,)).fetchone()
    if not box:
        conn.close()
        return HTMLResponse("Box not found", status_code=404)
    pc = None
    if page_count.strip():
        try:
            pc = int(page_count.strip())
        except ValueError:
            pc = None
    conn.execute(
        "INSERT INTO books (title, author, genre, box_id, isbn, publisher, published_year, page_count, language, thumbnail_url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (title.strip(), author.strip(), genre.strip(), box_id,
         isbn.strip(), publisher.strip(), published_year.strip(), pc,
         language.strip(), thumbnail_url.strip()),
    )
    conn.commit()
    ctx = _add_ctx(conn)
    if fb["from_box"]:
        ctx["suggested_box"] = fb["from_box"]
        ctx["suggestion_reason"] = ""
    conn.close()
    return templates.TemplateResponse("add.html", {
        "request": request, "form": {},
        "success": {"title": title, "box_id": box_id, "box_label": box["label"]},
        **fb, **ctx,
    })


# ── Suggestion API ───────────────────────────────

@app.get("/api/suggest-box")
def api_suggest(author: str = "", genre: str = ""):
    conn = get_db()
    box_id, reason = suggest_box(conn, author, genre)
    conn.close()
    return JSONResponse({"box_id": box_id, "reason": reason})


# ── ISBN lookup API ──────────────────────────────

@app.get("/api/isbn/{isbn}")
def api_isbn(isbn: str):
    candidates = lookup_isbn(isbn)
    return JSONResponse({"isbn": isbn, "candidates": candidates})


# ── Find book in library by ISBN ─────────────────

@app.get("/api/find-by-isbn/{isbn}")
def find_by_isbn(isbn: str):
    """Search for books already in the database matching this ISBN."""
    isbn = isbn.strip().replace("-", "").replace(" ", "")
    conn = get_db()
    rows = conn.execute(
        "SELECT bk.id, bk.title, bk.author, bk.genre, bk.isbn, bk.box_id, "
        "bk.publisher, bk.published_year, bk.thumbnail_url, b.label AS box_label "
        "FROM books bk JOIN boxes b ON b.id = bk.box_id "
        "WHERE bk.isbn = ? ORDER BY bk.title",
        (isbn,),
    ).fetchall()
    conn.close()
    results = [
        {"id": r["id"], "title": r["title"], "author": r["author"],
         "genre": r["genre"], "isbn": r["isbn"], "box_id": r["box_id"],
         "box_label": r["box_label"], "publisher": r["publisher"],
         "published_year": r["published_year"], "thumbnail_url": r["thumbnail_url"]}
        for r in rows
    ]
    return JSONResponse({"isbn": isbn, "found": results})


# ── Scan page ────────────────────────────────────

@app.get("/scan", response_class=HTMLResponse)
def scan_page(request: Request, box_id: str = ""):
    conn = get_db()
    fb = _from_box_ctx(conn, box_id)
    ctx = _add_ctx(conn)
    if fb["from_box"]:
        ctx["suggested_box"] = fb["from_box"]
        ctx["suggestion_reason"] = ""
    conn.close()
    return templates.TemplateResponse("scan.html", {"request": request, **fb, **ctx})


# ── Batch page + API ─────────────────────────────

@app.get("/batch", response_class=HTMLResponse)
def batch_page(request: Request, box_id: str = ""):
    conn = get_db()
    fb = _from_box_ctx(conn, box_id)
    ctx = _add_ctx(conn)
    if fb["from_box"]:
        ctx["suggested_box"] = fb["from_box"]
        ctx["suggestion_reason"] = ""
    conn.close()
    return templates.TemplateResponse("batch.html", {"request": request, **fb, **ctx})


class BatchBook(BaseModel):
    title: str
    author: str
    genre: str = ""
    isbn: str = ""
    publisher: str = ""
    published_year: str = ""
    page_count: int | None = None
    language: str = ""
    thumbnail_url: str = ""

class BatchAddRequest(BaseModel):
    box_id: str
    books: list[BatchBook]


@app.post("/api/batch-add")
def api_batch_add(req: BatchAddRequest):
    conn = get_db()
    box = conn.execute("SELECT * FROM boxes WHERE id = ? AND archived = 0", (req.box_id,)).fetchone()
    if not box:
        conn.close()
        return JSONResponse({"ok": False, "error": "Box not found or archived"}, status_code=404)
    added = 0
    for book in req.books:
        t, a = book.title.strip(), book.author.strip()
        if not t or not a:
            continue
        conn.execute(
            "INSERT INTO books (title, author, genre, box_id, isbn, publisher, published_year, page_count, language, thumbnail_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (t, a, book.genre.strip(), req.box_id,
             book.isbn.strip(), book.publisher.strip(), book.published_year.strip(),
             book.page_count, book.language.strip(), book.thumbnail_url.strip()),
        )
        added += 1
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True, "count": added})


# ── Export / Import ───────────────────────────────

def _box_to_dict(conn, box_id: str) -> dict | None:
    box = conn.execute("SELECT * FROM boxes WHERE id = ?", (box_id,)).fetchone()
    if not box:
        return None
    books = conn.execute(
        "SELECT * FROM books WHERE box_id = ? ORDER BY id", (box_id,)
    ).fetchall()
    return {
        "id": box["id"],
        "label": box["label"],
        "archived": bool(box["archived"]),
        "memo": box["memo"],
        "books": [
            {
                "title": bk["title"],
                "author": bk["author"],
                "genre": bk["genre"],
                "memo": bk["memo"],
                "isbn": bk["isbn"],
                "publisher": bk["publisher"],
                "published_year": bk["published_year"],
                "page_count": bk["page_count"],
                "language": bk["language"],
                "thumbnail_url": bk["thumbnail_url"],
                "created_at": bk["created_at"],
            }
            for bk in books
        ],
    }


@app.get("/api/box/{box_id}/export")
def export_box(box_id: str):
    conn = get_db()
    data = _box_to_dict(conn, box_id)
    conn.close()
    if not data:
        return JSONResponse({"ok": False, "error": "Box not found"}, status_code=404)
    content = json.dumps(data, ensure_ascii=False, indent=2)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{box_id}.json"'},
    )


@app.get("/api/export")
def export_all():
    conn = get_db()
    boxes = conn.execute("SELECT id FROM boxes ORDER BY id").fetchall()
    data = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "boxes": [_box_to_dict(conn, row["id"]) for row in boxes],
    }
    conn.close()
    content = json.dumps(data, ensure_ascii=False, indent=2)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="bookbox-export.json"'},
    )


@app.post("/api/box/{box_id}/import")
async def import_box(box_id: str, file: UploadFile = File(...)):
    conn = get_db()
    box = conn.execute("SELECT id FROM boxes WHERE id = ?", (box_id,)).fetchone()
    if not box:
        conn.close()
        return JSONResponse({"ok": False, "error": "Box not found"}, status_code=404)
    raw = await file.read()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        conn.close()
        return JSONResponse({"ok": False, "error": "Invalid JSON file"}, status_code=400)
    # Accept both {books: [...]} and bare [...]
    books = data if isinstance(data, list) else data.get("books", [])
    if not isinstance(books, list):
        conn.close()
        return JSONResponse({"ok": False, "error": "Expected a list of books or {books: [...]}"}, status_code=400)
    # Replace all books in this box
    conn.execute("DELETE FROM books WHERE box_id = ?", (box_id,))
    added = 0
    for bk in books:
        if not isinstance(bk, dict):
            continue
        title = str(bk.get("title", "")).strip()
        author = str(bk.get("author", "")).strip()
        if not title:
            continue
        genre = str(bk.get("genre", "")).strip()
        memo = str(bk.get("memo", "")).strip()
        isbn = str(bk.get("isbn", "")).strip()
        publisher = str(bk.get("publisher", "")).strip()
        published_year = str(bk.get("published_year", "")).strip()
        page_count = bk.get("page_count")
        if page_count is not None:
            try:
                page_count = int(page_count)
            except (ValueError, TypeError):
                page_count = None
        language = str(bk.get("language", "")).strip()
        thumbnail_url = str(bk.get("thumbnail_url", "")).strip()
        created = bk.get("created_at", None)
        cols = "title, author, genre, memo, box_id, isbn, publisher, published_year, page_count, language, thumbnail_url"
        vals = (title, author, genre, memo, box_id, isbn, publisher, published_year, page_count, language, thumbnail_url)
        if created:
            conn.execute(
                f"INSERT INTO books ({cols}, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                vals + (created,),
            )
        else:
            conn.execute(
                f"INSERT INTO books ({cols}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                vals,
            )
        added += 1
    # Update box memo/label if present in data
    if isinstance(data, dict):
        if "memo" in data:
            conn.execute("UPDATE boxes SET memo = ? WHERE id = ?", (str(data["memo"]), box_id))
        if "label" in data:
            conn.execute("UPDATE boxes SET label = ? WHERE id = ?", (str(data["label"]), box_id))
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True, "replaced": added})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
