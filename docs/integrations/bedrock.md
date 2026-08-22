# AWS Bedrock

Two ways to record agents that run on Bedrock. The first works with any
ledger version; the second is 0.10's flagship and needs the ledger to
hold AWS credentials of its own.

## Through a gateway (works today)

Put a gateway that speaks the OpenAI wire in front of Bedrock, and the
ledger in front of the gateway. LiteLLM is the common choice:

```
your agent  →  Agentic Ledger  →  LiteLLM  →  Bedrock
```

LiteLLM signs the Bedrock calls with its AWS credentials; the ledger
records plain OpenAI-format traffic and needs nothing Bedrock-specific:

```bash
agenticledger config set proxy.upstream_url http://localhost:4000   # LiteLLM
agenticledger stop && agenticledger start
```

Model ids such as `bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0`
price as the Claude model they name.

## Direct capture (0.10)

The ledger speaks Bedrock's InvokeModel wire itself, the one Claude
Code's Bedrock mode and the Anthropic SDK's Bedrock client use:

- `model/<modelId>/invoke` and `…/invoke-with-response-stream` are
  captured; the model comes from the path, Anthropic-shaped bodies are
  normalized as such, and the binary event stream is decoded for the
  record while the bytes pass through to your client untouched.
- Records wear the `bedrock` label; a `us.anthropic.claude-…` id prices
  as the Claude model it names.

Bedrock requests are SigV4-signed to their exact destination, so a
recorder in the middle must sign them itself. The ledger strips the
inbound signature (it was computed for the ledger's host and proves
nothing upstream) and re-signs with credentials it reads from the
standard AWS chain: environment variables, a profile, or an instance
role. Nothing AWS-related goes in the ledger's config file.

```bash
pip install "agentic-ledger[bedrock]"          # adds botocore
export AWS_PROFILE=ledger AWS_REGION=us-east-1  # or env keys, or an instance role
agenticledger stop && agenticledger start
```

Scope the ledger's credentials to `bedrock:InvokeModel` and
`bedrock:InvokeModelWithResponseStream` on the models you use; it needs
nothing else.

Then point the client at the ledger:

```bash
# Claude Code
export CLAUDE_CODE_USE_BEDROCK=1
export ANTHROPIC_BEDROCK_BASE_URL=http://localhost:8000
```

```python
# boto3
client = boto3.client("bedrock-runtime", endpoint_url="http://localhost:8000")
```

To name the work while you're at it, wrap the command instead of exporting
the URL yourself — the runner sets `ANTHROPIC_BEDROCK_BASE_URL` (and the
OpenAI/Anthropic base URLs) with the run name in it, counts each launch as
the next iteration, and `--project` files the tile:

```bash
export CLAUDE_CODE_USE_BEDROCK=1
agenticledger run nightly-digest --project acme -- claude -p "..."
```

Without credentials of its own, the ledger refuses Bedrock calls with
that instruction instead of forwarding an unsignable request.
