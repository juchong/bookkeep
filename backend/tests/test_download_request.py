"""Tests for the opaque manual download request contract."""

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import Base
from app.routers.downloads import DownloadRequest, require_download_authorization
from app.downloads import Release
from app.downloads.release_tokens import InvalidReleaseToken, issue_release_token, resolve_release_token
import pytest


def test_download_request_accepts_only_release_token():
    request = DownloadRequest(
        book_id=1,
        format_type="ebook",
        release_token="opaque",
    )
    assert request.release_token == "opaque"


def test_release_token_is_bound_to_user_book_and_format(monkeypatch):
    monkeypatch.setenv("BOOKKEEP_SECRET_KEY", "test-secret")
    release = Release(
        source="prowlarr",
        title="Example Book",
        download_url="https://prowlarr.invalid/download?apikey=secret",
        protocol="torrent",
        indexer="test",
        size_bytes=123,
    )
    token = issue_release_token(release=release, user_id=7, book_id=11, format_type="ebook")
    payload = resolve_release_token(token, user_id=7, book_id=11, format_type="ebook")
    assert payload["download_url"] == release.download_url
    assert "apikey" not in token
    with pytest.raises(InvalidReleaseToken):
        resolve_release_token(token, user_id=8, book_id=11, format_type="ebook")


def test_non_admin_download_requires_approved_owned_request():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = models.User(email="reader@example.com", username="reader", can_download=True, is_admin=False)
    book = models.Book(title="Example", author="Author")
    db.add_all([user, book])
    db.commit()

    with pytest.raises(HTTPException) as denied:
        require_download_authorization(db, user, book.id, "ebook")
    assert denied.value.status_code == 403

    db.add(models.BookRequest(user_id=user.id, book_id=book.id, format="ebook", status="approved"))
    db.commit()
    require_download_authorization(db, user, book.id, "ebook")


def test_download_permission_revocation_is_enforced():
    user = models.User(id=7, can_download=False, is_admin=False)
    with pytest.raises(HTTPException) as denied:
        require_download_authorization(None, user, 1, "ebook")
    assert denied.value.status_code == 403
