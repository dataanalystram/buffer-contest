"""studio.assets in a background research thread whose httpx client was already closed (a shutdown, or a test's client
torn down while a run is still going): every source answers "unavailable" instead of a RuntimeError traceback."""
import socket

import httpx
import pytest

from studio import assets, netguard


@pytest.fixture
def closed(monkeypatch):
    def fake_dns(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
    monkeypatch.setattr(netguard, "_getaddrinfo", fake_dns)
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html></html>")))
    c.close()
    return c


def test_get_turns_a_closed_client_into_fetch_error(closed):
    with pytest.raises(netguard.FetchError):
        assets._get("https://example.test/", closed)


def test_sources_are_skipped_not_raised_on_a_closed_client(closed):
    assert assets.site("https://example.test/", http=closed) is None
    assert assets.yc_company("acme", http=closed) is None
    assert assets.product_hunt("acme", http=closed)["ok"] is False
    assert assets.fetch_image("https://example.test/a.jpg", http=closed) is None
