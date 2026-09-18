from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.downloads.orchestrator import DownloadOrchestrator
from app.tasks import (
    _build_usenet_client,
    _client_download_key,
    _mark_download_reconciliation_failed,
    _recover_completed_download,
    _task_is_stale,
    _torrent_stalled_too_long,
)


def _task(**overrides):
    values = {
        "id": 7,
        "book_id": 11,
        "format": "audiobook",
        "protocol": "torrent",
        "client_type": "qbittorrent",
        "client_download_id": "a" * 40,
        "info_hash": "short-url-hash",
        "state": "downloading",
        "client_state": None,
        "message": None,
        "import_status": "pending",
        "import_message": None,
        "updated_at": datetime.now(timezone.utc) - timedelta(hours=1),
        "started_at": None,
        "created_at": None,
        "download_url": "https://example.invalid/release",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_client_download_key_prefers_real_client_identifier():
    task = _task(client_download_id="real-qbittorrent-hash", info_hash="url-fingerprint")
    assert _client_download_key(task) == "real-qbittorrent-hash"


def test_client_download_key_only_uses_legacy_full_torrent_hash():
    assert _client_download_key(_task(client_download_id=None, info_hash="b" * 40)) == "b" * 40
    assert _client_download_key(_task(client_download_id=None, info_hash="b" * 16)) is None


def test_stale_and_stalled_thresholds_are_time_based():
    now = datetime.now(timezone.utc)
    assert _task_is_stale(_task(updated_at=now - timedelta(minutes=11)), now)
    assert not _task_is_stale(_task(updated_at=now - timedelta(minutes=9)), now)

    old = SimpleNamespace(last_activity=int((now - timedelta(hours=25)).timestamp()))
    recent = SimpleNamespace(last_activity=int((now - timedelta(hours=23)).timestamp()))
    assert _torrent_stalled_too_long(old, now)
    assert not _torrent_stalled_too_long(recent, now)


def test_reconciliation_failure_is_an_explicit_task_failure():
    task = _task()
    _mark_download_reconciliation_failed(task, "client item missing", "missing")
    assert task.state == "error"
    assert task.client_state == "missing"
    assert task.message == "client item missing"


def test_sabnzbd_config_builds_sabnzbd_client():
    config = SimpleNamespace(
        type="sabnzbd",
        host="sabnzbd",
        port=8080,
        api_key="secret",
        password=None,
        use_ssl=False,
        url_base="",
        category="books",
    )
    with patch("app.downloads.clients.sabnzbd.SabnzbdClient") as client_class:
        assert _build_usenet_client(config) is client_class.return_value
        client_class.assert_called_once_with(
            host="sabnzbd",
            port=8080,
            api_key="secret",
            use_ssl=False,
            url_base="",
            category="books",
        )


def test_recovered_download_is_finalized_only_after_import():
    task = _task()
    client = MagicMock()
    client.get_completed_download_path.return_value = "/downloads/book"
    db = MagicMock()

    with patch("app.downloads.orchestrator.DownloadOrchestrator") as orchestrator_class:
        orchestrator = orchestrator_class.return_value

        def imported(*_args):
            task.import_status = "imported"
            return "/library/book"

        orchestrator._copy_to_destination.side_effect = imported
        assert _recover_completed_download(db, task, client, task.client_download_id)
        orchestrator._update_book_availability.assert_called_once_with(task, db)
        assert task.download_path == "/library/book"
        assert task.final_path == "/library/book"


def test_availability_does_not_move_before_import():
    task = _task(import_status="failed")
    db = MagicMock()
    DownloadOrchestrator()._update_book_availability(task, db)
    db.query.assert_not_called()


def test_successful_import_updates_book_and_all_open_requests():
    task = _task(import_status="imported")
    book = SimpleNamespace(
        id=task.book_id,
        ebook_available=False,
        audiobook_available=False,
        downloaded_release_hashes=None,
    )
    requests = [
        SimpleNamespace(id=19, status="processing", updated_at=None),
        SimpleNamespace(id=20, status="approved", updated_at=None),
    ]
    book_query = MagicMock()
    book_query.filter.return_value.first.return_value = book
    request_query = MagicMock()
    request_query.filter.return_value.all.return_value = requests
    db = MagicMock()
    db.query.side_effect = [book_query, request_query]

    DownloadOrchestrator()._update_book_availability(task, db)

    assert book.audiobook_available is True
    assert all(request.status == "available" for request in requests)
    assert all(request.updated_at is not None for request in requests)
    db.commit.assert_called_once()
