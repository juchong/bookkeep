import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Book, BookRequest, DownloadTask, User
from app.services.hardcover_metadata import (
    authors_match,
    apply_hardcover_metadata,
    extract_hardcover_metadata,
    hardcover_source_matches_identity,
    mark_book_unmapped,
    merge_book_operational_state,
    publication_date,
    select_hardcover_search_hit,
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


def search_hit(book_id, title, authors, **extra):
    return {"document": {
        "id": str(book_id),
        "title": title,
        "author_names": authors,
        **extra,
    }}


def test_search_hit_selection_does_not_trust_first_series_result():
    hits = [
        search_hit(427363, "God Emperor of Dune", ["Frank Herbert"]),
        search_hit(2046, "Dune", ["Frank Herbert"]),
    ]

    selected = select_hardcover_search_hit("Dune", "Frank Herbert", hits)

    assert selected["id"] == "2046"


def test_search_hit_selection_rejects_wrong_author_for_same_title():
    hits = [search_hit(1, "Home", ["Marilynne Robinson"])]

    assert select_hardcover_search_hit("Home", "Toni Morrison", hits) is None


def test_search_hit_selection_accepts_alternative_title_and_isbn():
    hits = [search_hit(
        2,
        "Harry Potter and the Philosopher's Stone",
        ["J. K. Rowling"],
        alternative_titles=["Harry Potter and the Sorcerer's Stone"],
        isbns=["978-0-7475-3269-9"],
    )]

    by_title = select_hardcover_search_hit(
        "Harry Potter and the Sorcerer's Stone", "J.K. Rowling", hits
    )
    by_isbn = select_hardcover_search_hit(
        "Harry Potter and the Philosopher's Stone",
        "J.K. Rowling",
        hits,
        isbn="9780747532699",
    )

    assert by_title["id"] == "2"
    assert by_isbn["id"] == "2"


def test_search_hit_selection_rejects_conflicting_title_despite_isbn():
    hits = [search_hit(
        110636,
        "Windhaven",
        ["George R. R. Martin", "Lisa Tuttle"],
        isbns=["9780553386177"],
    )]

    assert select_hardcover_search_hit(
        "A Feast for Crows",
        "George R. R. Martin",
        hits,
        isbn="9780553386177",
    ) is None


def test_direct_hardcover_id_must_match_library_identity():
    source = {
        "id": 1218835,
        "title": "Crocodile",
        "contributions": [{"author": {"name": "Dan Wylie"}}],
        "editions": [],
    }

    assert hardcover_source_matches_identity("Crocodile", "Dan Wylie", source)
    assert not hardcover_source_matches_identity(
        "Crocodile 000 (2025) (digital)", "Dan Wylie", source
    )


def test_author_matching_requires_more_than_a_shared_common_first_name():
    assert authors_match("J. K. Rowling", "Joanne Rowling")
    assert not authors_match("James Patterson", "James Rollins")


def test_local_identity_index_revalidates_mutated_book_keys():
    from app.tasks import _booklore_identity_matches, _select_local_book_match

    book = make_book(
        title="The Rules of Attraction",
        author="Bret Easton Ellis",
        hardcover_metadata_status="synced",
    )
    stale_index = {
        "american psycho": [book],
        "rules attraction": [book],
    }

    assert _select_local_book_match(
        "American Psycho", "Bret Easton Ellis", None, stale_index, {}
    ) is None
    assert _select_local_book_match(
        "The Rules of Attraction", "Bret Easton Ellis", None, stale_index, {}
    ) is book
    assert _booklore_identity_matches(
        book, "The Rules of Attraction", "Bret Easton Ellis"
    )


def test_local_isbn_match_cannot_override_conflicting_title():
    from app.tasks import _booklore_identity_matches, _select_local_book_match

    windhaven = make_book(
        title="Windhaven",
        author="George R.R. Martin, Lisa Tuttle",
        isbn="9780553386177",
        hardcover_metadata_status="synced",
    )

    assert _select_local_book_match(
        "A Feast for Crows",
        "George R. R. Martin",
        "9780553386177",
        {},
        {"9780553386177": [windhaven]},
    ) is None
    assert not _booklore_identity_matches(
        windhaven, "A Feast for Crows", "George R. R. Martin"
    )


def test_orphaned_booklore_mappings_are_detached():
    from app.tasks import _detach_orphaned_booklore_mappings

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db_session = sessionmaker(bind=engine)()
    current = make_book(hardcover_id=9001, booklore_id=101)
    orphaned = make_book(hardcover_id=9002, booklore_id=102)
    db_session.add_all([current, orphaned])
    db_session.commit()

    detached = _detach_orphaned_booklore_mappings(db_session, [101])
    db_session.flush()

    assert detached == 1
    assert current.booklore_id == 101
    assert orphaned.booklore_id is None


def test_duplicate_merge_preserves_requests_downloads_and_local_state():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    user = User(
        email="merge@example.com",
        username="merge-user",
        hashed_password="x",
    )
    target = make_book(
        hardcover_id=7001,
        title="Canonical",
        ebook_available=False,
        downloaded_release_hashes='["target"]',
        hardcover_metadata_status="synced",
    )
    source = make_book(
        hardcover_id=7002,
        title="Canonical",
        ebook_available=True,
        audiobook_available=True,
        audiobookshelf_id="audio-source",
        downloaded_release_hashes='["source"]',
        hardcover_metadata_status="review",
    )
    session.add_all([user, target, source])
    session.flush()
    request = BookRequest(
        book_id=source.id,
        user_id=user.id,
        format="ebook",
        status="available",
    )
    task = DownloadTask(book_id=source.id, format="ebook", source="manual")
    session.add_all([request, task])
    session.commit()

    assert merge_book_operational_state(session, source, target)
    session.commit()

    assert session.get(Book, source.id) is None
    assert request.book_id == target.id
    assert task.book_id == target.id
    assert target.ebook_available is True
    assert target.audiobook_available is True
    assert target.audiobookshelf_id == "audio-source"
    assert set(json.loads(target.downloaded_release_hashes)) == {
        "target", "source"
    }


def test_duplicate_merge_refuses_conflicting_external_library_ids():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    target = make_book(hardcover_id=7101, audiobookshelf_id="target-audio")
    source = make_book(hardcover_id=7102, audiobookshelf_id="source-audio")
    session.add_all([target, source])
    session.commit()

    assert not merge_book_operational_state(session, source, target)
    assert session.get(Book, source.id) is source


def test_mark_unmapped_removes_bad_metadata_but_preserves_operations():
    book = make_book(
        hardcover_id=7201,
        hardcover_slug="wrong",
        isbn="wrong-isbn",
        audiobookshelf_id="keep-audio",
        ebook_available=True,
        downloaded_release_hashes='["keep"]',
        hardcover_metadata_status="review",
    )

    mark_book_unmapped(book)

    assert book.title == "Existing Title"
    assert book.hardcover_id is None
    assert book.hardcover_slug is None
    assert book.isbn is None
    assert book.description is None
    assert book.hardcover_metadata_status == "unmapped"
    assert book.audiobookshelf_id == "keep-audio"
    assert book.ebook_available is True
    assert book.downloaded_release_hashes == '["keep"]'


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


def test_explicit_identity_repair_restores_canonical_fields_only():
    book = make_book(
        title="Different Seasons",
        author="Stephen King",
        hardcover_id=376341,
        ebook_available=True,
        downloaded_release_hashes='["kept"]',
    )

    confidence = apply_hardcover_metadata(
        book,
        {
            "id": 376341,
            "title": "The Stand",
            "description": "Canonical description",
            "contributions": [{"author": {"name": "Stephen King"}}],
        },
        authoritative=True,
        repair_identity=True,
    )

    assert confidence == "review"
    assert book.title == "The Stand"
    assert book.author == "Stephen King"
    assert book.description == "Canonical description"
    assert book.hardcover_metadata_status == "synced"
    assert book.ebook_available is True
    assert book.downloaded_release_hashes == '["kept"]'


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
