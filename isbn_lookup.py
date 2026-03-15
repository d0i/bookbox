"""Multi-source ISBN lookup with Japanese book support.

Sources (queried in parallel):
  1. openBD   — Japanese books (api.openbd.jp)
  2. Google Books — good JP + EN coverage
  3. Open Library — EN fallback

Returns a list of candidate dicts: {title, author, genre, source}
"""
import httpx
import asyncio
from typing import Optional

TIMEOUT = 6.0  # seconds per source


async def _openbd(isbn: str, client: httpx.AsyncClient) -> Optional[dict]:
    """openBD — best source for Japanese books."""
    try:
        r = await client.get(f"https://api.openbd.jp/v1/get?isbn={isbn}", timeout=TIMEOUT)
        data = r.json()
        if not data or not data[0]:
            return None
        summary = data[0].get("summary", {})
        title = summary.get("title", "").strip()
        author = summary.get("author", "").strip()
        if not title:
            return None
        # openBD doesn't have a clean genre field; try to extract from C-code
        genre = ""
        onix = data[0].get("onix", {})
        subjects = onix.get("DescriptiveDetail", {}).get("Subject", [])
        for s in subjects:
            text = s.get("SubjectHeadingText", "")
            if text:
                genre = text
                break
        return {"title": title, "author": author, "genre": genre, "source": "openBD"}
    except Exception:
        return None


async def _google_books(isbn: str, client: httpx.AsyncClient) -> Optional[dict]:
    """Google Books API — good international coverage."""
    try:
        r = await client.get(
            "https://www.googleapis.com/books/v1/volumes",
            params={"q": f"isbn:{isbn}", "maxResults": 1},
            timeout=TIMEOUT,
        )
        data = r.json()
        items = data.get("items", [])
        if not items:
            return None
        info = items[0].get("volumeInfo", {})
        title = info.get("title", "").strip()
        subtitle = info.get("subtitle", "").strip()
        if subtitle:
            title = f"{title} — {subtitle}"
        authors = info.get("authors", [])
        author = ", ".join(authors) if authors else ""
        categories = info.get("categories", [])
        genre = categories[0] if categories else ""
        if not title:
            return None
        return {"title": title, "author": author, "genre": genre, "source": "Google Books"}
    except Exception:
        return None


async def _openlibrary(isbn: str, client: httpx.AsyncClient) -> Optional[dict]:
    """Open Library — good EN fallback."""
    try:
        r = await client.get(
            f"https://openlibrary.org/isbn/{isbn}.json",
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        title = data.get("title", "").strip()
        if not title:
            return None
        # Authors require a second lookup; grab from authors key
        author = ""
        author_keys = data.get("authors", [])
        if author_keys:
            key = author_keys[0].get("key", "")
            if key:
                ar = await client.get(f"https://openlibrary.org{key}.json", timeout=TIMEOUT)
                if ar.status_code == 200:
                    author = ar.json().get("name", "")
        subjects = data.get("subjects", [])
        genre = subjects[0] if subjects else ""
        return {"title": title, "author": author, "genre": genre, "source": "Open Library"}
    except Exception:
        return None


async def _lookup_async(isbn: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            _openbd(isbn, client),
            _google_books(isbn, client),
            _openlibrary(isbn, client),
            return_exceptions=True,
        )
    candidates = []
    seen_titles = set()
    for r in results:
        if isinstance(r, dict) and r.get("title"):
            # Deduplicate by normalized title
            norm = r["title"].lower().strip()
            if norm not in seen_titles:
                seen_titles.add(norm)
                candidates.append(r)
    return candidates


def lookup_isbn(isbn: str) -> list[dict]:
    """Synchronous entry point. Returns list of candidate book dicts."""
    isbn = isbn.strip().replace("-", "").replace(" ", "")
    return asyncio.run(_lookup_async(isbn))


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "9784101010014"  # 吾輩は猫である
    results = lookup_isbn(code)
    for r in results:
        print(f"[{r['source']}] {r['title']} / {r['author']} / {r['genre']}")
    if not results:
        print("No results found.")
