"""Tests for the opaque manual download request contract."""

from app.routers.downloads import DownloadRequest
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
