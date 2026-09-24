"""Domain helpers for the deliberately small media-repair workflow."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app import models, schemas


ISSUE_TYPES = {
    "wrong_language",
    "incomplete",
    "wrong_content",
    "file_structure",
    "unplayable",
    "quality",
    "metadata",
    "other",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_page_path(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    parsed = urlsplit(value.strip())
    path = parsed.path if parsed.scheme or parsed.netloc else value.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = f"/{path}"
    return path[:500]


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def compute_issue_fingerprint(media_key: str, issue_type: str, report_text: str) -> str:
    source = f"{media_key}|{issue_type}"
    if issue_type == "other":
        source += f"|{_normalized_text(report_text)[:240]}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _reporter_name(user: models.User) -> str:
    return (user.full_name or user.username or f"User {user.id}")[:255]


def _owned_request(
    db: Session,
    request_id: Optional[int],
    current_user: models.User,
) -> Optional[models.BookRequest]:
    if request_id is None:
        return None
    query = db.query(models.BookRequest).filter(models.BookRequest.id == request_id)
    if not current_user.is_admin:
        query = query.filter(models.BookRequest.user_id == current_user.id)
    request = query.first()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    return request


def _owned_download_task(
    db: Session,
    task_id: Optional[int],
    current_user: models.User,
) -> Optional[models.DownloadTask]:
    if task_id is None:
        return None
    query = db.query(models.DownloadTask).filter(models.DownloadTask.id == task_id)
    if not current_user.is_admin:
        query = query.filter(models.DownloadTask.user_id == current_user.id)
    task = query.first()
    if not task:
        raise HTTPException(status_code=404, detail="Download task not found")
    return task


def resolve_media_context(
    db: Session,
    payload: schemas.MediaIssueCreate,
    current_user: models.User,
) -> tuple[Optional[models.Book], Optional[models.BookRequest], Optional[models.DownloadTask], str, Optional[str]]:
    page_path = sanitize_page_path(payload.page_path)
    request = _owned_request(db, payload.request_id, current_user)
    task = _owned_download_task(db, payload.download_task_id, current_user)

    book = None
    if payload.book_id is not None:
        book = db.query(models.Book).filter(models.Book.id == payload.book_id).first()
        if not book:
            raise HTTPException(status_code=404, detail="Book not found")

    if request and (request.book_id != payload.book_id or request.format != payload.format):
        raise HTTPException(status_code=400, detail="Request does not match the reported media")
    if task and (task.book_id != payload.book_id or task.format != payload.format):
        raise HTTPException(status_code=400, detail="Download does not match the reported media")

    if book and not task:
        task_query = db.query(models.DownloadTask).filter(
            models.DownloadTask.book_id == book.id,
            models.DownloadTask.format == payload.format,
        )
        # The completed media is shared even when the task was started by a
        # different requester. We use its identity for deduplication but never
        # expose task details through the user-facing serializer.
        task = task_query.order_by(
            models.DownloadTask.completed_at.desc().nullslast(),
            models.DownloadTask.created_at.desc(),
        ).first()

    if task:
        release_identity = task.info_hash or str(task.id)
        media_key = f"download:{book.id}:{payload.format}:{release_identity}"
    elif book and payload.format == "ebook" and book.booklore_id:
        media_key = f"booklore:{book.booklore_id}:ebook"
    elif book and payload.format == "audiobook" and book.audiobookshelf_id:
        media_key = f"audiobookshelf:{book.audiobookshelf_id}:audiobook"
    elif book and book.booklore_id:
        media_key = f"booklore:{book.booklore_id}:{payload.format}"
    elif book:
        media_key = f"book:{book.id}:{payload.format}"
    else:
        media_key = f"other:user:{current_user.id}:page:{page_path or '/'}"

    return book, request, task, media_key, page_path


def enforce_report_limits(db: Session, current_user: models.User) -> None:
    recent = db.query(models.MediaIssueReport.id).filter(
        models.MediaIssueReport.reporter_user_id == current_user.id,
        models.MediaIssueReport.created_at >= now_utc() - timedelta(hours=1),
    ).count()
    if recent >= 10:
        raise HTTPException(status_code=429, detail="Too many reports submitted; try again later")

    active = db.query(func.count(func.distinct(models.MediaIssueReport.issue_id))).join(
        models.MediaIssue,
        models.MediaIssue.id == models.MediaIssueReport.issue_id,
    ).filter(
        models.MediaIssueReport.reporter_user_id == current_user.id,
        models.MediaIssue.status == "open",
    ).scalar() or 0
    if active >= 25:
        raise HTTPException(status_code=429, detail="Too many open reports")


def _new_report(
    issue: models.MediaIssue,
    payload: schemas.MediaIssueCreate,
    current_user: models.User,
    request: Optional[models.BookRequest],
    task: Optional[models.DownloadTask],
    page_path: Optional[str],
) -> models.MediaIssueReport:
    context = {"page_path": page_path} if page_path else None
    return models.MediaIssueReport(
        issue=issue,
        reporter_user_id=current_user.id,
        reporter_name_snapshot=_reporter_name(current_user),
        report_text=payload.report_text.strip(),
        request_id=request.id if request else None,
        download_task_id=task.id if task else None,
        context_json=json.dumps(context) if context else None,
        is_critical=payload.is_critical,
        critical_explanation=(payload.critical_explanation or "").strip() or None,
    )


def create_or_attach_issue(
    db: Session,
    payload: schemas.MediaIssueCreate,
    current_user: models.User,
) -> tuple[models.MediaIssue, bool, bool]:
    """Return issue, created, and whether this user already reported it."""
    enforce_report_limits(db, current_user)
    book, request, task, media_key, page_path = resolve_media_context(db, payload, current_user)
    fingerprint = compute_issue_fingerprint(media_key, payload.issue_type, payload.report_text)
    timestamp = now_utc()

    issue = db.query(models.MediaIssue).filter(models.MediaIssue.active_key == fingerprint).first()
    if issue:
        existing_report = db.query(models.MediaIssueReport).filter(
            models.MediaIssueReport.issue_id == issue.id,
            models.MediaIssueReport.reporter_user_id == current_user.id,
        ).first()
        if existing_report:
            return issue, False, True
        db.add(_new_report(issue, payload, current_user, request, task, page_path))
        issue.report_count += 1
        issue.last_reported_at = timestamp
        if payload.is_critical:
            issue.critical_report_count += 1
            if not issue.is_critical:
                issue.is_critical = True
                issue.critical_first_reported_at = timestamp
        db.add(models.MediaIssueStatusEvent(
            issue=issue,
            actor_user_id=current_user.id,
            event_type="report_added",
            new_status=issue.status,
        ))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return create_or_attach_issue(db, payload, current_user)
        return issue, False, False

    issue = models.MediaIssue(
        public_id=str(uuid.uuid4()),
        book_id=book.id if book else None,
        format=payload.format,
        download_task_id=task.id if task else None,
        media_key=media_key,
        issue_type=payload.issue_type,
        status="open",
        fingerprint=fingerprint,
        active_key=fingerprint,
        report_count=1,
        is_critical=payload.is_critical,
        critical_report_count=1 if payload.is_critical else 0,
        critical_first_reported_at=timestamp if payload.is_critical else None,
        first_reported_at=timestamp,
        last_reported_at=timestamp,
    )
    db.add(issue)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return create_or_attach_issue(db, payload, current_user)
    db.add(_new_report(issue, payload, current_user, request, task, page_path))
    db.add(models.MediaIssueStatusEvent(
        issue=issue,
        actor_user_id=current_user.id,
        event_type="created",
        new_status="open",
    ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return create_or_attach_issue(db, payload, current_user)
    return issue, True, False


def issue_for_user(db: Session, public_id: str, current_user: models.User) -> models.MediaIssue:
    query = db.query(models.MediaIssue).options(
        joinedload(models.MediaIssue.book),
        joinedload(models.MediaIssue.reports),
    ).filter(models.MediaIssue.public_id == public_id)
    if not current_user.is_admin:
        query = query.join(models.MediaIssueReport).filter(
            models.MediaIssueReport.reporter_user_id == current_user.id
        )
    issue = query.first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    return issue


def _book_summary(book: Optional[models.Book]) -> Optional[dict]:
    if not book:
        return None
    return {
        "id": book.id,
        "title": book.title,
        "author": book.author,
        "cover_url": book.cover_url,
        "hardcover_id": book.hardcover_id,
        "booklore_id": book.booklore_id,
        "audiobookshelf_id": book.audiobookshelf_id,
    }


def issue_reference(issue: models.MediaIssue) -> str:
    return f"MI-{issue.public_id.split('-')[0].upper()}"


def serialize_user_issue(issue: models.MediaIssue, current_user: models.User) -> dict:
    report = next((item for item in issue.reports if item.reporter_user_id == current_user.id), None)
    if not report and not current_user.is_admin:
        raise HTTPException(status_code=404, detail="Issue not found")
    return {
        "public_id": issue.public_id,
        "reference": issue_reference(issue),
        "book": _book_summary(issue.book),
        "format": issue.format,
        "issue_type": issue.issue_type,
        "status": issue.status,
        "is_critical": bool(report.is_critical) if report else issue.is_critical,
        "report_count": issue.report_count,
        "report_text": report.report_text if report else None,
        "critical_explanation": report.critical_explanation if report and report.is_critical else None,
        "submitted_at": report.created_at if report else issue.first_reported_at,
        "last_reported_at": issue.last_reported_at,
        "done_at": issue.done_at,
        "done_note": issue.done_note,
    }


def serialize_admin_issue(issue: models.MediaIssue, include_reports: bool = False) -> dict:
    task = issue.download_task
    data = {
        "public_id": issue.public_id,
        "reference": issue_reference(issue),
        "book": _book_summary(issue.book),
        "format": issue.format,
        "issue_type": issue.issue_type,
        "status": issue.status,
        "is_critical": issue.is_critical,
        "critical_report_count": issue.critical_report_count,
        "critical_first_reported_at": issue.critical_first_reported_at,
        "report_count": issue.report_count,
        "first_reported_at": issue.first_reported_at,
        "last_reported_at": issue.last_reported_at,
        "done_at": issue.done_at,
        "done_note": issue.done_note,
        "download": None,
    }
    if task:
        data["download"] = {
            "id": task.id,
            "release_title": task.release_title,
            "source": task.source,
            "indexer": task.indexer,
            "state": task.state,
            "import_status": task.import_status,
            "final_path": task.final_path,
            "download_path": task.download_path,
            "completed_at": task.completed_at,
        }
    if include_reports:
        data["reports"] = [
            {
                "id": report.id,
                "reporter_user_id": report.reporter_user_id,
                "reporter_name": report.reporter_name_snapshot,
                "report_text": report.report_text,
                "is_critical": report.is_critical,
                "critical_explanation": report.critical_explanation,
                "request_id": report.request_id,
                "download_task_id": report.download_task_id,
                "created_at": report.created_at,
                "updated_at": report.updated_at,
            }
            for report in sorted(issue.reports, key=lambda value: value.created_at)
        ]
    return data


def create_status_event(
    db: Session,
    issue: models.MediaIssue,
    actor_user_id: int,
    event_type: str,
    previous_status: Optional[str],
    new_status: Optional[str],
    note: Optional[str] = None,
) -> models.MediaIssueStatusEvent:
    event = models.MediaIssueStatusEvent(
        issue_id=issue.id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        previous_status=previous_status,
        new_status=new_status,
        note=note,
    )
    db.add(event)
    db.flush()
    return event


def notify_reporters(
    db: Session,
    issue: models.MediaIssue,
    event: models.MediaIssueStatusEvent,
    kind: str,
    message: str,
) -> None:
    reporter_ids = {
        value[0]
        for value in db.query(models.MediaIssueReport.reporter_user_id).filter(
            models.MediaIssueReport.issue_id == issue.id,
            models.MediaIssueReport.reporter_user_id.isnot(None),
        ).all()
    }
    for user_id in reporter_ids:
        db.add(models.IssueNotification(
            recipient_user_id=user_id,
            issue_id=issue.id,
            status_event_id=event.id,
            kind=kind,
            message=message[:500],
        ))
