"""Tests for the optional proxy-ingest key (AGENTICLEDGER_INGEST_KEY).

When unset, the proxy forwards anything (zero-config dev UX). When set, only
requests carrying a matching x-agenticledger-ingest-key are forwarded — closing the
open relay. The key itself is never forwarded upstream.
"""

import httpx2 as httpx

from .conftest import openai_response

_CHAT = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}


def test_no_ingest_key_forwards_everything(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    assert client.post("/v1/chat/completions", json=_CHAT).status_code == 200


def test_missing_ingest_key_is_rejected_and_not_forwarded(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_INGEST_KEY", "ingest-secret")
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))

    resp = client.post("/v1/chat/completions", json=_CHAT)
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "unauthorized"
    assert client.upstream.requests == []  # nothing reached the upstream


def test_wrong_ingest_key_is_rejected(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_INGEST_KEY", "ingest-secret")
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))

    resp = client.post(
        "/v1/chat/completions", json=_CHAT,
        headers={"x-agenticledger-ingest-key": "nope"},
    )
    # A presented-but-wrong key is 403 (final — no credential-refresh retry
    # bursts), unlike the missing-key 401 above.
    assert resp.status_code == 403
    assert client.upstream.requests == []


def test_correct_ingest_key_forwards_and_strips_auth_headers(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_INGEST_KEY", "ingest-secret")
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response(content="pong")))

    resp = client.post(
        "/v1/chat/completions", json=_CHAT,
        headers={"x-agenticledger-ingest-key": "ingest-secret", "x-agenticledger-api-key": "k"},
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "pong"

    # The ingest key and api key must never leak to the upstream provider.
    fwd = client.upstream.last_request.headers
    assert "x-agenticledger-ingest-key" not in fwd
    assert "x-agenticledger-api-key" not in fwd


def test_ingest_key_gates_all_proxied_paths(proxy, monkeypatch):
    """Not just LLM paths — any proxied request is gated (closes the relay)."""
    monkeypatch.setenv("AGENTICLEDGER_INGEST_KEY", "ingest-secret")
    client = proxy(handler=lambda r: httpx.Response(200, json={"data": []}))

    assert client.get("/v1/models").status_code == 401
    assert client.upstream.requests == []
