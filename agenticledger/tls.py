"""Self-signed TLS for the dashboard's LAN listener (#118).

The offline fallback to `agenticledger share`: no tunnel, no internet,
just https on your own wifi. The certificate is self-generated, so the
phone shows a warning once; the docs say that trade squarely.

The https listener is DASHBOARD-ONLY, on its own port. The agent-facing
port stays plain http: a self-signed certificate would break every SDK
client pointed at the proxy (boto3 and friends verify), while a human
with a phone can tap through a warning. Supported condition, stated:
this protects the dashboard's traffic on the local network; it does not
authenticate the server to strangers the way a public CA would.

Generation uses the openssl binary (present on macOS and Linux; the
error names the fix where it is not) so the ledger carries no
cryptography dependency.
"""

import subprocess
from pathlib import Path
from typing import Optional


def ensure_cert(state_dir: Path, lan_ip: Optional[str]) -> tuple[Path, Path]:
    """The dashboard certificate and key, generated on first use and reused
    after (a certificate that changes every restart means a warning every
    restart). Returns (cert_path, key_path); raises RuntimeError with the
    fix named when openssl is unavailable or generation fails."""
    tls_dir = state_dir / "tls"
    cert, key = tls_dir / "dashboard.crt", tls_dir / "dashboard.key"
    if cert.exists() and key.exists():
        return cert, key
    tls_dir.mkdir(parents=True, exist_ok=True)
    san = "DNS:localhost,IP:127.0.0.1" + (f",IP:{lan_ip}" if lan_ip else "")
    result = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec",
         "-pkeyopt", "ec_paramgen_curve:prime256v1",
         "-keyout", str(key), "-out", str(cert),
         "-days", "825", "-nodes",
         "-subj", "/CN=Agentic Ledger dashboard",
         "-addext", f"subjectAltName={san}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
        raise RuntimeError(
            f"could not generate the dashboard certificate ({tail[0]}). "
            "The https listener needs the openssl tool; install it, or use "
            "`agenticledger share` (tunnel) instead.")
    key.chmod(0o600)
    return cert, key
