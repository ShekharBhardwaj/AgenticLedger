# Contributing to Agentic Ledger

Thanks for your interest in improving Agentic Ledger! This project is an observability
proxy for AI agents — small, dependency-light, and meant to be easy to read. Contributions
of all sizes are welcome: bug fixes, tests, docs, new provider support, and features.

## Quick start (development setup)

Agentic Ledger uses [`uv`](https://github.com/astral-sh/uv) for fast, reproducible environments,
but plain `pip` works too.

```bash
# clone your fork
git clone https://github.com/<you>/AgenticLedger.git
cd Agentic Ledger

# create an isolated environment and install the package with dev tooling
uv venv
uv pip install -e ".[dev]"

# or, without uv:
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

The `[dev]` extra pulls in `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, and the
`openai`/`anthropic` SDKs used by tests.

## Running the checks

Everything CI runs, you can run locally:

```bash
# tests (with coverage, exactly like CI)
pytest --cov=agentledger --cov-report=term-missing

# a single module while iterating
pytest tests/test_normalize.py -q

# lint
ruff check .
```

Please make sure `pytest` and `ruff check` are both green before opening a PR.

## Running the proxy locally

```bash
# point it at any OpenAI-compatible upstream
AGENTLEDGER_UPSTREAM_URL=https://api.openai.com python -m agentledger.proxy
# dashboard at http://localhost:8000
```

Then send an agent's traffic through `http://localhost:8000/v1` instead of the provider URL.

## How the test suite is organized

Tests live in `tests/` and lean on shared fixtures in `tests/conftest.py`:

- **`proxy`** — a fully wired proxy (FastAPI `TestClient`) whose upstream is a
  `httpx.MockTransport` mock, so no network call is ever made. Use it for
  integration-style tests of the request/response path, headers, budgets, rate
  limits, auth, and the MCP endpoint.
- **`store`** — a fresh in-memory SQLite `Store` for storage-layer unit tests.
- Wire-format builders (`openai_response`, `anthropic_response`, `openai_sse`,
  `anthropic_sse`, …) so you don't have to hand-write provider payloads.

When you add a feature, add tests next to the module it touches (e.g. changes to
`normalize.py` → `tests/test_normalize.py`). Tests must be deterministic and offline.

## Conventions

- Keep the dependency footprint small. The core runtime depends only on FastAPI,
  uvicorn, httpx, and aiosqlite — new core dependencies need a good reason.
- Match the surrounding code style (the codebase favors readable, column-aligned
  literals). `ruff check` enforces the lint rules in `pyproject.toml`.
- Prefer fail-open behavior in the request path: observability must never break the
  agent's actual LLM call.
- Update `README.md` and `CHANGELOG.md` when you change user-facing behavior.

## Submitting a pull request

1. Fork and create a topic branch (`features/...` or `fix/...`).
2. Make your change with tests and a green `pytest` + `ruff check`.
3. Add a `CHANGELOG.md` entry under "Unreleased".
4. Open the PR against `main` and fill in the template. Link any related issue.

## Reporting bugs and requesting features

Use the issue templates. For security vulnerabilities, **do not** open a public
issue — see [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions will be licensed under the
project's [MIT License](LICENSE).
