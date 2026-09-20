"""Canonical mapping and reconciliation for Hardcover book metadata."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Mapping, Optional

from sqlalchemy.orm import Session

from app.models import Book, BookRequest, DownloadTask


INVALID_TEXT_VALUES = {"", "none", "null", "nan", "undefined"}
IDENTITY_FIELDS = {"title", "author", "isbn"}
TITLE_NOISE = {
    "a", "an", "and", "book", "complete", "edition", "ebook", "illustrated",
    "novel", "of", "retail", "the", "unabridged", "vol", "volume",
}


HARDCOVER_BOOKS_BY_IDS_QUERY = """
query BookkeepMetadataByIds($ids: [Int!]!) {
  books(where: {id: {_in: $ids}}) {
    id
    title
    slug
    release_year
    release_date
    pages
    description
    cached_image
    cached_contributors
    rating
    ratings_count
    users_count
    activities_count
    default_ebook_edition_id
    default_audio_edition_id
    default_physical_edition_id
    default_cover_edition_id
    editions(limit: 25, order_by: {score: desc_nulls_last}) {
      id
      isbn_10
      isbn_13
      release_date
      release_year
      score
    }
    book_series {
      position
      series {
        id
        name
      }
    }
    contributions {
      contribution
      author {
        id
        name
        slug
      }
    }
    taggings(limit: 10) {
      tag {
        tag
      }
    }
  }
}
"""


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return {}


def clean_text(value: Any) -> Optional[str]:
    """Return meaningful text, rejecting sentinel strings produced by old writers."""
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in INVALID_TEXT_VALUES else text


def publication_date(release_date: Any, release_year: Any) -> Optional[str]:
    """Choose a valid publication date without ever serializing Python ``None``."""
    date = clean_text(release_date)
    if date:
        return date
    if release_year is None or isinstance(release_year, bool):
        return None
    try:
        year = int(release_year)
    except (TypeError, ValueError):
        return None
    return str(year) if 0 < year <= 9999 else None


def _normalize_title(value: Any) -> str:
    text = clean_text(value) or ""
    if text.lower() in {"unknown", "unknown title", "untitled"}:
        return ""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    tokens = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    return " ".join(token for token in tokens if token not in TITLE_NOISE)


def _author_tokens(value: Any) -> set[str]:
    text = clean_text(value) or ""
    if text.lower() in {"unknown", "unknown author", "various", "various authors"}:
        return set()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return {
        token
        for token in re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
        if len(token) >= 3
    }


def normalize_isbn(value: Any) -> Optional[str]:
    """Normalize an ISBN-like value for identity comparisons."""
    text = clean_text(value)
    if not text:
        return None
    normalized = re.sub(r"[^0-9Xx]", "", text).upper()
    return normalized if len(normalized) in {10, 13} else None


def authors_match(left: Any, right: Any) -> bool:
    """Return whether two author strings have a meaningful token overlap."""
    left_tokens = _author_tokens(left)
    right_tokens = _author_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    overlap = left_tokens & right_tokens
    if len(overlap) >= 2:
        return True
    # Initials disappear during tokenization, so a one-token name such as
    # "J. K. Rowling" legitimately matches the surname in a full name.
    return len(overlap) == 1 and min(len(left_tokens), len(right_tokens)) == 1


def select_hardcover_search_hit(
    title: Any,
    author: Any,
    hits: Any,
    *,
    isbn: Any = None,
) -> Optional[dict[str, Any]]:
    """Select a search hit only when its identity is strongly supported.

    Hardcover search relevance is not an identity guarantee: a query for a
    work can rank another book in the same series first. ISBN is definitive;
    otherwise require an exact/near-exact title (including alternative titles)
    plus meaningful author agreement.
    """
    wanted_title = _normalize_title(title)
    wanted_author = clean_text(author)
    wanted_isbn = normalize_isbn(isbn)
    candidates: list[tuple[float, int, dict[str, Any]]] = []

    for raw_hit in hits or []:
        hit = _as_dict(raw_hit)
        document = _as_dict(hit.get("document"))
        if not document.get("id"):
            continue

        candidate_isbns = {
            normalized
            for value in document.get("isbns") or []
            if (normalized := normalize_isbn(value))
        }
        isbn_matches = bool(wanted_isbn and wanted_isbn in candidate_isbns)

        raw_titles = [document.get("title"), *(document.get("alternative_titles") or [])]
        candidate_titles = [
            normalized for value in raw_titles
            if (normalized := _normalize_title(value))
        ]
        exact_title = bool(wanted_title and wanted_title in candidate_titles)
        title_ratio = max(
            (SequenceMatcher(None, wanted_title, candidate).ratio()
             for candidate in candidate_titles),
            default=0.0,
        )
        candidate_author = ", ".join(document.get("author_names") or [])
        author_matches = authors_match(wanted_author, candidate_author)

        # Library metadata is not authoritative: embedded ebook ISBNs are
        # sometimes copied from another work.  An ISBN strengthens a match but
        # must not override a contradictory title/author identity.
        title_matches = exact_title or title_ratio >= 0.90
        identity_matches = title_matches and (
            not wanted_author or author_matches
        )

        if isbn_matches and identity_matches:
            score = 300.0
        elif exact_title and author_matches:
            score = 200.0
        elif title_ratio >= 0.90 and author_matches:
            score = 100.0 + title_ratio
        else:
            continue

        popularity = int(document.get("users_count") or 0)
        candidates.append((score, popularity, document))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def _extract_authors(source: Mapping[str, Any]) -> tuple[Optional[str], Optional[int]]:
    contributions = source.get("contributions") or []
    authors: list[str] = []
    author_id = None
    if contributions:
        parsed = [_as_dict(item) for item in contributions]
        author_rows = [
            row for row in parsed
            if clean_text(_as_dict(row.get("author")).get("name"))
            and (not clean_text(row.get("contribution")) or row.get("contribution") == "Author")
        ]
        if not author_rows:
            author_rows = parsed
        for row in author_rows:
            author = _as_dict(row.get("author"))
            name = clean_text(author.get("name"))
            if name:
                authors.append(name)
                if author_id is None:
                    author_id = author.get("id")
    elif source.get("cached_contributors"):
        for item in source.get("cached_contributors") or []:
            row = _as_dict(item)
            author = _as_dict(row.get("author"))
            name = clean_text(author.get("name") or row.get("name"))
            if name:
                authors.append(name)
                if author_id is None:
                    author_id = author.get("id")
    return (", ".join(dict.fromkeys(authors)) or None, author_id)


def _select_isbn(source: Mapping[str, Any]) -> Optional[str]:
    editions = [_as_dict(item) for item in (source.get("editions") or [])]
    if not editions:
        return None
    by_id = {edition.get("id"): edition for edition in editions}
    preferred_ids = (
        source.get("default_physical_edition_id"),
        source.get("default_edition_id"),
        source.get("default_ebook_edition_id"),
    )
    ordered = [by_id[edition_id] for edition_id in preferred_ids if edition_id in by_id]
    ordered.extend(edition for edition in editions if edition not in ordered)
    for edition in ordered:
        isbn = clean_text(edition.get("isbn_13")) or clean_text(edition.get("isbn_10"))
        if isbn:
            return isbn.replace("-", "").replace(" ", "")
    return None


def _select_publication_date(source: Mapping[str, Any]) -> Optional[str]:
    direct = publication_date(source.get("release_date"), source.get("release_year"))
    if direct:
        return direct

    editions = [_as_dict(item) for item in (source.get("editions") or [])]
    by_id = {edition.get("id"): edition for edition in editions}
    preferred_ids = (
        source.get("default_physical_edition_id"),
        source.get("default_ebook_edition_id"),
        source.get("default_audio_edition_id"),
        source.get("default_cover_edition_id"),
        source.get("default_edition_id"),
    )
    ordered = [by_id[edition_id] for edition_id in preferred_ids if edition_id in by_id]
    ordered.extend(edition for edition in editions if edition not in ordered)
    for edition in ordered:
        date = publication_date(
            edition.get("release_date"), edition.get("release_year")
        )
        if date:
            return date
    return None


def extract_hardcover_metadata(source: Any) -> dict[str, Any]:
    """Map only fields actually present in a Hardcover response."""
    data = _as_dict(source)
    mapped: dict[str, Any] = {}

    direct_fields = {
        "title": "title",
        "description": "description",
        "pages": "page_count",
        "rating": "rating",
        "slug": "hardcover_slug",
        "ratings_count": "ratings_count",
        "users_count": "users_count",
        "activities_count": "activities_count",
        "release_year": "release_year",
        "default_physical_edition_id": "default_physical_edition_id",
        "default_ebook_edition_id": "default_ebook_edition_id",
        "default_audio_edition_id": "default_audio_edition_id",
    }
    for source_key, book_key in direct_fields.items():
        if source_key in data:
            value = data.get(source_key)
            if book_key in {"title", "description", "hardcover_slug"}:
                value = clean_text(value)
            mapped[book_key] = value

    if "id" in data:
        mapped["hardcover_id"] = data.get("id")

    if "release_date" in data or "release_year" in data or "editions" in data:
        mapped["published_date"] = _select_publication_date(data)
        if not mapped.get("release_year") and mapped["published_date"]:
            year = mapped["published_date"][:4]
            if year.isdigit():
                mapped["release_year"] = int(year)

    if "cached_image" in data:
        image = _as_dict(data.get("cached_image"))
        mapped["cover_url"] = clean_text(image.get("url"))

    if "contributions" in data or "cached_contributors" in data:
        author, author_id = _extract_authors(data)
        if author:
            mapped["author"] = author
        if author_id is not None:
            mapped["author_id"] = author_id

    if "book_series" in data:
        series_rows = data.get("book_series") or []
        if series_rows:
            row = _as_dict(series_rows[0])
            series = _as_dict(row.get("series"))
            mapped.update({
                "series": clean_text(series.get("name")),
                "series_id": series.get("id"),
                "series_position": row.get("position"),
            })
        else:
            mapped.update({"series": None, "series_id": None, "series_position": None})

    if "taggings" in data:
        genres = []
        for item in data.get("taggings") or []:
            genre = clean_text(_as_dict(_as_dict(item).get("tag")).get("tag"))
            if genre:
                genres.append(genre)
        mapped["genres"] = ", ".join(dict.fromkeys(genres)) or None

    if "default_edition_id" in data:
        mapped["default_edition_id"] = data.get("default_edition_id")
    elif "default_physical_edition_id" in data:
        mapped["default_edition_id"] = data.get("default_physical_edition_id")

    if "editions" in data:
        mapped["isbn"] = _select_isbn(data)

    return mapped


def hardcover_source_matches_identity(
    title: Any,
    author: Any,
    source: Any,
    *,
    isbn: Any = None,
) -> bool:
    """Validate a directly supplied Hardcover ID/slug against library identity."""
    raw_source = _as_dict(source)
    metadata = extract_hardcover_metadata(raw_source)
    source_isbns = []
    for edition in raw_source.get("editions") or []:
        edition = _as_dict(edition)
        source_isbns.extend((edition.get("isbn_13"), edition.get("isbn_10")))

    hit = {
        "document": {
            "id": raw_source.get("id"),
            "title": metadata.get("title"),
            "author_names": [metadata.get("author")] if metadata.get("author") else [],
            "isbns": [value for value in source_isbns if value],
        }
    }
    return select_hardcover_search_hit(title, author, [hit], isbn=isbn) is not None


def mark_book_unmapped(
    book: Book,
    *,
    checked_at: Optional[datetime] = None,
) -> None:
    """Remove an untrusted Hardcover identity while preserving local operations."""
    for field in (
        "hardcover_id",
        "hardcover_slug",
        "author_id",
        "isbn",
        "description",
        "cover_url",
        "genre",
        "published_date",
        "rating",
        "page_count",
        "default_edition_id",
        "default_physical_edition_id",
        "default_ebook_edition_id",
        "default_audio_edition_id",
        "series",
        "series_id",
        "series_position",
        "genres",
        "ratings_count",
        "users_count",
        "activities_count",
        "release_year",
    ):
        setattr(book, field, None)
    book.is_seed_data = False
    book.hardcover_metadata_status = "unmapped"
    book.hardcover_metadata_checked_at = checked_at or datetime.now(timezone.utc)
    book.last_refreshed = checked_at or datetime.now(timezone.utc)


def _release_hashes(value: Any) -> list[Any]:
    if not value:
        return []
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


def merge_book_operational_state(db: Session, source: Book, target: Book) -> bool:
    """Merge a duplicate book into a canonical row without losing local state.

    Returns ``False`` rather than discarding either row when both carry different
    external library IDs that the one-column model cannot represent together.
    """
    if source.id == target.id:
        return True
    for field in ("booklore_id", "audiobookshelf_id"):
        source_value = getattr(source, field)
        target_value = getattr(target, field)
        if source_value and target_value and source_value != target_value:
            return False

    external_values = {
        field: getattr(source, field)
        for field in ("booklore_id", "audiobookshelf_id")
        if getattr(source, field) and not getattr(target, field)
    }
    for field in external_values:
        setattr(source, field, None)
    if external_values:
        db.flush()
        for field, value in external_values.items():
            setattr(target, field, value)

    db.query(BookRequest).filter(BookRequest.book_id == source.id).update(
        {BookRequest.book_id: target.id}, synchronize_session=False
    )
    db.query(DownloadTask).filter(DownloadTask.book_id == source.id).update(
        {DownloadTask.book_id: target.id}, synchronize_session=False
    )

    target.ebook_available = bool(target.ebook_available or source.ebook_available)
    target.audiobook_available = bool(
        target.audiobook_available or source.audiobook_available
    )
    target.is_seed_data = bool(target.is_seed_data or source.is_seed_data)
    if source.booklore_added_on and (
        not target.booklore_added_on
        or source.booklore_added_on < target.booklore_added_on
    ):
        target.booklore_added_on = source.booklore_added_on

    hashes = list(dict.fromkeys(
        [*_release_hashes(target.downloaded_release_hashes),
         *_release_hashes(source.downloaded_release_hashes)]
    ))
    if hashes:
        target.downloaded_release_hashes = json.dumps(hashes)

    db.add(target)
    db.flush()
    db.delete(source)
    db.flush()
    return True


def classify_hardcover_mapping(book: Book, source: Any) -> str:
    """Classify identity confidence before authoritative metadata is applied."""
    data = extract_hardcover_metadata(source)
    source_title = _normalize_title(data.get("title"))
    local_title = _normalize_title(getattr(book, "title", None))
    if not source_title:
        return "likely"
    if not local_title:
        return "strong"
    local_author = _author_tokens(getattr(book, "author", None))
    source_author = _author_tokens(data.get("author"))
    author_matches = bool(local_author and source_author and local_author & source_author)
    if local_title == source_title:
        return "review" if local_author and source_author and not author_matches else "strong"

    title_ratio = SequenceMatcher(None, local_title, source_title).ratio()
    if local_author and source_author and not author_matches:
        return "review"
    if title_ratio >= 0.86 or (title_ratio >= 0.68 and author_matches):
        return "likely"
    return "review"


def apply_hardcover_metadata(
    book: Book,
    source: Any,
    *,
    authoritative: bool,
    repair_identity: bool = False,
    checked_at: Optional[datetime] = None,
) -> str:
    """Apply source metadata without touching Bookkeep operational fields."""
    metadata = extract_hardcover_metadata(source)
    mapping_confidence = classify_hardcover_mapping(book, source)
    confidence = mapping_confidence if authoritative else "partial"

    if mapping_confidence == "review" and not repair_identity:
        if authoritative:
            if clean_text(getattr(book, "published_date", None)) is None:
                book.published_date = None
            book.hardcover_metadata_status = "review"
            book.hardcover_metadata_checked_at = checked_at or datetime.now(timezone.utc)
        return confidence

    preserve_identity = mapping_confidence == "likely" and not repair_identity
    for field, value in metadata.items():
        if field == "hardcover_id":
            continue
        if preserve_identity and field in IDENTITY_FIELDS:
            continue
        if field == "isbn":
            if value and not clean_text(getattr(book, "isbn", None)):
                book.isbn = value
            continue
        if not authoritative and value is None:
            continue
        setattr(book, field, value)

    if authoritative:
        book.hardcover_metadata_status = "alias" if preserve_identity else "synced"
        book.hardcover_metadata_checked_at = checked_at or datetime.now(timezone.utc)
    book.last_refreshed = checked_at or datetime.now(timezone.utc)
    return confidence


def upsert_hardcover_book(
    db: Session,
    source: Any,
    *,
    authoritative: bool = True,
    repair_identity: bool = False,
    is_seed_data: Optional[bool] = None,
) -> tuple[Book, bool, str]:
    """Create or reconcile a Book. The caller owns the transaction."""
    metadata = extract_hardcover_metadata(source)
    hardcover_id = metadata.get("hardcover_id")
    if not hardcover_id:
        raise ValueError("Hardcover metadata is missing an id")

    book = db.query(Book).filter(Book.hardcover_id == hardcover_id).first()
    created = book is None
    if created:
        book = Book(
            hardcover_id=hardcover_id,
            title=metadata.get("title") or "Unknown",
            author=metadata.get("author") or "Unknown Author",
            is_seed_data=bool(is_seed_data),
        )
        db.add(book)

    if metadata.get("isbn") and not clean_text(book.isbn):
        duplicate = db.query(Book.id).filter(
            Book.isbn == metadata["isbn"],
            Book.id != book.id,
        ).first()
        if duplicate:
            source = _as_dict(source)
            source.pop("editions", None)

    confidence = apply_hardcover_metadata(
        book,
        source,
        authoritative=authoritative,
        repair_identity=repair_identity,
    )
    if is_seed_data is True:
        book.is_seed_data = True
    db.flush()
    return book, created, confidence
