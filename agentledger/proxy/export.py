"""
Compliance export — generates an integrity-tagged audit trail for a session.

GET /export/{session_id}             → JSON (machine-readable, with an integrity tag)
GET /export/{session_id}/report      → Printable HTML report

The JSON export carries an integrity tag over the calls array:

  * If AGENTLEDGER_EXPORT_HMAC_KEY is set, a keyed HMAC-SHA256 ("hmac-sha256:…").
    This is tamper-EVIDENT — a recipient holding the key can detect any change, and
    an attacker who edits the calls cannot forge a matching tag without the key.
  * Otherwise a plain SHA-256 checksum ("sha256:…"). A checksum catches accidental
    corruption but is NOT a signature — anyone who edits the calls can recompute it.
"""

import datetime
import hashlib
import hmac
import html
import json
import os
from typing import Any


def _integrity_tag(payload: str) -> str:
    """Return a keyed HMAC-SHA256 tag if a key is configured, else a SHA-256 checksum."""
    data = payload.encode()
    key = os.environ.get("AGENTLEDGER_EXPORT_HMAC_KEY", "").encode()
    if key:
        return "hmac-sha256:" + hmac.new(key, data, hashlib.sha256).hexdigest()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def build_export(session_id: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a structured compliance export for a session."""
    calls_json = json.dumps(calls, sort_keys=True, default=str)
    integrity = _integrity_tag(calls_json)

    total_cost = sum(c.get("cost_usd") or 0 for c in calls)
    total_tokens_in = sum(c.get("tokens_in") or 0 for c in calls)
    total_tokens_out = sum(c.get("tokens_out") or 0 for c in calls)
    total_latency_ms = sum(c.get("latency_ms") or 0 for c in calls)
    models = sorted({c["model_id"] for c in calls if c.get("model_id")})
    agents = sorted({c["agent_name"] for c in calls if c.get("agent_name")})
    errors = [c for c in calls if (c.get("status_code") or 200) != 200]
    warnings = [c for c in calls if (c.get("error_detail") or "").startswith("budget_warning:")]

    return {
        "export": {
            "generated_at": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
            "generator":    "Agentic Ledger",
            "integrity":    integrity,
        },
        "session": {
            "session_id":      session_id,
            "started_at":      calls[0]["timestamp"] if calls else None,
            "ended_at":        calls[-1]["timestamp"] if calls else None,
            "call_count":      len(calls),
            "error_count":     len(errors),
            "warning_count":   len(warnings),
            "models":          models,
            "agents":          agents,
            "total_tokens_in": total_tokens_in,
            "total_tokens_out": total_tokens_out,
            "total_latency_ms": round(total_latency_ms),
            "total_cost_usd":  round(total_cost, 8) if total_cost else None,
        },
        "calls": calls,
    }


def _pretty(value: str) -> str:
    """Pretty-print if value is JSON, otherwise return as-is."""
    try:
        return json.dumps(json.loads(value), indent=2, ensure_ascii=False)
    except Exception:
        return value


def render_html_report(export: dict[str, Any]) -> str:
    """Render the compliance export as a printable HTML page."""
    session = export["session"]
    meta = export["export"]
    calls = export["calls"]

    def esc(v: Any) -> str:
        return html.escape(str(v)) if v is not None else "—"

    def fmt_cost(v: Any) -> str:
        if v is None:
            return "—"
        return f"${float(v):.6f}"

    def fmt_ms(v: Any) -> str:
        if v is None:
            return "—"
        ms = float(v)
        return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms:.0f}ms"

    def render_call(call: dict, n: int) -> str:
        is_error = (call.get("status_code") or 200) != 200
        is_warning = not is_error and (call.get("error_detail") or "").startswith("budget_warning:")
        status_style = "color:#ef4444" if is_error else ("color:#f59e0b" if is_warning else "color:#22c55e")
        status_text = f"HTTP {call.get('status_code', 200)}"
        if is_error and call.get("error_detail"):
            status_text += f" — {call['error_detail'][:120]}"

        msgs = call.get("messages") or []
        last_user = next(
            (m.get("content", "") for m in reversed(msgs) if m.get("role") == "user"),
            None,
        )
        if isinstance(last_user, list):
            last_user = next((b.get("text") for b in last_user if b.get("type") == "text"), None)

        tool_calls_html = ""
        if call.get("tool_calls"):
            items = "".join(
                f"<li><code>{esc(tc.get('name','?'))}</code> — "
                f"<span style='color:#888;font-size:11px'>{esc(str(tc.get('arguments',''))[:200])}</span></li>"
                for tc in call["tool_calls"]
            )
            tool_calls_html = f"<p class='label'>Tool calls</p><ul>{items}</ul>"

        tool_results_html = ""
        if call.get("tool_results"):
            items = "".join(
                f"<li><code>{esc(tr.get('tool_call_id') or tr.get('tool_use_id','?'))}</code> — "
                f"<span style='color:#888;font-size:11px'>{esc(str(tr.get('content',''))[:200])}</span></li>"
                for tr in call["tool_results"]
            )
            tool_results_html = f"<p class='label'>Tool results</p><ul>{items}</ul>"

        handoff_html = ""
        hf, ht = call.get("handoff_from"), call.get("handoff_to")
        if hf or ht:
            parts = []
            if hf:
                parts.append(esc(hf))
            if hf and ht:
                parts.append("→")
            if ht:
                parts.append(esc(ht))
            handoff_html = f"<p class='label'>Handoff</p><p>{' '.join(parts)}</p>"

        warning_badge = '<span style="background:#78350f;color:#fbbf24;font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px;margin-left:8px">⚠ budget</span>' if is_warning else ""
        warning_msg = (call.get("error_detail") or "").removeprefix("budget_warning:").strip()
        warning_section = f'<p class="label" style="color:#f59e0b">Budget Warning</p><pre style="border-left:3px solid #f59e0b">{esc(warning_msg)}</pre>' if is_warning else ""

        card_class = "call-error" if is_error else ("call-warning" if is_warning else "")
        return f"""
        <div class="call {card_class}">
          <div class="call-header">
            <span class="call-n">#{n}</span>
            <span class="call-model">{esc(call.get('model_id',''))}</span>
            {warning_badge}
            <span style="{status_style};font-size:12px;margin-left:auto">{esc(status_text)}</span>
          </div>
          <table class="meta">
            <tr><td>Action ID</td><td><code>{esc(call.get('action_id',''))}</code></td>
                <td>Timestamp</td><td>{esc(call.get('timestamp',''))}</td></tr>
            <tr><td>Agent</td><td>{esc(call.get('agent_name',''))}</td>
                <td>User</td><td>{esc(call.get('user_id',''))}</td></tr>
            <tr><td>Environment</td><td>{esc(call.get('environment',''))}</td>
                <td>Stop reason</td><td>{esc(call.get('stop_reason',''))}</td></tr>
            <tr><td>Tokens in / out</td><td>{esc(call.get('tokens_in',''))} / {esc(call.get('tokens_out',''))}</td>
                <td>Cost</td><td>{fmt_cost(call.get('cost_usd'))}</td></tr>
            <tr><td>Latency</td><td>{fmt_ms(call.get('latency_ms'))}</td>
                <td>Temperature</td><td>{esc(call.get('temperature',''))}</td></tr>
          </table>
          {f'<p class="label">System prompt</p><pre>{esc(call.get("system_prompt",""))}</pre>' if call.get("system_prompt") else ""}
          {f'<p class="label">Input (last user message)</p><pre>{esc(last_user)}</pre>' if last_user else ""}
          {tool_results_html}
          {tool_calls_html}
          {f'<p class="label">Output</p><pre>{esc(_pretty(call.get("content","")) )}</pre>' if call.get("content") else ""}
          {handoff_html}
          {warning_section}
          {f'<p class="label" style="color:#ef4444">Error</p><pre>{esc(call.get("error_detail",""))}</pre>' if is_error and call.get("error_detail") else ""}
        </div>
        """

    calls_html = "".join(render_call(c, i + 1) for i, c in enumerate(calls))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Agentic Ledger Export — {esc(session['session_id'])}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 13px;
          color: #111; background: #fff; padding: 40px; max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 20px; margin-bottom: 4px; }}
  h2 {{ font-size: 14px; font-weight: 600; margin: 24px 0 8px; color: #333; border-bottom: 1px solid #e5e5e5; padding-bottom: 4px; }}
  .meta-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px 24px; margin-bottom: 16px; }}
  .meta-grid div {{ font-size: 12px; color: #555; }}
  .meta-grid strong {{ color: #111; }}
  .integrity {{ font-family: monospace; font-size: 11px; color: #666; background: #f5f5f5;
                padding: 8px 12px; border-radius: 4px; margin-bottom: 24px; word-break: break-all; }}
  .call {{ border: 1px solid #e5e5e5; border-radius: 6px; margin-bottom: 16px; overflow: hidden; page-break-inside: avoid; }}
  .call-error {{ border-color: #fca5a5; }}
  .call-warning {{ border-color: #f59e0b; }}
  .call-header {{ background: #f9f9f9; padding: 8px 12px; display: flex; align-items: center; gap: 10px; border-bottom: 1px solid #e5e5e5; }}
  .call-error .call-header {{ background: #fff5f5; }}
  .call-warning .call-header {{ background: #fffbeb; }}
  .call-n {{ font-size: 11px; color: #999; font-weight: 600; width: 24px; }}
  .call-model {{ font-family: monospace; font-size: 12px; font-weight: 600; color: #1d4ed8; }}
  table.meta {{ width: 100%; border-collapse: collapse; font-size: 11px; margin: 10px 12px; width: calc(100% - 24px); }}
  table.meta td {{ padding: 2px 8px 2px 0; color: #555; vertical-align: top; }}
  table.meta td:nth-child(odd) {{ color: #999; width: 120px; }}
  .label {{ font-size: 10px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
             color: #999; margin: 10px 12px 4px; }}
  pre {{ margin: 0 12px 10px; background: #f9f9f9; padding: 8px 10px; border-radius: 4px;
         font-size: 11px; white-space: pre-wrap; word-break: break-word; color: #333; }}
  @media screen {{ pre {{ max-height: 300px; overflow: auto; }} }}
  ul {{ margin: 0 12px 10px 24px; }}
  ul li {{ font-size: 12px; margin-bottom: 2px; color: #333; }}
  code {{ font-family: monospace; font-size: 11px; background: #f0f0f0; padding: 1px 4px; border-radius: 3px; }}
  .footer {{ margin-top: 40px; font-size: 11px; color: #999; border-top: 1px solid #e5e5e5; padding-top: 16px; }}
  @media print {{
    body {{ padding: 20px; }}
    .call {{ page-break-inside: avoid; }}
  }}
</style>
</head>
<body>

<h1>Agentic Ledger — Session Audit Report</h1>
<p style="color:#666;font-size:12px;margin:4px 0 20px">Generated {esc(meta['generated_at'])}</p>

<h2>Session Summary</h2>
<div class="meta-grid">
  <div><strong>Session ID:</strong> {esc(session['session_id'])}</div>
  <div><strong>Call count:</strong> {esc(session['call_count'])} ({esc(session['error_count'])} errors{f", {esc(session['warning_count'])} budget warnings" if session.get('warning_count') else ""})</div>
  <div><strong>Started:</strong> {esc(session['started_at'])}</div>
  <div><strong>Ended:</strong> {esc(session['ended_at'])}</div>
  <div><strong>Models:</strong> {esc(', '.join(session['models']))}</div>
  <div><strong>Agents:</strong> {esc(', '.join(session['agents']) if session['agents'] else '—')}</div>
  <div><strong>Tokens in / out:</strong> {esc(session['total_tokens_in'])} / {esc(session['total_tokens_out'])}</div>
  <div><strong>Total cost:</strong> {fmt_cost(session['total_cost_usd'])}</div>
  <div><strong>Total latency:</strong> {fmt_ms(session['total_latency_ms'])}</div>
</div>

<div class="integrity">Integrity: {esc(meta['integrity'])}</div>

<h2>Calls ({esc(session['call_count'])})</h2>
{calls_html}

<div class="footer">
  Generated by Agentic Ledger &mdash; {esc(meta['generated_at'])}<br>
  Integrity: <code>{esc(meta['integrity'])}</code> &mdash; computed over the canonical
  <code>calls</code> JSON (keys sorted). A <code>hmac-sha256</code> tag is tamper-evident;
  a <code>sha256</code> tag is a corruption checksum only.
</div>

</body>
</html>"""
