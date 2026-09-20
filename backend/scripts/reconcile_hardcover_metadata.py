#!/usr/bin/env python3
"""Audit or apply the resumable Hardcover metadata reconciliation."""

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import case, func, or_

from app.database import SessionLocal
from app.models import Book
from app.services.hardcover_metadata import (
    _normalize_title,
    apply_hardcover_metadata,
    mark_book_unmapped,
    merge_book_operational_state,
    normalize_isbn,
    upsert_hardcover_book,
)
from app.tasks import _select_local_book_match, sync_missing_metadata


def audit() -> dict[str, int]:
    db = SessionLocal()
    try:
        return {
            "total": db.query(func.count(Book.id)).scalar() or 0,
            "pending": db.query(func.count(Book.id)).filter(
                Book.hardcover_id.isnot(None),
                or_(
                    Book.hardcover_metadata_status.is_(None),
                    Book.hardcover_metadata_status == "error",
                ),
            ).scalar() or 0,
            "synced": db.query(func.count(Book.id)).filter(
                Book.hardcover_metadata_status == "synced"
            ).scalar() or 0,
            "alias": db.query(func.count(Book.id)).filter(
                Book.hardcover_metadata_status == "alias"
            ).scalar() or 0,
            "review": db.query(func.count(Book.id)).filter(
                Book.hardcover_metadata_status == "review"
            ).scalar() or 0,
            "unmapped": db.query(func.count(Book.id)).filter(
                Book.hardcover_metadata_status == "unmapped"
            ).scalar() or 0,
            "invalid_publication_date": db.query(func.count(Book.id)).filter(
                or_(
                    Book.published_date.is_(None),
                    func.lower(func.trim(Book.published_date)).in_(
                        ("", "none", "null", "nan", "undefined")
                    ),
                )
            ).scalar() or 0,
        }
    finally:
        db.close()


async def apply(batch_size: int, limit: int | None) -> dict[str, int]:
    totals = {"selected": 0, "synced": 0, "alias": 0, "review": 0, "failed": 0}
    while limit is None or totals["selected"] < limit:
        size = batch_size if limit is None else min(batch_size, limit - totals["selected"])
        result = await sync_missing_metadata(size)
        for key in totals:
            totals[key] += result[key]
        if result["selected"] == 0 or result["failed"]:
            break
    return totals


async def review_matches(limit: int) -> list[dict[str, object]]:
    """Suggest replacement Hardcover IDs for quarantined mappings without writing."""
    from app.routers.hardcover import lookup_book_by_title_author

    db = SessionLocal()
    try:
        books = db.query(Book).filter(
            Book.hardcover_metadata_status == "review"
        ).order_by(Book.id.asc()).limit(limit).all()
        results = []
        for book in books:
            candidate = await lookup_book_by_title_author(
                book.title, book.author, db, isbn=book.isbn
            )
            results.append({
                "book_id": book.id,
                "title": book.title,
                "author": book.author,
                "current_hardcover_id": book.hardcover_id,
                "suggested_hardcover_id": candidate.get("id") if candidate else None,
                "suggested_title": candidate.get("title") if candidate else None,
            })
            await asyncio.sleep(0.25)
        return results
    finally:
        db.close()


async def resolve_review_records(limit: int | None) -> dict[str, int]:
    """Resolve every quarantined row without discarding operational state."""
    from app.routers.hardcover import lookup_book_by_title_author

    summary = {
        "selected": 0,
        "local_merged": 0,
        "api_merged": 0,
        "remapped": 0,
        "unmapped": 0,
        "external_id_conflicts": 0,
        "failed": 0,
    }
    db = SessionLocal()
    try:
        verified_by_title = defaultdict(list)
        verified_by_isbn = defaultdict(list)

        def add_verified(book: Book) -> None:
            title_key = _normalize_title(book.title)
            if title_key and all(item.id != book.id for item in verified_by_title[title_key]):
                verified_by_title[title_key].append(book)
            isbn_key = normalize_isbn(book.isbn)
            if isbn_key and all(item.id != book.id for item in verified_by_isbn[isbn_key]):
                verified_by_isbn[isbn_key].append(book)

        for verified in db.query(Book).filter(
            Book.hardcover_id.isnot(None),
            Book.hardcover_metadata_status.in_(("synced", "alias")),
        ).all():
            add_verified(verified)

        query = (
            db.query(Book)
            .filter(Book.hardcover_metadata_status == "review")
            .order_by(case((Book.requests.any(), 0), else_=1), Book.id.asc())
        )
        books = query.limit(limit).all() if limit else query.all()
        summary["selected"] = len(books)

        for book in books:
            if book.hardcover_metadata_status != "review":
                continue
            try:
                local_match = _select_local_book_match(
                    book.title,
                    book.author,
                    book.isbn,
                    verified_by_title,
                    verified_by_isbn,
                )
                if local_match and local_match.id != book.id:
                    if merge_book_operational_state(db, book, local_match):
                        summary["local_merged"] += 1
                    else:
                        mark_book_unmapped(book)
                        summary["unmapped"] += 1
                        summary["external_id_conflicts"] += 1
                    db.commit()
                    continue

                candidate = await lookup_book_by_title_author(
                    book.title,
                    book.author,
                    db,
                    isbn=book.isbn,
                )
                await asyncio.sleep(0.25)
                if not candidate or not candidate.get("id"):
                    mark_book_unmapped(book)
                    summary["unmapped"] += 1
                    db.commit()
                    continue

                candidate_id = int(candidate["id"])
                target = db.query(Book).filter(
                    Book.hardcover_id == candidate_id,
                    Book.id != book.id,
                ).first()
                if target:
                    apply_hardcover_metadata(
                        target,
                        candidate,
                        authoritative=True,
                        repair_identity=True,
                    )
                    if merge_book_operational_state(db, book, target):
                        add_verified(target)
                        summary["api_merged"] += 1
                    else:
                        mark_book_unmapped(book)
                        summary["unmapped"] += 1
                        summary["external_id_conflicts"] += 1
                else:
                    book.hardcover_id = candidate_id
                    book.isbn = None
                    db.flush()
                    repaired, _, _ = upsert_hardcover_book(
                        db,
                        candidate,
                        authoritative=True,
                        repair_identity=True,
                        is_seed_data=book.is_seed_data,
                    )
                    add_verified(repaired)
                    summary["remapped"] += 1
                db.commit()
            except Exception as exc:
                db.rollback()
                summary["failed"] += 1
                print(
                    f"review resolution failed book_id={book.id} "
                    f"title={book.title!r}: {exc}",
                    file=sys.stderr,
                )
        return summary
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write reconciled metadata")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--resolve-review",
        action="store_true",
        help="rematch, merge, or explicitly unmap every quarantined row",
    )
    parser.add_argument(
        "--review-matches",
        type=int,
        metavar="COUNT",
        help="print read-only rematch suggestions for quarantined books",
    )
    args = parser.parse_args()

    before = audit()
    print("before", before)
    if args.review_matches:
        suggestions = asyncio.run(review_matches(max(1, args.review_matches)))
        print(json.dumps(suggestions, indent=2))
        return
    if args.resolve_review:
        result = asyncio.run(resolve_review_records(args.limit))
        print("review_resolution", result)
        print("after", audit())
        return
    if not args.apply:
        print("dry run only; pass --apply to write changes")
        return
    result = asyncio.run(apply(max(1, min(args.batch_size, 250)), args.limit))
    print("reconciliation", result)
    print("after", audit())


if __name__ == "__main__":
    main()
