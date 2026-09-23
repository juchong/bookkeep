from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.encryption import ENCRYPTED_PREFIX, migrate_plaintext_secrets
from app.models import AppSettings, ProwlarrServer


def _session():
    engine = create_engine("sqlite:///:memory:")
    AppSettings.__table__.create(engine)
    ProwlarrServer.__table__.create(engine)
    return engine, sessionmaker(bind=engine)()


def test_service_credentials_encrypt_on_write_and_decrypt_on_read(monkeypatch):
    monkeypatch.setenv("BOOKKEEP_SECRET_KEY", "test-encryption-key")
    engine, db = _session()
    server = ProwlarrServer(name="Prowlarr", host="prowlarr", api_key="secret-key")
    db.add(server)
    db.commit()

    stored = engine.connect().execute(
        text("SELECT api_key FROM prowlarr_servers WHERE id = :id"), {"id": server.id}
    ).scalar_one()
    db.expire_all()

    assert stored.startswith(ENCRYPTED_PREFIX)
    assert "secret-key" not in stored
    assert db.get(ProwlarrServer, server.id).api_key == "secret-key"


def test_startup_migrates_legacy_plaintext_credentials(monkeypatch):
    monkeypatch.setenv("BOOKKEEP_SECRET_KEY", "test-encryption-key")
    engine, db = _session()
    db.execute(text(
        "INSERT INTO prowlarr_servers (name, host, port, use_ssl, api_key) "
        "VALUES ('Prowlarr', 'prowlarr', 9696, 0, 'legacy-secret')"
    ))
    db.commit()

    assert migrate_plaintext_secrets(db) == 1
    stored = engine.connect().execute(
        text("SELECT api_key FROM prowlarr_servers")
    ).scalar_one()

    assert stored.startswith(ENCRYPTED_PREFIX)
    assert db.query(ProwlarrServer).one().api_key == "legacy-secret"
    assert migrate_plaintext_secrets(db) == 0
