"""Automatic fulfillment for approved ebook and audiobook requests."""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app import models
from app.database import SessionLocal
from app.downloads import Release
from app.downloads.orchestrator import DownloadOrchestrator


logger = structlog.get_logger(__name__)

ACTIVE_DOWNLOAD_STATES = ("queued", "downloading", "checking", "processing", "paused")
CLAIM_TIMEOUT = timedelta(minutes=30)
_run_lock = asyncio.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _json_list(value: Optional[str], default: list[Any]) -> list[Any]:
    try:
        decoded = json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return list(default)
    return decoded if isinstance(decoded, list) else list(default)


def get_or_create_settings(db) -> models.AutoDownloadSettings:
    settings = db.query(models.AutoDownloadSettings).filter(models.AutoDownloadSettings.id == 1).first()
    if settings:
        return settings
    settings = models.AutoDownloadSettings(
        id=1,
        enabled=False,
        dry_run=True,
        process_existing_backlog=True,
        interval_seconds=900,
        batch_size=5,
        max_active_downloads=10,
        minimum_score=70.0,
        ebook_formats_json=json.dumps(["epub", "azw3", "mobi", "pdf"]),
        audiobook_formats_json=json.dumps(["m4b", "mp3", "flac"]),
        preferred_languages_json=json.dumps(["en"]),
        protocol_order_json=json.dumps(["usenet", "torrent"]),
        minimum_seeders=1,
        ebook_min_size_mb=0.1,
        ebook_max_size_mb=500.0,
        audiobook_min_size_mb=10.0,
        audiobook_max_size_mb=5000.0,
        retry_schedule_json=json.dumps([900, 3600, 21600, 86400]),
        categoryless_fallback=True,
    )
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def serialize_settings(settings: models.AutoDownloadSettings) -> dict[str, Any]:
    summary = None
    if settings.last_run_summary_json:
        try:
            summary = json.loads(settings.last_run_summary_json)
        except json.JSONDecodeError:
            summary = None
    return {
        "enabled": bool(settings.enabled),
        "dry_run": bool(settings.dry_run),
        "process_existing_backlog": bool(settings.process_existing_backlog),
        "interval_seconds": settings.interval_seconds,
        "batch_size": settings.batch_size,
        "max_active_downloads": settings.max_active_downloads,
        "minimum_score": settings.minimum_score,
        "ebook_formats": _json_list(settings.ebook_formats_json, ["epub", "azw3", "mobi", "pdf"]),
        "audiobook_formats": _json_list(settings.audiobook_formats_json, ["m4b", "mp3", "flac"]),
        "preferred_languages": _json_list(settings.preferred_languages_json, ["en"]),
        "protocol_order": _json_list(settings.protocol_order_json, ["usenet", "torrent"]),
        "minimum_seeders": settings.minimum_seeders,
        "ebook_min_size_mb": settings.ebook_min_size_mb,
        "ebook_max_size_mb": settings.ebook_max_size_mb,
        "audiobook_min_size_mb": settings.audiobook_min_size_mb,
        "audiobook_max_size_mb": settings.audiobook_max_size_mb,
        "retry_schedule_seconds": _json_list(settings.retry_schedule_json, [900, 3600, 21600, 86400]),
        "categoryless_fallback": bool(settings.categoryless_fallback),
        "last_run_started_at": settings.last_run_started_at,
        "last_run_completed_at": settings.last_run_completed_at,
        "last_run_summary": summary,
    }


def apply_settings_update(settings: models.AutoDownloadSettings, update) -> None:
    data = update.model_dump()
    json_fields = {
        "ebook_formats": "ebook_formats_json",
        "audiobook_formats": "audiobook_formats_json",
        "preferred_languages": "preferred_languages_json",
        "protocol_order": "protocol_order_json",
        "retry_schedule_seconds": "retry_schedule_json",
    }
    for key, value in data.items():
        if key in json_fields:
            setattr(settings, json_fields[key], json.dumps(value))
        else:
            setattr(settings, key, value)


def get_request_statistics(db, current_user: models.User) -> dict[str, Any]:
    query = db.query(models.BookRequest).options(joinedload(models.BookRequest.book))
    if not current_user.is_admin:
        query = query.filter(models.BookRequest.user_id == current_user.id)
    requests = query.all()

    pairs = {(request.book_id, request.format) for request in requests}
    active_pairs: set[tuple[int, str]] = set()
    if pairs:
        active_tasks = db.query(models.DownloadTask.book_id, models.DownloadTask.format).filter(
            models.DownloadTask.state.in_(ACTIVE_DOWNLOAD_STATES)
        ).all()
        active_pairs = {(book_id, format_type) for book_id, format_type in active_tasks}

    statuses = {
        "pending": 0,
        "approved": 0,
        "processing": 0,
        "available": 0,
        "not_found": 0,
        "denied": 0,
    }
    by_format = {
        "ebook": {key: 0 for key in statuses},
        "audiobook": {key: 0 for key in statuses},
    }
    eligible = 0
    retrying = 0
    stale_available = 0
    now = _utcnow()

    for request in requests:
        status = request.status if request.status in statuses else "pending"
        statuses[status] += 1
        if request.format in by_format:
            by_format[request.format][status] += 1

        if request.status != "approved" or not request.book:
            continue
        available = (
            request.book.ebook_available if request.format == "ebook"
            else request.book.audiobook_available
        )
        if available:
            stale_available += 1
            continue
        next_search_at = _aware(request.next_search_at)
        if next_search_at and next_search_at > now:
            retrying += 1
        elif (request.book_id, request.format) not in active_pairs:
            eligible += 1

    processed = statuses["available"] + statuses["not_found"] + statuses["denied"]
    return {
        "total": len(requests),
        **statuses,
        "processed": processed,
        "eligible_for_auto_search": eligible,
        "waiting_for_retry": retrying,
        "stale_available": stale_available,
        "by_format": by_format,
    }


@dataclass
class RankedCandidate:
    release: Release
    score: float
    details: dict[str, Any]


def rank_candidates(
    db,
    book: models.Book,
    format_type: str,
    releases: list[Release],
    settings: models.AutoDownloadSettings,
) -> tuple[list[RankedCandidate], int]:
    format_preferences = _json_list(
        settings.ebook_formats_json if format_type == "ebook" else settings.audiobook_formats_json,
        ["epub", "azw3", "mobi", "pdf"] if format_type == "ebook" else ["m4b", "mp3", "flac"],
    )
    languages = _json_list(settings.preferred_languages_json, ["en"])
    protocols = _json_list(settings.protocol_order_json, ["usenet", "torrent"])
    min_size = settings.ebook_min_size_mb if format_type == "ebook" else settings.audiobook_min_size_mb
    max_size = settings.ebook_max_size_mb if format_type == "ebook" else settings.audiobook_max_size_mb

    existing_hashes = {
        value for (value,) in db.query(models.DownloadTask.info_hash).filter(
            models.DownloadTask.book_id == book.id,
            models.DownloadTask.format == format_type,
            models.DownloadTask.info_hash.isnot(None),
            or_(
                models.DownloadTask.state != "error",
                models.DownloadTask.client_download_id.isnot(None),
            ),
        ).all()
    }
    if book.downloaded_release_hashes:
        existing_hashes.update(_json_list(book.downloaded_release_hashes, []))

    ranked: list[RankedCandidate] = []
    rejected = 0
    for release in releases:
        release_hash = hashlib.sha256(release.download_url.encode()).hexdigest()[:16]
        if release_hash in existing_hashes:
            rejected += 1
            continue
        if release.protocol not in protocols:
            rejected += 1
            continue
        if release.protocol == "torrent" and (release.seeders or 0) < settings.minimum_seeders:
            rejected += 1
            continue
        size_mb = release.size_bytes / (1024 * 1024) if release.size_bytes else 0
        if size_mb and not min_size <= size_mb <= max_size:
            rejected += 1
            continue
        language = (release.language or "").lower()
        if language and languages and language not in languages:
            rejected += 1
            continue

        score = float(release.quality_score)
        details: dict[str, Any] = {"base_score": round(score, 2)}
        release_format = (release.format or "").lower()
        if not release_format or release_format not in format_preferences:
            rejected += 1
            continue
        index = format_preferences.index(release_format)
        format_bonus = max(5.0, 20.0 - (index * 5.0))
        score += format_bonus
        details["format_bonus"] = format_bonus

        if language and language in languages:
            score += 5.0
            details["language_bonus"] = 5.0

        protocol_index = protocols.index(release.protocol)
        protocol_bonus = max(1.0, 5.0 - (protocol_index * 2.0))
        score += protocol_bonus
        details["protocol_bonus"] = protocol_bonus
        details["size_mb"] = round(size_mb, 2)
        details["seeders"] = release.seeders
        score = max(0.0, min(100.0, score))

        if score < settings.minimum_score:
            rejected += 1
            continue
        ranked.append(RankedCandidate(release=release, score=score, details=details))

    ranked.sort(key=lambda candidate: candidate.score, reverse=True)
    return ranked, rejected


def _retry_at(settings: models.AutoDownloadSettings, attempt_count: int, now: datetime) -> datetime:
    schedule = _json_list(settings.retry_schedule_json, [900, 3600, 21600, 86400])
    index = min(max(attempt_count - 1, 0), len(schedule) - 1)
    return now + timedelta(seconds=int(schedule[index]))


def schedule_request_retry(
    request: models.BookRequest,
    settings: models.AutoDownloadSettings,
    message: str,
    *,
    now: Optional[datetime] = None,
) -> datetime:
    """Return an automated request to the approved queue with backoff."""
    now = now or _utcnow()
    request.status = "approved"
    request.auto_search_attempts += 1
    request.last_search_at = now
    request.last_search_error = message[:2000]
    request.next_search_at = _retry_at(settings, request.auto_search_attempts, now)
    request.search_claimed_at = None
    request.download_task_id = None
    return request.next_search_at


def _record_attempt(
    db,
    request_id: int,
    *,
    outcome: str,
    dry_run: bool,
    candidate_count: int = 0,
    selected: Optional[RankedCandidate] = None,
    task_id: Optional[int] = None,
    message: Optional[str] = None,
    next_retry_at: Optional[datetime] = None,
) -> models.FulfillmentAttempt:
    attempt = models.FulfillmentAttempt(
        request_id=request_id,
        download_task_id=task_id,
        dry_run=dry_run,
        outcome=outcome,
        candidate_count=candidate_count,
        selected_release_title=selected.release.title if selected else None,
        selected_score=selected.score if selected else None,
        score_details_json=json.dumps(selected.details) if selected else None,
        message=message,
        next_retry_at=next_retry_at,
        completed_at=_utcnow(),
    )
    db.add(attempt)
    return attempt


def _active_task(db, request: models.BookRequest) -> Optional[models.DownloadTask]:
    return db.query(models.DownloadTask).filter(
        models.DownloadTask.book_id == request.book_id,
        models.DownloadTask.format == request.format,
        models.DownloadTask.state.in_(ACTIVE_DOWNLOAD_STATES),
    ).order_by(models.DownloadTask.created_at.desc()).first()


def _claim_requests(db, settings: models.AutoDownloadSettings, force: bool) -> tuple[list[int], int, int]:
    now = _utcnow()
    active_count = db.query(models.DownloadTask).filter(
        models.DownloadTask.state.in_(ACTIVE_DOWNLOAD_STATES)
    ).count()
    capacity = max(0, settings.max_active_downloads - active_count)
    batch_size = min(settings.batch_size, capacity)
    if batch_size <= 0:
        return [], 0, active_count

    query = db.query(models.BookRequest).options(joinedload(models.BookRequest.book)).filter(
        models.BookRequest.status == "approved",
        or_(models.BookRequest.next_search_at.is_(None), models.BookRequest.next_search_at <= now),
        or_(
            models.BookRequest.search_claimed_at.is_(None),
            models.BookRequest.search_claimed_at < now - CLAIM_TIMEOUT,
        ),
    )
    if not settings.process_existing_backlog and settings.created_at:
        query = query.filter(models.BookRequest.created_at >= settings.created_at)
    requests = query.order_by(models.BookRequest.created_at.asc(), models.BookRequest.id.asc()).with_for_update(
        skip_locked=True,
        of=models.BookRequest,
    ).limit(max(batch_size * 3, batch_size)).all()

    claimed: list[int] = []
    reconciled = 0
    for request in requests:
        if not request.book:
            continue
        available = (
            request.book.ebook_available if request.format == "ebook"
            else request.book.audiobook_available
        )
        if available:
            request.status = "available"
            request.last_search_error = None
            request.next_search_at = None
            request.search_claimed_at = None
            _record_attempt(db, request.id, outcome="reconciled", dry_run=False, message="Format already available")
            reconciled += 1
            continue
        active = _active_task(db, request)
        if active:
            request.status = "processing"
            request.download_task_id = active.id
            request.search_claimed_at = None
            continue
        request.search_claimed_at = now
        claimed.append(request.id)
        if len(claimed) >= batch_size:
            break
    db.commit()
    return claimed, reconciled, active_count


def _process_one_request(request_id: int, dry_run: bool) -> dict[str, Any]:
    db = SessionLocal()
    try:
        request = db.query(models.BookRequest).options(joinedload(models.BookRequest.book)).filter(
            models.BookRequest.id == request_id
        ).first()
        if not request or request.status != "approved" or not request.book:
            if request:
                request.search_claimed_at = None
                db.commit()
            return {"outcome": "deferred", "request_id": request_id}

        settings = get_or_create_settings(db)
        available = request.book.ebook_available if request.format == "ebook" else request.book.audiobook_available
        if available:
            request.status = "available"
            request.search_claimed_at = None
            _record_attempt(db, request.id, outcome="reconciled", dry_run=dry_run, message="Format already available")
            db.commit()
            return {"outcome": "reconciled", "request_id": request_id}

        active = _active_task(db, request)
        if active:
            request.status = "processing"
            request.download_task_id = active.id
            request.search_claimed_at = None
            db.commit()
            return {"outcome": "deferred", "request_id": request_id, "message": "Active task already exists"}

        orchestrator = DownloadOrchestrator(db_session=db)
        releases = orchestrator.search_releases(
            request.book,
            request.format,
            "prowlarr",
            raise_errors=True,
            categoryless_fallback=settings.categoryless_fallback,
            stop_after_first_results=True,
        )
        ranked, rejected = rank_candidates(db, request.book, request.format, releases, settings)
        now = _utcnow()

        if not ranked:
            if dry_run:
                request.search_claimed_at = None
                _record_attempt(
                    db,
                    request.id,
                    outcome="no_candidate",
                    dry_run=True,
                    candidate_count=len(releases),
                    message=f"No candidate met the configured criteria ({rejected} rejected)",
                )
                db.commit()
                return {"outcome": "no_candidate", "request_id": request_id, "candidates": len(releases)}

            request.last_search_at = now
            request.auto_search_attempts += 1
            request.last_search_error = f"No candidate met the configured criteria ({rejected} rejected)"
            request.next_search_at = _retry_at(settings, request.auto_search_attempts, now)
            request.search_claimed_at = None
            _record_attempt(
                db,
                request.id,
                outcome="no_candidate",
                dry_run=dry_run,
                candidate_count=len(releases),
                message=request.last_search_error,
                next_retry_at=request.next_search_at,
            )
            db.commit()
            return {"outcome": "no_candidate", "request_id": request_id, "candidates": len(releases)}

        selected = ranked[0]
        if dry_run:
            request.search_claimed_at = None
            _record_attempt(
                db,
                request.id,
                outcome="would_start",
                dry_run=True,
                candidate_count=len(ranked),
                selected=selected,
            )
            db.commit()
            return {
                "outcome": "would_start",
                "request_id": request_id,
                "release": selected.release.title,
                "score": selected.score,
            }

        task = orchestrator.create_download_task(
            request.book,
            selected.release,
            request.format,
            request_id=request.id,
        )
        if not task:
            active = _active_task(db, request)
            if active:
                request.status = "processing"
                request.download_task_id = active.id
                request.search_claimed_at = None
                _record_attempt(
                    db,
                    request.id,
                    outcome="deferred",
                    dry_run=False,
                    task_id=active.id,
                    message="Another worker already created an active task",
                )
                db.commit()
                return {"outcome": "deferred", "request_id": request_id}

        if not task or not orchestrator.start_download(task.id):
            if task:
                task.state = "error"
                task.message = "Failed to start automatic download"
            request.auto_search_attempts += 1
            request.last_search_at = now
            request.last_search_error = "Selected candidate could not be started"
            request.next_search_at = _retry_at(settings, request.auto_search_attempts, now)
            request.search_claimed_at = None
            _record_attempt(
                db,
                request.id,
                outcome="error",
                dry_run=False,
                candidate_count=len(ranked),
                selected=selected,
                task_id=task.id if task else None,
                message=request.last_search_error,
                next_retry_at=request.next_search_at,
            )
            db.commit()
            return {"outcome": "error", "request_id": request_id}

        request.status = "processing"
        request.last_search_at = now
        request.download_task_id = task.id
        request.last_search_error = None
        request.next_search_at = None
        request.search_claimed_at = None
        _record_attempt(
            db,
            request.id,
            outcome="started",
            dry_run=False,
            candidate_count=len(ranked),
            selected=selected,
            task_id=task.id,
        )
        db.commit()
        return {"outcome": "started", "request_id": request_id, "task_id": task.id}
    except Exception as exc:
        db.rollback()
        request = db.query(models.BookRequest).filter(models.BookRequest.id == request_id).first()
        if request:
            settings = get_or_create_settings(db)
            now = _utcnow()
            request.search_claimed_at = None
            next_retry_at = None
            if not dry_run:
                request.auto_search_attempts += 1
                request.last_search_at = now
                request.last_search_error = str(exc)[:2000]
                request.next_search_at = _retry_at(settings, request.auto_search_attempts, now)
                next_retry_at = request.next_search_at
            _record_attempt(
                db,
                request.id,
                outcome="error",
                dry_run=dry_run,
                message=str(exc)[:2000],
                next_retry_at=next_retry_at,
            )
            db.commit()
        logger.error("automatic_fulfillment_request_failed", request_id=request_id, error=str(exc))
        return {"outcome": "error", "request_id": request_id, "message": str(exc)}
    finally:
        db.close()


async def process_approved_requests(*, force: bool = False, dry_run: Optional[bool] = None) -> dict[str, Any]:
    if _run_lock.locked():
        return {"status": "already_running"}

    async with _run_lock:
        db = SessionLocal()
        try:
            settings = get_or_create_settings(db)
            if not settings.enabled and not force:
                return {"status": "disabled"}
            effective_dry_run = settings.dry_run if dry_run is None else dry_run
            settings.last_run_started_at = _utcnow()
            settings.last_run_completed_at = None
            db.commit()
            request_ids, reconciled, active_count = _claim_requests(db, settings, force)
        finally:
            db.close()

        summary: dict[str, Any] = {
            "status": "completed",
            "dry_run": effective_dry_run,
            "claimed": len(request_ids),
            "reconciled": reconciled,
            "active_before_run": active_count,
            "started": 0,
            "would_start": 0,
            "no_candidate": 0,
            "deferred": 0,
            "errors": 0,
        }
        for request_id in request_ids:
            result = await asyncio.to_thread(_process_one_request, request_id, effective_dry_run)
            outcome = result.get("outcome")
            if outcome == "error":
                summary["errors"] += 1
            elif outcome in summary:
                summary[outcome] += 1

        db = SessionLocal()
        try:
            settings = get_or_create_settings(db)
            settings.last_run_completed_at = _utcnow()
            settings.last_run_summary_json = json.dumps(summary)
            db.commit()
        finally:
            db.close()

        logger.info("automatic_fulfillment_run_complete", **summary)
        return summary
