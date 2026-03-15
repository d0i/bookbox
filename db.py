import sqlite3
import os

DB_PATH = os.environ.get("BOOKBOX_DB", os.path.join(os.path.dirname(__file__), "bookbox.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS boxes (
    id          TEXT PRIMARY KEY,   -- e.g. 'rox-001'
    label       TEXT NOT NULL,      -- friendly name
    archived    INTEGER NOT NULL DEFAULT 0,
    memo        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS books (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    author          TEXT NOT NULL,
    genre           TEXT NOT NULL DEFAULT '',
    box_id          TEXT NOT NULL REFERENCES boxes(id),
    memo            TEXT NOT NULL DEFAULT '',
    isbn            TEXT NOT NULL DEFAULT '',
    publisher       TEXT NOT NULL DEFAULT '',
    published_year  TEXT NOT NULL DEFAULT '',
    page_count      INTEGER,
    language        TEXT NOT NULL DEFAULT '',
    thumbnail_url   TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_books_box ON books(box_id);
CREATE INDEX IF NOT EXISTS idx_books_author ON books(author);
CREATE INDEX IF NOT EXISTS idx_books_genre ON books(genre);
"""


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate(conn):
    """Add columns that may not exist in older databases."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(boxes)").fetchall()}
    if "memo" not in existing:
        conn.execute("ALTER TABLE boxes ADD COLUMN memo TEXT NOT NULL DEFAULT ''")
    existing = {row[1] for row in conn.execute("PRAGMA table_info(books)").fetchall()}
    new_cols = {
        "memo": "TEXT NOT NULL DEFAULT ''",
        "isbn": "TEXT NOT NULL DEFAULT ''",
        "publisher": "TEXT NOT NULL DEFAULT ''",
        "published_year": "TEXT NOT NULL DEFAULT ''",
        "page_count": "INTEGER",
        "language": "TEXT NOT NULL DEFAULT ''",
        "thumbnail_url": "TEXT NOT NULL DEFAULT ''",
    }
    for col, typedef in new_cols.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE books ADD COLUMN {col} {typedef}")
    # Create indexes for new columns
    conn.execute("CREATE INDEX IF NOT EXISTS idx_books_isbn ON books(isbn)")
    conn.commit()


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    _migrate(conn)
    # Seed default boxes if empty
    count = conn.execute("SELECT COUNT(*) FROM boxes").fetchone()[0]
    if count == 0:
        boxes = [(f"rox-{i:03d}", f"ROX 530m Box #{i}") for i in range(1, 11)]
        conn.executemany("INSERT INTO boxes (id, label) VALUES (?, ?)", boxes)
        conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
