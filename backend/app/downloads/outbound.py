"""Outbound URL policy for download sources."""
from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urlparse


class UnsafeDownloadUrl(ValueError):
    pass


def hostname_from_value(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value if "://" in value else f"//{value}")
    return parsed.hostname.lower().rstrip(".") if parsed.hostname else None


def validate_outbound_url(url: str, *, allowed_private_hosts: Iterable[str] = ()) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeDownloadUrl("Only absolute HTTP(S) download URLs are allowed")
    if parsed.username or parsed.password:
        raise UnsafeDownloadUrl("Download URLs may not contain credentials")

    hostname = parsed.hostname.lower().rstrip(".")
    allowed = {host.lower().rstrip(".") for host in allowed_private_hosts if host}
    if hostname in allowed:
        return

    try:
        addresses = {
            item[4][0].split("%", 1)[0]
            for item in socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        }
    except (socket.gaierror, OSError, ValueError) as exc:
        raise UnsafeDownloadUrl("Download URL host could not be resolved") from exc
    if not addresses:
        raise UnsafeDownloadUrl("Download URL host could not be resolved")
    if any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise UnsafeDownloadUrl("Download URL resolves to a non-public address")


def configured_release_hosts(db, source: str) -> set[str]:
    from app.models import DirectDownloadSettings, ProwlarrServer

    values: list[str | None] = []
    if source == "prowlarr":
        values.extend(server.host for server in db.query(ProwlarrServer).filter(ProwlarrServer.enabled == True).all())
    elif source == "direct":
        settings = db.query(DirectDownloadSettings).first()
        if settings:
            values.extend([settings.annas_archive_mirror, settings.zlibrary_domain])
    return {host for value in values if (host := hostname_from_value(value))}
