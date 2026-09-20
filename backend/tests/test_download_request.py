"""Tests for the manual download request contract."""

from app.routers.downloads import DownloadRequest


def test_download_request_defaults_missing_release_metadata():
    request = DownloadRequest(
        book_id=1,
        format_type="ebook",
        download_url="https://example.invalid/book.epub",
        protocol="direct",
        release_title="Example Book",
    )

    assert request.indexer == ""
    assert request.size_bytes == 0
