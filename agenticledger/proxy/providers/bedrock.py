"""AWS Bedrock, InvokeModel wire: what the Anthropic SDK's Bedrock client
and Claude Code's Bedrock mode speak.

Three twists on the Anthropic Messages wire:

- The model lives in the PATH (model/<modelId>/invoke), URL-encoded, and
  the body carries anthropic_version instead of model.
- Streaming is decided by the path (…/invoke-with-response-stream), not
  a body flag, and the stream is AWS's binary event stream, each frame
  wrapping one ordinary Anthropic stream event (see eventstream.py).
- Requests are SigV4-signed to their exact destination. The ledger
  strips any inbound signature (it proves nothing here) and re-signs
  with its own AWS credentials before forwarding; without credentials
  of its own it refuses Bedrock calls with the fix named. That part
  lives in the proxy's upstream layer, not in this adapter.

Records wear the bedrock label; prices resolve from the model id
(us.anthropic.claude-…-v2:0 prices as the Claude model it names).
"""

from typing import Optional
from urllib.parse import unquote

from ..normalize import CanonicalResponse
from . import eventstream
from .anthropic import AnthropicProvider

_MARKER = "model/"
_STREAM_SUFFIX = "/invoke-with-response-stream"
_SUFFIXES = ("/invoke", _STREAM_SUFFIX)


class BedrockProvider(AnthropicProvider):
    name = "bedrock"
    wire = "bedrock-invoke"
    binary_stream = True
    attribution_story = (
        "Tags ride the inbound URL (/r/<run>/<iter>/model/<id>/invoke) and the "
        "x-agenticledger-* headers; both are stripped before forwarding. The "
        "inbound SigV4 signature is stripped too (it was computed for the "
        "ledger's host and proves nothing upstream); the ledger re-signs the "
        "rebuilt request with its own AWS credentials as the very last step."
    )

    def matches_path(self, path: str) -> bool:
        return _MARKER in path and path.rstrip("/").endswith(_SUFFIXES)

    def captures_path(self, path: str) -> bool:
        return self.matches_path(path)

    def matches_response(self, body: dict) -> bool:
        return False  # Anthropic-shaped JSON: the Anthropic adapter normalizes it

    def matches_stream(self, first_chunk: Optional[dict]) -> bool:
        return False  # binary; reached by path, never by sniffing

    def streams(self, path: str, body: dict) -> bool:
        return path.rstrip("/").endswith(_STREAM_SUFFIX)

    def upstream_default(self) -> Optional[str]:
        return None  # per-region host, resolved by the upstream layer

    def _model_id(self, body: dict, path: str) -> str:
        tail = path.split(_MARKER, 1)[1] if _MARKER in path else ""
        return unquote(tail.split("/", 1)[0]) or body.get("model") or "unknown"

    def reconstruct_stream(self, raw: bytes, latency_ms: float, model_id: str) -> CanonicalResponse:
        # Decode the event stream into the SSE text the Anthropic
        # reconstructor already understands, then reuse it verbatim.
        return super().reconstruct_stream(eventstream.as_sse(raw).encode("utf-8"), latency_ms, model_id)

    def stream_error(self, raw: bytes) -> Optional[str]:
        for event in eventstream.bedrock_events(raw):
            if event.get("type") == "error":
                err = event.get("error") or {}
                return err.get("message") or str(err)[:300]
        return None
