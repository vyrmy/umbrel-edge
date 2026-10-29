from __future__ import annotations

import hashlib
import logging
import shutil
import ssl
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import respx

from umbrel_edge import unifi
from umbrel_edge.models import StageError
from umbrel_edge.unifi import Credentials, UnifiClient, normalise_fingerprint, verify_for

# A throwaway self-signed certificate; nothing trusts it and its key was discarded.
PEM = """-----BEGIN CERTIFICATE-----
MIIBgTCCASegAwIBAgIUeXbQ9GcBlIzFLXYLoFkXn9CMmFAwCgYIKoZIzj0EAwIw
FTETMBEGA1UEAwwKdW5pZmktdGVzdDAgFw0yNjEwMjkwOTUxNDFaGA8yMTI2MTAw
NTA5NTE0MVowFTETMBEGA1UEAwwKdW5pZmktdGVzdDBZMBMGByqGSM49AgEGCCqG
SM49AwEHA0IABErtbq6GmUVFq8FlNtWFmw0G8OX/LVDbsrU9Jomnzu4LKUyuJPPe
iNtXa+iXXqksGclE/aCpTn7L5gFJ59EPVAijUzBRMB0GA1UdDgQWBBQ7c1uiRCXm
IyGsnMIdfN+uor5LxTAfBgNVHSMEGDAWgBQ7c1uiRCXmIyGsnMIdfN+uor5LxTAP
BgNVHRMBAf8EBTADAQH/MAoGCCqGSM49BAMCA0gAMEUCIQCA8KSjYZnm73dclunq
sVrmYEzu6VvZNFvllDoKeIKz9AIgGdGJqrL3uWsca5P5wtTfUJmoq1+1Z+kMy/o4
J0KiR/8=
-----END CERTIFICATE-----
"""
FINGERPRINT = "5a78ac3463a20ef40be09a7f3397f4d7b3b3455ca14a02ac0c334e771546fdd1"
COLONS = ":".join(FINGERPRINT[i : i + 2] for i in range(0, 64, 2))
API_KEY = "secret-key"


def _creds(pin: str | None = FINGERPRINT, host: str = "192.168.10.1") -> Credentials:
    return Credentials(host, API_KEY, "site-1", pin)


@pytest.fixture(autouse=True)
def _fresh_warning() -> None:
    unifi._warn_unverified.cache_clear()


@pytest.mark.parametrize(
    "value",
    [FINGERPRINT, FINGERPRINT.upper(), COLONS, COLONS.upper(), f"  {COLONS}\n"],
)
def test_fingerprint_forms_normalise_alike(value: str) -> None:
    assert normalise_fingerprint(value) == FINGERPRINT


@pytest.mark.parametrize("value", ["", "abcd", FINGERPRINT[:-2], FINGERPRINT + "00", "zz" * 32])
def test_malformed_fingerprint_is_refused(value: str) -> None:
    with pytest.raises(StageError) as err:
        normalise_fingerprint(value)
    assert err.value.stage == "unifi"


def _env(**extra: str) -> dict[str, str]:
    return {"UNIFI_HOST": "gw", "UNIFI_API_KEY": "k", "UNIFI_SITE_ID": "s", **extra}


def test_from_env_reads_and_normalises_the_pin() -> None:
    creds = Credentials.from_env(_env(UNIFI_CERT_SHA256=COLONS.upper()))
    assert creds is not None
    assert creds.cert_sha256 == FINGERPRINT


@pytest.mark.parametrize("extra", [{}, {"UNIFI_CERT_SHA256": "  "}])
def test_from_env_without_a_pin(extra: dict[str, str]) -> None:
    creds = Credentials.from_env(_env(**extra))
    assert creds is not None
    assert creds.cert_sha256 is None


def test_from_env_with_a_malformed_pin_fails_rather_than_dropping_it() -> None:
    with pytest.raises(StageError):
        Credentials.from_env(_env(UNIFI_CERT_SHA256="not a fingerprint"))


@respx.mock(assert_all_called=False)
def test_mismatch_raises_before_any_request() -> None:
    with pytest.raises(StageError) as err:
        verify_for(_creds(pin="ab" * 32), fetch=lambda host, port: PEM)
    message = str(err.value)
    assert COLONS.upper() in message
    assert ":".join(["AB"] * 32) in message
    assert API_KEY not in message
    assert not err.value.retriable
    assert respx.calls.call_count == 0


def test_match_gives_a_context_that_trusts_only_that_certificate() -> None:
    context = verify_for(_creds(), fetch=lambda host, port: PEM)
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert not context.check_hostname
    assert context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
    assert len(context.get_ca_certs()) == 1


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("gw.local", ("gw.local", 443)),
        ("gw.local:8443", ("gw.local", 8443)),
        ("192.168.10.1", ("192.168.10.1", 443)),
        ("[fd00::1]:8443", ("fd00::1", 8443)),
    ],
)
def test_host_may_carry_a_port(host: str, expected: tuple[str, int]) -> None:
    seen: list[tuple[str, int]] = []

    def fetch(name: str, port: int) -> str:
        seen.append((name, port))
        return PEM

    verify_for(_creds(host=host), fetch=fetch)
    assert seen == [expected]


@pytest.mark.parametrize("host", ["gw:notaport", "gw:99999", ":443"])
def test_unusable_host_is_an_error(host: str) -> None:
    with pytest.raises(StageError):
        verify_for(_creds(host=host), fetch=lambda name, port: PEM)


def test_unreachable_gateway_is_a_retriable_error() -> None:
    def fetch(host: str, port: int) -> str:
        raise ConnectionRefusedError("refused")

    with pytest.raises(StageError) as err:
        verify_for(_creds(), fetch=fetch)
    assert err.value.retriable
    assert "192.168.10.1:443" in str(err.value)


def test_unset_pin_keeps_verification_off_and_warns_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fetch(host: str, port: int) -> str:
        raise AssertionError("nothing is fetched without a pin")

    with caplog.at_level(logging.WARNING, logger="umbrel_edge.unifi"):
        assert verify_for(_creds(pin=None), fetch=fetch) is False
        assert verify_for(_creds(pin=None), fetch=fetch) is False
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "unverified" in warnings[0]
    assert "UNIFI_CERT_SHA256" in warnings[0]
    assert "openssl x509 -noout -fingerprint -sha256" in warnings[0]


# A real TLS handshake against a local server, with certificates made by the openssl CLI.


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b'{"data": [], "totalCount": 0}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@contextmanager
def _serve(cert: Path, key: Path) -> Iterator[str]:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        yield f"127.0.0.1:{server.socket.getsockname()[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _make_certificate(directory: Path, name: str) -> tuple[Path, Path, str]:
    cert, key = directory / f"{name}.crt", directory / f"{name}.key"
    command = ["openssl", "req", "-x509", "-newkey", "ec", "-nodes", "-days", "2"]
    command += ["-pkeyopt", "ec_paramgen_curve:prime256v1", "-subj", f"/CN={name}"]
    command += ["-keyout", str(key), "-out", str(cert)]
    subprocess.run(command, check=True, capture_output=True)
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    return cert, key, hashlib.sha256(der).hexdigest()


@pytest.fixture(scope="module")
def certificates(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Path, Path, str]]:
    if shutil.which("openssl") is None:
        pytest.skip("the openssl CLI is not installed")
    directory = tmp_path_factory.mktemp("certificates")
    return {name: _make_certificate(directory, name) for name in ("gateway", "impostor")}


def _client(host: str, pin: str, verify: ssl.SSLContext | bool) -> UnifiClient:
    creds = Credentials(host, API_KEY, "site-1", pin)
    return UnifiClient(creds, verify=verify, sleep=lambda _: None)


def test_matching_pin_connects(certificates: dict[str, tuple[Path, Path, str]]) -> None:
    cert, key, fingerprint = certificates["gateway"]
    with _serve(cert, key) as host:
        context = verify_for(Credentials(host, API_KEY, "s", fingerprint))
        client = _client(host, fingerprint, context)
        try:
            assert client.list_a_records() == []
        finally:
            client.close()


def test_a_different_certificate_is_refused(
    certificates: dict[str, tuple[Path, Path, str]],
) -> None:
    cert, key, fingerprint = certificates["gateway"]
    impostor_cert, impostor_key, _ = certificates["impostor"]
    with _serve(cert, key) as gateway:
        context = verify_for(Credentials(gateway, API_KEY, "s", fingerprint))
    with _serve(impostor_cert, impostor_key) as impostor:
        # Fetching the impostor's certificate against the gateway's pin fails outright.
        with pytest.raises(StageError, match="has SHA-256"):
            verify_for(Credentials(impostor, API_KEY, "s", fingerprint))
        # And the context built from the gateway's certificate does not accept the impostor.
        client = _client(impostor, fingerprint, context)
        try:
            with pytest.raises(StageError, match="after 3 attempts"):
                client.list_a_records()
        finally:
            client.close()
