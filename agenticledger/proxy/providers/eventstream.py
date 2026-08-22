"""AWS binary event-stream decoding (application/vnd.amazon.eventstream).

Bedrock's streaming endpoints do not speak SSE. Each message is a frame:

    total_length (4 bytes, big-endian)
    headers_length (4)
    prelude CRC (4)
    headers: repeated [name_len (1)] [name] [value_type (1)] [value...]
    payload: total_length - headers_length - 16 bytes
    message CRC (4)

For Bedrock the interesting headers are :event-type ("chunk" for data,
"exception"/"error" otherwise) and the payload is JSON; a chunk's
payload is {"bytes": "<base64>"} whose decoded bytes are one provider
stream event (for Claude models, an Anthropic Messages stream event).
CRCs are not verified: the transport already did, and a corrupt frame
surfaces as a JSON failure we skip rather than a crash.
"""

import base64
import json
import struct
from typing import Iterator, Optional

_PRELUDE = 12
_TRAILER = 4


def iter_frames(data: bytes) -> Iterator[tuple[dict, bytes]]:
    """Yield (headers, payload) per frame; stops at the first malformed one."""
    pos = 0
    n = len(data)
    while pos + _PRELUDE <= n:
        total, hlen = struct.unpack(">II", data[pos:pos + 8])
        if total < _PRELUDE + _TRAILER or pos + total > n:
            return
        headers = _parse_headers(data[pos + _PRELUDE:pos + _PRELUDE + hlen])
        payload = data[pos + _PRELUDE + hlen:pos + total - _TRAILER]
        yield headers, payload
        pos += total


def _parse_headers(raw: bytes) -> dict:
    headers: dict = {}
    i = 0
    while i < len(raw):
        name_len = raw[i]
        i += 1
        name = raw[i:i + name_len].decode("utf-8", "replace")
        i += name_len
        vtype = raw[i]
        i += 1
        if vtype in (0, 1):            # bool true / false
            headers[name] = vtype == 0
        elif vtype == 2:               # byte
            headers[name] = raw[i]
            i += 1
        elif vtype == 3:               # short
            headers[name] = struct.unpack(">h", raw[i:i + 2])[0]
            i += 2
        elif vtype == 4:               # int
            headers[name] = struct.unpack(">i", raw[i:i + 4])[0]
            i += 4
        elif vtype == 5:               # long
            headers[name] = struct.unpack(">q", raw[i:i + 8])[0]
            i += 8
        elif vtype in (6, 7):          # bytes / string
            vlen = struct.unpack(">H", raw[i:i + 2])[0]
            i += 2
            value = raw[i:i + vlen]
            i += vlen
            headers[name] = value.decode("utf-8", "replace") if vtype == 7 else value
        elif vtype == 8:               # timestamp
            headers[name] = struct.unpack(">q", raw[i:i + 8])[0]
            i += 8
        elif vtype == 9:               # uuid
            headers[name] = raw[i:i + 16].hex()
            i += 16
        else:
            return headers
    return headers


def bedrock_events(data: bytes) -> Iterator[dict]:
    """The provider stream events inside a Bedrock response stream, as
    dicts, in order. Exception frames are yielded as {"type": "error",
    "error": {...}} so callers see the failure where it happened."""
    for headers, payload in iter_frames(data):
        try:
            body = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            continue
        if headers.get(":message-type") == "exception" or headers.get(":event-type") in ("exception", "error"):
            yield {"type": "error", "error": {"type": headers.get(":exception-type") or "exception",
                                               "message": body.get("message") or str(body)[:300]}}
            continue
        encoded = body.get("bytes")
        if not isinstance(encoded, str):
            continue
        try:
            yield json.loads(base64.b64decode(encoded))
        except (ValueError, json.JSONDecodeError):
            continue


def as_sse(data: bytes) -> str:
    """Re-express a Bedrock event stream as the SSE text the Anthropic
    stream reconstructor already understands."""
    return "".join(f"data: {json.dumps(ev)}\n\n" for ev in bedrock_events(data))


def encode_frame(headers: dict, payload: bytes) -> bytes:
    """Build one frame (tests and fixtures; CRCs are written as zeros)."""
    hraw = b""
    for name, value in headers.items():
        nb = name.encode()
        vb = str(value).encode()
        hraw += bytes([len(nb)]) + nb + bytes([7]) + struct.pack(">H", len(vb)) + vb
    total = _PRELUDE + len(hraw) + len(payload) + _TRAILER
    return struct.pack(">II", total, len(hraw)) + b"\0\0\0\0" + hraw + payload + b"\0\0\0\0"


def encode_chunk(event: dict) -> bytes:
    """A Bedrock 'chunk' frame carrying one provider event (for tests)."""
    payload = json.dumps({"bytes": base64.b64encode(json.dumps(event).encode()).decode()}).encode()
    return encode_frame({":event-type": "chunk", ":content-type": "application/json",
                         ":message-type": "event"}, payload)


def first_event(data: bytes) -> Optional[dict]:
    return next(bedrock_events(data), None)
