"""The remote guard: loopback callers keep the zero-config open dashboard;
callers from other machines must present the auto-generated remote key.
Born in #110 — the default bind is 0.0.0.0, so before this the default
install was an open book to the whole network."""

import pytest
from httpx import ASGITransport, AsyncClient

from agenticledger.proxy.auth import client_is_local, load_or_create_remote_key


def test_client_is_local_knows_its_neighbors():
    assert client_is_local("127.0.0.1")
    assert client_is_local("::1")
    assert client_is_local("localhost")
    assert client_is_local("testclient")   # in-process test harness
    assert client_is_local(None)           # ASGI embedding, no network peer
    assert not client_is_local("192.168.1.44")
    assert not client_is_local("203.0.113.9")


def test_remote_key_is_stable_across_restarts(tmp_path):
    first = load_or_create_remote_key(tmp_path / "remote.key")
    second = load_or_create_remote_key(tmp_path / "remote.key")
    assert first == second
    assert first.startswith("agl_")


@pytest.fixture
def guarded_app(tmp_path, monkeypatch):
    # The app reads the key from the home state dir; point home at tmp.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AGENTICLEDGER_API_KEY", raising=False)
    from agenticledger.proxy.app import create_app
    app = create_app("http://upstream.invalid", f"sqlite:///{tmp_path}/t.db")
    key = (tmp_path / ".agenticledger" / "remote.key").read_text().strip()
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
        assert "agenticledger remote" in denied.json()["detail"]
        assert (await remote.get(f"/api/sessions?api_key={key}")).status_code == 200
        assert (await remote.get("/api/sessions",
                                 headers={"x-agenticledger-api-key": key})).status_code == 200
        # A wrong key is a wrong key.
        assert (await remote.get("/api/sessions?api_key=agl_wrong")).status_code == 401
        await local.aclose(); await remote.aclose()


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
