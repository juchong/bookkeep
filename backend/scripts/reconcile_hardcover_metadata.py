#!/usr/bin/env python3
"""Audit or apply the resumable Hardcover metadata reconciliation."""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, or_

from app.database import SessionLocal
from app.models import Book
from app.tasks import sync_missing_metadata


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
            candidate = await lookup_book_by_title_author(book.title, book.author, db)
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write reconciled metadata")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--limit", type=int)
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
    if not args.apply:
        print("dry run only; pass --apply to write changes")
        return
    result = asyncio.run(apply(max(1, min(args.batch_size, 250)), args.limit))
    print("reconciliation", result)
    print("after", audit())


if __name__ == "__main__":
    main()
