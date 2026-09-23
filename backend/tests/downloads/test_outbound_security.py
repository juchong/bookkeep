import socket

import pytest

from app.downloads.outbound import UnsafeDownloadUrl, validate_outbound_url


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "http://127.0.0.1/private",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/private",
    "http://user:password@example.com/file",
])
def test_outbound_policy_rejects_unsafe_urls(url):
    with pytest.raises(UnsafeDownloadUrl):
        validate_outbound_url(url)


def test_outbound_policy_allows_explicit_internal_service(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: (_ for _ in ()).throw(AssertionError("should not resolve")))
    validate_outbound_url("http://prowlarr:9696/download", allowed_private_hosts={"prowlarr"})


def test_outbound_policy_rejects_mixed_public_private_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443)),
    ])
    with pytest.raises(UnsafeDownloadUrl):
        validate_outbound_url("https://example.test/file")
