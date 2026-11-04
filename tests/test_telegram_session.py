import ssl

import pytest

from tg_lottery.telegram.session import TelegramSession
from tg_lottery.telegram.single_request_session import SingleRequestSession


@pytest.mark.parametrize("session_type", [TelegramSession, SingleRequestSession])
def test_telegram_client_hello_fits_one_segment_and_retains_tls13(session_type):
    context = session_type()._connector_init["ssl"]
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.cert_store_stats()["x509_ca"] > 0
    outgoing = ssl.MemoryBIO()
    connection = context.wrap_bio(
        ssl.MemoryBIO(), outgoing, server_side=False, server_hostname="api.telegram.org"
    )
    with pytest.raises(ssl.SSLWantReadError):
        connection.do_handshake()
    hello = outgoing.read()
    assert len(hello) < 1200
    extensions = _client_hello_extensions(hello)
    versions = extensions[43]
    assert b"\x03\x04" in [versions[i : i + 2] for i in range(1, len(versions), 2)]
    keyshares = extensions[51]
    assert len(keyshares) == 38
    assert keyshares[2:6] == b"\x00\x1d\x00\x20"


def _client_hello_extensions(hello):
    position = 5 + 4 + 2 + 32
    position += 1 + hello[position]
    position += 2 + int.from_bytes(hello[position : position + 2])
    position += 1 + hello[position]
    position += 2
    extensions = {}
    while position < len(hello):
        kind = int.from_bytes(hello[position : position + 2])
        size = int.from_bytes(hello[position + 2 : position + 4])
        position += 4
        extensions[kind] = hello[position : position + size]
        position += size
    return extensions
