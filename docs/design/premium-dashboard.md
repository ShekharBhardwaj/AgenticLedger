# Premium Dashboard Design and Implementation Specification

Status: proposed implementation specification, not an implementation report.
Date: 2026-09-09.
Applies to: the dashboard served at `/app` and `/`, in `dashboard-app/`.

## 1. Objective

Make Agentic Ledger feel precise, calm, and dependable through typography,
alignment, deliberate information hierarchy, and complete interaction states.
The first viewport of a selected run must communicate its identity, observed
state, recorded spend, configured ceiling, and most relevant recorded concern.

The redesign must preserve existing capabilities and the UI honesty rules in
[ARCHITECTURE.md](../../ARCHITECTURE.md#ui-honesty-rules). The premium appearance
comes from clearer organization and execution, not additional decoration.

### Design References

- [Desktop, dark](premium-dashboard/desktop-dark.png).
- [Mobile, dark](premium-dashboard/mobile-dark.png).
- [Desktop, light](premium-dashboard/desktop-light.png).
- [Interactive visual reference](premium-dashboard/reference.html).

The references contain illustrative data. This specification takes precedence
over their labels and behavior. Three corrections are intentional:

1. The reference's `Calls allowed` label is not a production requirement: the
   existing run status does not establish whether every enforcement policy will
   admit a request.
2. Its run-level `Calls` tab requires an API addition. The core redesign uses
   `Activity` for live arrivals and retains iteration-to-session navigation.
3. Its example `Net cache savings` breakdown must not be populated using the
   run audit's `received_usd`, which represents a different quantity.

## 2. Scope and Delivery Boundary

### Required Core

- Shared color, typography, spacing, surface, icon, and control specifications.
- Application chrome, run/session navigation, and compact list rows.
- Run detail hierarchy, open summary metrics, and contextual actions.
- Session call rows and an inspector for existing captured content.
- Reports, comparison, replay, settings, and pairing visual consistency.
- Dark/light/system appearance preferences stored only in the browser.
- Desktop, tablet, phone, keyboard, and screen-reader behavior.
- Explicit loading, unavailable, stale, pending, success, and failure states.
- Frontend URL state and browser Back/Forward behavior.
- Preservation of existing functionality and backend authorization.

### Separate Follow-On Work

Full-history pagination, complete run-call browsing, instance-wide incident
counts, pricing coverage, and effective enforcement status require additional
server contracts. They are defined as dependencies in section 13 and are not
prerequisites for the core visual refresh.

### Excluded

New billing or enforcement algorithms, provider routing changes, a new eval
platform, hosted services, SSO, tenant isolation, and an unrestricted settings
editor. The marketing website is outside this specification.

Use React, TypeScript, Vite, the current API helpers, and plain CSS. No framework
migration, utility-CSS conversion, or wholesale component-library adoption is
required. Bundle any new icons locally; dashboard rendering must work offline.

## 3. Design Principles

| Principle | Implementation Rule |
|---|---|
| One clear hierarchy | Identity and state first; spend and ceiling second; evidence next; raw detail on demand. |
| One fact, one primary location | Do not repeat the same spend in a tile, sentence, and meter caption. |
| Color has meaning | Amber indicates a warning or deliberate refusal; red indicates an actual failure; financial deltas retain their meaning. |
| Numbers align | Monetary values and counts use tabular numerals and right-aligned table columns. |
| Evidence stays accessible | A headline links to recorded calls or exposes the method and scope behind the figure. |
| Stable geometry | Hover, selection, live updates, and loading do not move adjacent controls or columns. |
| Details remain available | Simplifying presentation must not remove raw IDs, provider IDs, tokens, tools, replay lineage, or capture limitations. |
| State is not inferred beyond evidence | A socket connection, lack of a manual block, and agent-declared completion are separate facts. |

## 4. Visual Foundations

### 4.1 Color Tokens

Extend the existing CSS-variable approach in `src/styles.css`. The names below
are the canonical meanings; preserve compatible existing names where possible.
Light and dark appearances use the same geometry and typography.

| Token | Dark | Light | Use |
|---|---|---|---|
| `--bg` | `#111315` | `#FFFFFF` | Main canvas |
| `--bg-panel` | `#16191C` | `#F7F8F9` | Sidebar and secondary bands |
| `--bg-card` | `#1B1F23` | `#FFFFFF` | Menus, dialogs, genuine framed tools |
| `--bg-hover` | `#23282D` | `#EEF1F4` | Hover state |
| `--bg-input` | `#15181B` | `#FFFFFF` | Inputs |
| `--border` | `#30363C` | `#DDE2E7` | Nonessential dividers |
| `--border-control` | `#77828C` | `#78838E` | Essential input/control boundaries |
| `--text` | `#EDF0F2` | `#202428` | Primary text |
| `--text-dim` | `#A0A9B1` | `#606972` | Secondary text |
| `--accent` | `#87B5EF` | `#245FAB` | Links, selection, focus, chart series |
| `--green` | `#8BCEAC` | `#247452` | Favorable measured financial results |
| `--amber` | `#E6BB73` | `#855413` | Warnings and intentional refusals |
| `--red` | `#FF9393` | `#B42332` | Failures and destructive actions |
| `--purple` | `#C7ACED` | `#7443AA` | Replay lineage |
| `--warning-bg` | `#211E18` | `#FCF8EF` | Warning evidence band |
| `--selected-bg` | `#1B2838` | `#EAF1FA` | Selected navigation item |

Do not use muted dividers as the sole visible boundary of an input. Validate
each foreground/background pairing actually used, including hover and selected
states; token selection alone is not an accessibility certification.

- Use solid fills. Remove decorative radial backgrounds, stat-tile accent
  streaks, gradients on routine surfaces, glows, and card-lift effects.
- Keep the existing brand mark in the chrome. Replace repeated decorative
  status animation inside data rows with the shared status treatment.
- Use a narrow leading rule plus background change for row selection.
- Alerts use a leading semantic rule, concise heading, and evidence/action.
  They are full-width bands, not a stack of floating cards.
- Shadows are reserved for overlays: dark `0 8px 24px rgba(0,0,0,.24)`;
  light `0 8px 24px rgba(20,30,40,.12)`.

### 4.2 Typography

Default sans stack: `-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`.
Default mono stack: `ui-monospace, SFMono-Regular, Consolas, monospace`.
Use local fonts. No external font request is required for the application.

| Role | Size / Line Height | Weight | Family |
|---|---|---|---|
| Brand | 16 / 24 px | 600 | Sans |
| Run or page title | 24 / 32 px | 600 | Sans |
| Primary spend | 32 / 40 px | 500 | Mono, tabular |
| Secondary metric | 24 / 32 px | 500 | Mono, tabular |
| Section heading | 14 / 20 px | 600 | Sans |
| Body and controls | 14 / 22 px | 400 or 500 | Sans |
| Table and list primary text | 13 / 20 px | 400 or 500 | Sans |
| Metadata and table headings | 12 / 18 px | 400 or 500 | Sans |
| Raw code, IDs, token detail | 12 / 18 px | 400 | Mono |

- Letter spacing is zero throughout; use sentence case for ordinary labels.
- No routine user-facing label below 12px in the final implementation. The
  prototype's smaller table labels are illustrative, not normative.
- Use monospace only for numerical values, code, and identifiers; human names,
  navigation, actions, and status labels use sans-serif.
- Mobile text inputs use 16px text to avoid browser zoom on focus.
- Do not scale text using viewport-width units. Reflow and disclosure resolve
  narrow layouts. Browser text enlargement must remain usable.

### 4.3 Spacing and Geometry

| Element | Specification |
|---|---|
| Spacing scale | 4, 8, 12, 16, 24, 32 px |
| Desktop content padding | 24px |
| Compact/tablet content padding | 20px |
| Phone content padding | 16px |
| Section separation | 24px; 32px only between major groups |
| Application header | 56px minimum; may grow for accessible text reflow |
| Sidebar | 304px at wide desktop; 280px at compact desktop |
| Detail content | Full available width; prose/raw-reading blocks capped near 80 characters per line |
| Controls | 36px desktop minimum height; 44px for coarse pointers |
| Icon buttons | 36 x 36px desktop; 44 x 44px for coarse pointers |
| Icons | 16px in rows; 18px in main toolbars; consistent stroke weight |
| Table rows | 48px minimum; secondary line permits 64px |
| List rows | 80px minimum; up to 112px with a two-line title and identity |
| Border radius | 6px controls/menus; 8px dialogs; 0px page sections |
| Transitions | 120-160ms for color/opacity; no layout or hover translation |

Use grid tracks, `min-width: 0`, and explicit action columns. Rows may grow for
long content or accessibility; fixed heights must never clip text.

### 4.4 Icons and Controls

Use locally bundled `lucide-react` icons with direct named imports and a locked
package version selected during implementation. Existing provider and brand
marks remain. Do not replace the Flow/Trace domain graphics with icon artwork.

| Action | Icon / Treatment |
|---|---|
| Copy ID/link | `Copy`, confirmation becomes `Check` without resizing |
| Rename | `Pencil` in the action menu |
| Pin | `Pin`, persistent selected indication |
| Compare | `GitCompareArrows`, with a two-selection comparison bar |
| Replay | `RotateCcw` plus text in the secondary toolbar |
| Download CSV | `Download`, accessible name includes export scope |
| Settings | `Settings` |
| Access/pairing | `KeyRound` / `QrCode` |
| More actions | `MoreHorizontal` |
| Block future calls | `Ban` plus explicit text |
| Remove operator block | `ShieldCheck` plus `Allow calls again` |
| Failure / warning | `CircleAlert` / `TriangleAlert` with text |

Icon-only controls need accessible names and tooltips on hover and focus.
Essential commands remain visible on touch. Native selects or established
local popovers are preferred over new custom selection systems.

## 5. Application Shell and Navigation

### Chrome

- Retain top-level `Loop Lens`, `Sessions`, and `Reports` labels for this
  release. Keep Settings and Access as consistently sized chrome actions.
- Show the existing brand mark and wordmark. At narrow widths, the accessible
  brand name remains even if the wordmark is shortened visually.
- Preserve the named-instance label. It must remain visible in every theme and
  at phone widths; long names wrap within a reserved secondary chrome line.
- Show `Live updates connected` or `Reconnecting` through a compact status
  control. A connection indicator describes the event connection only.
- Put version, documentation, issue reporting, and website links in an About
  menu. Remove the permanently occupying footer, particularly on phones.
- Appearance offers `Dark`, `Light`, and `System` in Settings as a local
  preference. Default to Dark to preserve the existing application experience.
  Persist the choice under `agenticledger.ui.theme` and resolve it before paint.

### URL State

Use hash routes so existing server paths and static serving need no rewrite:

```text
/app#/runs
/app#/runs/<encoded-run-id>?view=overview
/app#/runs/<encoded-run-id>?view=activity
/app#/sessions/<encoded-session-id>?view=calls&call=<encoded-action-id>
/app#/reports?days=30&project=<encoded-project>
/app#/settings
```

- Parse using URL APIs; preserve encoded opaque IDs without normalization.
- Selection and navigation create history entries. Debounced search edits
  replace the current entry. Back/Forward restores the appropriate view.
- Preserve filters, selected subview, and list scroll during in-app navigation.
  Selection state is scoped by entity and instance; responses for entity A must
  never populate entity B after a fast switch.
- Restore a previously selected run only after a fresh authorized fetch.
  A missing item gets a real not-found state with a return-to-list action.
- A directly linked item outside the recent list still opens via its detail
  endpoint. Do not pretend it is part of the currently loaded list.
- Ordinary copied links omit `api_key` and `token`; recipients authenticate
  normally. Only the explicit existing pairing flow carries a pairing key.
- Browser storage may contain appearance and opaque navigation state. Do not
  add persistence for prompts, outputs, search text, or credentials beyond the
  application's existing access-key behavior.

### No Selection / Empty Data

- If recent records exist, show a compact `Recent runs` summary and selectable
  rows in the detail area, or restore the previous valid selection.
- Any count is scoped to loaded records: for example, `18 recent runs`.
  Do not present loaded-list active/flagged counts as instance-wide totals.
- If a filter has no matches, show a filter-specific empty state and Clear
  filter action. Do not show the first-install message.
- If a successful response establishes no captured records, show `Waiting for
  the first call`, the local endpoint, and the existing integration setup
  actions. Keep setup copy concise and contextual.
- A failed fetch is an error state, never an empty state.

## 6. Run and Session Lists

Replace raised sidebar cards with flat, separated rows. Preserve project
grouping, pinning, sort order, team information, and session-to-run navigation.

Each row uses three zones: identity, summary, and a reserved action column.

1. Human name in sans-serif, maximum two visible lines. If no label exists,
   display the opaque ID with safe wrapping/ellipsis.
2. Observed status and a compact, right-aligned recorded cost.
3. Secondary metadata: relative age, calls/iterations, and shortened model
   description. Keep the full model identifier accessible on focus/inspection.

- A renamed entity exposes its raw ID on a secondary identity line and in the
  detail header; it must not depend on hover alone.
- Show one primary model label plus `+N models` when there are more.
- Keep the session's run link legible. Preserve the current title/run-chip
  fix rather than reintroducing a squeezed, empty chip.
- Pin state is persistent and visible. Less frequent actions move to More.
- Compare is reachable from a row menu; selected runs appear in an explicit
  `1 of 2 selected` / `2 of 2 selected` comparison bar with Clear and Compare.
- Use native links/buttons for keyboard navigation. Do not nest buttons inside
  a clickable button or make an entire table row the only way to open detail.
- Preserve scroll and focus when new records arrive. A user reading older
  records receives a `New activity` affordance rather than forced scrolling.

## 7. Run Detail

### 7.1 Header and Summary

Desktop order:

```text
Project / Runs                       Run actions
Human run name                       Observed status
Raw run ID / started / last activity / model summary

Recorded spend          Run ceiling             Model calls
$8.42                   $15.00                  60
                        [progress]              4 iterations

Overview    Activity    Cache          What-if / Replay
Priority recorded concern with Inspect action
Iteration cost chart and iteration rows
```

- Put spend, ceiling, and calls in one open metric strip; no individual boxes.
- Keep tokens in/out and full model breakdown in secondary detail rather than
  additional headline tiles. Preserve access to every existing figure.
- The ceiling track displays recorded spend divided by configured ceiling.
  It is an accounting display, not proof of guaranteed remaining admission.
- `No run ceiling` replaces a blank track when no ceiling is configured. Do not
  imply that other agent/team/daily limits are absent.
- Preserve existing burn/projection information as secondary text. Label a
  projection `At the recent pace`; never represent it as a predicted invoice.
  If the recent-cost field is unavailable, display unavailable, not zero.
- Do not add `Reserved`, `Available`, or pricing-completeness figures without
  the corresponding backend data described in section 13.

### 7.2 Observed Status and Control Semantics

| Backend Status | Visible Label | Color | Meaning |
|---|---|---|---|
| `running` | Running | Neutral/accent | Recent calls were observed. |
| `flagged` | Flagged | Amber | Recorded loop concerns exist; inspection explains them. |
| `complete` | Completion declared | Neutral/accent | The agent's completion signal was observed; task correctness is not established. |
| `ended` | Ended | Neutral | Exit or inactivity; no success claim. |
| `stopped` | Calls blocked | Amber | The operator block is set for this run ID. |

Do not derive `Healthy`, `Protected`, `Successful`, or `Calls allowed` from
these statuses. A separate provider failure remains a red event, and a stale
view is not evidence that the agent has stopped.

Control rules:

- Running/flagged run: visible `Block calls` action for authorized editors.
- Ended/completion-declared run: `Block future calls` in the action menu.
- Operator-blocked run: visible `Allow calls again` action for editors.
- Blocking refuses future requests for this ID. It does not terminate the
  process, cancel an in-flight provider request, or delete history.
- Allowing calls removes the operator block only. It neither restarts the
  agent nor bypasses other limits.
- Confirmation names the run and the exact effect. Use standard modal focus
  handling; avoid an action row that expands unpredictably inside the title.

### 7.3 Ceiling Editor

- Present a labeled USD input with explicit Save and Cancel controls.
- Accept finite values greater than zero and at most 1,000,000, matching the
  server limit. Preserve existing meaningful decimal precision.
- Treat clearing as a distinct `Remove run ceiling` action that sends zero.
  Blank input does not silently remove protection.
- Disable duplicate submission while pending. Keep typed input and the editor
  open on failure. Success uses the server-confirmed value.
- Refresh every visible copy of the ceiling and run state after success.
- If the new ceiling is below recorded spend, state that future requests may
  be refused by the ceiling; do not imply existing spend is reversed.

### 7.4 Overview and Evidence

- Order concerns by recorded significance: current explicit refusal, latest
  unresolved-by-evidence loop concern, then cache opportunity. Without an
  actual resolution model, use `Recorded concern`, not `Open incident`.
- Show a concise concern title, count/scope, recorded reason, and Inspect.
  Expand additional recorded flags under an accessible count.
- Inspect navigates to the relevant call or session and focuses the evidence.
  Use available identifiers; never fabricate tool-execution results.
- Place the iteration chart before dense technical detail. Default columns:
  iteration, activity/warnings, calls, recorded cost. Expand tokens, cache reads,
  timestamps, and session links.
- Derive iteration labels only from supplied fields. `No recorded flags` is
  not equivalent to successful completion.
- A row with multiple sessions says `N sessions`; it must not silently open
  one arbitrary session as though it represented the whole iteration.

### 7.5 Activity

The core redesign's Activity view contains the existing live-event buffer.
Label its scope `Arrivals since this view opened`; retain the existing bound
on the buffer. It is not the complete persisted call history.

Rows contain time, model, iteration, status, latency, and recorded cost.
Click-through uses event session/action IDs. Keep iteration-to-session history
available from Overview. A complete Calls view is a separate API-backed task.

### 7.6 Cache

Keep all supported verdicts visible, including `not_auditable`:

| Verdict | Required Presentation |
|---|---|
| `well_cached` | Backend verdict and reported read discount, with its scope. |
| `partially_cached` | Reported discount, estimated additional opportunity if supplied, reason, and fix. |
| `never_requested` | Estimated opportunity if supplied, reason, and provider-specific fix. |
| `unstable_opening` | Recorded divergence evidence and fix; no invented dollar amount. |
| `too_short` | Reason; no savings opportunity asserted. |
| `not_auditable` | Unavailable state with the supplied reason. Never style as healthy or zero waste. |

- `received_usd` is labeled `Cache-read discount`; it is not net savings after
  cache-write charges. Reports' `cache_savings_usd` remains `Net cache savings`.
- Keep `eligible.estimated_usd` visibly marked `Estimate`, with `eligible.method`
  available in an accessible disclosure. Do not combine estimates and reported
  discounts into one apparently exact total.
- Do not automatically modify prompts, cache markers, or provider traffic.
- A failed audit request shows an error and Retry; it does not disappear.

## 8. Sessions and Call Inspector

Retain the existing Calls, Flow, and Trace views as peer segmented controls.
Put What-if and Replay in the secondary action area. A saved replay result
remains discoverable with a `Replay result available` action; it must not force
the execution evidence below an expanded report on every open.

### Call Rows

Desktop default columns: call number/time, model and operation, status, latency,
recorded cost. Additional metadata appears in the inspector, not a badge chain.

- Preserve current chronological numbering independently of visual sort.
- A shortened model label keeps provider identity and exposes the full ID.
- Distinguish `blocked`, `transient`, `probe`, `partial`, and actual failures.
  Do not aggregate them into one red error count.
- Show cost unknown as `Unknown`, not `$0.0000` or an unexplained dash.
- Preserve loop, framework, agent, handoff, thread, and iteration metadata in
  the inspector. Keep replay-to-original navigation available.

### Inspector

Open inline below the selected row at desktop widths; at phone widths open a
full-width detail pane with Back to calls. Do not add a third narrow desktop
column beside the existing sidebar and main list.

| Tab | Content |
|---|---|
| Response | Captured output; a relevant failure/refusal reason precedes it. |
| Tools | Captured tool requests and returned results, paired when supplied. |
| Prompt | System prompt and messages, with large blocks collapsed. |
| Raw | Exact captured record, full identifiers, and copy/download controls where already authorized. |

- Keep captured thinking accessible under an explicitly labeled disclosure;
  do not describe it as proof of the agent's full reasoning or intent.
- Preserve original content and whitespace when copying. Display captured text
  safely; do not insert model-generated HTML into the document.
- Do not inject captured SVG or HTML into the Flow/Trace renderer. Preserve
  existing redaction and metadata-only capture limitations.
- A metadata-only record states content unavailable. Empty text with tool calls
  points to Tools instead of claiming that the model returned nothing.
- Large raw data is lazy-rendered on request. Bound initial DOM work; offer a
  deliberate expansion rather than truncating the record without a label.
- Search results must open on mobile even when no session was previously
  selected. Failed search and zero matches are distinct states.

## 9. Reports and Charts

### Reports Layout

- Keep existing 7/30/90-day windows, project scope, and CSV export.
- Preserve the actual server window semantics: a trailing window is not
  relabeled as complete calendar days. Chart and CSV use the same scope and
  timezone parameter. Team daily budgets remain explicitly UTC-day based.
- Use an open summary strip: recorded spend, model calls, failures/refusals,
  and signed net cache savings. Tokens become secondary detail.
- Default model columns: model, recorded spend, share of recorded spend,
  failures, refusals. Expand calls, tokens, cache read/write figures, and
  p50/p95/p99 latency together beneath the selected model.
- Share is derived from the selected report's recorded-spend denominator. If
  the denominator is zero, display unavailable rather than dividing by zero.
- Retain by-team, by-project, and by-agent reports, with money adjacent to the
  identity column. Honor the same units and formatting across sections.
- CSV remains the existing model export; label it accordingly. Do not imply
  it exports a new grouping merely because another table is expanded.

### Chart Rules

- Daily charts use a continuous date axis. Only fill missing dates with zero
  after a successful response establishes the selected reporting window.
  Do not convert missing/failed data into zero.
- Show the dollar scale and label the time basis. Positive-cost bar charts
  start at zero; net-savings charts show an explicit zero line.
- Do not color an entire day's spend red because that day contained an error.
  Use a separate failure marker with its count and accessible description.
- For more than 31 daily buckets, aggregate to labeled calendar-week buckets;
  retain partial first/last buckets and expose their date span. Totals across
  displayed buckets must equal the received daily totals before rounding.
- On phones, use at most 12 visible buckets; aggregate consistently or use an
  explicit chart-only scroll area. Labels must not collide or be silently lost.
- Iteration charts use iteration categories, including unknown/unassigned
  iterations; do not imply equal duration between categories.
- Provide a textual table alternative and keyboard-accessible values. A hover
  tooltip alone is insufficient, particularly on touch.
- Reuse existing chart logic where practical. If new chart infrastructure is
  needed, use a maintained library and keep its assets locally bundled.
- Full historical model-to-call drilldown is deferred until the server can
  provide the complete filtered scope. A filter over the newest 50 sessions is
  not a valid implementation of that feature.

## 10. Compare, What-if, Replay, Settings, and Access

### Compare

- Keep clear A/B names and raw identity access. Preserve signed deltas, all
  currently compared metrics, iteration ribbons, and prompt/config drift.
- Monetary deltas use favorable/unfavorable color where meaningful; counts,
  tokens, duration, and structural differences remain neutral unless the
  metric's interpretation explicitly justifies a judgment.
- At phone widths, display each metric with adjacent A/B values and delta,
  rather than hiding one run off-screen. Prompt diffs can use a unified view.
- Keep folded context and existing safeguards for very large prompt diffs.

### What-if and Replay

- What-if uses a select/input for the target model and an explicit Estimate
  action. Preserve that it is arithmetic over captured tokens.
- Replay is a separate action. Show destination, model, scope, and that cloud
  replay may incur charges before submission. Do not start it on selection.
- Reuse model discovery and remembered destination behavior. Empty/unavailable
  target lists have explicit states; do not silently choose a new destination.
- Keep job progress, skipped/failed counts, original/replay costs, and original
  links. Do not replace existing limited scoring with a generic quality score.
- Use `Answered and same tools` or an equivalently precise score label; task
  success and equivalence are not established by the present grader.
- Preserve generation guards and cleanup when the selected run/session changes.
  Do not lose a visible in-flight job merely because the panel is collapsed.

### Settings and Access

- Settings uses aligned setting/value/source rows with masked secrets. Runtime
  configuration stays read-only. Appearance is a clearly separate browser-local
  preference and does not claim to update proxy configuration.
- Preserve existing maintenance operations, their authorization, result counts,
  and errors; do not silently expose additional admin operations.
- Access opens a labeled dialog with key identity, role, and pairing controls.
  Keep viewer/editor/admin distinctions and ingest-only refusal explanations.
- The pairing QR and link are shown only through the existing admin-authorized
  flow. Preserve transport information and explicit rotation behavior.
- Never include pairing keys in routine screenshots, exported reports,
  diagnostic messages, or copied run links.

## 11. Shared Interaction and State Contract

### Read States

| State | Required UI |
|---|---|
| Initial loading | Geometry-preserving placeholders in the requested view; controls do not jump. |
| Loaded | Data with its scope; last successful refresh available. |
| Refreshing | Keep last good data; understated progress without full-page replacement. |
| Failed, no prior data | Scoped error, concise reason, and Retry. |
| Failed, prior data available | Keep data explicitly marked stale with the last successful refresh. |
| Authorized empty | Contextual empty/filter/capture state. |
| Unauthorized | Access-required state; stale protected content is not presented as current authorized data. |
| Forbidden | Role-specific unavailable action/view; do not suggest the key is necessarily invalid. |
| Not found | Missing/deleted entity state with navigation back. |

Differentiate successful data refresh time, last recorded call time, and socket
connection. An open socket does not erase a failed API refresh. Silence alone
does not make a successfully refreshed view stale. Keep the existing idle
heartbeat so relative ages and inactivity-derived states continue to update.

### Mutations

- States are idle, submitting, confirmed, and failed/unknown outcome.
- Show pending feedback and prevent duplicate submission. Confirm financial
  controls using the authoritative response plus refresh; do not optimistically
  claim a block or ceiling is active.
- Update all visible copies after success. Keep user input on failure.
- If a request times out after possible server application, re-fetch state
  before offering a blind retry of the mutation.
- Toasts can confirm copy/export/rename, but a toast must not be the only place
  an unresolved financial-control error is visible.
- Destructive deletion names the entity and scope. Preserve the distinction
  between removing a project label and purging its records.
- Focus returns to the initiating control after a dialog closes; when deletion
  removes that control, focus the next sensible list item or list heading.

### Permissions

Resolve `whoami` once in shared app state and revalidate after credential
changes. The server remains authoritative for every request.

| Capability | Viewer | Editor | Admin |
|---|---|---|---|
| Read dashboard, existing export, What-if | Yes | Yes | Yes |
| Names, projects, pins, ceilings, operator blocks | No | Yes | Yes |
| Session deletion and replay | No | Yes | Yes |
| Admin-only maintenance, credentials, pairing | No | No | Yes |

Verify the route's actual role requirement before wiring each control; this
table does not grant permissions. Ingest/team cards are not dashboard roles.
Hide unavailable mutation controls in ordinary navigation; when an action is
contextually relevant, use a disabled state with an accessible role explanation.
Do not derive permissions from the mere presence of a stored key.

### Live Updates

- Keep the existing debounce and reconnection backoff; avoid a socket per row.
- Cancel or ignore stale requests when selection changes. Scope caches by
  entity, filters, and authenticated instance.
- Preserve active inputs, scroll position, expanded evidence, and keyboard
  focus on live refresh. Never reorder a row out from under a pointer while
  its action menu is open.
- A live feed may follow the newest entry only while the user is already at
  its live edge. Otherwise display a new-arrival count with a jump action.
- Animate only brief background/opacity changes; no flashing numbers, glows,
  or count animations. Respect reduced-motion preferences.

## 12. Responsive and Accessibility Requirements

### Breakpoints

| Width | Layout |
|---|---|
| At least 1200px | 304px list sidebar, full detail pane, 24px content padding. |
| 960-1199px | 280px list sidebar, full detail pane, 20px content padding. |
| Below 960px | One pane at a time, explicit Back navigation, 16px phone padding. |
| Below 400px | Reflow secondary metadata and summary columns; retain all primary actions. |

- At narrow widths, stack the primary spend beside a vertical pair of ceiling
  and calls, as shown in the reference. Do not stack six metric cards.
- Sticky elements must not consume more than necessary: one app header and,
  where useful, one compact entity/action header. Use opaque surfaces so text
  never overlaps scrolling content incoherently.
- Use `100dvh` with an appropriate fallback and safe-area padding. The virtual
  keyboard must not cover a ceiling input's Save/Cancel controls.
- Tables prioritize identity, state, and money on phones. Put secondary
  columns in row detail; contained scrolling remains available for Raw,
  technical comparisons, and graphs where two-dimensional layout matters.
- A long ID cannot stretch the page. Titles reflow; exact values remain
  available and copyable. No root-level horizontal scrolling at 320px.
- Avoid fixed text container heights. Test 200% zoom and the equivalent narrow
  reflow condition; menus and dialogs stay inside the visible viewport.

### Accessibility

Target WCAG 2.2 AA for the implemented surfaces. Normal text requires at least
4.5:1 contrast, with the standard large-text exception at 3:1; see
[W3C contrast guidance](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).

- Validate essential non-text control and chart distinctions at 3:1. Decorative
  separators do not carry essential interaction meaning on their own.
- All controls work by keyboard and have visible focus. Provide a skip link to
  the main content and meaningful landmarks/headings.
- Implement tab semantics and keyboard behavior following the
  [W3C tabs pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/): arrows,
  Home/End, selected state, associated panels, and correct focus order.
- Only auto-activate a keyboard-focused tab if its content is immediately
  available; otherwise require Enter/Space to activate it.
- Use native links, buttons, form labels, and table headers. Dialogs have an
  accessible name, Escape handling, appropriate focus containment, and focus
  restoration. Menus support expected keyboard navigation.
- Color never carries status alone. Each status has readable text and/or a
  named icon. Honor forced-colors mode.
- The project's control-size standard is 36px desktop and 44px coarse pointer,
  intentionally larger than the AA minimum described in
  [W3C target-size guidance](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html).
- Announce mutation failures and confirmations appropriately. Do not announce
  every live token/cost update to a screen reader; offer a quiet summary.
- Tooltips appear on focus as well as hover and can be dismissed. Important
  explanations, full IDs, and uncertainty are reachable without a tooltip.

## 13. Data Contracts and Backend Dependencies

### Existing APIs Used by the Core

| Surface | Existing Contract | Constraint |
|---|---|---|
| Chrome and roles | `/health`, `/api/whoami`, `/ws` | Health, authorization, and live connection are different facts. |
| Lists | `/api/runs`, `/api/sessions`, `/api/projects` | Run/session responses are recent capped lists, currently 50 by default. |
| Run detail | `/api/runs/{id}` | Status is not a complete enforcement verdict. |
| Iteration detail | `/api/runs/{id}/iterations` | One aggregated iteration may contain multiple sessions. |
| Flags | `/api/runs/{id}/flags` | Use supplied call/session identifiers for evidence navigation. |
| Cache | `/api/runs/{id}/cache-audit` | Preserve verdict, reason, optional fix, reported discount, and estimate method. |
| Session calls | `/session/{id}`, `/api/calls/{id}` | Content can be absent; costs and tokens can be null. |
| Tool pairing | `/api/sessions/{id}/tools` | Captured/derived results do not prove external effects beyond the record. |
| Labels and ceiling | `PUT /api/labels/{scope}/{id}` | Run-only ceiling; zero clears it. |
| Operator block | `POST` / `DELETE /api/runs/{id}/stop` | Refuses future requests; no process lifecycle control. |
| Reporting | `/api/reports`, `/api/reports.csv` | Preserve trailing window, project filter, and timezone. |
| Replay / What-if | Existing `api.ts` helpers | Preserve authorization, destination, billing, and limited scoring semantics. |

### Numeric and Time Rules

- Aggregate only raw numerical values; round at display boundaries.
- Preserve the existing `fmtUsd` compatibility until a shared replacement is
  covered by focused tests. A proposed common display rule is two decimals for
  magnitudes at least one dollar, four below one dollar, and `<$0.0001` for a
  known positive smaller amount. Exact stored precision remains inspectable.
- Known zero is `$0.00`; null/missing cost is `Unknown`. A zero aggregate is not
  proof that every contributing call was free or fully priced.
- Label current aggregates `Recorded spend` or `Recorded cost`. Do not display
  pricing coverage or unknown-call counts that the response cannot establish.
- Signed net cache savings may be negative. Use `Cache cost extra` for a net
  premium; never clamp the number to zero.
- Use the API's canonical token convention and shared formatting; do not
  locally add cache tokens into an input total without checking that field's
  provider-normalized meaning.
- Show locale-formatted timestamps with their timezone available. Preserve
  server timestamp and day-window semantics when exporting or comparing.

### Optional Server Extensions: Separate Tickets

| Extension | Minimum Required Contract Before UI Ships |
|---|---|
| Complete run Calls tab | Authorized, paginated run-call endpoint; stable ordering; complete run scope; filter support; cursor/has-more metadata. |
| Full history browsing | Run/session endpoints with server-side filters, stable cursor pagination, and explicit count/scope semantics. |
| Global attention overview | Server aggregates for the selected scope and definitions of active/flagged/resolved; no inference from capped lists. |
| Pricing coverage | Known subtotal, unknown-cost count, total included calls, and pricing basis/version where supported. |
| Enforcement status | Explicit observed time, operator block, applicable ceilings, policy outcomes, reserved amounts if implemented, and failure-policy meaning. |
| Report-to-call drilldown | Calls selected with the exact report window, timezone, model/provider identity, and project-resolution rules. |

Do not invent endpoints in frontend code, fan out through one session ID per
iteration to pretend coverage is complete, or present illustrative figures as
runtime data. These additions require their own API/store tests and design.

## 14. Implementation Structure

| File / Area | Responsibility |
|---|---|
| `src/styles.css` | Tokens, themes, typography, shared spacing, control states, responsive layout. |
| `src/App.tsx` | Chrome, URL state, shared principal/connection state, About and appearance entry points. |
| `src/api.ts` | Existing request helpers, typed read/error state support, formatting and URL construction; no invented backend capability. |
| `src/views/LabelBits.tsx` | Preserve label/project/pin behavior; consistent menus, field validation, and action feedback. |
| `src/views/RunsView.tsx` | Header, metric strip, overview/activity/cache layout, ceiling and block controls, scoped evidence. |
| `src/views/SessionsView.tsx` | Flat rows, inspector, search/mobile navigation, Calls/Flow/Trace placement. |
| `src/views/ReportsView.tsx` | Money-first columns, expandable detail, accessible chart/time axes, export states. |
| `src/views/CompareView.tsx` | A/B layout, responsive metric comparison, existing diff safeguards. |
| `src/views/BatchReplay.tsx`, `WhatIf.tsx` | Secondary placement, preserved progress/results, precise captions. |
| `src/views/SettingsView.tsx` | Runtime read-only settings, local appearance preferences, maintenance feedback. |
| `src/views/FlowView.tsx`, `TraceView.tsx` | Apply tokens and typography while preserving graph/waterfall behavior. |
| `e2e/` | Existing smoke coverage plus focused workflow and visual acceptance tests. |

Extract shared controls only when reused: icon buttons, status labels, tabs,
metric strips, notices, and dialog/menu shells are likely candidates. A generic
dashboard configuration framework or new global state architecture is not
required. Preserve existing request-generation guards and event helpers.

The working tree may contain ongoing fixes in the same views or styles. Review
those changes before editing and retain their behavior. This specification is
not authorization to revert concurrent user work.

## 15. Delivery Sequence

| Stage | Deliverable | Exit Condition |
|---|---|---|
| 1. Foundations | Theme tokens, type scale, icons, shared control states, chrome/footer. | Existing views render; identity/roles preserved; dark/light contrast verified. |
| 2. Run experience | List rows, header, metric strip, overview/activity/cache, ceiling/block feedback. | Desktop and phone primary workflow passes, including failed mutations. |
| 3. Session experience | Call rows, inspector, mobile search, secondary replay placement. | Calls/Flow/Trace, original links, and large content remain usable. |
| 4. Reports and secondary surfaces | Charts, money-first tables, compare, replay, settings, pairing. | Scopes/totals unchanged; exports and existing workflows pass. |
| 5. Navigation and verification | URL state, Back/Forward, role review, responsive/a11y verification. | Acceptance matrix complete; no unsupported product claims introduced. |

Stages may be reviewed in small PRs. Do not publish half-migrated financial
controls or leave two contradictory status presentations visible together.
Release tagging/deployment remains a separate action after review.

## 16. Acceptance Criteria

### Visual and Responsive

| ID | Pass Condition |
|---|---|
| V01 | At 1440x960, title, observed state, recorded spend, ceiling, and the priority recorded concern fit in the first viewport of a representative run. |
| V02 | At 390x844, those same primary facts appear without a stack of six metric cards or a persistent promotional footer. |
| V03 | At 320, 390, 768, 1024, 1440, and 1920px widths, document width does not exceed viewport width; intended technical scrollers stay contained. |
| V04 | 120-character labels, 60-character project names, long provider IDs, and large currency figures do not overlap controls. |
| V05 | Dark and light use the same geometry; no unreadable retained inline color or invisible provider mark remains. |
| V06 | Hover/selected/loading states do not change row height or displace adjacent controls. |
| V07 | Charts show monetary/time scope, preserve data gaps honestly, and expose values without hover. |
| V08 | On mobile, money and status remain visible in report rows without horizontal scrolling to the final column. |

### Behavior and Data Integrity

| ID | Pass Condition |
|---|---|
| F01 | Existing named/pinned/projected runs and sessions remain findable within the clearly labeled recent scope. |
| F02 | A stale request for run/session A cannot render its contents after selecting B. |
| F03 | Ceiling save success updates all visible copies; HTTP failure preserves input and shows an inline error; duplicate submissions are prevented. |
| F04 | Block/allow confirmation states the future-request effect; no process-stop/restart claim appears. |
| F05 | Viewer controls cannot initiate editor/admin operations; a server 403 is handled correctly if roles change while open. |
| F06 | Socket disconnect and API-refresh failure produce distinguishable states; reconnect does not reset an open editor or jump the list. |
| F07 | Call-level unknown cost, known zero, and missing token data remain distinct. Aggregates use `Recorded spend` and do not imply complete pricing coverage when the API cannot establish it. |
| F08 | Each cache verdict, null estimate, estimate method, and negative report net savings has a distinct correct presentation. |
| F09 | Mobile search from an unselected session list opens results; no matches and failed requests differ. |
| F10 | Deep links, copy links, reload, Back, and Forward retain intended scope and never append credentials to ordinary links. |
| F11 | Replay remains explicit; destination/model selection alone sends no replay request. Progress, skipped/failed steps, and original navigation remain available. |
| F12 | Compare preserves A/B identity, metric deltas, folded prompt differences, and large-diff fallback. |
| F13 | Report chart aggregates and CSV use the same selected window, project, and timezone; rendering changes do not alter reported totals. |
| F14 | Metadata-only/redacted records show their limits; raw content cannot execute HTML or scripts. |
| F15 | Pairing remains admin-authorized and separate from ordinary navigation links; named-instance identity survives phone layout. |

### Accessibility and Verification Procedure

- Keyboard-test all primary navigation, tab sets, row links, menus, ceiling
  editing, confirmation dialogs, and inspector controls. Check focus restoration.
- Check text/control contrast in both themes, visible focus, reduced motion,
  forced colors, 200% text enlargement, and narrow reflow.
- Use deterministic synthetic fixtures for screenshots and CI. Include long
  names, every run status, mixed providers, partial cache, unknown costs,
  blocked/transient/probe/error calls, metadata-only captures, and replay jobs.
- Include a large-history fixture to exercise retained rendering limits. Do not
  accidentally claim full browsing coverage from the capped list API.
- Extend existing Playwright tests for meaningful behavior, not every styling
  implementation detail. Add screenshot checkpoints at desktop, tablet, and
  phone sizes and inspect them for overlap and information priority.
- Run `npm run build` and the dashboard smoke suite against a seeded temporary
  ledger with mocked upstream traffic. Never replay paid models, mutate a user's
  live ledger, or rotate real credentials as part of UI verification.
- If a backend extension is separately implemented, run its focused tests on
  SQLite and Postgres as appropriate. The visual-only phase does not imply
  new budget, pricing, or concurrency guarantees.
- Record browser console errors, page-level overflow, interaction results, and
  screenshots. Passing these checks supports the tested surfaces only; do not
  claim an accessibility or production-reliability certification from them.

## 17. Definition of Done

The core redesign is complete when all required views use the shared visual
foundation, existing workflows are preserved, applicable V/F criteria pass,
accessibility checks are recorded, and no mockup-only statistic or unsupported
enforcement claim remains. The final review must include a phone run-control
workflow, a failed mutation, a stale-data state, a real comparison, and a report
whose displayed totals match its source response.

The specification and reference images are design artifacts. They do not mean
that the application changes, proposed APIs, or production checks have shipped.
