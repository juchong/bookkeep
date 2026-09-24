"""Administrator repair-list endpoints for media issues."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app import database, models, schemas
from app.auth import require_admin
from app.services.media_issues import (
    compute_issue_fingerprint,
    create_status_event,
    notify_reporters,
    serialize_admin_issue,
)


router = APIRouter()


def _admin_issue(db: Session, public_id: str) -> models.MediaIssue:
    issue = db.query(models.MediaIssue).options(
        joinedload(models.MediaIssue.book),
        joinedload(models.MediaIssue.download_task),
        joinedload(models.MediaIssue.reports),
    ).filter(models.MediaIssue.public_id == public_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    return issue


@router.get("/stats")
def media_issue_stats(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(require_admin),
):
    del current_user
    return {
        "open": db.query(models.MediaIssue).filter(models.MediaIssue.status == "open").count(),
        "critical": db.query(models.MediaIssue).filter(
            models.MediaIssue.status == "open",
            models.MediaIssue.is_critical.is_(True),
        ).count(),
        "done": db.query(models.MediaIssue).filter(models.MediaIssue.status == "done").count(),
    }


@router.get("/")
def list_media_issues(
    status_filter: str = Query("open", alias="status"),
    format_filter: Optional[str] = Query(None, alias="format"),
    issue_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None, max_length=200),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(require_admin),
):
    del current_user
    if status_filter not in {"open", "done", "all"}:
        raise HTTPException(status_code=400, detail="Unsupported issue status")
    if format_filter and format_filter not in {"ebook", "audiobook"}:
        raise HTTPException(status_code=400, detail="Unsupported media format")

    query = db.query(models.MediaIssue).outerjoin(models.Book)
    if status_filter != "all":
        query = query.filter(models.MediaIssue.status == status_filter)
    if format_filter:
        query = query.filter(models.MediaIssue.format == format_filter)
    if issue_type:
        query = query.filter(models.MediaIssue.issue_type == issue_type)
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(or_(
            models.Book.title.ilike(term),
            models.Book.author.ilike(term),
            models.MediaIssue.public_id.ilike(term),
        ))

    total = query.count()
    issues = query.options(
        joinedload(models.MediaIssue.book),
        joinedload(models.MediaIssue.download_task),
    ).order_by(
        models.MediaIssue.is_critical.desc(),
        models.MediaIssue.first_reported_at.asc(),
    ).offset(skip).limit(limit).all()
    return {
        "items": [serialize_admin_issue(issue) for issue in issues],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get("/{public_id}")
def get_media_issue(
    public_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(require_admin),
):
    del current_user
    return serialize_admin_issue(_admin_issue(db, public_id), include_reports=True)


@router.post("/{public_id}/done")
def mark_media_issue_done(
    public_id: str,
    payload: schemas.MediaIssueDone,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(require_admin),
):
    issue = _admin_issue(db, public_id)
    if issue.status == "done":
        return serialize_admin_issue(issue, include_reports=True)
    note = (payload.note or "").strip() or None
    previous_status = issue.status
    issue.status = "done"
    issue.active_key = None
    issue.done_at = datetime.now(timezone.utc)
    issue.done_by_user_id = current_user.id
    issue.done_note = note
    event = create_status_event(db, issue, current_user.id, "done", previous_status, "done", note)
    title = issue.book.title if issue.book else "Your reported issue"
    message = f"{title} was marked done"
    if note:
        message = f"{message}: {note}"
    notify_reporters(db, issue, event, "done", message)
    db.commit()
    return serialize_admin_issue(_admin_issue(db, public_id), include_reports=True)


@router.post("/{public_id}/reopen")
def reopen_media_issue(
    public_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(require_admin),
):
    issue = _admin_issue(db, public_id)
    if issue.status == "open":
        return serialize_admin_issue(issue, include_reports=True)
    conflict = db.query(models.MediaIssue.id).filter(
        models.MediaIssue.active_key == issue.fingerprint,
        models.MediaIssue.id != issue.id,
    ).first()
    if conflict:
        raise HTTPException(status_code=409, detail="A matching open issue already exists")
    previous_status = issue.status
    issue.status = "open"
    issue.active_key = issue.fingerprint
    issue.done_at = None
    issue.done_by_user_id = None
    issue.done_note = None
    event = create_status_event(db, issue, current_user.id, "reopened", previous_status, "open")
    notify_reporters(
        db,
        issue,
        event,
        "reopened",
        f"{issue.book.title if issue.book else 'Your reported issue'} was reopened",
    )
    db.commit()
    return serialize_admin_issue(_admin_issue(db, public_id), include_reports=True)


@router.patch("/{public_id}")
def update_media_issue_classification(
    public_id: str,
    payload: schemas.MediaIssueAdminUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(require_admin),
):
    issue = _admin_issue(db, public_id)
    changed = False
    if payload.issue_type and payload.issue_type != issue.issue_type:
        first_text = issue.reports[0].report_text if issue.reports else ""
        fingerprint = compute_issue_fingerprint(issue.media_key, payload.issue_type, first_text)
        if issue.status == "open":
            conflict = db.query(models.MediaIssue.id).filter(
                models.MediaIssue.active_key == fingerprint,
                models.MediaIssue.id != issue.id,
            ).first()
            if conflict:
                raise HTTPException(status_code=409, detail="A matching open issue already exists")
            issue.active_key = fingerprint
        issue.issue_type = payload.issue_type
        issue.fingerprint = fingerprint
        changed = True
    if payload.is_critical is not None and payload.is_critical != issue.is_critical:
        issue.is_critical = payload.is_critical
        if payload.is_critical:
            issue.critical_first_reported_at = issue.critical_first_reported_at or datetime.now(timezone.utc)
        else:
            issue.critical_report_count = 0
            issue.critical_first_reported_at = None
        changed = True
    if changed:
        db.add(models.MediaIssueStatusEvent(
            issue_id=issue.id,
            actor_user_id=current_user.id,
            event_type="classification_updated",
            new_status=issue.status,
        ))
        db.commit()
    return serialize_admin_issue(_admin_issue(db, public_id), include_reports=True)


@router.delete("/{public_id}/spam", status_code=status.HTTP_204_NO_CONTENT)
def delete_media_issue_as_spam(
    public_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(require_admin),
):
    del current_user
    issue = _admin_issue(db, public_id)
    db.delete(issue)
    db.commit()
    return None
