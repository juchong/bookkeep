"""Fernet encryption for sensitive values stored in app_settings."""
import base64
import os
from functools import lru_cache

import structlog
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy import String, inspect, text
from sqlalchemy.types import TypeDecorator

logger = structlog.get_logger(__name__)

ENCRYPTED_PREFIX = "enc:"

SENSITIVE_KEYS = {"oidc_client_secret", "hardcover_api_token"}

CREDENTIAL_COLUMNS = (
    ("readarr_servers", "api_key"),
    ("booklore_servers", "password"),
    ("booklore_servers", "access_token"),
    ("booklore_servers", "refresh_token"),
    ("audiobookshelf_servers", "api_key"),
    ("download_clients", "password"),
    ("download_clients", "api_key"),
    ("prowlarr_servers", "api_key"),
    ("download_tasks", "download_url"),
    ("download_tasks", "release_data_json"),
    ("user_hardcover_sync", "hardcover_api_token"),
    ("direct_download_settings", "zlibrary_password"),
)

_SALT = b"bookkeep-settings-encryption-v1"


@lru_cache(maxsize=4)
def _fernet_for_secret(secret: str) -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_SALT,
        iterations=480_000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret.encode("utf-8")))
    return Fernet(key)


def _get_fernet() -> Fernet:
    """Derive a Fernet key from BOOKKEEP_SECRET_KEY via PBKDF2."""
    secret = os.getenv("BOOKKEEP_SECRET_KEY", "")
    if not secret:
        from app.jwt import SECRET_KEY
        secret = SECRET_KEY
    return _fernet_for_secret(secret)


def encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext string. Returns an 'enc:' prefixed ciphertext."""
    if not plaintext or plaintext.startswith(ENCRYPTED_PREFIX):
        return plaintext
    f = _get_fernet()
    token = f.encrypt(plaintext.encode("utf-8"))
    return ENCRYPTED_PREFIX + token.decode("utf-8")


def decrypt_value(stored: str) -> str:
    """Decrypt a stored value. If not prefixed with 'enc:', returns as-is
    (backward-compatible with existing plaintext values)."""
    if not stored or not stored.startswith(ENCRYPTED_PREFIX):
        return stored
    try:
        f = _get_fernet()
        ciphertext = stored[len(ENCRYPTED_PREFIX):].encode("utf-8")
        return f.decrypt(ciphertext).decode("utf-8")
    except (InvalidToken, Exception) as exc:
        logger.error("decrypt_failed", error=str(exc))
        return ""


class EncryptedString(TypeDecorator):
    """Encrypt string values on write and transparently decrypt them on read."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_value(value) if value else value

    def process_result_value(self, value, dialect):
        return decrypt_value(value) if value else value


def check_encryption_key_stability(db_session):
    """Warn if encrypted values exist but BOOKKEEP_SECRET_KEY is not set,
    which means the encryption key changes on every restart."""
    if os.getenv("BOOKKEEP_SECRET_KEY"):
        return

    inspector = inspect(db_session.get_bind())
    encrypted_values_exist = False
    if inspector.has_table("app_settings"):
        encrypted_values_exist = bool(db_session.execute(text(
            "SELECT 1 FROM app_settings WHERE value LIKE 'enc:%' LIMIT 1"
        )).first())
    if not encrypted_values_exist:
        tables = set(inspector.get_table_names())
        for table_name, column_name in CREDENTIAL_COLUMNS:
            if table_name not in tables:
                continue
            query = text(
                f"SELECT 1 FROM {table_name} WHERE {column_name} LIKE 'enc:%' LIMIT 1"
            )
            if db_session.execute(query).first():
                encrypted_values_exist = True
                break

    if encrypted_values_exist:
        logger.error(
            "BOOKKEEP_SECRET_KEY is not set but encrypted secrets exist in the "
            "database. The encryption key is randomly generated on each restart, "
            "so previously encrypted values will be unrecoverable. Set "
            "BOOKKEEP_SECRET_KEY to a stable value."
        )


def migrate_plaintext_secrets(db_session) -> int:
    """Encrypt legacy plaintext settings and service credentials. Idempotent."""
    from app.models import AppSettings

    check_encryption_key_stability(db_session)

    count = 0
    for key in SENSITIVE_KEYS:
        setting = db_session.query(AppSettings).filter(AppSettings.key == key).first()
        if setting and setting.value and not setting.value.startswith(ENCRYPTED_PREFIX):
            setting.value = encrypt_value(setting.value)
            count += 1
            logger.info("encrypted_plaintext_secret", key=key)

    tables = set(inspect(db_session.get_bind()).get_table_names())
    for table_name, column_name in CREDENTIAL_COLUMNS:
        if table_name not in tables:
            continue
        rows = db_session.execute(text(
            f"SELECT id, {column_name} AS value FROM {table_name} "
            f"WHERE {column_name} IS NOT NULL AND {column_name} != ''"
        )).mappings()
        for row in rows:
            if row["value"].startswith(ENCRYPTED_PREFIX):
                continue
            db_session.execute(
                text(f"UPDATE {table_name} SET {column_name} = :value WHERE id = :id"),
                {"id": row["id"], "value": encrypt_value(row["value"])},
            )
            count += 1
            logger.info(
                "encrypted_plaintext_credential",
                table=table_name,
                column=column_name,
                row_id=row["id"],
            )

    if count > 0:
        db_session.commit()
        logger.info("plaintext_secrets_migrated", count=count)

    return count
