"""
Background tasks for refreshing seed data
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func as sa_func
from app.database import SessionLocal
from app.models import Book
from app import schemas
from app.routers.hardcover import execute_graphql, _parse_hardcover_book
from app.routers.settings import get_hardcover_token
from app.downloads.orchestrator import _payload_matches_book
import structlog

logger = structlog.get_logger()


def create_book_from_booklore_data(
    title: str,
    author: Optional[str],
    description: Optional[str],
    cover_url: Optional[str],
    isbn: Optional[str],
    page_count: Optional[int],
    published_date: Optional[str],
    hardcover_id: int,
    hardcover_slug: Optional[str],
    booklore_id: str,
    booklore_added_on: Optional[datetime],
    series_name: Optional[str],
    series_id: Optional[int],
    series_number: Optional[float],
    rating: Optional[float],
    ratings_count: Optional[int],
    users_count: Optional[int],
    genres: Optional[str],
    format_type: str,
) -> Book:
    """
    Helper function to create a Book instance from Booklore data.
    Eliminates duplicate book creation logic.
    """
    return Book(
        title=title,
        author=author,
        description=description,
        cover_url=cover_url,
        isbn=isbn,
        page_count=page_count,
        published_date=published_date,
        hardcover_id=hardcover_id,
        hardcover_slug=hardcover_slug,
        booklore_id=booklore_id,
        booklore_added_on=booklore_added_on,
        series=series_name,
        series_id=series_id,
        series_position=series_number,
        rating=rating,
        ratings_count=ratings_count,
        users_count=users_count,
        genres=genres,
        ebook_available=(format_type == "ebook"),
        audiobook_available=(format_type == "audiobook"),
    )


async def refresh_seed_data():
    """Background task to fetch new books from Hardcover API using progressive offset"""
    import json
    from app.models import JobSchedule
    
    db: Session = SessionLocal()
    try:
        # Check if we have a token
        token, _ = get_hardcover_token(db)
        if not token:
            logger.info("refresh_seed_data_skipped", reason="no_token")
            return
        
        # Get job state for offset tracking
        job = db.query(JobSchedule).filter(JobSchedule.job_name == "refresh_seed_data").first()
        
        # Parse state JSON or initialize
        state = {}
        if job and job.state_json:
            try:
                state = json.loads(job.state_json)
            except:
                state = {}
        
        current_offset = state.get("offset", 0)
        batch_size = 1000  # Fetch 100 books per run
        
        logger.info("refresh_seed_data_starting", offset=current_offset, batch_size=batch_size)
        
        # Get all existing hardcover_ids to skip duplicates
        existing_ids = set(
            row[0] for row in db.query(Book.hardcover_id).filter(Book.hardcover_id.isnot(None)).all()
        )
        logger.info("refresh_seed_data_existing_books", count=len(existing_ids))
        
        # Fetch books using offset - order by users_count for variety
        query = """
        query PopularBooks($limit: Int!, $offset: Int!) {
          books(
            order_by: [{users_count: desc_nulls_last}, {rating: desc_nulls_last}],
            limit: $limit,
            offset: $offset,
            where: {ratings_count: {_gte: 50}}
          ) {
            id
            title
            slug
            release_year
            release_date
            pages
            description
            cached_image
            cached_contributors
            rating
            ratings_count
            users_count
            activities_count
            book_series {
              series {
                id
                name
              }
              position
            }
            contributions {
              author {
                id
                name
                slug
              }
            }
            taggings(limit: 10) {
              tag {
                tag
              }
            }
          }
        }
        """
        
        result = await execute_graphql(query, {"limit": batch_size, "offset": current_offset}, db=db)
        books_data = result.get("books", [])
        
        if not books_data:
            # No more books at this offset, reset to beginning
            logger.info("refresh_seed_data_offset_reset", old_offset=current_offset)
            current_offset = 0
            state["offset"] = 0
            if job:
                job.state_json = json.dumps(state)
                db.commit()
            return
        
        inserted = 0
        skipped = 0
        
        for hc_book_data in books_data:
            hardcover_id = hc_book_data.get("id")
            if not hardcover_id:
                continue
            
            # Skip if already exists
            if hardcover_id in existing_ids:
                skipped += 1
                continue
            
            # Parse book
            try:
                hc_book = _parse_hardcover_book(hc_book_data)
            except Exception as e:
                logger.debug("refresh_seed_data_parse_error", hardcover_id=hardcover_id, error=str(e))
                continue
            
            # Extract data
            authors = []
            if hc_book.contributions:
                authors = [c.author.name for c in hc_book.contributions if c.author]
            elif hc_book.cached_contributors:
                authors = [c.author.get("name", "") if isinstance(c.author, dict) else "" for c in hc_book.cached_contributors]
            
            author = ", ".join(authors) if authors else "Unknown Author"
            
            cover_url = None
            if hc_book.cached_image and isinstance(hc_book.cached_image, schemas.HardcoverCachedImage):
                cover_url = hc_book.cached_image.url
            
            series = None
            series_id = None
            series_position = None
            if hc_book.book_series and len(hc_book.book_series) > 0:
                series = hc_book.book_series[0].series.name
                series_id = hc_book.book_series[0].series.id
                series_position = hc_book.book_series[0].position
            
            genres = []
            if hc_book.taggings:
                genres = [t.tag.tag for t in hc_book.taggings if t.tag]
            
            # Create new book
            db_book = Book(
                title=hc_book.title,
                author=author,
                description=hc_book.description,
                cover_url=cover_url,
                published_date=hc_book.release_date or str(hc_book.release_year or ""),
                rating=hc_book.rating,
                page_count=hc_book.pages,
                hardcover_id=hardcover_id,
                hardcover_slug=hc_book.slug,
                series=series,
                series_id=series_id,
                series_position=series_position,
                genres=", ".join(genres) if genres else None,
                ratings_count=hc_book.ratings_count,
                users_count=hc_book.users_count,
                activities_count=hc_book.activities_count,
                release_year=hc_book.release_year,
                is_seed_data=True,
                last_refreshed=datetime.now(timezone.utc),
            )
            db.add(db_book)
            existing_ids.add(hardcover_id)  # Track to avoid duplicates in same batch
            inserted += 1
        
        # Update offset for next run
        new_offset = current_offset + batch_size
        state["offset"] = new_offset
        state["last_inserted"] = inserted
        state["total_processed"] = state.get("total_processed", 0) + len(books_data)
        
        if job:
            job.state_json = json.dumps(state)
        
        db.commit()
        logger.info("refresh_seed_data_complete", 
                   inserted=inserted, 
                   skipped=skipped,
                   fetched=len(books_data),
                   current_offset=current_offset,
                   next_offset=new_offset,
                   total_books_in_db=len(existing_ids))
        
    except Exception as e:
        logger.error("refresh_seed_data_error", error=str(e))
        db.rollback()
    finally:
        db.close()

def update_job_execution(job_name: str, max_retries: int = 3):
    """Update the last and next execution times for a job with retry logic"""
    import time
    from app.models import JobSchedule
    
    for attempt in range(max_retries):
        db = SessionLocal()
        try:
            schedule = db.query(JobSchedule).filter(JobSchedule.job_name == job_name).first()
            if schedule:
                schedule.last_execution = datetime.now(timezone.utc)
                interval = schedule.interval_seconds or 3600
                schedule.next_execution = datetime.now(timezone.utc) + timedelta(seconds=interval)
                db.commit()
            return  # Success
        except Exception as e:
            db.rollback()
            if "database is locked" in str(e) and attempt < max_retries - 1:
                logger.debug("update_job_execution_retry", job_name=job_name, attempt=attempt + 1)
                time.sleep(0.5 * (attempt + 1))  # Exponential backoff
            else:
                logger.warning("update_job_execution_failed", job_name=job_name, error=str(e))
        finally:
            db.close()


def get_job_interval_standalone(job_name: str, default_seconds: int = 3600) -> int:
    """Get the interval for a job from the database (standalone, creates own session)"""
    from app.models import JobSchedule
    db = SessionLocal()
    try:
        schedule = db.query(JobSchedule).filter(JobSchedule.job_name == job_name).first()
        if schedule and schedule.interval_seconds:
            return schedule.interval_seconds
        return default_seconds
    except Exception:
        return default_seconds
    finally:
        db.close()


def get_seconds_until_next_execution(job_name: str) -> int:
    """Get seconds until the next scheduled execution, or 0 if it should run now"""
    from app.models import JobSchedule
    db = SessionLocal()
    try:
        schedule = db.query(JobSchedule).filter(JobSchedule.job_name == job_name).first()
        if schedule and schedule.next_execution:
            now = datetime.now(timezone.utc)
            if schedule.next_execution > now:
                return int((schedule.next_execution - now).total_seconds())
        # If no next_execution or it's in the past, check last_execution + interval
        if schedule and schedule.last_execution:
            interval = schedule.interval_seconds or 3600
            next_run = schedule.last_execution + timedelta(seconds=interval)
            if next_run > datetime.now(timezone.utc):
                return int((next_run - datetime.now(timezone.utc)).total_seconds())
        return 0  # Run immediately if never run before
    except Exception:
        return 0
    finally:
        db.close()


async def run_background_refresh():
    """Background task to refresh seed data periodically"""
    job_name = "refresh_seed_data"
    
    # Wait until next scheduled execution before first run
    initial_wait = get_seconds_until_next_execution(job_name)
    if initial_wait > 0:
        logger.info("background_refresh_waiting", seconds=initial_wait)
        await asyncio.sleep(initial_wait)
    
    while True:
        try:
            await refresh_seed_data()
            update_job_execution(job_name)
        except Exception as e:
            logger.error("background_refresh_error", error=str(e))
        
        # Get interval from database (default 24 hours)
        interval = get_job_interval_standalone(job_name, 24 * 60 * 60)
        logger.debug("background_refresh_sleeping", interval_seconds=interval)
        await asyncio.sleep(interval)

async def check_processing_requests():
    """Background task to check Booklore and update processing requests"""
    from app.routers.requests import update_processing_requests_status
    db: Session = SessionLocal()
    try:
        await update_processing_requests_status(db)
    except Exception as e:
        logger.error("check_processing_requests_error", error=str(e))
    finally:
        db.close()


def _parse_booklore_instant(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


async def sync_from_booklore():
    """
    Sync book availability from Booklore.
    - Imports books from Booklore into the local database
    - Creates "available" requests for them marked as "booklore_import"
    - Looks up books on Hardcover API to get full metadata
    """
    from app.routers.booklore import get_default_booklore_server, get_booklore_books
    from app.routers.hardcover import lookup_book_by_slug, lookup_book_by_title_author
    from app.models import BookRequest, User
    
    db: Session = SessionLocal()
    try:
        # Get Booklore server
        booklore_server = get_default_booklore_server(db)
        
        if not booklore_server:
            logger.info("sync_from_booklore_skipped", reason="no_booklore_server")
            return
        
        logger.info("sync_from_booklore_starting", server_name=booklore_server.name)
        
        # Fetch all books from Booklore
        booklore_books = await get_booklore_books(booklore_server, db)
        
        if not booklore_books:
            logger.info("sync_from_booklore_skipped", reason="no_books_in_booklore")
            return
        
        logger.info("sync_from_booklore_fetched", count=len(booklore_books))
        
        # Get admin user for creating import requests (use first admin or first user)
        admin_user = db.query(User).filter(User.is_admin == True).first()
        if not admin_user:
            admin_user = db.query(User).first()
        
        if not admin_user:
            logger.warning("sync_from_booklore_skipped", reason="no_users_in_system")
            return
        
        updated_count = 0  # Existing requests updated to available
        skipped_count = 0  # Books skipped (no hardcover ID)
        books_created = 0  # New books created in DB
        books_updated = 0  # Existing books updated in DB
        
        # Log first book structure for debugging
        if booklore_books:
            first_book = booklore_books[0]
            first_metadata = first_book.get("metadata", {})
            logger.info("booklore_sample_book",
                       title=first_book.get("title"),
                       metadata_keys=list(first_metadata.keys()) if first_metadata else [],
                       hardcover_id=first_metadata.get("hardcoverId"),
                       isbn13=first_metadata.get("isbn13"),
                       goodreads_id=first_metadata.get("goodreadsId"))
        
        for bl_book in booklore_books:
            metadata = bl_book.get("metadata") or {}
            booklore_id = bl_book.get("id")
            booklore_added_on = _parse_booklore_instant(bl_book.get("addedOn"))
            hardcover_id_raw = metadata.get("hardcoverId")
            
            # Extract basic info from Booklore
            title = bl_book.get("title") or metadata.get("title") or "Unknown Title"
            authors_list = metadata.get("authors") or []
            author = ", ".join(authors_list) if authors_list else "Unknown Author"
            
            # Determine format from Booklore book type and library mapping
            book_type = bl_book.get("bookType", "").lower()
            bl_library_id = bl_book.get("libraryId")
            if "audio" in book_type:
                format_type = "audiobook"
            elif bl_library_id and booklore_server.audiobook_library_id and bl_library_id == booklore_server.audiobook_library_id:
                format_type = "audiobook"
            elif bl_library_id and booklore_server.ebook_library_id and bl_library_id == booklore_server.ebook_library_id:
                format_type = "ebook"
            else:
                format_type = "ebook"

            # Fast-path: if we've already imported this Booklore book, skip Hardcover lookups
            if booklore_id:
                existing_by_booklore = db.query(Book).filter(Book.booklore_id == booklore_id).first()
                if existing_by_booklore:
                    if booklore_added_on:
                        existing_by_booklore.booklore_added_on = booklore_added_on
                    if format_type == "audiobook":
                        existing_by_booklore.audiobook_available = True
                    else:
                        existing_by_booklore.ebook_available = True
                    existing_by_booklore.last_refreshed = datetime.now(timezone.utc)
                    db.add(existing_by_booklore)

                    existing_request = db.query(BookRequest).filter(
                        BookRequest.book_id == existing_by_booklore.id,
                        BookRequest.format == format_type
                    ).first()
                    if existing_request and existing_request.status in ("processing", "approved", "pending"):
                        existing_request.status = "available"
                        existing_request.updated_at = datetime.now(timezone.utc)
                        updated_count += 1
                        logger.info("booklore_request_updated_to_available",
                                  hardcover_id=existing_by_booklore.hardcover_id,
                                  request_id=existing_request.id,
                                  title=existing_by_booklore.title,
                                  format=format_type)
                    try:
                        db.commit()
                    except Exception as commit_error:
                        logger.warning("booklore_book_commit_failed",
                                     title=existing_by_booklore.title,
                                     error=str(commit_error))
                        db.rollback()
                    continue

            # hardcoverId in Booklore can be either a numeric ID or a slug
            hardcover_id_int = None
            hardcover_slug = None
            hardcover_book_data = None
            
            if hardcover_id_raw:
                try:
                    # Try to parse as integer first
                    hardcover_id_int = int(hardcover_id_raw)
                except (ValueError, TypeError):
                    # It's a slug (e.g., "dogs-of-war-2017")
                    hardcover_slug = str(hardcover_id_raw)
                    
                    # Look up the full book data from Hardcover using the slug
                    try:
                        hardcover_book_data = await lookup_book_by_slug(hardcover_slug, db)
                        # Small delay to avoid rate limiting (0.5 seconds between requests)
                        await asyncio.sleep(0.5)
                        if hardcover_book_data:
                            hardcover_id_int = hardcover_book_data.get("id")
                            logger.info("hardcover_lookup_success",
                                      slug=hardcover_slug,
                                      hardcover_id=hardcover_id_int,
                                      title=hardcover_book_data.get("title"))
                    except Exception as e:
                        logger.warning("hardcover_lookup_failed", slug=hardcover_slug, error=str(e))
            
            # If still no hardcover identifier, try searching by title/author
            if not hardcover_id_int and not hardcover_slug:
                try:
                    hardcover_book_data = await lookup_book_by_title_author(title, author, db)
                    # Small delay to avoid rate limiting
                    await asyncio.sleep(0.5)
                    if hardcover_book_data:
                        hardcover_id_int = hardcover_book_data.get("id")
                        hardcover_slug = hardcover_book_data.get("slug")
                        logger.info("hardcover_search_success",
                                  title=title,
                                  author=author,
                                  hardcover_id=hardcover_id_int)
                except Exception as e:
                    logger.warning("hardcover_search_failed", title=title, error=str(e))
            
            # If still no hardcover ID, skip this book
            if not hardcover_id_int:
                skipped_count += 1
                logger.debug("booklore_book_skipped_no_hardcover_id",
                           title=title,
                           isbn=metadata.get("isbn13") or metadata.get("isbn10"))
                continue
            
            # Use data from Hardcover if available, otherwise fall back to Booklore
            if hardcover_book_data:
                # Use Hardcover data for better quality
                description = hardcover_book_data.get("description") or metadata.get("description")
                cached_image = hardcover_book_data.get("cached_image")
                cover_url = cached_image.get("url") if isinstance(cached_image, dict) else None
                page_count = hardcover_book_data.get("pages") or metadata.get("pageCount")
                published_date = hardcover_book_data.get("release_date") or str(hardcover_book_data.get("release_year", "")) or str(metadata.get("publishedDate") or "")
                rating = hardcover_book_data.get("rating")
                ratings_count = hardcover_book_data.get("ratings_count")
                users_count = hardcover_book_data.get("users_count")
                
                # Get series info
                book_series = hardcover_book_data.get("book_series", [])
                if book_series and len(book_series) > 0:
                    first_series = book_series[0]
                    series_info = first_series.get("series", {})
                    series_name = series_info.get("name")
                    series_id = series_info.get("id")
                    series_number = first_series.get("position")
                else:
                    series_name = metadata.get("seriesName")
                    series_id = None
                    series_number = metadata.get("seriesNumber")
                
                # Get genres from taggings
                taggings = hardcover_book_data.get("taggings", [])
                genres = ", ".join([t.get("tag", {}).get("tag", "") for t in taggings if t.get("tag", {}).get("tag")])
                
                # Get contributors
                contributions = hardcover_book_data.get("contributions", [])
                if contributions:
                    author_names = [c.get("author", {}).get("name") for c in contributions if c.get("author", {}).get("name")]
                    if author_names:
                        author = ", ".join(author_names)
            else:
                # Use Booklore data
                description = metadata.get("description")
                booklore_book_id = bl_book.get("id")
                cover_url = metadata.get("thumbnailUrl")
                if not cover_url and booklore_book_id and booklore_server:
                    base_url = booklore_server.url.rstrip('/')
                    cover_url = f"{base_url}/api/v1/book/{booklore_book_id}/cover"
                page_count = metadata.get("pageCount")
                published_date = str(metadata.get("publishedDate") or "")
                series_name = metadata.get("seriesName")
                series_id = None
                series_number = metadata.get("seriesNumber")
                rating = None
                ratings_count = None
                users_count = None
                genres = None
            
            isbn = metadata.get("isbn13") or metadata.get("isbn10")
            
            # Check if we have this book locally - try multiple identifiers for upsert
            db_book = None
            
            # Try hardcover_id first (most reliable)
            if hardcover_id_int:
                db_book = db.query(Book).filter(Book.hardcover_id == hardcover_id_int).first()
            
            # Try slug if not found
            if not db_book and hardcover_slug:
                db_book = db.query(Book).filter(Book.hardcover_slug == hardcover_slug).first()
            
            # Try ISBN if still not found
            if not db_book and isbn:
                db_book = db.query(Book).filter(Book.isbn == isbn).first()
            
            # Try title + author match as last resort to prevent duplicates
            if not db_book and title:
                db_book = db.query(Book).filter(
                    Book.title == title,
                    Book.author == author
                ).first()
                if db_book:
                    logger.debug("book_matched_by_title_author",
                               title=title,
                               author=author,
                               book_id=db_book.id)
            
            if db_book:
                # UPDATE existing book with new data
                db_book.title = title
                db_book.author = author
                db_book.description = description or db_book.description
                db_book.cover_url = cover_url or db_book.cover_url
                
                # Only update ISBN if it won't cause a conflict
                if isbn and isbn != db_book.isbn:
                    existing_with_isbn = db.query(Book).filter(
                        Book.isbn == isbn,
                        Book.id != db_book.id
                    ).first()
                    if not existing_with_isbn:
                        db_book.isbn = isbn
                
                db_book.page_count = page_count or db_book.page_count
                db_book.published_date = published_date or db_book.published_date
                db_book.hardcover_id = hardcover_id_int  # Update with proper ID
                db_book.hardcover_slug = hardcover_slug or db_book.hardcover_slug
                if booklore_id:
                    db_book.booklore_id = booklore_id
                if booklore_added_on:
                    db_book.booklore_added_on = booklore_added_on
                db_book.series = series_name or db_book.series
                if hardcover_book_data and series_id:
                    db_book.series_id = series_id
                db_book.series_position = series_number or db_book.series_position
                if rating is not None:
                    db_book.rating = rating
                if ratings_count is not None:
                    db_book.ratings_count = ratings_count
                if users_count is not None:
                    db_book.users_count = users_count
                if genres:
                    db_book.genres = genres
                # Set format-specific availability based on book type from Booklore
                if format_type == "audiobook":
                    db_book.audiobook_available = True
                else:
                    db_book.ebook_available = True
                db_book.last_refreshed = datetime.now(timezone.utc)
                db.add(db_book)
                
                try:
                    db.flush()
                    books_updated += 1
                    logger.info("booklore_book_updated",
                              hardcover_id=hardcover_id_int,
                              hardcover_slug=hardcover_slug,
                              title=title,
                              has_hardcover_data=bool(hardcover_book_data))
                except Exception as flush_error:
                    db.rollback()
                    logger.warning("booklore_book_update_failed",
                                 hardcover_id=hardcover_id_int,
                                 title=title,
                                 error=str(flush_error))
                    # Re-fetch the book after rollback
                    db_book = db.query(Book).filter(Book.hardcover_id == hardcover_id_int).first()
                    if not db_book:
                        continue
            else:
                # CREATE new book - but first do final duplicate checks
                
                # Final check: ensure hardcover_id doesn't already exist
                if hardcover_id_int:
                    existing_by_hc_id = db.query(Book).filter(Book.hardcover_id == hardcover_id_int).first()
                    if existing_by_hc_id:
                        db_book = existing_by_hc_id
                        if booklore_id:
                            db_book.booklore_id = booklore_id
                        if booklore_added_on:
                            db_book.booklore_added_on = booklore_added_on
                        books_updated += 1
                        logger.info("booklore_book_found_by_hardcover_id_final_check",
                                  hardcover_id=hardcover_id_int,
                                  title=title,
                                  existing_id=existing_by_hc_id.id)
                        # Skip to request handling
                    else:
                        # Check ISBN conflict
                        if isbn:
                            existing_with_isbn = db.query(Book).filter(Book.isbn == isbn).first()
                            if existing_with_isbn:
                                # Use existing book instead of creating duplicate
                                db_book = existing_with_isbn
                                db_book.hardcover_id = hardcover_id_int
                                db_book.hardcover_slug = hardcover_slug
                                if booklore_id:
                                    db_book.booklore_id = booklore_id
                                if booklore_added_on:
                                    db_book.booklore_added_on = booklore_added_on
                                db.add(db_book)
                                db.flush()
                                books_updated += 1
                                logger.info("booklore_book_linked_by_isbn",
                                          hardcover_id=hardcover_id_int,
                                          isbn=isbn,
                                          title=title)
                            else:
                                # Create new book with ISBN
                                db_book = create_book_from_booklore_data(
                                    title=title,
                                    author=author,
                                    description=description,
                                    cover_url=cover_url,
                                    isbn=isbn,
                                    page_count=page_count,
                                    published_date=published_date,
                                    hardcover_id=hardcover_id_int,
                                    hardcover_slug=hardcover_slug,
                                    booklore_id=booklore_id,
                                    booklore_added_on=booklore_added_on,
                                    series_name=series_name,
                                    series_id=series_id if hardcover_book_data else None,
                                    series_number=series_number,
                                    rating=rating,
                                    ratings_count=ratings_count,
                                    users_count=users_count,
                                    genres=genres,
                                    format_type=format_type,
                                )
                                db.add(db_book)
                                db.flush()
                                books_created += 1
                                logger.info("booklore_book_created",
                                          hardcover_id=hardcover_id_int,
                                          hardcover_slug=hardcover_slug,
                                          title=title,
                                          format_type=format_type,
                                          has_hardcover_data=bool(hardcover_book_data))
                        else:
                            # Create new book without ISBN
                            db_book = create_book_from_booklore_data(
                                title=title,
                                author=author,
                                description=description,
                                cover_url=cover_url,
                                isbn=None,
                                page_count=page_count,
                                published_date=published_date,
                                hardcover_id=hardcover_id_int,
                                hardcover_slug=hardcover_slug,
                                booklore_id=booklore_id,
                                booklore_added_on=booklore_added_on,
                                series_name=series_name,
                                series_id=series_id if hardcover_book_data else None,
                                series_number=series_number,
                                rating=rating,
                                ratings_count=ratings_count,
                                users_count=users_count,
                                genres=genres,
                                format_type=format_type,
                            )
                            db.add(db_book)
                            db.flush()
                            books_created += 1
                            logger.info("booklore_book_created",
                                      hardcover_id=hardcover_id_int,
                                      hardcover_slug=hardcover_slug,
                                      title=title,
                                      has_hardcover_data=bool(hardcover_book_data))
                else:
                    # No hardcover_id - skip creating book without proper identifier
                    logger.warning("booklore_book_skipped_no_hardcover_id_for_creation",
                                 title=title,
                                 author=author)
                    skipped_count += 1
                    continue
            
            # Check if we have an existing request for this book matching the format
            # Update to "available" if the book is now in Booklore
            existing_request = db.query(BookRequest).filter(
                BookRequest.book_id == db_book.id,
                BookRequest.format == format_type  # Match the format from Booklore
            ).first()
            
            if existing_request and existing_request.status in ("processing", "approved", "pending"):
                existing_request.status = "available"
                existing_request.updated_at = datetime.now(timezone.utc)
                updated_count += 1
                logger.info("booklore_request_updated_to_available",
                          hardcover_id=hardcover_id_int,
                          request_id=existing_request.id,
                          title=title,
                          format=format_type)
            
            # Commit after each book to avoid long-running transactions and database locks
            try:
                db.commit()
            except Exception as commit_error:
                logger.warning("booklore_book_commit_failed",
                             title=title,
                             error=str(commit_error))
                db.rollback()
        
        logger.info("sync_from_booklore_complete",
                   booklore_books=len(booklore_books),
                   books_created=books_created,
                   books_updated=books_updated,
                   requests_updated=updated_count,
                   skipped=skipped_count)
        
    except Exception as e:
        logger.error("sync_from_booklore_error", error=str(e))
        db.rollback()
    finally:
        db.close()


async def run_background_request_check():
    """Background task to check processing requests periodically"""
    job_name = "check_processing_requests"
    
    # Wait until next scheduled execution before first run
    initial_wait = get_seconds_until_next_execution(job_name)
    if initial_wait > 0:
        logger.info("background_request_check_waiting", seconds=initial_wait)
        await asyncio.sleep(initial_wait)
    
    while True:
        try:
            await check_processing_requests()
            update_job_execution(job_name)
        except Exception as e:
            logger.error("background_request_check_error", error=str(e))
        
        # Get interval from database (default 5 minutes)
        interval = get_job_interval_standalone(job_name, 5 * 60)
        logger.debug("background_request_check_sleeping", interval_seconds=interval)
        await asyncio.sleep(interval)


async def run_background_booklore_sync():
    """Background task to sync from Booklore periodically"""
    job_name = "sync_from_booklore"
    
    # Wait until next scheduled execution before first run
    initial_wait = get_seconds_until_next_execution(job_name)
    if initial_wait > 0:
        logger.info("background_booklore_sync_waiting", seconds=initial_wait)
        await asyncio.sleep(initial_wait)
    
    while True:
        try:
            await sync_from_booklore()
            update_job_execution(job_name)
        except Exception as e:
            logger.error("background_booklore_sync_error", error=str(e))
        
        # Get interval from database (default 24 hours)
        interval = get_job_interval_standalone(job_name, 24 * 60 * 60)
        logger.debug("background_booklore_sync_sleeping", interval_seconds=interval)
        await asyncio.sleep(interval)


async def sync_from_audiobookshelf():
    """
    Sync audiobook availability from Audiobookshelf.
    - Imports audiobooks from Audiobookshelf into the local database
    - Updates existing books with audiobookshelf_id links
    - Marks matching requests as "available"
    """
    from app.routers.audiobookshelf import get_default_audiobookshelf_server, get_all_audiobookshelf_items
    from app.routers.hardcover import lookup_book_by_title_author
    from app.models import BookRequest, User

    db: Session = SessionLocal()
    try:
        abs_server = get_default_audiobookshelf_server(db)

        if not abs_server:
            logger.info("sync_from_audiobookshelf_skipped", reason="no_audiobookshelf_server")
            return

        logger.info("sync_from_audiobookshelf_starting", server_name=abs_server.name)

        items = await get_all_audiobookshelf_items(abs_server)

        if not items:
            logger.info("sync_from_audiobookshelf_skipped", reason="no_items_in_audiobookshelf")
            return

        logger.info("sync_from_audiobookshelf_fetched", count=len(items))

        updated_count = 0
        skipped_count = 0
        books_created = 0
        books_updated = 0

        for item in items:
            item_id = item.get("id")
            media = item.get("media", {})
            metadata = media.get("metadata", {})

            title = metadata.get("title") or "Unknown Title"
            author = metadata.get("authorName") or "Unknown Author"
            isbn = metadata.get("isbn")

            # Fast path: already linked by audiobookshelf_id
            if item_id:
                existing_by_abs_id = db.query(Book).filter(Book.audiobookshelf_id == item_id).first()
                if existing_by_abs_id:
                    existing_by_abs_id.audiobook_available = True
                    existing_by_abs_id.last_refreshed = datetime.now(timezone.utc)
                    db.add(existing_by_abs_id)

                    existing_request = db.query(BookRequest).filter(
                        BookRequest.book_id == existing_by_abs_id.id,
                        BookRequest.format == "audiobook"
                    ).first()
                    if existing_request and existing_request.status in ("processing", "approved", "pending"):
                        existing_request.status = "available"
                        existing_request.updated_at = datetime.now(timezone.utc)
                        updated_count += 1

                    try:
                        db.commit()
                    except Exception as commit_error:
                        logger.warning("audiobookshelf_book_commit_failed",
                                     title=existing_by_abs_id.title,
                                     error=str(commit_error))
                        db.rollback()
                    continue

            # Try ISBN match
            db_book = None
            if isbn:
                db_book = db.query(Book).filter(Book.isbn == isbn).first()

            # Try title + author match (case-insensitive)
            if not db_book and title and author:
                db_book = db.query(Book).filter(
                    sa_func.lower(Book.title) == title.lower(),
                    sa_func.lower(Book.author) == author.lower()
                ).first()

            # Try Hardcover lookup by title+author
            if not db_book:
                try:
                    hardcover_data = await lookup_book_by_title_author(title, author, db)
                    await asyncio.sleep(0.5)

                    if hardcover_data:
                        hardcover_id_int = hardcover_data.get("id")
                        if hardcover_id_int:
                            db_book = db.query(Book).filter(Book.hardcover_id == hardcover_id_int).first()

                            if not db_book:
                                # Create new book from Hardcover data
                                description = hardcover_data.get("description")
                                cached_image = hardcover_data.get("cached_image")
                                cover_url = cached_image.get("url") if isinstance(cached_image, dict) else None
                                page_count = hardcover_data.get("pages")
                                published_date = hardcover_data.get("release_date") or str(hardcover_data.get("release_year", ""))
                                rating = hardcover_data.get("rating")
                                hardcover_slug = hardcover_data.get("slug")

                                # Series info
                                series_name = None
                                series_id = None
                                series_position = None
                                book_series = hardcover_data.get("book_series", [])
                                if book_series:
                                    first_series = book_series[0]
                                    series_info = first_series.get("series", {})
                                    series_name = series_info.get("name")
                                    series_id = series_info.get("id")
                                    series_position = first_series.get("position")

                                # Contributors
                                contributions = hardcover_data.get("contributions", [])
                                if contributions:
                                    author_names = [c.get("author", {}).get("name") for c in contributions if c.get("author", {}).get("name")]
                                    if author_names:
                                        author = ", ".join(author_names)

                                # Genres
                                taggings = hardcover_data.get("taggings", [])
                                genres = ", ".join([t.get("tag", {}).get("tag", "") for t in taggings if t.get("tag", {}).get("tag")])

                                db_book = Book(
                                    title=title,
                                    author=author,
                                    description=description,
                                    cover_url=cover_url,
                                    isbn=isbn,
                                    page_count=page_count,
                                    published_date=published_date,
                                    hardcover_id=hardcover_id_int,
                                    hardcover_slug=hardcover_slug,
                                    audiobookshelf_id=item_id,
                                    series=series_name,
                                    series_id=series_id,
                                    series_position=series_position,
                                    rating=rating,
                                    ratings_count=hardcover_data.get("ratings_count"),
                                    users_count=hardcover_data.get("users_count"),
                                    genres=genres or None,
                                    audiobook_available=True,
                                )
                                db.add(db_book)
                                try:
                                    db.flush()
                                    books_created += 1
                                    logger.info("audiobookshelf_book_created",
                                              hardcover_id=hardcover_id_int,
                                              title=title)
                                except Exception as flush_error:
                                    db.rollback()
                                    logger.warning("audiobookshelf_book_create_failed",
                                                 title=title,
                                                 error=str(flush_error))
                                    continue
                except Exception as e:
                    logger.warning("audiobookshelf_hardcover_lookup_failed",
                                 title=title,
                                 error=str(e))

            if not db_book:
                skipped_count += 1
                continue

            # Update existing book
            if item_id:
                db_book.audiobookshelf_id = item_id
            db_book.audiobook_available = True
            db_book.last_refreshed = datetime.now(timezone.utc)
            db.add(db_book)
            books_updated += 1

            # Update matching requests
            existing_request = db.query(BookRequest).filter(
                BookRequest.book_id == db_book.id,
                BookRequest.format == "audiobook"
            ).first()

            if existing_request and existing_request.status in ("processing", "approved", "pending"):
                existing_request.status = "available"
                existing_request.updated_at = datetime.now(timezone.utc)
                updated_count += 1

            try:
                db.commit()
            except Exception as commit_error:
                logger.warning("audiobookshelf_book_commit_failed",
                             title=title,
                             error=str(commit_error))
                db.rollback()

        logger.info("sync_from_audiobookshelf_complete",
                   audiobookshelf_items=len(items),
                   books_created=books_created,
                   books_updated=books_updated,
                   requests_updated=updated_count,
                   skipped=skipped_count)

    except Exception as e:
        logger.error("sync_from_audiobookshelf_error", error=str(e))
        db.rollback()
    finally:
        db.close()


async def run_background_metadata_sync():
    """Background task to sync missing metadata periodically"""
    job_name = "sync_missing_metadata"
    
    # Wait until next scheduled execution before first run
    initial_wait = get_seconds_until_next_execution(job_name)
    if initial_wait > 0:
        logger.info("background_metadata_sync_waiting", seconds=initial_wait)
        await asyncio.sleep(initial_wait)
    
    while True:
        try:
            await sync_missing_metadata()
            update_job_execution(job_name)
        except Exception as e:
            logger.error("background_metadata_sync_error", error=str(e))
        
        # Get interval from database (default 6 hours)
        interval = get_job_interval_standalone(job_name, 6 * 60 * 60)
        logger.debug("background_metadata_sync_sleeping", interval_seconds=interval)
        await asyncio.sleep(interval)


def get_job_interval(job_name: str, db: Session) -> int:
    """Get job interval from database, falling back to defaults"""
    from app.models import JobSchedule
    
    defaults = {
        "refresh_seed_data": 24 * 60 * 60,
        "check_processing_requests": 5 * 60,
        "sync_from_booklore": 24 * 60 * 60,
        "sync_from_audiobookshelf": 24 * 60 * 60,
        "sync_missing_metadata": 6 * 60 * 60,
    }
    
    try:
        schedule = db.query(JobSchedule).filter(JobSchedule.job_name == job_name).first()
        if schedule:
            return schedule.interval_seconds
    except Exception:
        pass
    
    return defaults.get(job_name, 3600)


async def sync_missing_metadata():
    """
    Find books in the database that are missing metadata (no hardcover_id, 
    no cover, no rating, etc.) and look them up on Hardcover.
    """
    from app.routers.hardcover import lookup_book_by_slug, lookup_book_by_title_author
    
    db: Session = SessionLocal()
    try:
        # Find books missing key metadata
        # Priority 1: Books with hardcover_slug but no hardcover_id (numeric)
        # Priority 2: Books without cover_url
        # Priority 3: Books without rating
        
        books_with_slug_no_id = db.query(Book).filter(
            Book.hardcover_slug.isnot(None),
            Book.hardcover_id.is_(None)
        ).all()
        
        books_without_cover = db.query(Book).filter(
            Book.cover_url.is_(None),
            Book.hardcover_id.is_(None)
        ).limit(50).all()  # Limit to avoid too many API calls
        
        books_without_rating = db.query(Book).filter(
            Book.rating.is_(None),
            Book.hardcover_id.isnot(None)
        ).limit(50).all()
        
        # Books with hardcover_id but missing series_id (and have a series name or position)
        books_without_series_id = db.query(Book).filter(
            Book.hardcover_id.isnot(None),
            Book.series_id.is_(None),
            or_(Book.series.isnot(None), Book.series_position.isnot(None))
        ).limit(50).all()
        
        # Combine and dedupe
        all_books = {b.id: b for b in books_with_slug_no_id + books_without_cover + books_without_rating + books_without_series_id}
        
        if not all_books:
            logger.info("sync_missing_metadata_skipped", reason="no_books_need_update")
            return
        
        logger.info("sync_missing_metadata_starting", 
                   books_count=len(all_books),
                   with_slug_no_id=len(books_with_slug_no_id),
                   without_cover=len(books_without_cover),
                   without_rating=len(books_without_rating),
                   without_series_id=len(books_without_series_id))
        
        updated_count = 0
        failed_count = 0
        skipped_duplicates = 0
        
        for book_id, book in all_books.items():
            try:
                hardcover_data = None
                
                # Try slug first
                if book.hardcover_slug:
                    hardcover_data = await lookup_book_by_slug(book.hardcover_slug, db)
                    await asyncio.sleep(0.5)  # Rate limit protection
                
                # Try title/author if no data yet
                if not hardcover_data and book.title:
                    hardcover_data = await lookup_book_by_title_author(book.title, book.author, db)
                    await asyncio.sleep(0.5)  # Rate limit protection
                
                if hardcover_data:
                    new_hardcover_id = hardcover_data.get("id")
                    
                    # Check if another book already has this hardcover_id
                    if new_hardcover_id:
                        existing = db.query(Book).filter(
                            Book.hardcover_id == new_hardcover_id,
                            Book.id != book.id
                        ).first()
                        
                        if existing:
                            # Another book has this ID - skip this one (it's a duplicate)
                            logger.info("book_skipped_duplicate_hardcover_id",
                                      book_id=book.id,
                                      hardcover_id=new_hardcover_id,
                                      title=book.title,
                                      existing_book_id=existing.id,
                                      existing_title=existing.title)
                            skipped_duplicates += 1
                            continue
                    
                    # Update book with Hardcover data
                    book.hardcover_id = new_hardcover_id
                    book.hardcover_slug = hardcover_data.get("slug") or book.hardcover_slug
                    
                    # Update cover
                    cached_image = hardcover_data.get("cached_image")
                    if cached_image and isinstance(cached_image, dict):
                        book.cover_url = cached_image.get("url") or book.cover_url
                    
                    # Update other metadata
                    book.description = hardcover_data.get("description") or book.description
                    book.page_count = hardcover_data.get("pages") or book.page_count
                    book.rating = hardcover_data.get("rating") or book.rating
                    book.ratings_count = hardcover_data.get("ratings_count") or book.ratings_count
                    book.users_count = hardcover_data.get("users_count") or book.users_count
                    
                    # Update series info
                    book_series = hardcover_data.get("book_series", [])
                    if book_series and len(book_series) > 0:
                        first_series = book_series[0]
                        series_info = first_series.get("series", {})
                        book.series = series_info.get("name") or book.series
                        book.series_id = series_info.get("id") or book.series_id
                        book.series_position = first_series.get("position") or book.series_position
                    
                    # Update genres
                    taggings = hardcover_data.get("taggings", [])
                    if taggings:
                        genres = ", ".join([t.get("tag", {}).get("tag", "") for t in taggings if t.get("tag", {}).get("tag")])
                        if genres:
                            book.genres = genres
                    
                    book.last_refreshed = datetime.now(timezone.utc)
                    db.add(book)
                    
                    try:
                        db.commit()
                        updated_count += 1
                        logger.debug("book_metadata_updated",
                                   book_id=book.id,
                                   hardcover_id=book.hardcover_id,
                                   title=book.title)
                    except Exception as commit_error:
                        db.rollback()
                        logger.warning("book_metadata_commit_failed",
                                     book_id=book.id,
                                     error=str(commit_error))
                        failed_count += 1
                else:
                    logger.debug("book_not_found_on_hardcover",
                               book_id=book.id,
                               title=book.title,
                               slug=book.hardcover_slug)
                    failed_count += 1
                    
            except Exception as e:
                logger.warning("sync_missing_metadata_book_error",
                             book_id=book.id,
                             title=book.title,
                             error=str(e))
                failed_count += 1
                db.rollback()
        
        logger.info("sync_missing_metadata_complete",
                   total_books=len(all_books),
                   updated=updated_count,
                   skipped_duplicates=skipped_duplicates,
                   failed=failed_count)

    except Exception as e:
        logger.error("sync_missing_metadata_error", error=str(e))
        db.rollback()
    finally:
        db.close()


async def sync_download_states():
    """
    Background task to sync download states from download clients.
    Updates orphaned downloads that lost their handler threads after backend restart.
    """
    from app.models import DownloadTask
    db: Session = SessionLocal()
    try:
        logger.info("sync_download_states_starting")

        # Get all active download tasks that might need syncing
        tasks = db.query(DownloadTask).filter(
            or_(
                DownloadTask.state.in_(['downloading', 'queued', 'checking', 'paused']),
                and_(
                    DownloadTask.state.in_(['complete', 'seeding']),
                    or_(
                        DownloadTask.import_status.is_(None),
                        DownloadTask.import_status != 'imported',
                    ),
                ),
            )
        ).all()

        if not tasks:
            logger.info("sync_download_states_no_tasks")
            return

        logger.info("sync_download_states_found_tasks", count=len(tasks))

        # Group tasks by protocol
        torrent_tasks = [t for t in tasks if t.protocol == 'torrent']
        usenet_tasks = [t for t in tasks if t.protocol == 'usenet']

        updated_count = 0

        # Sync torrent downloads
        if torrent_tasks:
            updated_count += await _sync_torrent_downloads(db, torrent_tasks)

        # Sync usenet downloads
        if usenet_tasks:
            updated_count += await _sync_usenet_downloads(db, usenet_tasks)

        logger.info("sync_download_states_complete",
                   total_tasks=len(tasks),
                   updated=updated_count)

    except Exception as e:
        logger.error("sync_download_states_error", error=str(e))
        db.rollback()
    finally:
        db.close()


async def _sync_torrent_downloads(db: Session, tasks: list) -> int:
    """Sync torrent download states from qBittorrent"""
    from app.models import DownloadClient
    from app.downloads.clients.qbittorrent import QBittorrentClient

    try:
        # Get enabled qBittorrent client
        client_config = db.query(DownloadClient).filter(
            DownloadClient.type == 'qbittorrent',
            DownloadClient.enabled == True
        ).first()

        if not client_config:
            logger.warning("sync_torrents_no_client")
            return 0

        # Connect to client
        client = QBittorrentClient(
            host=client_config.host,
            port=client_config.port,
            username=client_config.username,
            password=client_config.password,
            use_ssl=client_config.use_ssl,
            url_base=client_config.url_base,
        )

        if not client.test_connection():
            logger.error("sync_torrents_connection_failed")
            return 0

        # Get all torrents from client
        all_torrents = client.client.torrents_info()
        torrent_map = {t.hash.lower(): t for t in all_torrents}

        logger.info("sync_torrents_fetched", count=len(all_torrents))

        updated = 0
        now = datetime.now(timezone.utc)
        for task in tasks:
            torrent_hash = _client_download_key(task)
            stale = _task_is_stale(task, now)
            if not torrent_hash:
                if stale:
                    _mark_download_reconciliation_failed(
                        task,
                        "Download has no qBittorrent client identifier",
                        client_state="missing-id",
                    )
                    updated += 1
                continue

            torrent = torrent_map.get(torrent_hash.lower())
            if torrent is None:
                if stale:
                    _mark_download_reconciliation_failed(
                        task,
                        "Download no longer exists in qBittorrent",
                        client_state="missing",
                    )
                    updated += 1
                continue

            old_state = task.state
            old_client_state = task.client_state
            qb_state = str(torrent.state).lower()
            progress = float(torrent.progress) * 100

            if 'error' in qb_state or 'missing' in qb_state:
                _mark_download_reconciliation_failed(
                    task,
                    f"qBittorrent error: {torrent.state}",
                    client_state=str(torrent.state),
                )
            elif qb_state == 'stalleddl' and _torrent_stalled_too_long(torrent, now):
                _mark_download_reconciliation_failed(
                    task,
                    "qBittorrent download has made no progress for 24 hours",
                    client_state=str(torrent.state),
                )
            elif float(torrent.progress) >= 1.0:
                task.state = 'seeding' if qb_state.endswith('up') else 'complete'
                if not task.completed_at:
                    task.completed_at = now
            elif qb_state in ['pauseddl', 'pausedup']:
                task.state = 'paused'
            elif qb_state in ['queueddl', 'queuedup']:
                task.state = 'queued'
            elif qb_state in ['checkingdl', 'checkingup', 'checkingresumedata']:
                task.state = 'checking'
            else:
                task.state = 'downloading'

            task.client_state = str(torrent.state)
            task.progress = progress

            if old_state != task.state or old_client_state != task.client_state:
                logger.info("sync_torrent_updated",
                           task_id=task.id,
                           old_state=old_state,
                           new_state=task.state,
                           old_client_state=old_client_state,
                           new_client_state=task.client_state,
                           progress=task.progress)
                updated += 1

            if task.state in ('complete', 'seeding') and stale and task.import_status != 'imported':
                if _recover_completed_download(db, task, client, torrent_hash):
                    updated += 1

        db.commit()
        return updated

    except Exception as e:
        logger.error("sync_torrents_error", error=str(e))
        return 0


async def _sync_usenet_downloads(db: Session, tasks: list) -> int:
    """Sync usenet download states from the configured SABnzbd/NZBGet clients."""
    from app.models import DownloadClient

    try:
        client_configs = db.query(DownloadClient).filter(
            DownloadClient.protocol == 'usenet',
            DownloadClient.enabled == True
        ).order_by(DownloadClient.priority.desc()).all()

        if not client_configs:
            logger.warning("sync_usenet_no_client")
            return 0

        config_by_type = {config.type: config for config in client_configs}
        clients = {}

        updated = 0
        now = datetime.now(timezone.utc)
        for task in tasks:
            stale = _task_is_stale(task, now)
            client_type = task.client_type if task.client_type in config_by_type else client_configs[0].type
            client_config = config_by_type.get(client_type)
            client_id = _client_download_key(task)

            if client_config is None or not client_id:
                if stale:
                    _mark_download_reconciliation_failed(
                        task,
                        f"Usenet task cannot resolve its {client_type or 'configured'} client",
                        client_state="missing-client",
                    )
                    updated += 1
                continue

            if client_type not in clients:
                client = _build_usenet_client(client_config)
                if not client.test_connection():
                    logger.error("sync_usenet_connection_failed", client_type=client_type)
                    clients[client_type] = None
                else:
                    clients[client_type] = client
            client = clients[client_type]
            if client is None:
                continue

            old_state = task.state
            old_client_state = task.client_state
            status = client.get_download_status(client_id)
            state = _download_state_name(status.get('state'))

            if state == 'error':
                _mark_download_reconciliation_failed(
                    task,
                    status.get('message') or status.get('fail_message') or f"{client_type} download failed",
                    client_state='error',
                )
            elif state == 'complete':
                task.state = 'complete'
                if not task.completed_at:
                    task.completed_at = now
            elif state in ('processing', 'checking'):
                task.state = 'checking'
            elif state == 'paused':
                task.state = 'paused'
            elif state == 'queued':
                task.state = 'queued'
            else:
                task.state = 'downloading'

            task.client_state = state
            task.progress = float(status.get('progress') or 0)

            if old_state != task.state or old_client_state != task.client_state:
                logger.info("sync_usenet_updated",
                           task_id=task.id,
                           client_type=client_type,
                           old_state=old_state,
                           new_state=task.state,
                           old_client_state=old_client_state,
                           new_client_state=task.client_state,
                           progress=task.progress)
                updated += 1

            if task.state == 'complete' and stale and task.import_status != 'imported':
                if _recover_completed_download(db, task, client, client_id):
                    updated += 1

        db.commit()
        return updated

    except Exception as e:
        logger.error("sync_usenet_error", error=str(e))
        return 0


def _client_download_key(task) -> Optional[str]:
    """Return the real download-client identifier, with a legacy torrent fallback."""
    if task.client_download_id:
        return str(task.client_download_id)
    if task.protocol == 'torrent' and task.info_hash and len(task.info_hash) == 40:
        return str(task.info_hash)
    return None


def _task_is_stale(task, now: datetime, after: timedelta = timedelta(minutes=10)) -> bool:
    last_touch = task.updated_at or task.started_at or task.created_at
    if last_touch is None:
        return True
    if last_touch.tzinfo is None:
        last_touch = last_touch.replace(tzinfo=timezone.utc)
    return now - last_touch >= after


def _torrent_stalled_too_long(torrent, now: datetime, after: timedelta = timedelta(hours=24)) -> bool:
    last_activity = getattr(torrent, 'last_activity', None)
    if not last_activity or int(last_activity) <= 0:
        return False
    return now - datetime.fromtimestamp(int(last_activity), tz=timezone.utc) >= after


def _download_state_name(state) -> str:
    return str(getattr(state, 'value', state) or '').lower()


def _build_usenet_client(config):
    if config.type == 'sabnzbd':
        from app.downloads.clients.sabnzbd import SabnzbdClient
        return SabnzbdClient(
            host=config.host,
            port=config.port,
            api_key=config.api_key or config.password,
            use_ssl=config.use_ssl,
            url_base=config.url_base,
            category=config.category,
        )
    if config.type == 'nzbget':
        from app.downloads.clients.nzbget import NZBGetClient
        return NZBGetClient(
            host=config.host,
            port=config.port,
            username=config.username,
            password=config.password,
            use_ssl=config.use_ssl,
            url_base=config.url_base,
            category=config.category,
        )
    raise ValueError(f"Unsupported Usenet client type: {config.type}")


def _resolve_torrent_rescan_id(task, client) -> Optional[str]:
    """Resolve a torrent task to an exact info hash without adding anything."""
    client_id = _client_download_key(task)
    if client_id:
        return client_id.lower()

    source = task.download_url or ""
    if source.startswith("magnet:"):
        return client._extract_hash_from_magnet(source)
    if source.startswith(("http://", "https://")):
        from app.downloads.clients.qbittorrent import extract_info_hash_from_torrent

        torrent_data = client._download_torrent_file(source, log_errors=False)
        if torrent_data:
            return extract_info_hash_from_torrent(torrent_data)
    return None


def _normalized_usenet_name(value: Optional[str]) -> str:
    value = (value or "").strip().casefold()
    return value[:-4] if value.endswith(".nzb") else value


def _find_usenet_rescan_ids(task, client, client_type: str) -> list[str]:
    """Find unique exact-name/category matches in a Usenet queue and history."""
    expected_name = _normalized_usenet_name(task.release_title)
    expected_category = (client.category or "").casefold()
    if not expected_name:
        return []

    matches = set()
    items = [(item, False) for item in client.get_queue()]
    items.extend((item, True) for item in client.get_history())
    for item, is_history in items:
        if client_type == "sabnzbd":
            name = item.get("name" if is_history else "filename")
            category = item.get("category" if is_history else "cat", "")
            item_id = item.get("nzo_id")
        else:
            name = item.get("NZBName")
            category = item.get("Category", "")
            item_id = item.get("NZBID")

        if _normalized_usenet_name(name) != expected_name:
            continue
        if expected_category and str(category).casefold() != expected_category:
            continue
        if item_id is not None:
            matches.add(str(item_id))
    return sorted(matches)


def _rescan_result(task, outcome: str, message: str, client_id: Optional[str] = None) -> dict:
    return {
        "task_id": task.id,
        "protocol": task.protocol,
        "outcome": outcome,
        "message": message,
        "client_id": client_id,
    }


def rescan_downloads(db: Session, dry_run: bool = True) -> dict:
    """Reconcile non-imported tasks with existing qBittorrent/Usenet items."""
    from app.downloads.handlers.torrent import TorrentHandler
    from app.downloads.handlers.usenet import UsenetHandler
    from app.models import DownloadTask

    tasks = db.query(DownloadTask).filter(
        DownloadTask.protocol.in_(("torrent", "usenet")),
        or_(
            DownloadTask.import_status.is_(None),
            DownloadTask.import_status != "imported",
        ),
    ).order_by(DownloadTask.id.desc()).all()

    summary = {
        "dry_run": dry_run,
        "scanned": len(tasks),
        "matched": 0,
        "completed": 0,
        "imported": 0,
        "active": 0,
        "unmatched": 0,
        "ambiguous": 0,
        "mismatched": 0,
        "skipped": 0,
        "failed": 0,
        "results": [],
    }
    torrent_handler = TorrentHandler(db_session=db)
    usenet_handler = UsenetHandler(db_session=db)
    now = datetime.now(timezone.utc)
    completed_keys = set()

    for task in tasks:
        try:
            client = torrent_handler._get_client(task) if task.protocol == "torrent" else usenet_handler._get_client(task)
            if client is None:
                summary["failed"] += 1
                summary["results"].append(_rescan_result(task, "failed", "No enabled download client"))
                continue

            if task.protocol == "torrent":
                client_id = _resolve_torrent_rescan_id(task, client)
                if not client_id or not client.find_existing_download(info_hash=client_id):
                    summary["unmatched"] += 1
                    summary["results"].append(
                        _rescan_result(task, "unmatched", "No exact qBittorrent hash match")
                    )
                    continue
                client_type = "qbittorrent"
            else:
                client_type = task.client_type or type(client).__name__.removesuffix("Client").lower()
                client_id = str(task.client_download_id) if task.client_download_id else None
                if client_id:
                    if client_type == "sabnzbd":
                        client_id = client.find_existing_download(nzo_id=client_id)
                    else:
                        client_id = client.find_existing_download(nzb_id=int(client_id))
                    client_id = str(client_id) if client_id is not None else None
                else:
                    matches = _find_usenet_rescan_ids(task, client, client_type)
                    if len(matches) > 1:
                        summary["ambiguous"] += 1
                        summary["results"].append(
                            _rescan_result(task, "ambiguous", "Multiple exact Usenet matches")
                        )
                        continue
                    client_id = matches[0] if matches else None
                if not client_id:
                    summary["unmatched"] += 1
                    summary["results"].append(
                        _rescan_result(task, "unmatched", "No exact Usenet match")
                    )
                    continue

            summary["matched"] += 1
            status = client.get_download_status(client_id)
            state = _download_state_name(status.get("state"))
            if state == "error":
                summary["failed"] += 1
                summary["results"].append(
                    _rescan_result(task, "failed", status.get("message") or "Client reports an error", client_id)
                )
                continue

            if state in ("complete", "seeding"):
                source_path = client.get_completed_download_path(client_id)
                if not source_path:
                    summary["failed"] += 1
                    summary["results"].append(
                        _rescan_result(task, "failed", "Completed client item has no source path", client_id)
                    )
                    continue
                if not _payload_matches_book(task.book.title, task.format, source_path):
                    summary["mismatched"] += 1
                    summary["results"].append(
                        _rescan_result(
                            task,
                            "mismatched",
                            "Completed payload does not match the requested book and format",
                            client_id,
                        )
                    )
                    logger.error(
                        "download_payload_mismatch",
                        task_id=task.id,
                        book_id=task.book_id,
                        format=task.format,
                        protocol=task.protocol,
                    )
                    continue
                task_key = (task.book_id, task.format)
                if task_key in completed_keys:
                    summary["skipped"] += 1
                    summary["results"].append(
                        _rescan_result(
                            task,
                            "skipped",
                            "A newer completed task for this book and format was selected",
                            client_id,
                        )
                    )
                    continue
                completed_keys.add(task_key)
                summary["completed"] += 1
                if dry_run:
                    summary["results"].append(
                        _rescan_result(task, "would_import", "Completed download is ready to import", client_id)
                    )
                    continue

                task.client_type = client_type
                task.client_download_id = client_id
                task.client_state = state
                task.state = state
                task.progress = 100.0
                task.completed_at = task.completed_at or now
                task.message = "Recovered by download rescan"
                db.commit()
                if _recover_completed_download(db, task, client, client_id, source_path=source_path):
                    summary["imported"] += 1
                    summary["results"].append(
                        _rescan_result(task, "imported", task.import_message or "Imported", client_id)
                    )
                else:
                    summary["failed"] += 1
                    summary["results"].append(
                        _rescan_result(task, "failed", task.import_message or "Import failed", client_id)
                    )
                continue

            summary["active"] += 1
            summary["results"].append(
                _rescan_result(task, "active", f"Matched non-complete client item ({state}); left unchanged", client_id)
            )

        except Exception as exc:
            summary["failed"] += 1
            message = f"Rescan failed with {type(exc).__name__}"
            summary["results"].append(_rescan_result(task, "failed", message))
            logger.error(
                "download_rescan_failed",
                task_id=task.id,
                protocol=task.protocol,
                error_type=type(exc).__name__,
            )

    if not dry_run:
        db.commit()
    logger.info(
        "download_rescan_complete",
        dry_run=dry_run,
        scanned=summary["scanned"],
        matched=summary["matched"],
        completed=summary["completed"],
        imported=summary["imported"],
        active=summary["active"],
        unmatched=summary["unmatched"],
        ambiguous=summary["ambiguous"],
        mismatched=summary["mismatched"],
        skipped=summary["skipped"],
        failed=summary["failed"],
    )
    return summary


def _mark_download_reconciliation_failed(task, message: str, client_state: str) -> None:
    task.state = 'error'
    task.client_state = client_state
    task.message = message
    logger.error(
        "download_reconciliation_failed",
        task_id=task.id,
        book_id=task.book_id,
        format=task.format,
        protocol=task.protocol,
        client_type=task.client_type,
        reason=message,
    )


def _recover_completed_download(
    db: Session,
    task,
    client,
    client_id: str,
    source_path: Optional[str] = None,
) -> bool:
    from app.downloads.orchestrator import DownloadOrchestrator

    try:
        source_path = source_path or client.get_completed_download_path(client_id)
        if not source_path:
            task.import_status = 'failed'
            task.import_message = 'Download completed, but the client returned no source path'
            db.commit()
            logger.error(
                "download_reconciliation_failed",
                task_id=task.id,
                book_id=task.book_id,
                format=task.format,
                protocol=task.protocol,
                reason=task.import_message,
            )
            return False

        orchestrator = DownloadOrchestrator(db_session=db)
        destination = orchestrator._copy_to_destination(task, source_path, db)
        if not destination or task.import_status != 'imported':
            logger.error(
                "download_reconciliation_failed",
                task_id=task.id,
                book_id=task.book_id,
                format=task.format,
                protocol=task.protocol,
                reason=task.import_message or 'Import did not complete',
            )
            return False

        task.download_path = destination
        task.final_path = destination
        orchestrator._update_book_availability(task, db)
        db.commit()
        logger.info(
            "download_reconciliation_recovered",
            task_id=task.id,
            book_id=task.book_id,
            format=task.format,
            destination=destination,
        )
        return True
    except Exception as exc:
        db.rollback()
        task.import_status = 'failed'
        task.import_message = f'Import recovery failed: {exc}'
        db.commit()
        logger.error(
            "download_reconciliation_failed",
            task_id=task.id,
            book_id=task.book_id,
            format=task.format,
            protocol=task.protocol,
            reason=str(exc),
        )
        return False


# ---------------------------------------------------------------------------
# Hardcover list / to-read sync
# ---------------------------------------------------------------------------

_TO_READ_QUERY = """
{
  me {
    user_books(where: {status_id: {_eq: 1}}) {
      book {
        id
        title
      }
    }
  }
}
"""

_LIST_BOOKS_QUERY = """
query GetListBooks($list_id: Int!) {
  list_books(where: {list_id: {_eq: $list_id}}) {
    book_id
  }
}
"""

_GET_BOOK_QUERY = """
query GetBook($id: Int!) {
  books_by_pk(id: $id) {
    id
    title
    slug
    release_year
    release_date
    pages
    description
    cached_image
    cached_contributors
    rating
    ratings_count
    users_count
    activities_count
    default_ebook_edition_id
    default_audio_edition_id
    default_physical_edition_id
    book_series {
      position
      series {
        id
        name
      }
    }
    contributions {
      author {
        id
        name
        slug
      }
    }
    taggings(limit: 10) {
      tag {
        tag
      }
    }
  }
}
"""


async def _hardcover_graphql(query: str, variables: dict, token: str) -> dict:
    """Execute a Hardcover GraphQL query with a specific token."""
    import httpx
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            "https://api.hardcover.app/v1/graphql",
            headers=headers,
            json={"query": query, "variables": variables or {}},
        )
    if not response.is_success:
        logger.warning("hardcover_sync_request_failed", status=response.status_code)
        return {}
    data = response.json()
    if "errors" in data:
        logger.warning("hardcover_sync_graphql_errors", errors=data["errors"])
        return {}
    return data.get("data", {})


async def _ensure_book_in_db(hardcover_id: int, token: str, db: Session) -> Optional[Any]:
    """Return the local Book for a Hardcover ID, creating it from the API if needed."""
    from app.routers.hardcover import _parse_hardcover_book

    existing = db.query(Book).filter(Book.hardcover_id == hardcover_id).first()
    if existing:
        return existing

    # Fetch from Hardcover
    data = await _hardcover_graphql(_GET_BOOK_QUERY, {"id": hardcover_id}, token)
    book_data = data.get("books_by_pk")
    if not book_data:
        return None

    try:
        parsed = _parse_hardcover_book(book_data)
    except Exception as e:
        logger.warning("hardcover_sync_parse_failed", hardcover_id=hardcover_id, error=str(e))
        return None

    cover_url = None
    if parsed.cached_image and isinstance(parsed.cached_image, dict):
        cover_url = parsed.cached_image.get("url")

    author = None
    if parsed.contributions:
        author = parsed.contributions[0].author.name if parsed.contributions else None

    genres = ",".join(t.tag.tag for t in (parsed.taggings or []) if t.tag) or None

    db_book = Book(
        title=parsed.title,
        author=author,
        hardcover_id=parsed.id,
        hardcover_slug=parsed.slug,
        cover_url=cover_url,
        description=parsed.description,
        page_count=parsed.pages,
        rating=parsed.rating,
        ratings_count=parsed.ratings_count,
        users_count=parsed.users_count,
        genres=genres,
        release_year=parsed.release_year,
        is_seed_data=False,
    )
    db.add(db_book)
    try:
        db.commit()
        db.refresh(db_book)
        logger.info("hardcover_sync_book_created", hardcover_id=hardcover_id, title=parsed.title)
        return db_book
    except Exception as e:
        db.rollback()
        logger.warning("hardcover_sync_book_create_failed", hardcover_id=hardcover_id, error=str(e))
        return None


async def sync_hardcover_lists_for_user(user_id: int) -> None:
    """Sync Hardcover to-read / lists for a single user and create pending requests."""
    from app.models import UserHardcoverSync, BookRequest, User
    from app.encryption import decrypt_value
    import json

    db: Session = SessionLocal()
    try:
        config = db.query(UserHardcoverSync).filter(
            UserHardcoverSync.user_id == user_id
        ).first()
        if not config or not config.is_enabled:
            return

        user = db.query(User).filter(User.id == user_id).first()
        if not user or not user.is_active:
            return

        # Use personal token if set, otherwise fall back to the global app token
        if config.hardcover_api_token:
            token = decrypt_value(config.hardcover_api_token)
        else:
            from app.routers.settings import get_hardcover_token as _get_global_token
            token, _ = _get_global_token(db)
        if not token:
            logger.warning("hardcover_sync_no_token", user_id=user_id)
            return
        hardcover_ids: set = set()

        # Collect to-read books
        if config.sync_to_read:
            data = await _hardcover_graphql(_TO_READ_QUERY, {}, token)
            for ub in data.get("me", [])[0].get("user_books", []):
                bid = ub.get("book", {}).get("id")
                if bid:
                    hardcover_ids.add(int(bid))

        # Collect list books
        list_ids = json.loads(config.sync_list_ids or "[]")
        for list_id in list_ids:
            data = await _hardcover_graphql(_LIST_BOOKS_QUERY, {"list_id": list_id}, token)
            for lb in data.get("list_books", []):
                bid = lb.get("book_id")
                if bid:
                    hardcover_ids.add(int(bid))

        formats = (
            ["ebook", "audiobook"] if config.default_format == "both"
            else [config.default_format or "ebook"]
        )

        requested = 0
        skipped = 0

        for hc_id in hardcover_ids:
            db_book = await _ensure_book_in_db(hc_id, token, db)
            if not db_book:
                skipped += 1
                continue

            for fmt in formats:
                # Skip if user lacks permission
                if fmt == "ebook" and not user.can_request_ebook:
                    continue
                if fmt == "audiobook" and not user.can_request_audiobook:
                    continue

                # Skip if already in library
                if fmt == "ebook" and db_book.ebook_available:
                    continue
                if fmt == "audiobook" and db_book.audiobook_available:
                    continue

                # Skip if a non-denied request already exists
                existing = db.query(BookRequest).filter(
                    BookRequest.book_id == db_book.id,
                    BookRequest.format == fmt,
                    BookRequest.status != "denied",
                ).first()
                if existing:
                    continue

                initial_status = "approved" if (
                    (fmt == "ebook" and user.auto_approve_ebooks) or
                    (fmt == "audiobook" and user.auto_approve_audiobooks)
                ) else "pending"

                db_request = BookRequest(
                    book_id=db_book.id,
                    user_id=user.id,
                    format=fmt,
                    status=initial_status,
                    source="hardcover_sync",
                )
                db.add(db_request)
                try:
                    db.commit()
                    db.refresh(db_request)
                    requested += 1
                    logger.info(
                        "hardcover_sync_request_created",
                        user_id=user.id,
                        book_id=db_book.id,
                        hardcover_id=hc_id,
                        format=fmt,
                        status=initial_status,
                    )
                except Exception as e:
                    db.rollback()
                    logger.warning("hardcover_sync_request_failed", book_id=db_book.id, error=str(e))

            await asyncio.sleep(0.2)  # gentle rate limit

        # Update last synced timestamp
        config.last_synced_at = datetime.now(timezone.utc)
        db.commit()

        logger.info(
            "hardcover_sync_user_complete",
            user_id=user_id,
            hardcover_ids=len(hardcover_ids),
            requested=requested,
            skipped=skipped,
        )
    except Exception as e:
        logger.error("hardcover_sync_user_error", user_id=user_id, error=str(e))
    finally:
        db.close()


async def sync_hardcover_lists() -> None:
    """Global job: sync Hardcover lists for all users with sync enabled."""
    from app.models import UserHardcoverSync

    db: Session = SessionLocal()
    try:
        configs = db.query(UserHardcoverSync).filter(
            UserHardcoverSync.is_enabled == True,
        ).all()
        user_ids = [c.user_id for c in configs]
    finally:
        db.close()

    logger.info("hardcover_sync_job_starting", user_count=len(user_ids))
    for uid in user_ids:
        await sync_hardcover_lists_for_user(uid)
    logger.info("hardcover_sync_job_complete", user_count=len(user_ids))
