# Changelog

All notable changes to Agentic Ledger are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.8.2] - 2026-08-02

### Added
- **OpenClaw traffic names itself.** OpenClaw cannot send identifying
  headers, so its calls landed as "(unattributed)" in the by-agent tables.
  The detector now recognizes its self-identifying system prompt (verified
  against live captures) and tags the framework and agent as openclaw.
  Merely talking about OpenClaw does not count, and a BMAD persona hosted
  inside OpenClaw still files as BMAD, same as on any other host.

### Fixed
- **The background service now keeps its database in one place.** The
  default database path was relative, and `agenticledger start` inherits
  the directory it was run from. Start the service from one directory,
  restart it from another, and the proxy quietly created a second empty
  database there: the dashboard opened blank and the day's captures looked
  lost (they were still in the first directory's file). When nothing names
  a database, the service now uses the absolute path
  `~/.agenticledger/agenticledger.db`, so the data lands in the same place
  no matter where `start` was run. An explicit `AGENTICLEDGER_DSN`, a `db`
  value in agenticledger.toml, and foreground `agenticledger serve` behave
  exactly as before. Migration note: if you relied on the old behavior and
  have an `agenticledger.db` next to your project, point the service at it
  with `agenticledger config set proxy.db sqlite:///path/to/agenticledger.db`
  (or set `AGENTICLEDGER_DSN`); `start` prints a note when it spots such a
  file that it is no longer using.
- **Config commands name the exact file they touched.** `config set` and
  friends printed a bare "agenticledger.toml", so two shells in different
  folders could quietly edit two different files while believing they
  shared one (observed live: a budget wall stayed up because the unset
  edited the wrong file). All config command output now prints the
  absolute path, and the settings page shows the absolute path of the
  file the proxy actually loaded.

## [0.8.1] - 2026-08-02

### Fixed
- **Zero config now includes the upstream.** With no upstream configured,
  the proxy routes each call by the wire format it speaks: Anthropic-style
  calls (`/v1/messages`) go to api.anthropic.com, OpenAI-style calls to
  api.openai.com. Until now the default sent everything to OpenAI, so an
  Anthropic agent's first call on a fresh install bounced. An explicitly
  configured upstream behaves exactly as before: one proxy, one provider,
  your word wins, mismatch hints included. The settings page says
  "auto: by call format" so the behavior is never a secret. Found by
  walking a brand-new user's path: fresh venv, pip install, connect
  OpenClaw, ask the first question.

## [0.8.0] - 2026-08-01

Built and hardened through two real-framework test rounds (BMAD v6 and a
Dockerized OpenClaw), which produced twenty-plus findings, every one
fixed and re-verified in the same session it was found.

### Added
- **Replay the whole run** (flagship). 0.7 answered "what would this *call*
  look like on another model?"; 0.8 answers the question that actually
  decides a model switch: would the whole loop have survived? Pick a run or
  session, pick a destination (a free local model counts), and every step
  re-executes with its original inputs. `POST /api/replay/batch` starts a
  background job; `GET /api/replay/jobs/{id}` reports progress and the
  result. Honest by design: each step is the real captured moment, not a
  pretend re-run, since a different model would have steered a different
  conversation after step one.
- **The report card.** Forty side-by-sides don't get read, so each replayed
  step is graded (did it answer, did it reach for the same tools) and the
  batch collapses to one line: "34 / 40 moments matched", with the fumbles
  listed and everything else one click away. Cost is totalled both ways
  ("$0.00 on qwen vs $0.0963 original").
- **Names, pins, projects.** Sessions and runs get human names ("the
  overnight auth fix" instead of `cc-73a26366`), a ★ pin that keeps them at
  the top, and a project they can be filed under, with a project filter over
  both lists. `PUT /api/labels/{scope}/{id}`, `GET /api/projects`.
- **Projects can exist first, and fill themselves.** "+ project" (or
  `POST /api/projects`) declares a project before anything is filed under
  it, optionally bound to an app id: sessions and runs carrying that app
  tag file themselves under the project, including everything already
  captured, since the match is computed when you look, not stamped at
  capture. A hand-assigned project always beats the rule; auto-filed chips
  say `·auto` and explain themselves on hover. `agenticledger connect`
  already writes the app tag, so a connected framework can land in its
  project from the first call.
- **Settings page** (`GET /api/settings` + ⚙ in the dashboard): what the
  proxy is actually running with (config file in effect, upstream, budgets,
  capture and retention, replay targets), each row labeled with where its
  value came from (file, env, or default), what it means in plain words,
  and exactly where to set it. Read-only, admin-only, secrets never shown.
- **`agenticledger connect <framework>`**: wire claude-code, BMAD, or
  OpenClaw to the ledger without memorizing anyone's config schema. The
  OpenClaw writer detects Docker installs (and uses host.docker.internal,
  since localhost inside a container is the container), satisfies its
  validator's models-array demands from the config's own model list, and
  attributes runs via the URL path because OpenClaw can't send headers.
  Everything written is printed; existing files are backed up and merged.
- **`agenticledger config set/get/unset/path`**: change one setting from
  the terminal; the writer reuses the template's own commented lines, so
  the file stays readable and no key is ever typed blind.
- **The dashboard diagnoses its own silence.** A ledger with zero captures
  shows a wiring checklist in the proxy's own concrete URLs (base URL,
  the /v1 form, Docker's host.docker.internal, upstream match) instead of
  a blank page, because wrong wiring produces silence, and silence must
  say what to check.
- **Report cards are durable and find the reader.** A finished batch
  comparison reopens itself when its session is selected, survives proxy
  restarts (rebuilt from the ledger's own replay records), labels its
  columns "original, really ran" vs "replay: answer only, nothing
  executed", numbers its rows with the same #N as the session's call
  list, and explains its headline fraction in plain words.
- **Sessions grew a header** (human name, always-visible copyable id, team
  and project chips, live totals), **calls grew numbers and honest token
  figures** (full input including cache reads/writes, split on hover; no
  more "2 → 352 tok" next to a $0.34 price), the call list reads
  newest-first, and Reports gained a **By-project** table.
- **Provider marks**: every model carries a small colour chip: clay
  Anthropic, teal OpenAI, purple for anything running locally (detected
  from the model name, so an LM Studio qwen is never mislabeled as
  OpenAI). Deliberately not the providers' logos: no trademarks shipped,
  no CDN fetches, nothing leaves your machine.
- **The header shows the running version** (with a DEV tag for editable
  installs and an explanation of why their stamp can lag), and the filter
  dropdown gained an **★ all starred** view.
- **A quiet console footer**: one line at the end of the page with the
  local-first promise and links to agentic-ledger.dev, the repo, and the
  issue tracker. No sticky bar, no viewport tax.

### Fixed
- **UI copy dropped its em-dashes.** Every tooltip, hint, and panel now uses
  plain punctuation. The "no value" placeholder in tables stays.
- **Deleting a session no longer summons the browser's stock popup.** The
  × now opens an inline confirmation on the card itself (call count, a
  red "delete permanently", and a Cancel), matching the project-delete
  flow instead of `window.confirm`.
- **Danger buttons are actually red.** A stylesheet-order tie was quietly
  painting the project-purge (and now session-delete) confirm buttons the
  same blue as every other button.
- **Upstream detection matches hostnames, not substrings.** The
  wire-format-mismatch hint decided "this is Anthropic/OpenAI" via
  substring search on the whole URL, so a gateway named
  `not-anthropic.com.example.dev` could trigger a bogus hint (also flagged
  by CodeQL). It now parses the URL and compares the hostname exactly.
- **Log lines can't be forged via the model id.** The "no pricing for
  model" warning logged the request-supplied id raw; newlines are now
  stripped so a crafted id can't inject fake log entries (CodeQL).
- **The MCP get_session and search tools stop firehosing.** Found live when a real
  MCP client met a 1.6MB session: full conversation snapshots were being
  dumped into the asking model's context. Sessions now return compact
  per-call summaries by default: everything about each call, a content
  preview, and the byte sizes of what was withheld, with the action_id to
  drill in via the explain tool. `include_messages=true` remains the
  explicit firehose. Search had the same disease (three hits once weighed
  498KB) and got the same cure.
- **Red means "your agent had a problem", nothing else.** The errors
  column no longer counts Claude Code's routine quota probes or upstream
  429/503/529s that clients retry through; both are labeled on the call
  (probe, transient) and excluded from every error count, completing what
  the blocked-vs-errors split started.
- **BMAD v6 was entirely undetected**: its personas ship as host-tool
  skills, not system prompts, so a full real project ran through the proxy
  tagged plain claude-code. The detector now reads skill invocations (last
  one wins) and the installed-skill listing; merely *talking about* BMAD
  still doesn't count.
- **Failed calls always say why and where** ("upstream 404 on POST
  /v1/messages (no error body)"). A red badge with no reason was a dead
  end, and a wire-format mismatch (Anthropic agent, OpenAI upstream) now
  names itself instead of masquerading as "model does not exist".
- **Flow no longer draws one story as islands**: a detected persona change
  between consecutive calls becomes an inferred (dotted) handoff edge.
- **The report card resets when you switch sessions**: session A's
  comparison no longer haunts session B's page.

## [0.7.0] - 2026-07-31

Teams, free time travel, and the start of premiumness. Every feature and
fix below was hand-tested in a full guided walkthrough (16 findings filed
during testing, all fixed and re-verified) plus a second retest round.

### Added (premium ops)
- **One config file instead of nine env vars.** `agenticledger init` writes
  a commented `agenticledger.toml` (upstream, keys or key-file paths,
  budgets, replay targets); the proxy loads it at startup. Environment
  variables always win over the file, so Docker/Kubernetes deployments are
  unaffected.
- **Background service commands.** `agenticledger start` detaches the proxy
  (survives the terminal closing; logs to ~/.agenticledger/proxy.log),
  `status` reports version/port/store health, `stop` shuts down cleanly,
  `logs -f` tails, `serve` keeps today's foreground mode for containers.
- **The ledger look** — a CSS-only design pass: layered surfaces with real
  elevation, tabular numerals everywhere a figure appears, calm 160ms
  transitions, refined tables, gradient spend bars, styled scrollbars.
  Color stays reserved for meaning (green money, amber walls, red breakage,
  purple replay lineage).

### Fixed & improved (0.7 walkthrough findings)
- **Blocked is not broken.** Calls the ledger refuses on purpose (budget
  walls) are now counted as *blocked* (amber) everywhere, never as errors
  (red) — reports, session cards, call badges. A healthy wall no longer
  makes a healthy agent look sick.
- **Dead cards answer 403, not 401.** A revoked or invalid team card gets a
  final "no" instead of a retry-inviting "authenticate again" — no more
  agent retry bursts after revocation. Missing keys still get 401.
- **Sessions say whose they are and how they're doing**: team badge, red
  tile + "N failed", amber "N blocked" on every session card.
- **By-team report answers "who ran dry?"**: errors and blocked columns
  plus each team's spend-today against its card's daily allowance.
- **Replay panel redesigned**: destination first (labeled by where it goes —
  "local — localhost:1234", not a wire-format name), then a model box that
  lists what the destination actually serves (local servers are asked via
  /v1/models), remembered across panels. Unrecognized model names route to
  your only configured target automatically.
- **Replays wear their lineage**: purple session tiles, a replay badge, and
  "↩ Open original" jumping back to the call that was re-run.
- **Errors name the address they couldn't reach** ("is LM Studio running?"),
  and a call whose reply was all tool calls says so instead of showing an
  empty response box.
- **Keys can come from files**: every AGENTICLEDGER_*_KEY accepts a _FILE
  variant (Docker-secrets pattern) so secrets stay out of shell history.
- **Run badges explain themselves**: hover "complete" vs "ended" for the
  plain-words difference (declared victory vs just stopped).

### Removed
- **The classic dashboard.** /classic and the embedded single-file
  dashboard are gone; the web app has been the real dashboard since 0.3.
  A source checkout without a Node build now gets build instructions at /.

### Added
- **Cross-provider replay.** ↻ Replay now crosses providers: a captured
  Claude call can re-execute on an OpenAI-format model and vice versa —
  tool_use/tool_result blocks become tool_calls/tool messages, schemas
  swap between input_schema and function.parameters, and the system prompt
  moves to the right place, automatically. Configure per-provider targets
  (`AGENTICLEDGER_REPLAY_OPENAI_KEY`/`_URL`, `..._ANTHROPIC_...`); point
  the OpenAI target at LM Studio and replaying captured Claude calls on a
  local model is free. Captures containing images are refused with a clear
  reason instead of being mangled; the replayed call is stored under the
  provider that actually served it.
- **Cost what-if** (`GET /api/whatif` + widgets in run and session views):
  reprice any run, session, or call's captured token counts on another
  model — "this run on haiku: $0.31 instead of $4.20" — pure arithmetic,
  zero API calls, no key needed. Cache tokens repriced under the target
  family's convention; clearly labeled an estimate.
- **Team cards** (virtual keys): mint ingest-role tokens via
  `POST /api/tokens` — each opens the proxy, attributes every call to its
  team, and can carry its own daily budget enforced in-path with
  `Retry-After`. Reports gains a by-team spend table. The shared
  `AGENTICLEDGER_INGEST_KEY` keeps working unchanged; cards are stored
  hashed and shown once.
- **⚿ in the topbar** — a small panel (not a browser popup) that sets or
  clears the stored dashboard access key, no more `?key=` URL trick. Before
  saving, the server identifies the key (`GET /api/whoami`): master key,
  named viewer/editor/admin token, or — plainly warned — a team card, which
  opens the relay for agents but not the dashboard. Minted tokens pasted
  into the ⚿ field now authenticate too (the header the dashboard sends
  previously only carried the master key).

## [0.6.1] - 2026-07-30

Nineteen fixes across two rounds of a full hands-on user walkthrough —
every finding was filed as an issue during the test, fixed, and re-verified
live before this release. Round two added: an instant run-end signal
(`POST /api/runs/{id}/end`, fired by `agenticledger run` on exit, so runs
read `ended` the moment the loop stops), size-based prompt-drift call
selection (no more diffing Claude Code's hidden title-generator prompt),
latency percentiles computed over successful calls only, a `partial` badge
for aborted streams, and comparison-layout polish.

### Fixed
- **Budget blocks no longer cause client retry storms** (#27): budget 429s
  carry an honest `Retry-After` (seconds until the UTC-midnight reset;
  session budgets never reset so they send none), rate-limit and
  loop-breaker 429s carry a short one, and `AGENTICLEDGER_BUDGET_STATUS=402`
  opts into Payment Required, which clients never retry.
- **Replay preserves block-form system prompts** (#25): Claude Code sends
  `system` as content blocks; these are now flattened into the stored
  system_prompt and passed through verbatim on replay instead of being
  silently dropped.
- **Finished runs read `ended`, not `running` forever** (#17): a run with
  no promise and no flags flips to `ended` once its last call is older than
  the run-gap window.
- **Prompt drift compares the real prompt, not Claude Code's utility
  probe** (#19): the diff now selects the first substantive call (first
  with a system prompt, then first non-trivial).
- **Replay errors show the reason** (#24, #26): upstream status and detail
  render in the panel, and a 401 explains that a subscription login is not
  an API key, pointing at the console and the free LM Studio path.
- **Comparison durations format sanely** (#20): seconds under two minutes,
  and deltas invisible at display precision show "=" instead of "−0.0 (-31%)".

### Added
- **Cache-write visibility** (#16): call cards show a ✍ written chip next
  to the ⚡ cached one, and the replay side-by-side includes cache figures —
  the "why does a 6-token call cost $0.23" mystery now answers itself.
- **Models everywhere they were missing** (#18): run cards, the run detail
  header, and the comparison's configuration table (now always visible)
  name the models involved.
- **Local-time reports** (#22): `/api/reports` accepts `tz_offset_minutes`
  and the web app buckets days in your browser's timezone; budgets and the
  digest remain UTC.
- **Readable spend-bar labels** (#21): "Jul 29" instead of a bare "29".
- **Cache explained in-UI** (#23): hover titles on the cache tile and
  columns plus a footnote defining cache Δ.
- **Relative timestamps on session and run tiles** (#14): "2h ago" makes
  the newest-first order visible.

## [0.6.0] - 2026-07-28

### Added
- **Replay.** Re-execute any captured call from the dashboard (or
  `POST /api/replay`): same messages, system prompt, tools, and sampling
  parameters, on the original model or a swapped same-provider one — then
  compare output, tokens, cost, and latency side by side. Replays are
  stored as first-class ledger calls linked to the original
  (`framework=replay`, `parent_action_id`), so what-if experiments are
  themselves audited and priced. The proxy never stores agent credentials,
  so replay uses its own key: set `AGENTICLEDGER_REPLAY_API_KEY` to enable
  (unset = feature off).
- **Prompt drift in run comparison.** Comparing two runs now also diffs
  their system prompts and opening instructions — the recorder-native
  answer to "what did I actually change between these runs."
- **Latency percentiles and error rates in Reports.** p50/p95/p99 latency
  and error counts per model and per agent (`percentile_cont` on Postgres,
  nearest-rank on SQLite).
- **Per-user daily budgets.** `AGENTICLEDGER_BUDGET_USER` caps spend per
  `user_id` per UTC day — the budget follows the user across sessions,
  blocking with 429 before the call reaches the provider.

## [0.5.1] - 2026-07-28

### Security
- **Cleared all CodeQL path-injection alerts in SPA asset serving.** The
  asset route validated containment with a prefix `startswith` check (the
  sibling-directory bypass shape). Assets are now served by exact-name
  lookup against the directory listing — the request name is only ever a
  dictionary key, so user input never becomes a filesystem path. Behavior
  is strictly tighter: only files literally present in the build output
  can be served. Code scanning now reports zero open alerts.

## [0.5.0] - 2026-07-28

### Added
- **Reports view — where your money goes.** New tab in the web app (and
  `GET /api/reports?days=N`): spend per day, model mix, per-agent totals,
  and **signed cache savings** — what your prompt-cache traffic would have
  cost at full input rates minus what it actually cost, per provider
  convention. Heavy cache writes that never get read back show up as a net
  cost rather than being clamped to zero.
- **Daily digest webhook.** Set `AGENTICLEDGER_DIGEST_HOUR` (UTC hour) and
  the proxy POSTs a last-24h summary — spend, calls, cache savings, top
  models and agents — to `AGENTICLEDGER_ALERT_WEBHOOK_URL`, formatted to
  render nicely in a Slack incoming webhook.
- **Run comparison in Loop Lens.** Pick any two runs with the new ⇆ toggle
  and get a side-by-side diff — cost, iterations, calls, tokens, flagged
  calls, duration, each with a signed delta and percentage — plus both
  cost-per-iteration ribbons on one shared scale. Built for the
  change-the-prompt-and-rerun workflow: did the new run actually get
  cheaper, shorter, and cleaner?

### Fixed
- **The header's live dot now tells the truth.** It was static CSS — green
  forever, even with the proxy shut down (dogfood report). It now tracks
  the WebSocket connection state: green while connected, red with a
  "disconnected — proxy unreachable, retrying" tooltip while down, and it
  recovers automatically when the proxy comes back.

### Added
- **OTLP `http/protobuf` ingest.** `/v1/traces` and `/v1/logs` now accept
  the protobuf encoding alongside JSON (needs `opentelemetry-proto`,
  shipped by the `[otel]` extra; the Docker image includes it). Protobuf
  batches produce the same deterministic action_ids as their JSON twins
  (trace/span ids are normalized from proto3-JSON base64 back to OTLP/JSON
  hex), and success responses are returned in the caller's encoding. Python
  OTel SDKs now work with a bare
  `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:8000` — no Collector
  bridge, no `http/json` override.
- **Session delete in the web app.** Session cards grew a hover-reveal ×
  that deletes the session's captured calls (confirmation required; audit-
  logged; editor role when auth is enabled) — matching what the API always
  allowed via `DELETE /api/sessions/{id}`.

### Changed — BREAKING
- **Full naming cleanup: every technical name is now `agenticledger`.**
  The pre-rebrand `agentledger` slug is gone from the entire surface — one
  hard cut while the install base is small, so the brand and the commands
  finally say the same thing. What stays the same: the PyPI package
  (`pip install agentic-ledger`), the Docker image
  (`ghcr.io/shekharbhardwaj/agentic-ledger`), and your data schema.
  What renames:

  | 0.3.x | 0.4.0 |
  |---|---|
  | `python -m agentledger.proxy` | `python -m agenticledger.proxy` |
  | `agentledger run` / `agentledger mcp` | `agenticledger run` / `agenticledger mcp` |
  | `AGENTLEDGER_*` env vars | `AGENTICLEDGER_*` |
  | `x-agentledger-*` headers | `x-agenticledger-*` |
  | default DB `agentledger.db` (Docker: `/data/agentledger.db`) | `agenticledger.db` (Docker: `/data/agenticledger.db`) |
  | OTel service name `agentledger` | `agenticledger` |

  Migration: upgrade with `pip install -U agentic-ledger`, add `IC` to your
  env vars and headers, switch commands to `agenticledger`, and rename your
  existing database file (e.g. `mv agentledger.db agenticledger.db`) to keep
  your capture history. Old names stop working entirely — there are no
  compatibility aliases.

## [0.3.4] - 2026-07-27

### Added
- **Hardened, multi-arch Docker image.** The container now runs as a
  dedicated non-root user (uid 10001), is built for linux/amd64 and
  linux/arm64, and ships a `HEALTHCHECK` probing `/health`. A cold
  `docker build` from a plain checkout now works: when no wheel is present
  in `dist/`, the build installs the published release from PyPI (pin with
  `--build-arg AGENTLEDGER_VERSION=x.y.z`); CI exercises this path on every
  push.
- **Signed images with SBOM and provenance.** Release images are signed with
  Sigstore cosign (keyless — verifiable against the release workflow's
  GitHub Actions identity) and pushed with BuildKit SBOM + SLSA provenance
  attestations. Each GitHub release attaches a standalone SPDX SBOM of the
  image.
- **Docker Hub mirror.** When the `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`
  repo secrets are configured, releases also push
  `docker.io/<username>/agentic-ledger` with the same digest as GHCR.
- **Deployment hardening guide** ([docs/deployment.md](docs/deployment.md)):
  enterprise mirrors (Artifactory/Nexus/CodeArtifact), signature and SBOM
  verification, TLS termination examples, auth keys, redaction/retention,
  bind-mount permissions for the non-root container, and an honest
  single-replica scaling stance.

- **Startup version banner.** The proxy prints `Agentic Ledger vX.Y.Z`,
  the upstream, and the dashboard URL on boot — a stale venv silently
  serving an old release was indistinguishable from a current one (a
  tester on a pre-0.3 install had no way to notice). The pip quick start
  now uses `pip install -U`, and Troubleshooting explains the
  "requirement already satisfied" trap.

### Changed
- **The Docker image is now `ghcr.io/shekharbhardwaj/agentic-ledger`**,
  matching the PyPI package name. The old `ghcr.io/shekharbhardwaj/agentledger`
  path stays frozen at 0.3.3 — update your pulls.

## [0.3.3] - 2026-07-26

### Added
- **Stdio transport for the MCP server** (`agentledger mcp`): newline-delimited
  JSON-RPC on stdin/stdout, sharing the exact tool dispatch with the HTTP
  endpoint. For clients that launch MCP servers as subprocesses (Claude
  Desktop command configs, Glama's inspection harness, Cursor stdio servers).
  Reads the proxy's database via AGENTLEDGER_DSN; diagnostics go to stderr so
  stdout stays protocol-clean.

## [0.3.2] - 2026-07-26

### Security
- Refreshed locked dependencies (uv.lock), clearing all open Dependabot
  alerts (starlette, urllib3, setuptools, pytest, idna).

### Added
- The classic dashboard links back to the new app ("New app →" in its
  header) — the reverse link already existed.
- **Loop Lens iterations click through to their session.** Iteration table
  rows and cost-chart bars open the underlying session in the Sessions view
  (new `session_id` on the per-iteration aggregates), matching the flag
  cards' drill-down.

## [0.3.1] - 2026-07-26

### Fixed
- Claude Code session detection now understands the claude-cli 2.x metadata
  format: `metadata.user_id` is a JSON blob (`{"device_id": …, "account_uuid":
  …, "session_id": …}`) rather than the 1.x `…_session_<uuid>` string, so real
  sessions were landing in the shared auto-`<date>` bucket. Both formats are
  recognized, and the 2.x `x-anthropic-billing-header: cc_version=…` system
  block is a new body-side fingerprint for `claude -p` (sdk-cli) traffic,
  whose system prompt no longer opens with "You are Claude Code".
- Utility-call classification no longer swallows main-loop calls with a
  user-shrunk `CLAUDE_CODE_MAX_OUTPUT_TOKENS`: small `max_tokens` alone
  (≤1024) used to exclude a call from loop inference (`step_index` null), but
  real claude-cli 2.x main calls send 32k–64k by default and as little as the
  user configures. Utility now means a near-zero probe (`max_tokens` ≤ 8,
  e.g. the startup "quota" ping) or a haiku-class model at ≤1024 tokens
  (title/summary housekeeping) — all shapes verified against captured
  claude-cli 2.1.220 traffic.

## [0.3.0.post1] - 2026-07-26

Metadata-only post-release: corrects the MCP registry ownership marker's
namespace casing in the package README (no code changes).

## [0.3.0] - 2026-07-26

The agentic-loop release. Consolidates the 0.3.0 pre-release line
(alpha.1 through beta.1, all detailed below) into the first stable release
under the Agentic Ledger name. Headlines:

- **Loop engine** — threads, runs, iterations, and stuck-loop detection
  inferred from raw traffic, with an in-path circuit breaker.
- **Loop Lens web app** at `/` — runs, cost per iteration, flag explanations,
  Flow DAG, and a Trace waterfall built from real parent links.
- **Costs you can bill against** — cache-aware pricing per provider
  convention, gateway-tolerant model matching, loud warnings for unpriced
  models.
- **`agentledger run`** — the observable, budgeted loop runner, with
  completion promises and a morning-report webhook.
- **Zero-config Claude Code support** and OTLP ingest for OTel-native tools;
  fourteen framework guides under docs/integrations/.

### Added
- Per-framework integration guides for all fourteen supported tools under
  docs/integrations/ with an index and an OTLP protocol note (JSON encoding;
  Collector bridge snippet for protobuf-only Python SDKs). Initially:
  guides for opencode, LangGraph/LangChain, and the OpenAI Agents
  SDK (docs/integrations/), plus an MCP registry manifest (server.json) as a
  PyPI package listing with the ownership marker in the README; the release
  workflow re-publishes the registry listing on every tag via GitHub OIDC.

## [0.3.0-beta.1] - 2026-07-26

### Added
- **Gateway-tolerant pricing + unpriced-model visibility.** Model-id matching
  now unifies dots and dashes, so OpenRouter (`anthropic/claude-3.5-sonnet`),
  Bedrock (`us.anthropic.claude-3-5-sonnet-20241022-v2:0`), and dated ids all
  price at the underlying model's rate. Unknown models log one loud warning
  (with the exact `AGENTLEDGER_PRICING` override to add) instead of silently
  recording unknown cost forever. Table refreshed: GPT-5 family, Claude
  4.5-generation models, Gemini 2.5 Flash, and o3's mid-2025 reprice.

## [0.3.0-alpha.4] - 2026-07-26

### Added
- **Flow + Trace in the web app — full parity, and the app is now the default.**
  The Sessions view gains a Calls / Flow / Trace switcher: Flow renders the
  agent handoff DAG (per-agent cost, calls, latency; cycles as dashed
  back-edges with counts), and Trace renders the waterfall with parent
  connectors from **real thread links** (`prev_action_id`) instead of the
  classic view's timestamp inference. `/` now serves the web app (falling back
  to the classic dashboard on source checkouts without a Node build);
  the classic dashboard moved to `/classic`; `/app` still works.
- **Loop-engine robustness.** (1) Claude Code's small housekeeping calls
  (session titles/summaries, identified by tiny `max_tokens` on detected
  claude-code traffic) are captured but excluded from loop inference — they
  were inflating step counts and resetting repeat-streak detection. (2)
  Context compaction is tolerated: a rewritten history carrying Claude Code's
  continuation marker re-links to its thread with an informational
  `context_compaction` flag instead of starting a phantom new thread.
  Flagged-call counts now include only problem flags (`repeat_tool_call`,
  `step_budget_exceeded`) — informational flags don't turn things amber.

## [0.3.0-alpha.3] - 2026-07-26

Alpha-tester feedback release — everything here came out of first-contact
dogfooding.

### Added
- **Flag drill-down.** New `GET /api/runs/{run_id}/flags` and a "Flags — what
  happened and why" section in the Loop Lens: each flagged call gets a
  plain-English explanation, the offending tool call and arguments, and an
  "Open session" jump into the Sessions view. Flag badges carry explanatory
  tooltips everywhere; `completion_promise` renders green (it's the good flag).
- **Call tiles show tools and interaction types.** Collapsed call cards show
  which tools the call issued, plus H2A / A2T / A2A / A2H indicators (human→agent,
  agent→tool, agent→agent handoff, agent→human) derived from captured data.
- **Abstract raccoon mark** in the web app's top bar and favicon — inline SVG,
  theme-aware, no image assets.
- **Fresh-install CI job**: builds the wheel, installs it with `--pre` into a
  clean venv, boots the proxy, and checks `/health` and `/app` — the exact
  first-contact path that broke in alpha.1.
- **Troubleshooting section** in the README (Rosetta/arch mismatch, port
  conflicts, expired OAuth, source installs without the web-app build).

## [0.3.0-alpha.2] - 2026-07-26

### Fixed
- **Fresh `pip install --pre` was broken.** `--pre` applies to dependency
  resolution too, so the unbounded `httpx>=0.27` requirement pulled httpx's
  experimental `1.0.dev3` build, which removes `AsyncClient` and crashed the
  proxy at startup. httpx is now capped below its 1.0 dev builds
  (`<1.0.dev0` — PEP 440 orders `1.0.devN` before `1.0`, so a plain `<1.0`
  cap would not have excluded them under `--pre`).

## [0.3.0-alpha.1] - 2026-07-25

### Upgrade notes
- **Costs for cache-heavy traffic (e.g. Claude Code) are now accurate.** Prompt-cache
  reads/writes were previously ignored, so `tokens_in` could reflect under 1% of the
  real context and `cost_usd` was significantly under-reported — which also meant
  budgets and alerts under-enforced. Newly captured calls will show higher, correct
  costs. Three new columns are added automatically: `cache_read_tokens`,
  `cache_write_tokens`, `thinking`.

### Added
- **Prompt-cache accounting.** Anthropic `cache_read_input_tokens` /
  `cache_creation_input_tokens` (native and via LiteLLM) and OpenAI
  `prompt_tokens_details.cached_tokens` / Responses `input_tokens_details.cached_tokens`
  are captured, stored, and priced with the correct provider convention (Anthropic:
  reads 0.1×, writes 1.25× on top of input; OpenAI: cached subset re-billed at 0.5×).
  Pricing overrides accept an extended `[input, output, cache_read, cache_write]` form.
- **Extended-thinking capture.** Anthropic `thinking` blocks (streaming
  `thinking_delta` and non-streaming) are stored in a new `thinking` field, covered by
  capture levels and redaction like all other content.
- **OpenAI Responses API streaming capture.** `response.*` SSE events (OpenAI Agents
  SDK et al.) are now reconstructed from the terminal `response.completed` /
  `response.incomplete` / `response.failed` event — previously they were misrouted to
  the Anthropic reconstructor and captured as an empty response.
- **Errored and interrupted streams are captured.** Streaming calls that return a
  non-200 are recorded with the upstream status and error body; client disconnects
  mid-stream record a partial capture (`error_detail: partial: client disconnected…`);
  mid-stream provider error events (e.g. Anthropic `overloaded_error` after a 200) are
  surfaced as `stream_error: …` instead of posing as clean calls.

- **`count_tokens` capture.** `POST /v1/messages/count_tokens` is now recorded
  (previously it was forwarded by the catch-all but invisible in the ledger). Captured
  calls are marked `stop_reason="count_tokens"` with the counted value in `content`,
  carry zero cost and no `tokens_in`/`tokens_out` — so session cost/token aggregates
  are unaffected — and are exempt from rate-limit and budget enforcement (the
  endpoint is free).
- **Zero-config agent detection.** Well-known clients are fingerprinted when no
  `x-agentledger-*` headers are present: Claude Code traffic is tagged
  `framework=claude-code` / `agent_name=claude-code` and grouped under its **real
  session UUID** (extracted from `metadata.user_id`, the same id `claude --resume`
  shows) instead of the shared `auto-<date>` bucket. LiteLLM clients are tagged
  `framework=litellm`. Explicit headers always win. New `framework` column and
  `x-agentledger-framework` header.

- **Loop engine (v1).** Raw traffic is stitched into agentic structure with no
  client cooperation:
  - *ReAct threads* — message-chain prefix matching links calls into threads with
    `thread_id`, `step_index`, `turn_index`, and `prev_action_id` columns.
  - *Runs* — `x-agentledger-run-id`/`x-agentledger-iteration` headers, or automatic
    grouping of fresh-context loop iterations (new session, same system-prompt hash,
    within `AGENTLEDGER_LOOP_RUN_GAP_SECONDS`) — the Ralph-loop pattern. New
    `GET /api/runs` aggregation endpoint.
  - *Stuck-loop detection* — consecutive identical tool calls
    (`AGENTLEDGER_LOOP_REPEAT_THRESHOLD`, default 3) and step budgets
    (`AGENTLEDGER_LOOP_MAX_STEPS`) raise `loop_flags` on the call, fire a
    `loop_flag` webhook alert, and — with `AGENTLEDGER_LOOP_ACTION=block` — return
    HTTP 429 (`loop_detected`) before the next call burns more budget.
  - *Completion promises* — `AGENTLEDGER_COMPLETION_PROMISE` (regex) flags the
    response that declares a loop done; `GET /api/runs/{run_id}` derives run
    status (`running` / `flagged` / `complete`) for loop runners to poll.
  - *Path-segment attribution* — `/r/<run_id>/<iteration>/v1/...` base URLs tag
    calls for clients that can't send custom headers.
- **`agentledger run` loop runner (new CLI).** Wraps any agent command in an
  observable, budgeted Ralph-style loop: per-iteration base-URL attribution,
  stops on completion promise / `--budget` USD ceiling / `--max-iterations`,
  prints an end-of-run cost summary. Installed as the `agentledger` console
  script.
- **MCP run tools.** `list_runs` and `get_run_status(run_id)` join the MCP
  server, so agents can inspect their own loops and self-terminate.
- **Derived tool executions.** The proxy pairs every tool call issued by call N
  with the result fed back in call N+1, yielding a `tool_executions` table with
  tool name, arguments, wall-clock latency (the gap between the calls), and
  error status (from Anthropic `is_error`). Served at
  `GET /api/sessions/{session_id}/tools`.
- **OTLP ingest.** `POST /v1/traces` accepts the OTLP/HTTP JSON encoding and
  maps GenAI-semconv spans into ledger calls (model, tokens, cost, session from
  `gen_ai.conversation.id`, agent, framework from `service.name`, error status)
  with deterministic ids so re-exported batches never duplicate. Unlocks Gemini
  CLI, Codex `[otel]`, AutoGen/AG2, Pydantic AI, and Vercel AI SDK with one env
  var; `/v1/logs` + `/v1/metrics` are acknowledged. Honors `AGENTLEDGER_INGEST_KEY`.
- **BMAD-METHOD detection + guide.** BMAD persona prompts are fingerprinted:
  calls tagged `framework=bmad` with `agent_name=bmad:sm|dev|qa|architect|analyst|pm|po|ux`
  (data-driven table in detect.py). New docs/integrations/bmad.md and
  docs/integrations/openclaw.md recipes.
- **New web app at `/app` (React SPA).** Loop Lens — runs with status badges,
  a cost-per-iteration chart, and per-iteration breakdowns (calls, tokens,
  cache reads, flags, errors, via new `GET /api/runs/{run_id}/iterations`) —
  plus a Sessions browser with expandable call cards (response, thinking, tool
  calls, cache tokens, loop flags) and live WebSocket updates. Built from
  `dashboard-app/` (Vite + React), shipped inside the wheel; source installs
  without the build keep the classic dashboard at `/`. The SPA attaches the
  api key to its requests (the classic dashboard's fetches didn't).

### Changed
- The request body is parsed once per call instead of two-to-three times — a real
  saving on multi-megabyte coding-agent contexts.

## [0.2.0] - 2026-06-19

### Upgrade notes
- **Postgres:** on first connect, `session_id` is migrated in place from `UUID` to `TEXT`
  (`ALTER COLUMN`). This is automatic and safe — no action required — but it is a schema change.
- **Audit log is on by default:** a new `audit_log` table is created and a row is written for
  each sensitive read/export/delete. Set `AGENTLEDGER_AUDIT_LOG=0` to disable.
- **Costs for `*-mini`/`*-nano` models are now correct** (previously over-reported, e.g.
  `gpt-4o-mini` was billed at `gpt-4o` rates). Newly captured costs will be lower/accurate.

### Security
- **The live-events WebSocket (`/ws`) now requires auth.** When `AGENTLEDGER_API_KEY`
  is set, `/ws` accepts the same credentials as the dashboard and API (master key or
  API token via `?api_key=`/`?token=`, `Authorization: Bearer`, or
  `x-agentledger-token`) and closes unauthenticated connects with code 1008.
  Previously any client could connect and observe live call metadata (action ids,
  session ids, status codes). The dashboard forwards its page credential to the
  socket automatically.
- Optional **proxy-ingest key** (`AGENTLEDGER_INGEST_KEY`): when set, the proxy only
  forwards requests that carry a matching `x-agentledger-ingest-key`, closing the open
  relay. Off by default to preserve the zero-config quickstart, with a loud startup
  warning when unset. The key (and the dashboard `x-agentledger-api-key`) are stripped
  before forwarding upstream.
- API key comparison is now constant-time (`hmac.compare_digest`), removing a timing
  oracle that could leak the key.
- Compliance export integrity is now honest about its guarantee. The default remains a
  SHA-256 **checksum** (catches corruption, not tampering — anyone who edits the calls
  can recompute it). Set `AGENTLEDGER_EXPORT_HMAC_KEY` to emit a tamper-evident keyed
  `hmac-sha256` tag instead. README/docs wording corrected (no longer calls the default
  checksum a "signed" trail).

### Added
- **Audit log.** Records who did what to which target — session views, searches, exports,
  deletes, token create/revoke, and erasure — with the acting principal (master / token /
  open, role, name) and client IP. Viewable at `GET /api/audit` (admin). On by default;
  `AGENTLEDGER_AUDIT_LOG=0` disables it.
- **Right-to-erasure.** `DELETE /api/users/{user_id}` (admin) deletes all captured calls for
  a user (`Store.delete_user` on SQLite and Postgres) and records the action in the audit log.
- **Data retention / TTL.** `AGENTLEDGER_RETENTION_DAYS` runs a background worker that
  periodically deletes captured calls older than the configured window (new
  `Store.purge_older_than` on SQLite and Postgres). Unset = keep forever.
- **Data governance — capture levels & redaction.** `AGENTLEDGER_CAPTURE_LEVEL=metadata`
  stores only metrics/metadata (model, tokens, cost, latency, agent, status) and drops
  prompts, responses, and tools. `AGENTLEDGER_REDACT` (`all` or a comma list of
  `email,ssn,credit_card,ip,api_key`) plus `AGENTLEDGER_REDACT_PATTERNS` (custom regexes)
  replace PII/secrets with `[REDACTED:<label>]` before anything is stored, traced, or
  broadcast. Governance transforms only the captured copy — the agent always receives the
  real, unmodified upstream response. (In async mode, redaction runs off the hot path.)
- **Async ingestion (opt-in)** via `AGENTLEDGER_ASYNC_CAPTURE`: post-call persistence
  (store write, OTel span, dashboard broadcast, alerts) runs on a bounded background
  worker so it never adds latency to the agent's call. When the queue is full (default
  10000, `AGENTLEDGER_CAPTURE_QUEUE_MAX`) load is shed and counted rather than blocking.
  Default off — sync mode keeps read-after-write; async mode is eventually consistent.
- **`/metrics`** Prometheus endpoint: captures persisted/dropped (counters), capture queue
  depth, and whether async capture is enabled. Low-cardinality (no per-session labels).
- **Scoped, role-based API tokens** (`viewer` < `editor` < `admin`) as an alternative to
  sharing the master key. Tokens are random secrets shown once at creation; only their
  SHA-256 hash is stored, and each can be given an expiry or revoked. Managed via
  `POST/GET/DELETE /api/tokens` (admin only); presented as `Authorization: Bearer`,
  `x-agentledger-token`, or `?token=`. Read endpoints require `viewer`, session delete
  requires `editor`, token management requires `admin`. Auth is enforced only when
  `AGENTLEDGER_API_KEY` is set (the master key grants `admin` and bootstraps tokens).
- `/readyz` readiness probe (runs a store `SELECT 1`; returns 503 when the store is
  unreachable) so load balancers and k8s can gate traffic. `/health` remains a pure
  liveness check that never touches the store.
- A `capture_dropped` counter: when a call is served upstream but its record can't be
  persisted, the proxy still fails open, but the loss is now logged at WARNING and
  counted (surfaced via `/readyz`) instead of being silently swallowed.

- Comprehensive automated test suite (`tests/`) with ~280 tests covering request/response
  normalization, streaming SSE reconstruction, pricing, rate limiting, budgets, the storage
  layer, the proxy request path, compliance export, the MCP server, and webhook alerts.
- Shared pytest harness (`tests/conftest.py`) with a mock-upstream proxy fixture and
  provider wire-format builders — tests are fully offline and deterministic.
- CI now runs `ruff` linting and `pytest` with coverage (gated at 70%) across Python
  3.10 / 3.11 / 3.12.
- CodeQL security scanning and Dependabot dependency updates.
- Community health files: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`,
  pull-request template, and issue templates.
- `ruff` configuration and a `[dev]` tooling extra (`pytest-cov`, `ruff`).

### Fixed
- **Postgres data loss:** the production Postgres backend typed `session_id` as `UUID`
  and cast every id with `uuid.UUID(...)`. Agent session ids are arbitrary strings — the
  proxy mints `auto-<date>` when no header is supplied, and users pass human-readable run
  names — so any non-UUID session id raised and, because the proxy save path is fail-open,
  was **silently dropped**. `session_id` is now `TEXT` (matching SQLite); existing databases
  are migrated in place on connect (`ALTER COLUMN session_id TYPE TEXT`). Added a Postgres
  regression test suite (runs in CI against a Postgres service; skipped locally without one).
- **Cost computation:** `compute_cost` now matches the longest (most specific) pricing
  pattern instead of the first substring match. Previously `gpt-4o-mini`, `o1-mini`,
  `o3-mini`, `gpt-4.1-mini`, and `gpt-4.1-nano` were each priced at their parent model's
  (much higher) rate — e.g. `gpt-4o-mini` was billed at `gpt-4o` rates (~16× too high on
  input). Captured costs for these models are now correct.

### Changed
- Rate-limiter memory is now bounded: idle per-session/agent/user windows are evicted as
  they age out, with a sweep guarding against pathological key cardinality. Corrected the
  docstring that overclaimed cross-process safety (limits are enforced per process).
- Stopped tracking the runtime SQLite database (`agentledger.db`) in git and added `*.db`
  to `.gitignore`. The database is a runtime artifact and may contain captured prompt data.

<!--
## [0.1.7] - YYYY-MM-DD
Older releases predate this changelog. See the GitHub Releases page for history:
https://github.com/ShekharBhardwaj/AgenticLedger/releases
-->

[Unreleased]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.8.2...HEAD
[0.8.2]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.7.0...v0.8.0
[0.3.0.post1]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.3.0...v0.3.0.post1
[0.3.0]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.2.0...v0.3.0
[0.3.0-beta.1]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.3.0-alpha.4...v0.3.0-beta.1
[0.3.0-alpha.4]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.3.0-alpha.3...v0.3.0-alpha.4
[0.3.0-alpha.3]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.3.0-alpha.2...v0.3.0-alpha.3
[0.3.0-alpha.2]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.3.0-alpha.1...v0.3.0-alpha.2
[0.3.0-alpha.1]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.2.0...v0.3.0-alpha.1
[0.2.0]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.1.7...v0.2.0
