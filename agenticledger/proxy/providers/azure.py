"""Azure OpenAI: the OpenAI chat wire with three twists. The path names a
DEPLOYMENT (openai/deployments/<name>/chat/completions), the request body
usually carries no model, and the real model id arrives in the response.
Records wear their own label (azure-openai) so reports separate Azure
spend from OpenAI spend; prices resolve from the response's model id.

Azure has no default host: the upstream must be the user's resource
(https://<resource>.openai.azure.com), so auto routing refuses Azure
calls with that instruction instead of sending them to api.openai.com.
"""

from typing import Optional

from .openai import OpenAIProvider

_MARKER = "openai/deployments/"


class AzureProvider(OpenAIProvider):
    name = "azure-openai"
    wire = "azure-openai"
    attribution_story = (
        "Tags ride the inbound URL (/r/<run>/<iter>/openai/deployments/…) and "
        "the x-agenticledger-* headers; both are stripped before forwarding. "
        "Azure authenticates with an api-key header, forwarded untouched; the "
        "deployment name stays in the path; api-version stays in the query."
    )

    def matches_path(self, path: str) -> bool:
        return _MARKER in path

    def captures_path(self, path: str) -> bool:
        return _MARKER in path and path.rstrip("/").endswith(
            ("/chat/completions", "/completions", "/responses"))

    def matches_response(self, body: dict) -> bool:
        return False  # same shape as OpenAI: the chat adapter normalizes it

    def matches_stream(self, first_chunk: Optional[dict]) -> bool:
        return False

    def upstream_default(self) -> Optional[str]:
        return None  # your resource, nobody's default

    def _model_id(self, body: dict, path: str) -> str:
        if body.get("model"):
            return body["model"]
        tail = path.split(_MARKER, 1)[1] if _MARKER in path else ""
        return tail.split("/", 1)[0] or "unknown"
