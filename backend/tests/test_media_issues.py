"""Tests for media issue deduplication, privacy, Critical, and Done notifications."""

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pytest

from app import models, schemas
from app.database import Base
from app.routers.admin_media_issues import mark_media_issue_done
from app.routers.notifications import read_notification
from app.routers.requests import get_request
from app.services.media_issues import create_or_attach_issue, issue_for_user, serialize_user_issue


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def records(db):
    reporter = models.User(email="reader@example.com", username="reader")
    second = models.User(email="second@example.com", username="second")
    outsider = models.User(email="outside@example.com", username="outside")
    admin = models.User(email="admin@example.com", username="admin", is_admin=True)
    book = models.Book(title="Example Book", author="Example Author")
    db.add_all([reporter, second, outsider, admin, book])
    db.flush()
    task = models.DownloadTask(
        book_id=book.id,
        user_id=reporter.id,
        format="audiobook",
        source="prowlarr",
        release_title="Example.Book.English",
        info_hash="same-release",
        state="complete",
    )
    request = models.BookRequest(
        book_id=book.id,
        user_id=reporter.id,
        format="audiobook",
        status="available",
    )
    db.add_all([task, request])
    db.commit()
    return reporter, second, outsider, admin, book, task, request


def test_duplicate_reports_share_one_issue_and_promote_critical(db, records):
    reporter, second, _outsider, _admin, book, task, _request = records
    first, created, already = create_or_attach_issue(
        db,
        schemas.MediaIssueCreate(
            book_id=book.id,
            format="audiobook",
            issue_type="wrong_language",
            report_text="This audiobook is not in English.",
            download_task_id=task.id,
        ),
        reporter,
    )
    assert created is True
    assert already is False

    duplicate, created, already = create_or_attach_issue(
        db,
        schemas.MediaIssueCreate(
            book_id=book.id,
            format="audiobook",
            issue_type="wrong_language",
            report_text="The narration is in another language.",
            is_critical=True,
            critical_explanation="The included audio contains violent material from another title.",
        ),
        second,
    )
    assert duplicate.id == first.id
    assert created is False
    assert already is False
    db.refresh(first)
    assert first.report_count == 2
    assert first.is_critical is True
    assert first.critical_report_count == 1


def test_user_serializer_never_exposes_other_report_text(db, records):
    reporter, second, _outsider, _admin, book, _task, _request = records
    issue, *_ = create_or_attach_issue(
        db,
        schemas.MediaIssueCreate(
            book_id=book.id,
            format="ebook",
            issue_type="incomplete",
            report_text="My private first report.",
        ),
        reporter,
    )
    create_or_attach_issue(
        db,
        schemas.MediaIssueCreate(
            book_id=book.id,
            format="ebook",
            issue_type="incomplete",
            report_text="My private second report.",
        ),
        second,
    )
    first_view = serialize_user_issue(issue_for_user(db, issue.public_id, reporter), reporter)
    second_view = serialize_user_issue(issue_for_user(db, issue.public_id, second), second)
    assert first_view["report_text"] == "My private first report."
    assert second_view["report_text"] == "My private second report."


def test_unrelated_user_cannot_read_issue(db, records):
    reporter, _second, outsider, _admin, book, _task, _request = records
    issue, *_ = create_or_attach_issue(
        db,
        schemas.MediaIssueCreate(
            book_id=book.id,
            format="ebook",
            issue_type="quality",
            report_text="The scan is unreadable.",
        ),
        reporter,
    )
    with pytest.raises(HTTPException) as denied:
        issue_for_user(db, issue.public_id, outsider)
    assert denied.value.status_code == 404


def test_done_notifies_all_reporters_and_notification_is_owner_scoped(db, records):
    reporter, second, outsider, admin, book, _task, _request = records
    issue, *_ = create_or_attach_issue(
        db,
        schemas.MediaIssueCreate(
            book_id=book.id,
            format="ebook",
            issue_type="unplayable",
            report_text="The file will not open.",
        ),
        reporter,
    )
    create_or_attach_issue(
        db,
        schemas.MediaIssueCreate(
            book_id=book.id,
            format="ebook",
            issue_type="unplayable",
            report_text="The ebook is corrupt.",
        ),
        second,
    )

    result = mark_media_issue_done(
        issue.public_id,
        schemas.MediaIssueDone(note="Replaced with a working EPUB."),
        db,
        admin,
    )
    assert result["status"] == "done"
    notifications = db.query(models.IssueNotification).all()
    assert {item.recipient_user_id for item in notifications} == {reporter.id, second.id}

    with pytest.raises(HTTPException) as denied:
        read_notification(notifications[0].id, db, outsider)
    assert denied.value.status_code == 404


def test_request_detail_is_owner_or_admin_only(db, records):
    reporter, _second, outsider, admin, _book, _task, request = records
    assert get_request(request.id, db, reporter).id == request.id
    assert get_request(request.id, db, admin).id == request.id
    with pytest.raises(HTTPException) as denied:
        get_request(request.id, db, outsider)
    assert denied.value.status_code == 404
