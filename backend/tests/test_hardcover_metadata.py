import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Book, BookRequest, User
from app.services.hardcover_metadata import (
    apply_hardcover_metadata,
    extract_hardcover_metadata,
    publication_date,
    upsert_hardcover_book,
)


def make_book(**values):
    defaults = {
        "title": "Existing Title",
        "author": "Existing Author",
        "hardcover_id": 123,
        "published_date": "2020-01-01",
        "description": "Local description",
        "ebook_available": True,
        "downloaded_release_hashes": '["kept"]',
    }
    defaults.update(values)
    return Book(**defaults)


def test_publication_date_never_serializes_none():
    assert publication_date(None, None) is None
    assert publication_date("None", None) is None
    assert publication_date("", 2024) == "2024"


def test_extractor_distinguishes_omitted_and_explicit_null_date():
    assert "published_date" not in extract_hardcover_metadata({"id": 1})
    assert extract_hardcover_metadata(
        {"id": 1, "release_date": None, "release_year": None}
    )["published_date"] is None


def test_extractor_falls_back_to_preferred_edition_date():
    metadata = extract_hardcover_metadata({
        "id": 1,
        "release_date": None,
        "release_year": None,
        "default_cover_edition_id": 20,
        "editions": [
            {"id": 10, "release_date": "2023-01-01", "release_year": 2023},
            {"id": 20, "release_date": "2022-12-27", "release_year": None},
        ],
    })

    assert metadata["published_date"] == "2022-12-27"
    assert metadata["release_year"] == 2022


def test_partial_payload_cannot_erase_existing_metadata():
    book = make_book()
    apply_hardcover_metadata(
        book,
        {"id": 123, "title": "Existing Title", "release_date": None},
        authoritative=False,
    )
    assert book.published_date == "2020-01-01"
    assert book.description == "Local description"


def test_partial_payload_cannot_apply_a_suspicious_identity_mismatch():
    book = make_book(title="Fairy Tale", author="Stephen King")
    apply_hardcover_metadata(
        book,
        {
            "id": 123,
            "title": "Lord of the Flies",
            "description": "Wrong book",
            "contributions": [{"author": {"name": "William Golding"}}],
        },
        authoritative=False,
    )
    assert book.title == "Fairy Tale"
    assert book.description == "Local description"


def test_authoritative_strong_match_records_known_absence():
    book = make_book(title="The Beautiful Place", author="G. Nee")
    confidence = apply_hardcover_metadata(
        book,
        {
            "id": 123,
            "title": "The Beautiful Place",
            "release_date": None,
            "release_year": None,
            "contributions": [{"author": {"id": 9, "name": "G. Nee"}}],
        },
        authoritative=True,
    )
    assert confidence == "strong"
    assert book.published_date is None
    assert book.hardcover_metadata_status == "synced"


def test_likely_match_preserves_local_identity_aliases():
    book = make_book(
        title="Project Hail Mary US",
        author="Andy Weir, narrator",
        isbn="local-isbn",
    )
    confidence = apply_hardcover_metadata(
        book,
        {
            "id": 123,
            "title": "Project Hail Mary",
            "description": "Canonical description",
            "contributions": [{"author": {"name": "Andy Weir"}}],
            "editions": [{"id": 7, "isbn_13": "9780000000000"}],
        },
        authoritative=True,
    )
    assert confidence == "likely"
    assert book.title == "Project Hail Mary US"
    assert book.author == "Andy Weir, narrator"
    assert book.isbn == "local-isbn"
    assert book.description == "Canonical description"
    assert book.hardcover_metadata_status == "alias"


def test_review_match_does_not_overwrite_metadata_or_operational_state():
    book = make_book(
        title="Fairy Tale",
        author="Stephen King",
        published_date="None",
    )
    confidence = apply_hardcover_metadata(
        book,
        {
            "id": 123,
            "title": "Lord of the Flies",
            "description": "Wrong book",
            "contributions": [{"author": {"name": "William Golding"}}],
        },
        authoritative=True,
    )
    assert confidence == "review"
    assert book.title == "Fairy Tale"
    assert book.description == "Local description"
    assert book.published_date is None
    assert book.ebook_available is True
    assert book.downloaded_release_hashes == '["kept"]'
    assert book.hardcover_metadata_status == "review"


def test_default_physical_edition_supplies_missing_isbn():
    book = make_book(title="Dune", author="Frank Herbert", isbn=None)
    apply_hardcover_metadata(
        book,
        {
            "id": 123,
            "title": "Dune",
            "contributions": [{"author": {"name": "Frank Herbert"}}],
            "default_physical_edition_id": 20,
            "default_ebook_edition_id": 10,
            "editions": [
                {"id": 10, "isbn_13": "9781111111111"},
                {"id": 20, "isbn_13": "9782222222222"},
            ],
        },
        authoritative=True,
    )
    assert book.isbn == "9782222222222"
    assert book.default_edition_id == 20


def test_upsert_persists_metadata_without_touching_operational_fields():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        book = make_book(title="Dune", author="Frank Herbert", isbn=None)
        session.add(book)
        session.commit()

        saved, created, confidence = upsert_hardcover_book(
            session,
            {
                "id": 123,
                "title": "Dune",
                "release_year": 1965,
                "release_date": None,
                "description": "Canonical description",
                "contributions": [{"author": {"name": "Frank Herbert"}}],
                "default_physical_edition_id": 20,
                "editions": [{"id": 20, "isbn_13": "9782222222222"}],
            },
            authoritative=True,
        )
        session.commit()

        assert created is False
        assert confidence == "strong"
        assert saved.published_date == "1965"
        assert saved.isbn == "9782222222222"
        assert saved.ebook_available is True
        assert saved.downloaded_release_hashes == '["kept"]'
    finally:
        session.close()


@pytest.mark.asyncio
async def test_metadata_batch_prioritizes_request_books(monkeypatch):
    from app import tasks

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    seed = Book(title="Seed", author="One", hardcover_id=1)
    requested = Book(title="Requested", author="Two", hardcover_id=2)
    user = User(email="reader@example.com", username="reader")
    session.add_all([seed, requested, user])
    session.flush()
    session.add(BookRequest(
        book_id=requested.id,
        user_id=user.id,
        format="ebook",
        status="approved",
    ))
    session.commit()

    selected_ids = []

    async def fake_graphql(query, variables, db):
        selected_ids.extend(variables["ids"])
        return {
            "books": [{
                "id": 2,
                "title": "Requested",
                "release_year": 2024,
                "release_date": None,
                "contributions": [{"author": {"name": "Two"}}],
                "editions": [],
            }]
        }

    monkeypatch.setattr(tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(tasks, "execute_graphql", fake_graphql)
    result = await tasks.sync_missing_metadata(batch_size=1)

    assert selected_ids == [2]
    assert result["synced"] == 1
    check = Session()
    try:
        assert check.query(Book).filter(Book.hardcover_id == 2).one().published_date == "2024"
        assert check.query(Book).filter(Book.hardcover_id == 1).one().hardcover_metadata_status is None
    finally:
        check.close()


@pytest.mark.asyncio
async def test_metadata_batch_counts_likely_matches_as_aliases(monkeypatch):
    from app import tasks

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add(Book(
        title="Project Hail Mary US",
        author="Andy Weir, narrator",
        hardcover_id=123,
    ))
    session.commit()

    async def fake_graphql(query, variables, db):
        return {
            "books": [{
                "id": 123,
                "title": "Project Hail Mary",
                "release_year": 2021,
                "release_date": None,
                "contributions": [{"author": {"name": "Andy Weir"}}],
                "editions": [],
            }]
        }

    monkeypatch.setattr(tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(tasks, "execute_graphql", fake_graphql)
    result = await tasks.sync_missing_metadata(batch_size=1)

    assert result == {
        "selected": 1,
        "synced": 0,
        "alias": 1,
        "review": 0,
        "failed": 0,
    }
    check = Session()
    try:
        saved = check.query(Book).filter(Book.hardcover_id == 123).one()
        assert saved.hardcover_metadata_status == "alias"
        assert saved.published_date == "2021"
    finally:
        check.close()
