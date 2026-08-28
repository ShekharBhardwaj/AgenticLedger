"""The remote guard: loopback callers keep the zero-config open dashboard;
callers from other machines must present the auto-generated pairing key.
Born in #110 — the default bind is 0.0.0.0, so before this the default
install was an open book to the whole network."""

import pytest
from httpx import ASGITransport, AsyncClient

from agenticledger.proxy.auth import client_is_local, load_or_create_pairing_key


def test_client_is_local_knows_its_neighbors():
    assert client_is_local("127.0.0.1")
    assert client_is_local("::1")
    assert client_is_local("localhost")
    assert client_is_local("testclient")   # in-process test harness
    assert client_is_local(None)           # ASGI embedding, no network peer
    assert not client_is_local("192.168.1.44")
    assert not client_is_local("203.0.113.9")


def test_pairing_key_is_stable_across_restarts(tmp_path):
    first = load_or_create_pairing_key(tmp_path / "pairing.key")
    second = load_or_create_pairing_key(tmp_path / "pairing.key")
    assert first == second
    assert first.startswith("agl_")


@pytest.fixture
def guarded_app(tmp_path, monkeypatch):
    # The app reads the key from the home state dir; point home at tmp.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AGENTICLEDGER_API_KEY", raising=False)
    from agenticledger.proxy.app import create_app
    app = create_app("http://upstream.invalid", f"sqlite:///{tmp_path}/t.db")
    key = (tmp_path / ".agenticledger" / "pairing.key").read_text().strip()
    return app, key


@pytest.mark.asyncio
async def test_loopback_stays_open_remote_needs_the_key(guarded_app):
    app, key = guarded_app
    async with app.router.lifespan_context(app):
        local = AsyncClient(transport=ASGITransport(app=app, client=("127.0.0.1", 9)),
                            base_url="http://t")
        remote = AsyncClient(transport=ASGITransport(app=app, client=("203.0.113.9", 9)),
                             base_url="http://t")
        assert (await local.get("/api/sessions")).status_code == 200
        denied = await remote.get("/api/sessions")
        assert denied.status_code == 401
        assert "agenticledger share" in denied.json()["detail"]
        assert (await remote.get(f"/api/sessions?api_key={key}")).status_code == 200
        assert (await remote.get("/api/sessions",
                                 headers={"x-agenticledger-api-key": key})).status_code == 200
        # A wrong key is a wrong key.
        assert (await remote.get("/api/sessions?api_key=agl_wrong")).status_code == 401
        await local.aclose()
        await remote.aclose()


@pytest.mark.asyncio
async def test_explicit_api_key_mode_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    from agenticledger.proxy.app import create_app
    app = create_app("http://upstream.invalid", f"sqlite:///{tmp_path}/t.db")
    async with app.router.lifespan_context(app):
        local = AsyncClient(transport=ASGITransport(app=app, client=("127.0.0.1", 9)),
                            base_url="http://t")
        # With an explicit key even loopback must authenticate — as before.
        assert (await local.get("/api/sessions")).status_code == 401
        assert (await local.get("/api/sessions?api_key=master-key")).status_code == 200
        await local.aclose()


@pytest.mark.asyncio
async def test_tunnel_visitors_are_remote_even_from_loopback(guarded_app):
    """cloudflared/nginx deliver visitors FROM 127.0.0.1 — the forwarded
    client header must decide, or `share` hands strangers the open board."""
    app, key = guarded_app
    async with app.router.lifespan_context(app):
        tunneled = AsyncClient(transport=ASGITransport(app=app, client=("127.0.0.1", 9)),
                               base_url="http://t")
        hdr = {"x-forwarded-for": "203.0.113.9"}
        assert (await tunneled.get("/api/sessions", headers=hdr)).status_code == 401
        assert (await tunneled.get(f"/api/sessions?api_key={key}",
                                   headers=hdr)).status_code == 200
        assert (await tunneled.get("/api/whoami", headers=hdr)).status_code == 401
        ok = await tunneled.get(f"/api/whoami?api_key={key}", headers=hdr)
        assert ok.status_code == 200
        assert ok.json()["source"] == "pairing-key"
        await tunneled.aclose()


@pytest.mark.asyncio
async def test_pairing_info_is_gated_and_keyed(guarded_app, tmp_path):
    """/api/share and its QR carry the key — open to the local machine,
    refused to keyless remote callers."""
    app, key = guarded_app
    async with app.router.lifespan_context(app):
        local = AsyncClient(transport=ASGITransport(app=app, client=("127.0.0.1", 9)),
                            base_url="http://t")
        remote = AsyncClient(transport=ASGITransport(app=app, client=("203.0.113.9", 9)),
                             base_url="http://t")
        assert (await remote.get("/api/share")).status_code == 401
        info = await local.get("/api/share")
        assert info.status_code == 200
        body = info.json()
        assert body["keyed"] is True
        assert body["wifi_url"] is None or key in body["wifi_url"]
        qr = await local.get("/api/share/qr.svg")
        # 404 is legitimate on a runner with no LAN address; otherwise SVG.
        assert qr.status_code in (200, 404)
        if qr.status_code == 200:
            assert qr.headers["content-type"].startswith("image/svg")
            assert b"<svg" in qr.content[:200] or b"svg" in qr.content[:200]
        await local.aclose()
        await remote.aclose()


def test_legacy_remote_key_file_is_adopted(tmp_path):
    """The key file was born as remote.key; the pairing-key rename must not
    silently un-pair every device that holds the old secret."""
    legacy = tmp_path / "remote.key"
    legacy.write_text("agl_legacy-secret\n")
    key = load_or_create_pairing_key(tmp_path / "pairing.key")
    assert key == "agl_legacy-secret"
    assert not legacy.exists()
