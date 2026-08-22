"""Azure OpenAI through the adapter contract (0.10 Phase A, step 4): the
first provider added THROUGH the new architecture rather than into the
old dispatch soup. Azure speaks OpenAI's wire under a deployment path,
usually sends no model, and names the real model only in the response."""

import httpx

from agenticledger.proxy import providers
from agenticledger.proxy.normalize import normalize_request, normalize_response

from .conftest import openai_response

AZURE_PATH = "openai/deployments/prod-chat/chat/completions"
_BODY = {"messages": [{"role": "user", "content": "ping"}]}   # note: no model


def test_registry_claims_deployment_paths_only():
    assert providers.for_path(AZURE_PATH).wire == "azure-openai"
    assert providers.for_path("v1/chat/completions").wire == "openai-chat"
    assert providers.captures(AZURE_PATH)
    assert providers.captures("openai/deployments/x/responses")
    assert not providers.captures("openai/deployments/x/embeddings")
    assert not providers.captures("v1/chat/completions")  # the exact set handles it


def test_request_wears_the_azure_label_and_the_deployment_name():
    req = normalize_request(_BODY, AZURE_PATH)
    assert req.provider == "azure-openai"
    assert req.model_id == "prod-chat"          # deployment name, absent a body model
    req2 = normalize_request({**_BODY, "model": "gpt-4o"}, AZURE_PATH)
    assert req2.model_id == "gpt-4o"            # a body model still wins


def test_price_comes_from_the_response_model_when_the_deployment_is_unpriceable():
    body = openai_response(model="gpt-4o-2024-08-06", prompt_tokens=1000, completion_tokens=100)
    priced = normalize_response(body, 10.0, model_id="prod-chat")
    assert priced.cost_usd and priced.cost_usd > 0
    # Plain OpenAI is untouched: a priceable request id is used as before.
    same = normalize_response(body, 10.0, model_id="gpt-4o")
    before = normalize_response({**body, "model": "something-else"}, 10.0, model_id="gpt-4o")
    assert same.cost_usd == before.cost_usd


def test_azure_call_is_captured_with_its_label_and_cost(proxy):
    client = proxy(handler=lambda r: httpx.Response(
        200, json=openai_response(model="gpt-4o-2024-08-06", prompt_tokens=1000, completion_tokens=100)),
        upstream_url="https://my-resource.openai.azure.com")
    resp = client.post(f"/{AZURE_PATH}?api-version=2024-10-21", json=_BODY,
                       headers={"api-key": "azure-secret", "x-agenticledger-session-id": "az-1"})
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "Hello from the model."
    # Forwarded with the deployment path, the query, and Azure's auth header intact.
    sent = client.upstream.requests[-1]
    assert "/openai/deployments/prod-chat/chat/completions" in str(sent.url)
    assert "api-version=2024-10-21" in str(sent.url)
    assert sent.headers["api-key"] == "azure-secret"
    # Recorded under its own label, priced from the response's model id.
    record = client.get(f"/explain/{resp.headers['x-agenticledger-action-id']}").json()
    assert record["provider"] == "azure-openai"
    assert record["cost_usd"] and record["cost_usd"] > 0


def test_auto_routing_refuses_azure_with_the_fix_named(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   upstream_auto=True)
    resp = client.post(f"/{AZURE_PATH}", json=_BODY)
    assert resp.status_code == 502
    assert resp.json()["error"]["type"] == "upstream_not_configured"
    # Exact-match the refusal so the test pins the whole instruction (and
    # CodeQL sees no URL-substring check to mistake for sanitization).
    assert resp.json()["error"]["message"] == (
        "Azure OpenAI calls need an explicit upstream: set proxy.upstream_url "
        "to your resource, e.g. https://<resource>.openai.azure.com, then "
        "restart the ledger.")
    assert client.upstream.requests == []   # nothing was forwarded anywhere
