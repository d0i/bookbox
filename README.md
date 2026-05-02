# 📦 BookBox

A personal book inventory management web app for tracking physical books stored in plastic boxes (ROX 530m or similar). Designed for mobile-first use with NFC tag integration — stick an NFC tag on each box linking to its page.

![Python](https://img.shields.io/badge/Python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green)
![SQLite](https://img.shields.io/badge/SQLite-3-lightgrey)

## Features

- **Box Management** — Create boxes with custom IDs, rename, archive/restore/delete
- **Three ways to add books**
  - ✏️ **Manual** — Title, author, genre with smart box suggestion
  - 📷 **Scan** — Camera barcode scanner → ISBN lookup → add
  - 📦 **Batch** — Continuous scanning with beep feedback, review & add all at once
- **ISBN Lookup** — Parallel search across openBD (Japanese books), Google Books, and Open Library
- **Smart Box Suggestion** — Recommends a box based on author/genre clustering
- **Book Detail View** — Per-book page with free-text memo
- **Box Memo** — Free-text notes on each box
- **Search** — Incremental search across title/author/genre with Japanese support
- **Move Books** — Select & move books between boxes
- **Export/Import** — Per-box JSON export/import (replaces contents), full database export
- **Archive System** — Archive boxes to hide from main view; restore or permanently delete
- **Mobile-first UI** — Clean, touch-friendly interface
- **No authentication** — Designed for personal/local use (put it behind a reverse proxy if needed)

## Quick Start

### Option 1: Run directly with Python

```bash
git clone https://github.com/YOUR_USERNAME/bookbox.git
cd bookbox
pip install -r requirements.txt
python main.py
```

Open http://localhost:8000

### Option 2: Docker

```bash
git clone https://github.com/YOUR_USERNAME/bookbox.git
cd bookbox
docker compose up -d
```

Open http://localhost:8000

Data is persisted in a Docker volume (`bookbox-data`).

### Option 3: Docker (without Compose)

```bash
docker build -t bookbox .
docker run -d -p 8000:8000 -v bookbox-data:/app/data --name bookbox bookbox
```

## Installation

### Requirements

- Python 3.12+
- pip

### Step-by-step

1. **Clone the repo**
   ```bash
   git clone https://github.com/YOUR_USERNAME/bookbox.git
   cd bookbox
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run**
   ```bash
   python main.py
   ```
   The app starts on port 8000. The SQLite database (`bookbox.db`) is created automatically on first run with 10 pre-seeded boxes (rox-001 through rox-010).

4. **(Optional) Run as a systemd service**
   ```bash
   sudo cp bookbox.service /etc/systemd/system/
   # Edit the service file to match your paths and username
   sudo systemctl daemon-reload
   sudo systemctl enable --now bookbox
   ```

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `BOOKBOX_DB` | `./bookbox.db` | Path to the SQLite database file |

## Project Structure

```
bookbox/
├── main.py              # FastAPI app — all routes and API endpoints
├── db.py                # SQLite schema, migrations, connection helper
├── suggest.py           # Smart box suggestion engine
├── isbn_lookup.py       # Parallel ISBN lookup (openBD, Google Books, Open Library)
├── requirements.txt     # Python dependencies
├── Dockerfile           # Container build
├── docker-compose.yml   # One-command deployment
├── bookbox.service      # systemd unit file
└── templates/
    ├── index.html       # Homepage — box grid, search, export all
    ├── box.html         # Box detail — book list, memo, move, export/import
    ├── book.html        # Book detail — memo
    ├── add.html         # Manual add form
    ├── scan.html        # Single barcode scan + ISBN lookup
    ├── batch.html       # Batch barcode scan
    └── archived.html    # Archived boxes management
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Homepage |
| `GET` | `/box/{box_id}` | Box detail page |
| `GET` | `/book/{book_id}` | Book detail page |
| `GET` | `/add` | Manual add form |
| `POST` | `/add` | Submit new book |
| `GET` | `/scan` | Barcode scan page |
| `GET` | `/batch` | Batch scan page |
| `GET` | `/archived` | Archived boxes page |
| `POST` | `/api/boxes` | Create a new box |
| `PATCH` | `/api/box/{box_id}` | Rename a box |
| `PATCH` | `/api/box/{box_id}/memo` | Update box memo |
| `POST` | `/api/box/{box_id}/archive` | Archive a box |
| `POST` | `/api/box/{box_id}/restore` | Restore an archived box |
| `DELETE` | `/api/box/{box_id}` | Permanently delete an archived box |
| `PATCH` | `/api/book/{book_id}` | Update book memo |
| `POST` | `/api/books/move` | Move books between boxes |
| `GET` | `/api/search?q=` | Search books |
| `GET` | `/api/suggest-box` | Get box suggestion |
| `GET` | `/api/isbn/{isbn}` | Look up ISBN |
| `POST` | `/api/batch-add` | Batch add books |
| `GET` | `/api/box/{box_id}/export` | Export box as JSON |
| `POST` | `/api/box/{box_id}/import` | Import JSON into box (replaces contents) |
| `GET` | `/api/export` | Export entire database as JSON |

## NFC Integration

Each box page has a **📱 Write NFC** button (visible on devices that support Web NFC — Chrome on Android).

1. Open a box page on your phone
2. Tap **📱 Write NFC**
3. Hold a blank NFC tag against the back of your phone
4. The tag is written with a URL record pointing to that box’s page
5. Stick the tag on the physical box

Now anyone can tap the tag to jump straight to the box’s inventory page.

> **Note:** Web NFC requires HTTPS and Chrome on Android. The button is automatically hidden on unsupported browsers.

## Export Format

Per-box export (`/api/box/{box_id}/export`):
```json
{
  "id": "rox-001",
  "label": "ROX 530m Box #1",
  "archived": false,
  "memo": "Living room shelf",
  "books": [
    {
      "title": "吾輩は猫である",
      "author": "夏目漱石",
      "genre": "Literature",
      "memo": "First edition",
      "created_at": "2024-01-15 10:30:00"
    }
  ]
}
```

Full export (`/api/export`):
```json
{
  "exported_at": "2024-01-15T12:00:00+00:00",
  "boxes": [ ... ]
}
```

Importing a JSON file into a box **replaces all existing books** in that box.

## License

MIT

