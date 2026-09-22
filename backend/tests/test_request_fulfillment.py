"""Tests for approved-request statistics and automatic fulfillment."""
from datetime import datetime, timezone
from unittest.mock import Mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app import models
from app.downloads import Release
from app.downloads.prowlarr.api import ProwlarrClient
from app.downloads.prowlarr.source import ProwlarrSource
from app.services import request_fulfillment as fulfillment


def make_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def add_settings(db, **overrides):
    values = {
        "id": 1,
        "enabled": True,
        "dry_run": True,
        "process_existing_backlog": True,
        "interval_seconds": 900,
        "batch_size": 5,
        "max_active_downloads": 10,
        "minimum_score": 70,
        "ebook_formats_json": '["epub", "azw3", "mobi", "pdf"]',
        "audiobook_formats_json": '["m4b", "mp3", "flac"]',
        "preferred_languages_json": '["en"]',
        "protocol_order_json": '["usenet", "torrent"]',
        "minimum_seeders": 1,
        "ebook_min_size_mb": 0.1,
        "ebook_max_size_mb": 500,
        "audiobook_min_size_mb": 10,
        "audiobook_max_size_mb": 5000,
        "retry_schedule_json": '[900, 3600, 21600, 86400]',
        "categoryless_fallback": True,
    }
    values.update(overrides)
    settings = models.AutoDownloadSettings(**values)
    db.add(settings)
    db.commit()
    return settings


def add_user_and_book(db, *, title="Example Book", available=False):
    user = models.User(
        email=f"{title.replace(' ', '').lower()}@example.com",
        username=title.replace(" ", "").lower(),
        hashed_password="unused",
        is_admin=True,
    )
    book = models.Book(
        title=title,
        author="Example Author",
        ebook_available=available,
        audiobook_available=False,
    )
    db.add_all([user, book])
    db.commit()
    return user, book


def test_request_statistics_are_server_scoped_and_identify_eligible_work():
    factory = make_session_factory()
    db = factory()
    user, missing_book = add_user_and_book(db, title="Missing")
    _, available_book = add_user_and_book(db, title="Available", available=True)
    db.add_all([
        models.BookRequest(book_id=missing_book.id, user_id=user.id, format="ebook", status="approved"),
        models.BookRequest(book_id=available_book.id, user_id=user.id, format="ebook", status="approved"),
        models.BookRequest(book_id=available_book.id, user_id=user.id, format="audiobook", status="not_found"),
    ])
    db.commit()

    stats = fulfillment.get_request_statistics(db, user)

    assert stats["total"] == 3
    assert stats["approved"] == 2
    assert stats["not_found"] == 1
    assert stats["eligible_for_auto_search"] == 1
    assert stats["stale_available"] == 1
    assert stats["processed"] == 1


def test_candidate_ranking_uses_configured_format_and_protocol_preferences():
    factory = make_session_factory()
    db = factory()
    _, book = add_user_and_book(db)
    settings = add_settings(db, minimum_score=0)
    releases = [
        Release(
            source="prowlarr",
            title="Example Book EPUB",
            download_url="https://example.invalid/epub",
            protocol="usenet",
            size_bytes=5 * 1024 * 1024,
            format="epub",
            language="en",
            quality_score=55,
        ),
        Release(
            source="prowlarr",
            title="Example Book MOBI",
            download_url="https://example.invalid/mobi",
            protocol="torrent",
            size_bytes=5 * 1024 * 1024,
            seeders=20,
            format="mobi",
            language="en",
            quality_score=60,
        ),
    ]

    ranked, rejected = fulfillment.rank_candidates(db, book, "ebook", releases, settings)

    assert rejected == 0
    assert ranked[0].release.format == "epub"
    assert ranked[0].details["format_bonus"] == 20
    assert ranked[0].details["protocol_bonus"] == 5


def test_candidate_ranking_rejects_formats_that_are_not_allowed():
    factory = make_session_factory()
    db = factory()
    _, book = add_user_and_book(db)
    settings = add_settings(db, minimum_score=0, ebook_formats_json='["epub"]')
    releases = [
        Release(
            source="prowlarr",
            title="Example Book EPUB",
            download_url="https://example.invalid/allowed",
            protocol="usenet",
            size_bytes=5 * 1024 * 1024,
            format="epub",
            language="en",
            quality_score=50,
        ),
        Release(
            source="prowlarr",
            title="Example Book PDF",
            download_url="https://example.invalid/disallowed",
            protocol="usenet",
            size_bytes=5 * 1024 * 1024,
            format="pdf",
            language="en",
            quality_score=95,
        ),
        Release(
            source="prowlarr",
            title="Example Book Unknown Format",
            download_url="https://example.invalid/unknown",
            protocol="usenet",
            size_bytes=5 * 1024 * 1024,
            format=None,
            language="en",
            quality_score=95,
        ),
    ]

    ranked, rejected = fulfillment.rank_candidates(db, book, "ebook", releases, settings)

    assert rejected == 2
    assert [candidate.release.format for candidate in ranked] == ["epub"]


def test_candidate_ranking_allows_retry_after_interrupted_client_handoff():
    factory = make_session_factory()
    db = factory()
    _, book = add_user_and_book(db)
    settings = add_settings(db, minimum_score=0)
    release = Release(
        source="prowlarr",
        title="Example Book EPUB",
        download_url="https://example.invalid/interrupted",
        protocol="torrent",
        size_bytes=5 * 1024 * 1024,
        seeders=20,
        format="epub",
        language="en",
        quality_score=60,
    )
    db.add(models.DownloadTask(
        book_id=book.id,
        format="ebook",
        source="prowlarr",
        download_url=release.download_url,
        protocol="torrent",
        state="error",
        info_hash=fulfillment.hashlib.sha256(release.download_url.encode()).hexdigest()[:16],
        client_download_id=None,
    ))
    db.commit()

    ranked, rejected = fulfillment.rank_candidates(db, book, "ebook", [release], settings)

    assert rejected == 0
    assert [candidate.release for candidate in ranked] == [release]


def test_dry_run_records_decision_without_mutating_request(monkeypatch):
    factory = make_session_factory()
    db = factory()
    user, book = add_user_and_book(db)
    add_settings(db, minimum_score=60)
    request = models.BookRequest(
        book_id=book.id,
        user_id=user.id,
        format="ebook",
        status="approved",
        search_claimed_at=datetime.now(timezone.utc),
    )
    db.add(request)
    db.commit()
    request_id = request.id
    db.close()

    monkeypatch.setattr(fulfillment, "SessionLocal", factory)
    monkeypatch.setattr(
        fulfillment.DownloadOrchestrator,
        "search_releases",
        lambda self, *args, **kwargs: [
            Release(
                source="prowlarr",
                title="Example Book EPUB",
                download_url="https://example.invalid/epub",
                protocol="usenet",
                size_bytes=5 * 1024 * 1024,
                format="epub",
                language="en",
                quality_score=60,
            )
        ],
    )

    result = fulfillment._process_one_request(request_id, dry_run=True)

    check = factory()
    stored_request = check.get(models.BookRequest, request_id)
    attempt = check.query(models.FulfillmentAttempt).one()
    assert result["outcome"] == "would_start"
    assert stored_request.status == "approved"
    assert stored_request.auto_search_attempts == 0
    assert stored_request.next_search_at is None
    assert stored_request.search_claimed_at is None
    assert attempt.outcome == "would_start"
    assert attempt.dry_run is True


def test_no_candidate_schedules_retry_for_live_run(monkeypatch):
    factory = make_session_factory()
    db = factory()
    user, book = add_user_and_book(db)
    add_settings(db)
    request = models.BookRequest(book_id=book.id, user_id=user.id, format="ebook", status="approved")
    db.add(request)
    db.commit()
    request_id = request.id
    db.close()

    monkeypatch.setattr(fulfillment, "SessionLocal", factory)
    monkeypatch.setattr(
        fulfillment.DownloadOrchestrator,
        "search_releases",
        lambda self, *args, **kwargs: [],
    )

    result = fulfillment._process_one_request(request_id, dry_run=False)

    check = factory()
    stored_request = check.get(models.BookRequest, request_id)
    assert result["outcome"] == "no_candidate"
    assert stored_request.status == "approved"
    assert stored_request.auto_search_attempts == 1
    assert stored_request.next_search_at is not None
    assert "No candidate" in stored_request.last_search_error


def test_live_run_links_one_task_and_marks_request_processing(monkeypatch):
    factory = make_session_factory()
    db = factory()
    user, book = add_user_and_book(db)
    add_settings(db, minimum_score=60)
    request = models.BookRequest(book_id=book.id, user_id=user.id, format="ebook", status="approved")
    db.add(request)
    db.commit()
    request_id = request.id
    db.close()

    monkeypatch.setattr(fulfillment, "SessionLocal", factory)
    monkeypatch.setattr(
        fulfillment.DownloadOrchestrator,
        "search_releases",
        lambda self, *args, **kwargs: [
            Release(
                source="prowlarr",
                title="Example Book EPUB",
                download_url="https://example.invalid/epub-live",
                protocol="usenet",
                size_bytes=5 * 1024 * 1024,
                format="epub",
                language="en",
                quality_score=60,
            )
        ],
    )
    monkeypatch.setattr(fulfillment.DownloadOrchestrator, "start_download", lambda self, task_id: True)

    result = fulfillment._process_one_request(request_id, dry_run=False)

    check = factory()
    stored_request = check.get(models.BookRequest, request_id)
    task = check.query(models.DownloadTask).one()
    assert result["outcome"] == "started"
    assert stored_request.status == "processing"
    assert stored_request.download_task_id == task.id
    assert task.request_id == stored_request.id
    assert check.query(models.FulfillmentAttempt).one().outcome == "started"


def test_bulk_search_stops_only_after_a_valid_result():
    source = ProwlarrSource(base_url="http://prowlarr.invalid", api_key="test")
    source.client = Mock(spec=ProwlarrClient)
    source.stop_after_first_results = True
    source.client.search_with_retry.side_effect = [
        [{
            "title": "A Different Book [EPUB]",
            "downloadUrl": "https://example.invalid/wrong",
            "size": 2 * 1024 * 1024,
            "protocol": "torrent",
            "seeders": 5,
            "categories": [{"id": 7000}],
        }],
        [{
            "title": "Example Book [EPUB]",
            "downloadUrl": "https://example.invalid/right",
            "size": 2 * 1024 * 1024,
            "protocol": "torrent",
            "seeders": 5,
            "categories": [{"id": 7000}],
        }],
    ]

    results = source.search(title="Example Book", format_type="ebook")

    assert len(results) == 1
    assert results[0].download_url.endswith("/right")
    assert source.client.search_with_retry.call_count == 2
