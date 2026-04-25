from __future__ import annotations

import ipaddress
import re
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def sanitize_hostname(hostname: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", hostname)


class CertificateAuthorityManager:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.hosts_dir = self.base_dir / "hosts"
        self.hosts_dir.mkdir(parents=True, exist_ok=True)
        self.root_key_path = self.base_dir / "timehole-root-ca.key.pem"
        self.root_cert_path = self.base_dir / "timehole-root-ca.crt.pem"
        self._lock = Lock()
        self._ensure_root_ca()

    def _ensure_root_ca(self) -> None:
        if self.root_key_path.exists() and self.root_cert_path.exists():
            return

        root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "TimeHole Local Root CA")]
        )
        now = datetime.now(UTC)
        root_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(root_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_encipherment=False,
                    key_cert_sign=True,
                    key_agreement=False,
                    content_commitment=False,
                    data_encipherment=False,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(private_key=root_key, algorithm=hashes.SHA256())
        )

        self.root_key_path.write_bytes(
            root_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        self.root_cert_path.write_bytes(root_cert.public_bytes(serialization.Encoding.PEM))

    def get_root_ca_pem(self) -> bytes:
        return self.root_cert_path.read_bytes()

    def issue_host_certificate(self, hostname: str) -> tuple[Path, Path]:
        safe_name = sanitize_hostname(hostname)
        cert_path = self.hosts_dir / f"{safe_name}.crt.pem"
        key_path = self.hosts_dir / f"{safe_name}.key.pem"

        if cert_path.exists() and key_path.exists():
            return cert_path, key_path

        with self._lock:
            if cert_path.exists() and key_path.exists():
                return cert_path, key_path

            root_key = serialization.load_pem_private_key(
                self.root_key_path.read_bytes(),
                password=None,
            )
            root_cert = x509.load_pem_x509_certificate(self.root_cert_path.read_bytes())

            leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            now = datetime.now(UTC)
            subject = x509.Name(
                [x509.NameAttribute(NameOID.COMMON_NAME, hostname)]
            )

            san_entries: list[x509.GeneralName]
            try:
                san_entries = [x509.IPAddress(ipaddress.ip_address(hostname))]
            except ValueError:
                san_entries = [x509.DNSName(hostname)]

            leaf_cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(root_cert.subject)
                .public_key(leaf_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=30))
                .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(
                    x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                    critical=False,
                )
                .sign(private_key=root_key, algorithm=hashes.SHA256())
            )

            key_path.write_bytes(
                leaf_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
            cert_path.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))

        return cert_path, key_path

    def build_server_context(self, hostname: str) -> ssl.SSLContext:
        cert_path, key_path = self.issue_host_certificate(hostname)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        return context
