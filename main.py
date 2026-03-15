from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from db import init_db, get_db

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
