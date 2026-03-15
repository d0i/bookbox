"""Smart box suggestion engine.

Scoring per box:
  +3  for each book by the same author in that box
  +1  for each book in the same genre in that box

Tiebreaker: prefer boxes with fewer books.
Returns (box_id, reason_string) or (None, "") if nothing to suggest.

"""
import sqlite3


def suggest_box(conn: sqlite3.Connection, author: str = "", genre: str = "") -> tuple[str | None, str]:
    author = author.strip()
    genre = genre.strip()
    if not author and not genre:
        return None, ""

    # Get active boxes with their counts
    boxes = conn.execute(
        "SELECT b.id, b.label, COUNT(bk.id) AS book_count "
        "FROM boxes b LEFT JOIN books bk ON bk.box_id = b.id "
        "WHERE b.archived = 0 GROUP BY b.id"
    ).fetchall()

    scores: list[tuple[float, int, str, str, list[str]]] = []  # (score, -count, box_id, label, reasons)

    for box in boxes:
        score = 0.0
        reasons = []

        if author:
            author_count = conn.execute(
                "SELECT COUNT(*) FROM books WHERE box_id = ? AND LOWER(author) = LOWER(?)",
                (box["id"], author),
            ).fetchone()[0]
            if author_count:
                score += author_count * 3
                reasons.append(f"{author_count} book{'s' if author_count > 1 else ''} by same author")

        if genre:
            genre_count = conn.execute(
                "SELECT COUNT(*) FROM books WHERE box_id = ? AND LOWER(genre) = LOWER(?)",
                (box["id"], genre),
            ).fetchone()[0]
            if genre_count:
                score += genre_count * 1
                reasons.append(f"{genre_count} book{'s' if genre_count > 1 else ''} in same genre")

        if score > 0:
            scores.append((score, -box["book_count"], box["id"], box["label"], reasons))

    if not scores:
        # No match — suggest the box with fewest books
        all_boxes = [(box["book_count"], box["id"], box["label"]) for box in boxes]
        if all_boxes:
            all_boxes.sort()
            box_id, label = all_boxes[0][1], all_boxes[0][2]
            return box_id, f"{label} has the most space"
        return None, ""

    scores.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best = scores[0]
    reason = f"{best[3]}: {', '.join(best[4])}"
    return best[2], reason
