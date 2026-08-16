"""Find out why the serving database is unreachable, one layer at a time.

/v1/status can only report what psycopg tells it, and psycopg reports the
*last* failure. When a connection fails there are four separate things that
could be wrong — the name, the route, the TLS handshake, the credential — and
knowing which one narrows the fix from "the database is down" to a single
console setting.

So this walks the layers in order and stops at the first that fails:

    1. DNS      — does the host resolve, and to what
    2. TCP      — does something accept on the port
    3. TLS      — does the handshake complete
    4. auth     — is the endpoint alive, tested with a role that cannot exist
    5. connect  — the real credential, and a real query

Step 4 is the one that earns this script. A deliberately wrong role separates
"the database is refusing everyone" from "the database is refusing us": if a
nonexistent role gets a password rejection, the endpoint is up and the fault is
ours; if it gets anything else, the endpoint itself is the fault and no change
to the URL will help.

Usage:

    python scripts/diagnose_db.py                    # reads .env / environment
    python scripts/diagnose_db.py --pipeline         # the writer URL instead
    python scripts/diagnose_db.py "postgresql://..." # an explicit URL

The password is never printed, and every error is redacted before display, so
the output is safe to paste into an issue.
"""

from __future__ import annotations

import argparse
import re
import socket
import ssl
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.routers.status import _diagnose  # noqa: E402
from gridcast.config import get_settings  # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

# A role no server can have: the probe must fail on every reachable database,
# so that the *way* it fails is the signal rather than whether it fails.
IMPOSSIBLE_ROLE = "gridcast_probe_no_such_role"


def redact(text: str) -> str:
    """Remove anything that identifies the deployment, keep the sentence."""
    text = re.sub(r"://[^@\s]+@", "://<credential>@", text)
    text = re.sub(r"password=\S+", "password=<redacted>", text, flags=re.I)
    return " ".join(text.split())


def ok(step: str, detail: str = "") -> None:
    print(f"  {GREEN}pass{RESET}  {step}" + (f"  {DIM}{detail}{RESET}" if detail else ""))


def fail(step: str, detail: str = "") -> None:
    print(f"  {RED}FAIL{RESET}  {step}" + (f"  {DIM}{detail}{RESET}" if detail else ""))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", help="connection string; defaults to the configured one")
    parser.add_argument(
        "--pipeline",
        action="store_true",
        help="diagnose GRIDCAST_DATABASE_URL (the writer) instead of the serving URL",
    )
    args = parser.parse_args()

    settings = get_settings()
    url = args.url or (settings.database_url if args.pipeline else settings.serving_url)
    if not url:
        print(f"{RED}No URL configured.{RESET} Set GRIDCAST_READONLY_DATABASE_URL, or pass one.")
        return 2

    parts = urlsplit(url)
    host, port = parts.hostname, parts.port or 5432
    if not host:
        print(f"{RED}That connection string has no host in it.{RESET}")
        return 2

    role = parts.username or "(none)"
    print(f"\n{YELLOW}Diagnosing{RESET} {host}:{port}  {DIM}role {role}{RESET}\n")

    # 1. DNS. A host that resolves only to IPv6 on a network with no IPv6 route
    # out fails later with "network is unreachable", which reads like the
    # database is down when the database was never contacted.
    try:
        addresses = sorted(
            {a[4][0] for a in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        )
    except OSError as exc:
        fail("DNS", redact(str(exc)))
        print(f"\n{RED}The hostname does not resolve.{RESET} The endpoint may have been deleted.\n")
        return 1
    families = {"IPv6" if ":" in a else "IPv4" for a in addresses}
    ok("DNS", f"{len(addresses)} address(es), {'/'.join(sorted(families))}")

    # 2. TCP.
    try:
        with socket.create_connection((host, port), timeout=15):
            ok("TCP", f"port {port} accepts")
    except OSError as exc:
        fail("TCP", redact(str(exc)))
        print(f"\n{RED}Nothing is accepting on that port from here.{RESET}\n")
        return 1

    # 3. TLS. Neon routes on SNI, so the handshake has to carry the hostname —
    # an IP in the URL reaches the proxy and then fails in a way that looks
    # like a credential problem.
    #
    # A refused handshake is only a fault if the URL asked for one. A local
    # Postgres declining TLS is how a local Postgres is normally configured,
    # and stopping there would make this script useless for the case it is
    # most often reached for: comparing a working local URL against a broken
    # remote one.
    tls_required = "require" in parse_qs(parts.query).get("sslmode", [""])[0]
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=15) as raw:
            # Postgres negotiates TLS in-band: ask first, then wrap.
            raw.sendall(b"\x00\x00\x00\x08\x04\xd2\x16\x2f")
            if raw.recv(1) != b"S":
                if tls_required:
                    fail("TLS", "the server declined TLS, but the URL requires it")
                    print(f"\n{RED}The server will not do TLS.{RESET} Check the host.\n")
                    return 1
                ok("TLS", "not offered, and this URL does not ask for it")
            else:
                with context.wrap_socket(raw, server_hostname=host) as tls:
                    ok("TLS", tls.version() or "negotiated")
    except OSError as exc:
        fail("TLS", redact(str(exc)))
        return 1

    # 4. Is the endpoint alive, independent of our credential?
    probe_url = (
        url.replace(f"//{parts.username}:", f"//{IMPOSSIBLE_ROLE}:", 1) if parts.username else url
    )
    try:
        with psycopg.connect(probe_url, connect_timeout=20):
            fail(
                "endpoint", "a role that cannot exist was accepted — this URL is not what it seems"
            )
            return 1
    except Exception as exc:  # noqa: BLE001 — the failure IS the measurement
        message = str(exc).lower()
        if "authentication" in message or "does not exist" in message:
            ok("endpoint", "alive — it rejected an impossible role, so it is answering")
        else:
            fail("endpoint", redact(str(exc))[:300])
            print(f"\n{RED}The endpoint refuses everyone, not just us.{RESET}")
            print(f"  {_diagnose(exc)}\n")
            return 1

    # 5. The real credential.
    try:
        with psycopg.connect(url, connect_timeout=20) as conn:
            row = conn.execute("SELECT current_user, current_database(), version()").fetchone()
    except Exception as exc:  # noqa: BLE001 — reported, not raised
        fail("connect", redact(str(exc))[:300])
        print(f"\n{RED}The endpoint is up and rejected this credential.{RESET}")
        print(f"  {_diagnose(exc)}\n")
        return 1

    assert row is not None
    ok("connect", f"as {row[0]} on {row[1]}")
    print(f"\n{GREEN}The database is reachable with this URL.{RESET}")
    print(f"{DIM}{row[2].split(' on ')[0]}{RESET}")
    print(
        "\nIf /v1/status still says unreachable, the serving environment holds a\n"
        "different URL than this one. Compare the host it reports.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
