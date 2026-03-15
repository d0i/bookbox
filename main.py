from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
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
        "SELECT bk.id, bk.title, bk.author, bk.genre, bk.box_id, b.label AS box_label "
        "FROM books bk JOIN boxes b ON b.id = bk.box_id "
        "WHERE b.archived = 0 AND (bk.title LIKE ? OR bk.author LIKE ? OR bk.genre LIKE ?) "
        "ORDER BY bk.title LIMIT 30",
        (like, like, like),
    ).fetchall()
    conn.close()
    results = [{"id": r["id"], "title": r["title"], "author": r["author"],
                "genre": r["genre"], "box_id": r["box_id"], "box_label": r["box_label"]}
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
             genre: str = Form(""), box_id: str = Form(...), from_box: str = Form("")):
    conn = get_db()
    fb = _from_box_ctx(conn, from_box)
    box = conn.execute("SELECT * FROM boxes WHERE id = ?", (box_id,)).fetchone()
    if not box:
        conn.close()
        return HTMLResponse("Box not found", status_code=404)
    conn.execute("INSERT INTO books (title, author, genre, box_id) VALUES (?, ?, ?, ?)",
                 (title.strip(), author.strip(), genre.strip(), box_id))
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
            "INSERT INTO books (title, author, genre, box_id) VALUES (?, ?, ?, ?)",
            (t, a, book.genre.strip(), req.box_id),
        )
        added += 1
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True, "count": added})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
