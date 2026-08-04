#!/usr/bin/env python3
"""
Generate a CA certificate and a wildcard server certificate for bedrock-proxy.

Run once as root after creating /etc/bedrock-proxy/:

    python3 generate_certs.py

The CA cert must be trusted by the claude binary via NODE_EXTRA_CA_CERTS.
"""
import datetime
import os
import sys

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except ImportError:
    print("error: cryptography package required", file=sys.stderr)
    print("  python3 -m pip install cryptography", file=sys.stderr)
    sys.exit(1)

CONFIG_DIR = os.environ.get("BEDROCK_PROXY_CONFIG_DIR", "/etc/bedrock-proxy")
PROXY_PORT = os.environ.get("BEDROCK_PROXY_PORT", "8888")
BEDROCK_BASE_URL = os.environ.get("BEDROCK_BASE_URL", "https://bedrock.ap-northeast-1.amazonaws.com/v1")


def _region_from_url(url):
    """Extract AWS region from a Bedrock URL like https://bedrock.us-east-1.amazonaws.com/v1."""
    import urllib.parse
    host = urllib.parse.urlparse(url).hostname or ""
    parts = host.split(".")
    # e.g. ["bedrock", "us-east-1", "amazonaws", "com"] → "us-east-1"
    if len(parts) >= 4 and parts[-2] == "amazonaws" and parts[-1] == "com":
        return parts[-3]
    return None


def _build_sans(region):
    """Return DNS SANs covering *.amazonaws.com and the given AWS region tier."""
    sans = [
        x509.DNSName("*.amazonaws.com"),
        x509.DNSName("amazonaws.com"),
    ]
    if region:
        sans.append(x509.DNSName(f"*.{region}.amazonaws.com"))
    return sans


def generate_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def key_pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )


def cert_pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


region = _region_from_url(BEDROCK_BASE_URL)
if region:
    print(f"Region: {region}")
else:
    print("warning: could not parse region from BEDROCK_BASE_URL; regional SAN omitted", file=sys.stderr)

now = datetime.datetime.utcnow()
ten_years = now + datetime.timedelta(days=3650)

# CA
ca_key = generate_key()
ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bedrock-proxy CA")])
ca_cert = (
    x509.CertificateBuilder()
    .subject_name(ca_name)
    .issuer_name(ca_name)
    .public_key(ca_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now)
    .not_valid_after(ten_years)
    .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    .sign(ca_key, hashes.SHA256())
)

# Server cert: SANs cover *.amazonaws.com and *.<region>.amazonaws.com
srv_key = generate_key()
srv_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "*.amazonaws.com")])
srv_cert = (
    x509.CertificateBuilder()
    .subject_name(srv_name)
    .issuer_name(ca_name)
    .public_key(srv_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now)
    .not_valid_after(ten_years)
    .add_extension(
        x509.SubjectAlternativeName(_build_sans(region)),
        critical=False,
    )
    .sign(ca_key, hashes.SHA256())
)

files = {
    "ca.key":     (key_pem(ca_key),   0o400),
    "ca.crt":     (cert_pem(ca_cert), 0o444),
    "server.key": (key_pem(srv_key),  0o400),
    "server.crt": (cert_pem(srv_cert), 0o444),
}

for name, (data, mode) in files.items():
    path = os.path.join(CONFIG_DIR, name)
    with open(path, "wb") as f:
        f.write(data)
    os.chmod(path, mode)
    print(f"  wrote {path}")

print()
print("Add to /etc/systemd/system/hookd.service [Service]:")
print(f"  Environment=AWS_BEARER_TOKEN_BEDROCK=dummy")
print(f"  Environment=HTTPS_PROXY=http://127.0.0.1:{PROXY_PORT}")
print(f"  Environment=NODE_EXTRA_CA_CERTS={CONFIG_DIR}/ca.crt")
