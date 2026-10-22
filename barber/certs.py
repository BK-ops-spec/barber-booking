from __future__ import annotations

import datetime
import ipaddress
import shutil
import subprocess
from pathlib import Path


class CertError(Exception):
    pass


def _san_entries(hostnames: list[str]):
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    entries = []
    for host in hostnames:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            entries.append(x509.DNSName(host))
    return entries


def generate_self_signed(
    cert_path: Path | str,
    key_path: Path | str,
    hostnames: list[str] | None = None,
    days: int = 825,
) -> tuple[Path, Path]:
    cert_path = Path(cert_path)
    key_path = Path(key_path)
    hostnames = hostnames or ["localhost", "127.0.0.1"]
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        return _generate_with_cryptography(cert_path, key_path, hostnames, days)
    except ImportError:
        return _generate_with_openssl(cert_path, key_path, hostnames, days)


def _generate_with_cryptography(cert_path, key_path, hostnames, days):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Barber Shop Booking"),
            x509.NameAttribute(NameOID.COMMON_NAME, hostnames[0]),
        ]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(
            x509.SubjectAlternativeName(_san_entries(hostnames)), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.chmod(0o600)
    cert_path.chmod(0o644)
    return cert_path, key_path


def _generate_with_openssl(cert_path, key_path, hostnames, days):
    if shutil.which("openssl") is None:
        raise CertError("openssl is not installed and the cryptography package is unavailable")
    san = ",".join(
        f"DNS:{h}" if not _is_ip(h) else f"IP:{h}" for h in hostnames
    )
    config = cert_path.with_suffix(".cnf")
    config.write_text(
        "[req]\ndistinguished_name=dn\nx509_extensions=v3\nprompt=no\n"
        f"[dn]\nCN={hostnames[0]}\nO=Barber Shop Booking\n"
        "[v3]\nsubjectAltName=@alt\nbasicConstraints=CA:FALSE\n"
        f"keyUsage=digitalSignature,keyEncipherment\n"
        f"[alt]\n{san}\n"
    )
    try:
        subprocess.run(
            [
                "openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048",
                "-days", str(days), "-keyout", str(key_path), "-out", str(cert_path),
                "-config", str(config),
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise CertError(f"openssl failed: {exc.stderr.decode('utf-8', 'replace')[:400]}") from exc
    finally:
        config.unlink(missing_ok=True)
    key_path.chmod(0o600)
    cert_path.chmod(0o644)
    return cert_path, key_path


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def describe(cert_path: Path | str) -> str:
    cert_path = Path(cert_path)
    if not cert_path.is_file():
        return "no certificate found"
    try:
        from cryptography import x509

        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        try:
            sans = cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value
            hosts = ", ".join(str(s) for s in sans)
        except x509.ExtensionNotFound:
            hosts = "none"
        return (
            f"subject: {cert.subject.rfc4514_string()}\n"
            f"valid:   {cert.not_valid_before_utc} -> {cert.not_valid_after_utc}\n"
            f"hosts:   {hosts}"
        )
    except ImportError:
        return f"certificate at {cert_path}"
