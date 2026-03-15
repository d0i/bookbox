from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from db import init_db, get_db
from suggest import suggest_box
from isbn_lookup import lookup_isbn

app = FastAPI(title="BookBox")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
def startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    conn = get_db()
    boxes = conn.execute(
        "SELECT b.*, COUNT(bk.id) AS book_count "
        "FROM boxes b LEFT JOIN books bk ON bk.box_id = b.id "
        "GROUP BY b.id ORDER BY b.id"
    ).fetchall()
    conn.close()
    return templates.TemplateResponse("index.html", {"request": request, "boxes": boxes})


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
    book_count = len(books)
    conn.close()
    return templates.TemplateResponse(
        "box.html",
        {"request": request, "box": box, "books": books, "book_count": book_count},
    )


# ── Helpers ──────────────────────────────────────────

def _boxes_with_counts(conn):
    return conn.execute(
        "SELECT b.*, COUNT(bk.id) AS book_count "
        "FROM boxes b LEFT JOIN books bk ON bk.box_id = b.id "
        "GROUP BY b.id ORDER BY b.id"
    ).fetchall()


def _genres(conn):
    rows = conn.execute("SELECT DISTINCT genre FROM books WHERE genre != '' ORDER BY genre").fetchall()
    return [r["genre"] for r in rows]


def _add_ctx(conn, author="", genre=""):
    """Build the template context for the add form."""
    boxes = _boxes_with_counts(conn)
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


# ── Add Book ─────────────────────────────────────────

def _from_box_ctx(conn, box_id: str) -> dict:
    """Build from_box context if a valid box_id is provided."""
    if not box_id:
        return {"from_box": None, "from_box_label": None}
    box = conn.execute("SELECT * FROM boxes WHERE id = ?", (box_id,)).fetchone()
    if box:
        return {"from_box": box["id"], "from_box_label": box["label"]}
    return {"from_box": None, "from_box_label": None}


@app.get("/add", response_class=HTMLResponse)
def add_form(request: Request, box_id: str = ""):
    conn = get_db()
    fb = _from_box_ctx(conn, box_id)
    ctx = _add_ctx(conn)
    if fb["from_box"]:
        # Lock to the originating box, suppress suggestion
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


# ── Suggestion API (for live JS updates) ─────────────

@app.get("/api/suggest-box")
def api_suggest(author: str = "", genre: str = ""):
    conn = get_db()
    box_id, reason = suggest_box(conn, author, genre)
    conn.close()
    return JSONResponse({"box_id": box_id, "reason": reason})


# ── ISBN lookup API ────────────────────────────────

@app.get("/api/isbn/{isbn}")
def api_isbn(isbn: str):
    candidates = lookup_isbn(isbn)
    return JSONResponse({"isbn": isbn, "candidates": candidates})


# ── Scan page ─────────────────────────────────────

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


# ── Batch page + API ──────────────────────────────

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


from pydantic import BaseModel

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
    box = conn.execute("SELECT * FROM boxes WHERE id = ?", (req.box_id,)).fetchone()
    if not box:
        conn.close()
        return JSONResponse({"ok": False, "error": "Box not found"}, status_code=404)
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
