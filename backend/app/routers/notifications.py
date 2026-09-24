"""In-app media issue notification inbox."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app import database, models
from app.auth import get_current_user
from app.services.media_issues import issue_reference


router = APIRouter()


def _serialize(notification: models.IssueNotification) -> dict:
    issue = notification.issue
    return {
        "id": notification.id,
        "kind": notification.kind,
        "message": notification.message,
        "read_at": notification.read_at,
        "created_at": notification.created_at,
        "issue": {
            "public_id": issue.public_id,
            "reference": issue_reference(issue),
            "status": issue.status,
            "book_title": issue.book.title if issue.book else None,
            "format": issue.format,
            "issue_type": issue.issue_type,
        },
    }


@router.get("/")
def list_notifications(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    unread_only: bool = Query(False),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.IssueNotification).filter(
        models.IssueNotification.recipient_user_id == current_user.id
    )
    if unread_only:
        query = query.filter(models.IssueNotification.read_at.is_(None))
    total = query.count()
    notifications = query.options(
        joinedload(models.IssueNotification.issue).joinedload(models.MediaIssue.book)
    ).order_by(models.IssueNotification.created_at.desc()).offset(skip).limit(limit).all()
    return {
        "items": [_serialize(item) for item in notifications],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get("/unread-count")
def unread_notification_count(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    count = db.query(models.IssueNotification).filter(
        models.IssueNotification.recipient_user_id == current_user.id,
        models.IssueNotification.read_at.is_(None),
    ).count()
    return {"count": count}


@router.post("/read-all")
def read_all_notifications(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    read_at = datetime.now(timezone.utc)
    updated = db.query(models.IssueNotification).filter(
        models.IssueNotification.recipient_user_id == current_user.id,
        models.IssueNotification.read_at.is_(None),
    ).update({"read_at": read_at}, synchronize_session=False)
    db.commit()
    return {"updated": updated}


@router.post("/{notification_id}/read")
def read_notification(
    notification_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    notification = db.query(models.IssueNotification).filter(
        models.IssueNotification.id == notification_id,
        models.IssueNotification.recipient_user_id == current_user.id,
    ).first()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        db.commit()
    return {"read_at": notification.read_at}
