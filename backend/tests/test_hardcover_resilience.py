import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Book, Series
from app.routers import hardcover


class FakeResponse:
    def __init__(self, status_code, *, headers=None, payload=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class FakeAsyncClient:
    responses = []
    calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, *args, **kwargs):
        type(self).calls += 1
        return type(self).responses.pop(0)


@pytest.fixture(autouse=True)
def reset_rate_limit_circuit():
    hardcover._hardcover_rate_limited_until = 0.0
    FakeAsyncClient.responses = []
    FakeAsyncClient.calls = 0
    yield
    hardcover._hardcover_rate_limited_until = 0.0


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.mark.asyncio
async def test_long_retry_after_opens_circuit_without_sleeping(monkeypatch):
    FakeAsyncClient.responses = [
        FakeResponse(429, headers={"Retry-After": "3600"})
    ]
    monkeypatch.setattr(hardcover.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(hardcover, "get_hardcover_token", lambda: "token")

    async def unexpected_sleep(_delay):
        raise AssertionError("long rate limits must not sleep inside a request")

    monkeypatch.setattr(hardcover.asyncio, "sleep", unexpected_sleep)

    with pytest.raises(HTTPException) as first_error:
        await hardcover.execute_graphql("query GetBook($id: Int!) { books_by_pk(id: $id) { id } }")

    assert first_error.value.status_code == 429
    assert first_error.value.headers["Retry-After"] == "3600"
    assert FakeAsyncClient.calls == 1

    with pytest.raises(HTTPException) as circuit_error:
        await hardcover.execute_graphql("query GetBook($id: Int!) { books_by_pk(id: $id) { id } }")

    assert circuit_error.value.status_code == 429
    assert FakeAsyncClient.calls == 1


@pytest.mark.asyncio
async def test_short_retry_after_is_bounded_and_retried(monkeypatch):
    FakeAsyncClient.responses = [
        FakeResponse(429, headers={"Retry-After": "1"}),
        FakeResponse(200, payload={"data": {"books_by_pk": {"id": 1}}}),
    ]
    monkeypatch.setattr(hardcover.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(hardcover, "get_hardcover_token", lambda: "token")
    sleeps = []

    async def record_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(hardcover.asyncio, "sleep", record_sleep)

    result = await hardcover.execute_graphql(
        "query GetBook($id: Int!) { books_by_pk(id: $id) { id } }"
    )

    assert result == {"books_by_pk": {"id": 1}}
    assert sleeps == [1.0]
    assert FakeAsyncClient.calls == 2


@pytest.mark.asyncio
async def test_book_details_fall_back_to_database_on_rate_limit(
    monkeypatch, db_session
):
    db_session.add(
        Book(
            title="Local Book",
            author="Local Author",
            hardcover_id=123,
            description="Stored description",
            cover_url="https://example.invalid/cover.jpg",
        )
    )
    db_session.commit()

    async def cache_miss(_key):
        return None

    async def rate_limited(*args, **kwargs):
        raise HTTPException(status_code=429, detail="rate limited")

    monkeypatch.setattr(hardcover.cache, "get_cached", cache_miss)
    monkeypatch.setattr(hardcover, "execute_graphql", rate_limited)

    response = await hardcover.get_book_details(123, db=db_session)

    assert response.books_by_pk.title == "Local Book"
    assert response.books_by_pk.description == "Stored description"


@pytest.mark.asyncio
async def test_series_falls_back_to_database_on_rate_limit(monkeypatch, db_session):
    db_session.add(Series(hardcover_id=77, name="Local Series", books_count=2))
    db_session.add_all(
        [
            Book(
                title="Second",
                author="Local Author",
                hardcover_id=702,
                series="Local Series",
                series_id=77,
                series_position=2,
            ),
            Book(
                title="First",
                author="Local Author",
                hardcover_id=701,
                series="Local Series",
                series_id=77,
                series_position=1,
            ),
        ]
    )
    db_session.commit()

    async def cache_miss(_key):
        return None

    async def rate_limited(*args, **kwargs):
        raise HTTPException(status_code=429, detail="rate limited")

    monkeypatch.setattr(hardcover.cache, "get_cached", cache_miss)
    monkeypatch.setattr(hardcover, "execute_graphql", rate_limited)

    response = await hardcover.get_series_books(77, db=db_session)

    assert response.series_by_pk.name == "Local Series"
    assert [entry.book.title for entry in response.series_by_pk.book_series] == [
        "First",
        "Second",
    ]
