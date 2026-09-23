"""Short-lived, opaque tokens for user-selected download releases."""
import base64
import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


TOKEN_TTL_SECONDS = 15 * 60
_SALT = b"bookkeep-download-release-token-v1"


class InvalidReleaseToken(ValueError):
    pass


def _fernet() -> Fernet:
    secret = os.getenv("BOOKKEEP_SECRET_KEY", "")
    if not secret:
        from app.jwt import SECRET_KEY
        secret = SECRET_KEY
    key = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_SALT,
        iterations=480_000,
    ).derive(secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def issue_release_token(*, release: Any, user_id: int, book_id: int, format_type: str) -> str:
    payload = {
        "user_id": user_id,
        "book_id": book_id,
        "format_type": format_type,
        "title": release.title,
        "download_url": release.download_url,
        "protocol": release.protocol,
        "indexer": release.indexer,
        "size_bytes": release.size_bytes,
    }
    return _fernet().encrypt(json.dumps(payload, separators=(",", ":")).encode()).decode()


def resolve_release_token(
    token: str,
    *,
    user_id: int,
    book_id: int,
    format_type: str,
) -> dict[str, Any]:
    try:
        raw = _fernet().decrypt(token.encode(), ttl=TOKEN_TTL_SECONDS)
        payload = json.loads(raw)
    except (InvalidToken, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise InvalidReleaseToken("Release selection is invalid or expired") from exc

    if (
        payload.get("user_id") != user_id
        or payload.get("book_id") != book_id
        or payload.get("format_type") != format_type
    ):
        raise InvalidReleaseToken("Release selection does not match this request")
    if payload.get("protocol") not in {"torrent", "usenet", "direct"} or not payload.get("download_url"):
        raise InvalidReleaseToken("Release selection is incomplete")
    return payload
