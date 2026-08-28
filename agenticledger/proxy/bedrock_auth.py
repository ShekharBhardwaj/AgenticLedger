"""SigV4 re-signing for direct Bedrock capture (0.10 Phase B, part 2).

Bedrock requests are signed over their exact destination, so a recorder
in the middle must sign them itself. The ledger reads credentials from
the standard AWS chain only (environment, profile, instance role; never
its own config file), strips whatever signature the client attached
(computed for the ledger's host, it proves nothing upstream), and signs
the rebuilt request as the very last step before sending.

botocore is an optional extra: `pip install "agentic-ledger[bedrock]"`.
Without it, or without credentials and a region, the signer is None and
the proxy refuses Bedrock calls with the fix named.
"""

import os
from typing import Optional

# Inbound headers that must never travel upstream: the client's own
# signature and signing inputs, which SigV4 recomputes for the new host.
_STRIPPED = {"authorization", "x-amz-date", "x-amz-security-token",
             "x-amz-content-sha256", "x-amz-user-agent"}


class BedrockSigner:
    service = "bedrock"

    def __init__(self, credentials, region: str) -> None:
        self._credentials = credentials
        self.region = region
        self.endpoint = f"https://bedrock-runtime.{region}.amazonaws.com"

    # Why the last from_environment returned None, when the chain said so.
    last_failure: Optional[str] = None

    @classmethod
    def from_environment(cls) -> Optional["BedrockSigner"]:
        """Resolve credentials and region through botocore's standard chain.
        Returns None (never raises) when anything is missing, so a ledger
        without AWS intent pays nothing for this module's existence."""
        try:
            import botocore.session
        except ImportError:
            return None
        try:
            session = botocore.session.get_session()
            credentials = session.get_credentials()
            region = (os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
                      or session.get_config_variable("region"))
            if credentials is None or not region:
                cls.last_failure = None if credentials is None else "no region configured"
                return None
            # Touch the frozen credentials once so a broken profile fails here, not mid-call.
            credentials.get_frozen_credentials()
            cls.last_failure = None
            return cls(credentials, region)
        except Exception as exc:
            # The chain KNOWS why it failed (an expired login session, a
            # missing crt dependency, a broken profile) — losing that reason
            # cost a three-layer debugging dig on user zero's machine. Keep
            # it for why_unavailable.
            cls.last_failure = str(exc)[:300]
            return None

    @staticmethod
    def why_unavailable() -> str:
        try:
            import botocore  # noqa: F401
        except ImportError:
            return ("direct Bedrock capture needs the ledger's own signing library: "
                    "pip install \"agentic-ledger[bedrock]\", then restart")
        if getattr(BedrockSigner, "last_failure", None):
            return ("direct Bedrock capture could not use this machine's AWS "
                    f"credentials: {BedrockSigner.last_failure} — fix that, then "
                    "restart the ledger")
        return ("direct Bedrock capture needs AWS credentials and a region the ledger can "
                "read from the standard chain (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or "
                "AWS_PROFILE, plus AWS_REGION), scoped to bedrock:InvokeModel; set them "
                "in the ledger's environment and restart")

    def sign(self, method: str, url: str, headers: dict, body: bytes) -> dict:
        """Headers for the outbound request: the client's signature gone,
        the ledger's in its place. `url` must be the exact upstream URL,
        query included; the body must be final."""
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        clean = {k: v for k, v in headers.items() if k.lower() not in _STRIPPED}
        request = AWSRequest(method=method, url=url, data=body, headers=clean)
        SigV4Auth(self._credentials.get_frozen_credentials(), self.service, self.region).add_auth(request)
        return dict(request.headers)
