from pathlib import Path

from app.static_files import resolve_static_file


def test_resolves_regular_file_inside_static_root(tmp_path: Path):
    static_root = tmp_path / "static"
    static_root.mkdir()
    favicon = static_root / "favicon.ico"
    favicon.write_text("icon")

    assert resolve_static_file(static_root, "favicon.ico") == favicon


def test_rejects_parent_directory_traversal(tmp_path: Path):
    static_root = tmp_path / "static"
    static_root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")

    assert resolve_static_file(static_root, "../secret.txt") is None
    assert resolve_static_file(static_root, str(secret)) is None


def test_rejects_symlink_that_escapes_static_root(tmp_path: Path):
    static_root = tmp_path / "static"
    static_root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    (static_root / "linked.txt").symlink_to(secret)

    assert resolve_static_file(static_root, "linked.txt") is None
