"""Multi-source ISBN lookup with Japanese book support.

Sources (queried in parallel):
  1. openBD   — Japanese books (api.openbd.jp)
  2. Google Books — good JP + EN coverage
  3. Open Library — EN fallback

Returns a list of candidate dicts with bibliography fields.
"""
import re
import httpx
import asyncio
from typing import Optional

TIMEOUT = 6.0  # seconds per source


def _extract_year(date_str: str) -> str:
    """Extract 4-digit year from various date formats."""
    if not date_str:
        return ""
    m = re.search(r"(\d{4})", str(date_str))
    return m.group(1) if m else ""


async def _openbd(isbn: str, client: httpx.AsyncClient) -> Optional[dict]:
    """openBD — best source for Japanese books."""
    try:
        r = await client.get(f"https://api.openbd.jp/v1/get?isbn={isbn}", timeout=TIMEOUT)
        data = r.json()
        if not data or not data[0]:
            return None
        item = data[0]
        summary = item.get("summary", {})
        title = summary.get("title", "").strip()
        author = summary.get("author", "").strip()
        if not title:
            return None

        onix = item.get("onix", {})
        desc = onix.get("DescriptiveDetail", {})
        pub_detail = onix.get("PublishingDetail", {})

        # Genre from subjects
        genre = ""
        subjects = desc.get("Subject", [])
        for s in subjects:
            text = s.get("SubjectHeadingText", "")
            if text:
                genre = text
                break

        # Publisher
        publisher = summary.get("publisher", "").strip()

        # Published year
        published_year = ""
        pub_dates = pub_detail.get("PublishingDate", [])
        for pd in pub_dates:
            y = _extract_year(pd.get("Date", ""))
            if y:
                published_year = y
                break

        # Page count — look for ExtentType "02" (pages)
        page_count = None
        extents = desc.get("Extent", [])
        for ext in extents:
            # ExtentType 02 = number of pages of content
            if ext.get("ExtentType") in ("02", "00", 2, 0):
                try:
                    page_count = int(ext.get("ExtentValue", 0))
                except (ValueError, TypeError):
                    pass
                if page_count:
                    break

        # Language
        language = ""
        langs = desc.get("Language", [])
        if langs:
            language = langs[0].get("LanguageCode", "").strip()

        # Thumbnail
        thumbnail_url = summary.get("cover", "").strip()

        return {
            "title": title, "author": author, "genre": genre, "source": "openBD",
            "isbn": summary.get("isbn", isbn).strip(),
            "publisher": publisher,
            "published_year": published_year,
            "page_count": page_count,
            "language": language,
            "thumbnail_url": thumbnail_url,
        }
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

        # ISBN — prefer ISBN_13
        found_isbn = isbn
        for ident in info.get("industryIdentifiers", []):
            if ident.get("type") == "ISBN_13":
                found_isbn = ident.get("identifier", isbn)
                break
            elif ident.get("type") == "ISBN_10":
                found_isbn = ident.get("identifier", isbn)

        # Thumbnail
        thumbnail_url = ""
        img_links = info.get("imageLinks", {})
        thumbnail_url = img_links.get("thumbnail", img_links.get("smallThumbnail", ""))
        # Upgrade to https
        if thumbnail_url.startswith("http://"):
            thumbnail_url = "https://" + thumbnail_url[7:]

        return {
            "title": title, "author": author, "genre": genre, "source": "Google Books",
            "isbn": found_isbn,
            "publisher": info.get("publisher", "").strip(),
            "published_year": _extract_year(info.get("publishedDate", "")),
            "page_count": info.get("pageCount"),
            "language": info.get("language", "").strip(),
            "thumbnail_url": thumbnail_url,
        }
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
        # Authors require a second lookup
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

        # ISBN
        found_isbn = isbn
        isbn_13 = data.get("isbn_13", [])
        isbn_10 = data.get("isbn_10", [])
        if isbn_13:
            found_isbn = isbn_13[0]
        elif isbn_10:
            found_isbn = isbn_10[0]

        # Publisher
        publishers = data.get("publishers", [])
        publisher = publishers[0] if publishers else ""

        # Published year
        published_year = _extract_year(data.get("publish_date", ""))

        # Page count
        page_count = data.get("number_of_pages")

        # Language — format is [{"key": "/languages/eng"}]
        language = ""
        langs = data.get("languages", [])
        if langs:
            lang_key = langs[0].get("key", "")
            lang_code = lang_key.rsplit("/", 1)[-1] if lang_key else ""
            # Map 3-letter to 2-letter for common ones
            lang_map = {"eng": "en", "jpn": "ja", "fre": "fr", "ger": "de", "spa": "es",
                        "ita": "it", "por": "pt", "chi": "zh", "kor": "ko", "rus": "ru"}
            language = lang_map.get(lang_code, lang_code)

        # Thumbnail from covers
        thumbnail_url = ""
        covers = data.get("covers", [])
        if covers:
            thumbnail_url = f"https://covers.openlibrary.org/b/id/{covers[0]}-M.jpg"

        return {
            "title": title, "author": author, "genre": genre, "source": "Open Library",
            "isbn": found_isbn,
            "publisher": publisher,
            "published_year": published_year,
            "page_count": page_count,
            "language": language,
            "thumbnail_url": thumbnail_url,
        }
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
    # Collect all valid results
    all_results = [r for r in results if isinstance(r, dict) and r.get("title")]

    # Find the best value for each field from any source to use as fallback
    fill_fields = ["genre", "publisher", "published_year", "page_count", "language", "thumbnail_url"]
    best = {}
    for f in fill_fields:
        for r in all_results:
            if r.get(f):
                best[f] = r[f]
                break

    # Back-fill missing fields from other sources
    for r in all_results:
        for f in fill_fields:
            if not r.get(f) and f in best:
                r[f] = best[f]

    # Deduplicate by normalized title
    candidates = []
    seen_titles = set()
    for r in all_results:
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
        print(f"  ISBN: {r.get('isbn')} | Publisher: {r.get('publisher')} | Year: {r.get('published_year')}")
        print(f"  Pages: {r.get('page_count')} | Lang: {r.get('language')} | Cover: {r.get('thumbnail_url', '')[:60]}")
    if not results:
        print("No results found.")
