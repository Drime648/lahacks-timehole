from __future__ import annotations

from pathlib import Path

from gateway.proxy.certificates import CertificateAuthorityManager


def test_certificate_authority_manager_creates_root_ca(tmp_path: Path):
    manager = CertificateAuthorityManager(tmp_path)

    assert manager.root_key_path.exists()
    assert manager.root_cert_path.exists()
    assert b"BEGIN CERTIFICATE" in manager.get_root_ca_pem()


def test_certificate_authority_manager_issues_host_certificate(tmp_path: Path):
    manager = CertificateAuthorityManager(tmp_path)

    cert_path, key_path = manager.issue_host_certificate("example.com")

    assert cert_path.exists()
    assert key_path.exists()
    assert cert_path.read_text().startswith("-----BEGIN CERTIFICATE-----")
    assert key_path.read_text().startswith("-----BEGIN RSA PRIVATE KEY-----")
