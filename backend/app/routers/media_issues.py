"""Authenticated user endpoints for reporting and tracking media problems."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session, joinedload

from app import database, models, schemas
from app.auth import get_current_user
from app.services.media_issues import (
    create_or_attach_issue,
    create_status_event,
    issue_for_user,
    notify_reporters,
    serialize_user_issue,
)


router = APIRouter()


@router.post("/")
def create_media_issue(
    payload: schemas.MediaIssueCreate,
    response: Response,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    issue, created, already_reported = create_or_attach_issue(db, payload, current_user)
    issue = issue_for_user(db, issue.public_id, current_user)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return {
        "issue": serialize_user_issue(issue, current_user),
        "created": created,
        "deduplicated": not created,
        "already_reported": already_reported,
    }


@router.get("/")
def list_media_issues(
    status_filter: Optional[str] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    if status_filter and status_filter not in {"open", "done"}:
        raise HTTPException(status_code=400, detail="Unsupported issue status")
    query = db.query(models.MediaIssue).join(models.MediaIssueReport).filter(
        models.MediaIssueReport.reporter_user_id == current_user.id
    )
    if status_filter:
        query = query.filter(models.MediaIssue.status == status_filter)
    total = query.count()
    issues = query.options(
        joinedload(models.MediaIssue.book),
        joinedload(models.MediaIssue.reports),
    ).order_by(
        models.MediaIssue.status.asc(),
        models.MediaIssue.last_reported_at.desc(),
    ).offset(skip).limit(limit).all()
    return {
        "items": [serialize_user_issue(issue, current_user) for issue in issues],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get("/{public_id}")
def get_media_issue(
    public_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    return serialize_user_issue(issue_for_user(db, public_id, current_user), current_user)


@router.patch("/{public_id}/report")
def update_media_issue_report(
    public_id: str,
    payload: schemas.MediaIssueReportUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    issue = issue_for_user(db, public_id, current_user)
    report = db.query(models.MediaIssueReport).filter(
        models.MediaIssueReport.issue_id == issue.id,
        models.MediaIssueReport.reporter_user_id == current_user.id,
    ).first()
    if not report:
        raise HTTPException(status_code=404, detail="Issue not found")
    report.report_text = payload.report_text.strip()
    # A reporter can promote an issue to Critical. Removing the canonical flag
    # is an admin action so a later edit cannot silently demote an urgent item.
    if payload.is_critical and not report.is_critical:
        report.is_critical = True
        report.critical_explanation = (payload.critical_explanation or "").strip()
        issue.critical_report_count += 1
        if not issue.is_critical:
            issue.is_critical = True
            issue.critical_first_reported_at = datetime.now(timezone.utc)
        db.add(models.MediaIssueStatusEvent(
            issue_id=issue.id,
            actor_user_id=current_user.id,
            event_type="critical_promoted",
            new_status=issue.status,
        ))
    elif report.is_critical and payload.critical_explanation:
        report.critical_explanation = payload.critical_explanation.strip()
    db.commit()
    return serialize_user_issue(issue_for_user(db, public_id, current_user), current_user)


@router.post("/{public_id}/still-broken")
def mark_media_issue_still_broken(
    public_id: str,
    payload: schemas.MediaIssueStillBroken,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    issue = issue_for_user(db, public_id, current_user)
    if issue.status == "open":
        return serialize_user_issue(issue, current_user)

    competing = db.query(models.MediaIssue).filter(
        models.MediaIssue.active_key == issue.fingerprint,
        models.MediaIssue.id != issue.id,
    ).first()
    if competing:
        existing = db.query(models.MediaIssueReport).filter(
            models.MediaIssueReport.issue_id == competing.id,
            models.MediaIssueReport.reporter_user_id == current_user.id,
        ).first()
        if not existing:
            original = db.query(models.MediaIssueReport).filter(
                models.MediaIssueReport.issue_id == issue.id,
                models.MediaIssueReport.reporter_user_id == current_user.id,
            ).first()
            db.add(models.MediaIssueReport(
                issue_id=competing.id,
                reporter_user_id=current_user.id,
                reporter_name_snapshot=original.reporter_name_snapshot,
                report_text=payload.explanation.strip(),
                request_id=original.request_id,
                download_task_id=original.download_task_id,
                context_json=original.context_json,
                is_critical=original.is_critical,
                critical_explanation=original.critical_explanation,
            ))
            competing.report_count += 1
            competing.last_reported_at = datetime.now(timezone.utc)
        db.commit()
        return serialize_user_issue(issue_for_user(db, competing.public_id, current_user), current_user)

    previous_status = issue.status
    issue.status = "open"
    issue.active_key = issue.fingerprint
    issue.done_at = None
    issue.done_by_user_id = None
    issue.done_note = None
    issue.last_reported_at = datetime.now(timezone.utc)
    report = db.query(models.MediaIssueReport).filter(
        models.MediaIssueReport.issue_id == issue.id,
        models.MediaIssueReport.reporter_user_id == current_user.id,
    ).first()
    report.report_text = f"{report.report_text}\n\nStill a problem: {payload.explanation.strip()}"
    event = create_status_event(
        db,
        issue,
        current_user.id,
        "reopened",
        previous_status,
        "open",
        payload.explanation.strip(),
    )
    notify_reporters(db, issue, event, "reopened", f"{issue.book.title if issue.book else 'Your issue'} was reopened")
    db.commit()
    return serialize_user_issue(issue_for_user(db, public_id, current_user), current_user)
