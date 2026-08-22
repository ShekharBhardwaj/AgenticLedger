# Azure OpenAI

Azure speaks OpenAI's wire format under a deployment path, so the ledger
records it with zero code changes on the agent side. Three things differ
from plain OpenAI, and the ledger handles all three:

- Calls go to `openai/deployments/<deployment>/chat/completions`, so the
  path names your deployment, not a model.
- Request bodies usually carry no `model`; the real model id arrives in
  the response. The ledger prices each call from the response's model id,
  so a deployment called `prod-chat` running `gpt-4o` is billed as gpt-4o.
- Records wear their own `azure-openai` label, so Reports separate Azure
  spend from OpenAI spend.

## Setup

Azure has no default host: the upstream is your own resource. Set it and
restart:

```bash
agenticledger config set proxy.upstream_url https://<your-resource>.openai.azure.com
agenticledger stop && agenticledger start
```

Then point the Azure SDK at the ledger instead of the resource, keeping
everything else (deployment, API version, key) exactly as it was:

```python
from openai import AzureOpenAI

client = AzureOpenAI(
    azure_endpoint="http://localhost:8000",   # the ledger
    api_key="<your-azure-key>",               # forwarded, never stored
    api_version="2024-10-21",
    default_headers={"x-agenticledger-session-id": "nightly-1"},
)
client.chat.completions.create(model="prod-chat", messages=[...])
```

`agenticledger run` works unchanged: the runner's `/r/<run>/<iter>` tag
rides in front of the deployment path.

## If you forget the upstream

With no upstream configured, the ledger refuses Azure calls with a 502
naming the fix instead of forwarding them to api.openai.com and handing
back a baffling 404:

```
upstream_not_configured: Azure OpenAI calls need an explicit upstream ...
```

## Pricing

Azure's list prices match OpenAI's for the same model ids, so the
built-in OpenAI price pack applies. If your region or contract differs,
override per model in `agenticledger.toml` (`[pricing]`), exactly as for
any other provider; see docs/pricing.md.
