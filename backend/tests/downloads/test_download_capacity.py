from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import Base
from app.downloads import Release
from app.downloads.orchestrator import DownloadCapacityError, DownloadOrchestrator, DuplicateDownloadError


def make_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def add_book(db):
    book = models.Book(title="Example", author="Author")
    db.add(book)
    db.commit()
    return book


def release(url="https://example.test/file.epub", size=1024):
    return Release(source="direct", title="Example", download_url=url, protocol="direct", size_bytes=size)


def test_task_creation_enforces_shared_capacity(monkeypatch):
    db = make_db()
    book = add_book(db)
    db.add(models.AutoDownloadSettings(id=1, max_active_downloads=1))
    db.add(models.DownloadTask(book_id=book.id, format="ebook", source="direct", protocol="direct", state="downloading"))
    db.commit()
    monkeypatch.setattr("app.downloads.orchestrator.validate_outbound_url", lambda *args, **kwargs: None)

    with pytest.raises(DownloadCapacityError):
        DownloadOrchestrator(db).create_download_task(book, release(), "ebook")


def test_task_creation_rejects_duplicate_active_release(monkeypatch):
    db = make_db()
    book = add_book(db)
    monkeypatch.setattr("app.downloads.orchestrator.validate_outbound_url", lambda *args, **kwargs: None)
    orchestrator = DownloadOrchestrator(db)
    orchestrator.create_download_task(book, release(), "ebook")
    with pytest.raises(DuplicateDownloadError):
        orchestrator.create_download_task(book, release(), "ebook")


def test_direct_handler_uses_configured_size_limit():
    from app.downloads.handlers.direct import DirectHandler

    db = make_db()
    db.add(models.AutoDownloadSettings(id=1, ebook_max_size_mb=12.5, audiobook_max_size_mb=100))
    db.commit()
    assert DirectHandler(db)._max_download_bytes(SimpleNamespace(format="ebook")) == int(12.5 * 1024 * 1024)
