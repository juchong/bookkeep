from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.downloads.orchestrator import DownloadOrchestrator, _payload_matches_book
from app.tasks import (
    _build_usenet_client,
    _client_download_key,
    _find_usenet_rescan_ids,
    _mark_download_reconciliation_failed,
    _resolve_torrent_rescan_id,
    _recover_completed_download,
    _task_is_stale,
    _torrent_stalled_too_long,
    rescan_downloads,
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
        "release_title": "Example Book",
        "completed_at": None,
        "progress": 0.0,
        "book": SimpleNamespace(title="Example Book"),
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


def test_rescan_derives_exact_torrent_hash_from_stored_release():
    task = _task(client_download_id=None, info_hash="short-url-hash")
    client = MagicMock()
    client._download_torrent_file.return_value = b"d4:infod4:name4:testee"

    resolved = _resolve_torrent_rescan_id(task, client)

    assert resolved is not None
    assert len(resolved) == 40


def test_rescan_requires_unique_usenet_name_and_category():
    task = _task(protocol="usenet", release_title="Example Book")
    client = MagicMock(category="bookkeep")
    client.get_queue.return_value = []
    client.get_history.return_value = [
        {"name": "Example Book.nzb", "category": "bookkeep", "nzo_id": "one"},
        {"name": "Example Book", "category": "bookkeep", "nzo_id": "two"},
        {"name": "Example Book", "category": "other", "nzo_id": "ignored"},
    ]

    assert _find_usenet_rescan_ids(task, client, "sabnzbd") == ["one", "two"]


def test_rescan_dry_run_previews_without_mutating():
    task = _task(client_download_id="a" * 40, state="error")
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [task]
    client = MagicMock()
    client.find_existing_download.return_value = task.client_download_id
    client.get_download_status.return_value = {"state": "seeding", "progress": 100.0}
    client.get_completed_download_path.return_value = "/downloads/Example Book.m4b"

    with (
        patch("app.downloads.handlers.torrent.TorrentHandler._get_client", return_value=client),
        patch("app.tasks._payload_matches_book", return_value=True),
    ):
        result = rescan_downloads(db, dry_run=True)

    assert result["completed"] == 1
    assert result["results"][0]["outcome"] == "would_import"
    assert task.state == "error"
    db.commit.assert_not_called()


def test_rescan_apply_reattaches_and_imports_completed_task():
    task = _task(client_download_id="a" * 40, state="error")
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [task]
    client = MagicMock()
    client.find_existing_download.return_value = task.client_download_id
    client.get_download_status.return_value = {"state": "seeding", "progress": 100.0}
    client.get_completed_download_path.return_value = "/downloads/Example Book.m4b"

    with (
        patch("app.downloads.handlers.torrent.TorrentHandler._get_client", return_value=client),
        patch("app.tasks._payload_matches_book", return_value=True),
        patch("app.tasks._recover_completed_download", return_value=True),
    ):
        result = rescan_downloads(db, dry_run=False)

    assert result["imported"] == 1
    assert task.state == "seeding"
    assert task.client_type == "qbittorrent"
    assert task.client_download_id == "a" * 40


def test_rescan_apply_leaves_non_complete_match_unchanged():
    task = _task(client_download_id="a" * 40, state="error", message="stalled")
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [task]
    client = MagicMock()
    client.find_existing_download.return_value = task.client_download_id
    client.get_download_status.return_value = {"state": "downloading", "progress": 12.0}

    with patch("app.downloads.handlers.torrent.TorrentHandler._get_client", return_value=client):
        result = rescan_downloads(db, dry_run=False)

    assert result["active"] == 1
    assert task.state == "error"
    assert task.message == "stalled"


def test_rescan_selects_only_newest_completed_task_per_book_and_format():
    newer = _task(id=9, client_download_id="a" * 40, state="error")
    older = _task(id=8, client_download_id="b" * 40, state="error")
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [newer, older]
    client = MagicMock()
    client.find_existing_download.side_effect = lambda info_hash: info_hash
    client.get_download_status.return_value = {"state": "seeding", "progress": 100.0}
    client.get_completed_download_path.side_effect = lambda client_id: f"/downloads/{client_id}.m4b"

    with (
        patch("app.downloads.handlers.torrent.TorrentHandler._get_client", return_value=client),
        patch("app.tasks._payload_matches_book", return_value=True),
    ):
        result = rescan_downloads(db, dry_run=True)

    assert result["completed"] == 1
    assert result["skipped"] == 1
    assert result["results"][0]["task_id"] == newer.id
    assert result["results"][0]["outcome"] == "would_import"
    assert result["results"][1]["task_id"] == older.id
    assert result["results"][1]["outcome"] == "skipped"


def test_payload_match_accepts_named_file_and_nested_pack(tmp_path):
    audiobook = tmp_path / "Stephen King - 11.22.63.m4b"
    audiobook.touch()
    subtitled_ebook = tmp_path / "Extinction Machine.epub"
    subtitled_ebook.touch()
    pack = tmp_path / "Jonathan Maberry joe"
    pack.mkdir()
    (pack / "Joe Ledger Short Stories 05 - Special Ops.epub").touch()
    truncated = tmp_path / "The Wilful Princess and the Piebald (933)"
    truncated.mkdir()
    (truncated / "The Wilful Princess and the Pie - Robin Hobb.azw3").touch()

    assert _payload_matches_book("11/22/63", "audiobook", str(audiobook))
    assert _payload_matches_book(
        "Extinction Machine: A Joe Ledger Novel",
        "ebook",
        str(subtitled_ebook),
    )
    assert _payload_matches_book("Joe Ledger: Special Ops", "ebook", str(pack))
    assert _payload_matches_book(
        "The Wilful Princess & The Piebald Prince",
        "ebook",
        str(truncated),
    )


def test_payload_match_accepts_verified_title_variants(tmp_path):
    files = {
        "Exit Party": tmp_path / "ExitPartyANovel.m4b",
        "The Consuming Fire (Unabridged)": tmp_path / "The Consuming Fire - John Scalzi.epub",
        "The Butcher's Masquerade": tmp_path / "Butchers Masquerade.epub",
        "Ender's Shadow": tmp_path / "Enders Shadow (Unabridged).m4b",
        "Things That Make Us Smart: Defending Human Attributes In The Age Of The Machine": (
            tmp_path / "Things That Make Us Smart - Don Norman.epub"
        ),
        "Joe Ledger 02 - The Dragon Factory": tmp_path / "The Dragon Factory-Part03.mp3",
        "The Deal of a Lifetime - A Novella": tmp_path / "The Deal of a Lifetime-Part01.mp3",
    }
    for path in files.values():
        path.touch()

    for title, path in files.items():
        format_type = "audiobook" if path.suffix in {".m4b", ".mp3"} else "ebook"
        assert _payload_matches_book(title, format_type, str(path))


def test_payload_match_rejects_wrong_series_entry_and_archive(tmp_path):
    wrong_audiobook = tmp_path / "Jack Carr - Red Sky Mourning.m4b"
    wrong_audiobook.touch()
    archive = tmp_path / "Red Empire"
    archive.mkdir()
    (archive / "release.zip").touch()
    wrong_stormlight = tmp_path / "Words of Radiance.mp3"
    wrong_stormlight.touch()
    incomplete_anthology = tmp_path / "1984.mp3"
    incomplete_anthology.touch()

    assert not _payload_matches_book("The Terminal List", "audiobook", str(wrong_audiobook))
    assert not _payload_matches_book("Extinction Machine: A Joe Ledger Novel", "ebook", str(archive))
    assert not _payload_matches_book("The Way of Kings", "audiobook", str(wrong_stormlight))
    assert not _payload_matches_book("1984 and Related Readings", "audiobook", str(incomplete_anthology))


def test_rescan_rejects_completed_payload_mismatch_without_mutating():
    task = _task(client_download_id="a" * 40, state="error")
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [task]
    client = MagicMock()
    client.find_existing_download.return_value = task.client_download_id
    client.get_download_status.return_value = {"state": "seeding", "progress": 100.0}
    client.get_completed_download_path.return_value = "/downloads/Wrong Book.m4b"

    with (
        patch("app.downloads.handlers.torrent.TorrentHandler._get_client", return_value=client),
        patch("app.tasks._payload_matches_book", return_value=False),
        patch("app.tasks._recover_completed_download") as recover,
    ):
        result = rescan_downloads(db, dry_run=False)

    assert result["completed"] == 0
    assert result["mismatched"] == 1
    assert result["results"][0]["outcome"] == "mismatched"
    assert task.state == "error"
    recover.assert_not_called()


def test_import_boundary_rejects_mismatched_payload(tmp_path):
    wrong_audiobook = tmp_path / "Jack Carr - Red Sky Mourning.m4b"
    wrong_audiobook.touch()
    task = _task(book_id=4605, book=SimpleNamespace(title="The Terminal List"))
    book_query = MagicMock()
    book_query.filter.return_value.first.return_value = task.book
    db = MagicMock()
    db.query.return_value = book_query

    result = DownloadOrchestrator()._copy_to_destination(task, str(wrong_audiobook), db)

    assert result is None
    assert task.state == "error"
    assert task.import_status == "failed"
    assert "does not match" in task.import_message
    db.commit.assert_called_once()


def test_completed_transport_with_failed_import_stays_error():
    task = _task(state="queued", import_message="Payload mismatch")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = task
    handler = MagicMock()
    handler.download.return_value = "/downloads/Wrong Book.m4b"
    handler_class = MagicMock(return_value=handler)
    orchestrator = DownloadOrchestrator()

    with (
        patch("app.downloads.orchestrator.SessionLocal", return_value=db),
        patch("app.downloads.orchestrator.get_handler", return_value=handler_class),
        patch.object(orchestrator, "_copy_to_destination", return_value=None),
    ):
        orchestrator._execute_download(task.id, MagicMock())

    assert task.state == "error"
    assert task.download_path == "/downloads/Wrong Book.m4b"
    handler.cleanup.assert_called_once_with(task, success=True)


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
