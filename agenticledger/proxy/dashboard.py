"""
Serves the Agentic Ledger visual dashboard at GET /.
Single-file HTML/CSS/JS — no build step, no external dependencies.
"""

import base64
import pathlib

_MASCOT_B64 = ""
try:
    _img = pathlib.Path(__file__).parent / "mascot.jpg"
    _MASCOT_B64 = base64.b64encode(_img.read_bytes()).decode()
except Exception:
    pass

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Agentic Ledger</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%231a1a2e'/%3E%3Ctext x='16' y='22' font-family='monospace' font-size='13' font-weight='bold' fill='%237c3aed' text-anchor='middle'%3EAL%3C/text%3E%3C/svg%3E">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0a0a0a;
    color: #e0e0e0;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  header {
    padding: 12px 20px;
    border-bottom: 1px solid #1e1e1e;
    display: flex;
    align-items: center;
    gap: 10px;
    flex-shrink: 0;
  }
  header h1 { font-size: 15px; font-weight: 600; color: #fff; letter-spacing: -0.3px; }
  .live-dot { width: 8px; height: 8px; border-radius: 50%; background: #555; transition: background 0.3s; flex-shrink: 0; }
  .live-dot.connected { background: #22c55e; }

  .search-wrap {
    margin-left: auto;
    position: relative;
  }
  .search-input {
    background: #141414;
    border: 1px solid #2a2a2a;
    color: #e0e0e0;
    font-size: 12px;
    padding: 5px 10px 5px 28px;
    border-radius: 6px;
    width: 220px;
    outline: none;
    transition: border-color 0.15s;
  }
  .search-input:focus { border-color: #444; }
  .search-input::placeholder { color: #444; }
  .search-icon {
    position: absolute;
    left: 8px;
    top: 50%;
    transform: translateY(-50%);
    color: #444;
    font-size: 12px;
    pointer-events: none;
  }

  .layout {
    display: flex;
    flex: 1;
    overflow: hidden;
  }

  /* Sessions panel */
  .sessions-panel {
    width: 280px;
    border-right: 1px solid #1e1e1e;
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    overflow: hidden;
  }
  .panel-header {
    padding: 12px 16px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #666;
    border-bottom: 1px solid #1a1a1a;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-shrink: 0;
  }
  .refresh-btn {
    background: none;
    border: 1px solid #333;
    color: #888;
    font-size: 11px;
    padding: 3px 8px;
    border-radius: 4px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .refresh-btn:hover { border-color: #555; color: #ccc; }

  .sessions-list {
    flex: 1;
    overflow-y: auto;
    padding: 8px;
  }
  .sessions-list::-webkit-scrollbar { width: 4px; }
  .sessions-list::-webkit-scrollbar-track { background: transparent; }
  .sessions-list::-webkit-scrollbar-thumb { background: #333; border-radius: 2px; }

  .session-item {
    padding: 10px 12px;
    border-radius: 6px;
    cursor: pointer;
    margin-bottom: 2px;
    transition: background 0.1s;
    border: 1px solid transparent;
  }
  .session-item:hover { background: #141414; }
  .session-item.active { background: #141414; border-color: #2a2a2a; }

  .session-id {
    font-size: 13px;
    font-weight: 500;
    color: #c8b5f5;
    font-family: "SF Mono", "Fira Code", monospace;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .session-meta {
    font-size: 11px;
    color: #555;
    margin-top: 3px;
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .session-meta span { display: flex; align-items: center; gap: 3px; }

  .empty-state {
    padding: 32px 16px;
    text-align: center;
    color: #444;
    font-size: 13px;
    line-height: 1.6;
  }

  /* Search results */
  .search-result-item {
    padding: 10px 12px;
    border-radius: 6px;
    cursor: pointer;
    margin-bottom: 2px;
    border: 1px solid transparent;
    transition: background 0.1s;
  }
  .search-result-item:hover { background: #141414; }
  .search-result-model {
    font-size: 12px;
    font-weight: 500;
    color: #60a5fa;
    font-family: "SF Mono", "Fira Code", monospace;
  }
  .search-result-snippet {
    font-size: 11px;
    color: #555;
    margin-top: 3px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* Detail panel */
  .detail-panel {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .detail-header {
    padding: 12px 20px;
    border-bottom: 1px solid #1a1a1a;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    gap: 16px;
    min-height: 45px;
  }
  .detail-session-id {
    font-family: "SF Mono", "Fira Code", monospace;
    font-size: 13px;
    color: #c8b5f5;
    font-weight: 500;
  }
  .detail-stats {
    display: flex;
    gap: 16px;
    font-size: 12px;
    color: #555;
  }
  .detail-stats strong { color: #999; }

  .detail-tabs {
    display: flex;
    gap: 2px;
    margin-left: 16px;
  }
  .tab-btn {
    font-size: 11px;
    font-weight: 600;
    color: #555;
    background: none;
    border: none;
    padding: 4px 10px;
    border-radius: 4px;
    cursor: pointer;
    letter-spacing: 0.04em;
    transition: all 0.15s;
  }
  .tab-btn:hover { color: #999; background: #1a1a1a; }
  .tab-btn.active { color: #e0e0e0; background: #1e1e1e; }

  .export-btn {
    margin-left: auto;
    display: flex;
    gap: 6px;
  }
  .export-link {
    font-size: 11px;
    color: #666;
    text-decoration: none;
    border: 1px solid #2a2a2a;
    padding: 3px 8px;
    border-radius: 4px;
    transition: all 0.15s;
  }
  .export-link:hover { border-color: #444; color: #ccc; }

  /* Flow DAG */
  .flow-view, .trace-view {
    flex: 1;
    overflow: auto;
    display: flex;
    align-items: flex-start;
    justify-content: flex-start;
    padding: 32px 20px;
  }
  .flow-view svg, .trace-view svg { overflow: visible; display: block; }
  .flow-empty {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #333;
    font-size: 13px;
    text-align: center;
    line-height: 1.8;
  }

  .detail-body {
    flex: 1;
    overflow-y: auto;
    padding: 16px 20px;
  }
  .detail-body::-webkit-scrollbar { width: 4px; }
  .detail-body::-webkit-scrollbar-track { background: transparent; }
  .detail-body::-webkit-scrollbar-thumb { background: #333; border-radius: 2px; }

  .placeholder {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #333;
    font-size: 13px;
  }

  /* Call cards */
  .call-card {
    background: #111;
    border: 1px solid #1e1e1e;
    border-radius: 8px;
    margin-bottom: 12px;
    overflow: hidden;
  }
  .call-card.call-error   { border-color: #3a1a1a; }
  .call-card.call-warning { border-color: #3a2a00; }
  .call-card-header {
    padding: 10px 14px;
    display: flex;
    align-items: center;
    gap: 10px;
    border-bottom: 1px solid #1a1a1a;
    background: #0d0d0d;
    cursor: pointer;
    user-select: none;
  }
  .call-card-header:hover { background: #131313; }
  .call-card.call-error   .call-card-header { background: #130808; }
  .call-card.call-warning .call-card-header { background: #120900; }
  .call-toggle {
    font-size: 10px;
    color: #444;
    margin-left: auto;
    transition: transform 0.15s;
  }
  .call-card.collapsed .call-toggle { transform: rotate(-90deg); }
  .call-card.collapsed .call-card-body { display: none; }
  .call-card.collapsed .call-card-header { border-bottom: none; }
  .call-number {
    font-size: 11px;
    color: #444;
    font-weight: 600;
    width: 20px;
  }
  .call-model {
    font-size: 12px;
    font-weight: 600;
    color: #60a5fa;
    font-family: "SF Mono", "Fira Code", monospace;
  }
  .call-badges {
    display: flex;
    gap: 6px;
    margin-left: auto;
    align-items: center;
    flex-wrap: wrap;
  }
  .badge {
    font-size: 11px;
    padding: 2px 7px;
    border-radius: 4px;
    font-weight: 500;
  }
  .badge-latency { background: #1a2a1a; color: #4ade80; }
  .badge-tokens  { background: #1a1a2a; color: #818cf8; }
  .badge-stop    { background: #2a1a1a; color: #f87171; }
  .badge-stop.end_turn, .badge-stop.stop { background: #1a2a1a; color: #4ade80; }
  .badge-stop.tool_calls, .badge-stop.tool_use { background: #2a2a1a; color: #fbbf24; }
  .badge-cost    { background: #1a2510; color: #86efac; }
  .badge-error   { background: #3a1010; color: #f87171; }

  .call-card-body { padding: 12px 14px; }

  .call-meta-row {
    display: flex;
    gap: 16px;
    margin-bottom: 10px;
    flex-wrap: wrap;
  }
  .meta-item { font-size: 11px; }
  .meta-label { color: #444; margin-right: 4px; }
  .meta-value { color: #888; font-family: "SF Mono", "Fira Code", monospace; }

  .section-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #444;
    margin-bottom: 6px;
    margin-top: 12px;
  }
  .section-label:first-of-type { margin-top: 0; }
  .section-label.error-label { color: #7f1d1d; }

  .message-bubble {
    background: #0d0d0d;
    border: 1px solid #1e1e1e;
    border-radius: 6px;
    padding: 8px 10px;
    font-size: 12px;
    color: #ccc;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 160px;
    overflow-y: auto;
    margin-bottom: 4px;
    font-family: inherit;
  }
  .message-bubble.system-prompt { color: #888; font-style: italic; border-color: #252525; }
  .message-bubble.output { color: #e0e0e0; border-color: #2a2a2a; }
  .message-bubble.error-bubble { color: #f87171; border-color: #3a1a1a; background: #130808; }

  .tool-call {
    background: #0d0d0d;
    border: 1px solid #2a2510;
    border-radius: 6px;
    padding: 8px 10px;
    margin-bottom: 4px;
    font-size: 12px;
  }
  .tool-name {
    font-family: "SF Mono", "Fira Code", monospace;
    color: #fbbf24;
    font-weight: 600;
    margin-bottom: 4px;
  }
  .tool-args {
    color: #666;
    font-family: "SF Mono", "Fira Code", monospace;
    font-size: 11px;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .tool-result {
    background: #0d1a0d;
    border: 1px solid #1a3a1a;
    border-radius: 6px;
    padding: 8px 10px;
    margin-bottom: 4px;
    font-size: 12px;
  }
  .tool-result-id {
    font-family: "SF Mono", "Fira Code", monospace;
    color: #4ade80;
    font-size: 10px;
    margin-bottom: 4px;
  }
  .tool-result-content {
    color: #666;
    font-family: "SF Mono", "Fira Code", monospace;
    font-size: 11px;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .parent-link {
    font-size: 11px;
    color: #555;
    font-family: "SF Mono", "Fira Code", monospace;
    margin-top: 4px;
  }
  .parent-link a { color: #c8b5f5; text-decoration: none; cursor: pointer; }
  .parent-link a:hover { text-decoration: underline; }

  .handoff-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 11px;
    background: #1a1225;
    border: 1px solid #3b2a5a;
    color: #c8b5f5;
    padding: 3px 8px;
    border-radius: 4px;
    margin-top: 6px;
    font-family: "SF Mono", "Fira Code", monospace;
  }
  .handoff-arrow { color: #7c3aed; }
</style>
</head>
<body>

<header>
  <div class="live-dot" id="live-dot"></div>
  {mascot_img}
  <h1>Agentic Ledger</h1>
  <a href="/" style="color:#4ea1ff;text-decoration:none;font-size:13px;margin-left:10px;padding:4px 10px;border:1px solid #2b3644;border-radius:6px;white-space:nowrap;">New app →</a>
  <div class="search-wrap">
    <span class="search-icon">⌕</span>
    <input class="search-input" id="search-input" type="text"
           placeholder="Search prompts, outputs, agents…" autocomplete="off">
  </div>
</header>

<div class="layout">
  <div class="sessions-panel">
    <div class="panel-header">
      <span id="panel-title">Sessions</span>
      <button class="refresh-btn" onclick="loadSessions()">Refresh</button>
    </div>
    <div class="sessions-list" id="sessions-list">
      <div class="empty-state">Loading sessions…</div>
    </div>
  </div>

  <div class="detail-panel">
    <div class="detail-header" id="detail-header"></div>
    <div id="agent-filter-bar" style="display:none;align-items:center;gap:8px;padding:6px 20px;background:#0d0d0d;border-bottom:1px solid #1e1e1e;font-size:11px;color:#a78bfa;">
      Showing: <span style="font-weight:600"></span>
      <button onclick="clearAgentFilter();this.closest('#agent-filter-bar').style.display='none'" style="margin-left:auto;font-size:10px;color:#555;background:none;border:none;cursor:pointer;padding:2px 6px;border-radius:3px;border:1px solid #333">✕ Clear filter</button>
    </div>
    <div class="detail-body" id="detail-body">
      <div class="placeholder">Select a session to inspect</div>
    </div>
    <div class="flow-view" id="flow-view" style="display:none">
      <div class="flow-empty" id="flow-empty">No agent data in this session.<br>Add <code>x-agenticledger-agent-name</code> to your LLM calls<br>to see the flow here.</div>
      <svg id="flow-svg"></svg>
    </div>
    <div class="trace-view" id="trace-view" style="display:none">
      <div class="flow-empty" id="trace-empty" style="display:none">No calls in this session.</div>
      <svg id="trace-svg"></svg>
    </div>
  </div>
</div>

<script>
let activeSessionId = null;
let sessionRefreshTimer = null;
let searchDebounce = null;
let activeTab = 'calls';

// ── Utilities ────────────────────────────────────────────────────────────────

function timeAgo(isoStr) {
  const diff = Date.now() - new Date(isoStr).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60)   return s + 's ago';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}

function ms(val) {
  if (val == null) return '—';
  return val >= 1000 ? (val / 1000).toFixed(1) + 's' : Math.round(val) + 'ms';
}

function cost(val) {
  if (val == null || val === 0) return null;
  if (val < 0.001) return '<$0.001';
  return '$' + val.toFixed(4);
}

function shortId(id) {
  if (!id) return '—';
  return id.length > 12 ? id.slice(0, 8) + '…' : id;
}

function formatArgs(args) {
  if (!args) return '';
  if (typeof args === 'object') return JSON.stringify(args, null, 2);
  try { return JSON.stringify(JSON.parse(args), null, 2); }
  catch { return args; }
}

function lastUserMessage(messages) {
  if (!Array.isArray(messages)) return null;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === 'user') {
      if (typeof m.content === 'string') return m.content;
      if (Array.isArray(m.content)) {
        const text = m.content.find(b => b.type === 'text');
        if (text) return text.text;
      }
    }
  }
  return null;
}

function escHtml(str) {
  if (str == null) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// ── WebSocket (live updates) ──────────────────────────────────────────────────

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  // Forward the page's credential (?api_key= / ?token=) so /ws passes auth.
  const pageQS = new URLSearchParams(location.search);
  const creds = new URLSearchParams();
  for (const k of ['api_key', 'token']) {
    if (pageQS.get(k)) creds.set(k, pageQS.get(k));
  }
  const qs = creds.toString();
  const ws = new WebSocket(`${proto}://${location.host}/ws${qs ? '?' + qs : ''}`);
  const dot = document.getElementById('live-dot');

  ws.onopen = () => dot.classList.add('connected');
  ws.onclose = () => {
    dot.classList.remove('connected');
    setTimeout(connectWS, 3000); // reconnect
  };
  ws.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.type === 'call') {
      // Refresh session list silently
      loadSessions(true);
      // If the new call belongs to the active session, refresh it too
      if (data.session_id && data.session_id === activeSessionId) {
        loadSession(activeSessionId, true);
      }
    }
  };

  // Keep-alive ping every 25s
  setInterval(() => { if (ws.readyState === 1) ws.send('ping'); }, 25000);
}

// ── Search ───────────────────────────────────────────────────────────────────

document.getElementById('search-input').addEventListener('input', (e) => {
  clearTimeout(searchDebounce);
  const q = e.target.value.trim();
  if (!q) {
    document.getElementById('panel-title').textContent = 'Sessions';
    loadSessions();
    return;
  }
  searchDebounce = setTimeout(() => doSearch(q), 300);
});

async function doSearch(q) {
  document.getElementById('panel-title').textContent = 'Search results';
  const el = document.getElementById('sessions-list');
  el.innerHTML = '<div class="empty-state">Searching…</div>';
  try {
    const res = await fetch('/api/search?q=' + encodeURIComponent(q));
    const results = await res.json();
    if (!results.length) {
      el.innerHTML = '<div class="empty-state">No results.</div>';
      return;
    }
    el.innerHTML = results.map(r => {
      const snippet = r.content || lastUserMessage(r.messages) || '';
      return `
        <div class="search-result-item" data-action-id="${escHtml(r.action_id)}" data-session-id="${escHtml(r.session_id || '')}">
          <div class="search-result-model">${escHtml(r.model_id)}</div>
          <div class="search-result-snippet">${escHtml(snippet.slice(0, 80))}</div>
          <div class="session-meta"><span>${escHtml(r.agent_name || '')}</span><span>${timeAgo(r.timestamp)}</span></div>
        </div>
      `;
    }).join('');
    el.querySelectorAll('.search-result-item').forEach(item => {
      item.addEventListener('click', () => showSearchResult(item.dataset.actionId, item.dataset.sessionId));
    });
  } catch(e) {
    el.innerHTML = '<div class="empty-state">Search failed.</div>';
  }
}

async function showSearchResult(actionId, sessionId) {
  if (sessionId) {
    await loadSession(sessionId);
    // Scroll to the specific call card
    setTimeout(() => {
      const el = document.getElementById('call-' + actionId);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 300);
  } else {
    // No session — fetch and show the single call
    const res = await fetch('/explain/' + actionId);
    if (!res.ok) return;
    const call = await res.json();
    document.getElementById('detail-header').innerHTML =
      `<span class="detail-session-id">${escHtml(call.model_id)}</span>`;
    const detailBody = document.getElementById('detail-body');
    detailBody.innerHTML = renderCall(call, 1);
    bindParentLinks(detailBody);
  }
}

// ── Sessions list ────────────────────────────────────────────────────────────

async function loadSessions(silent = false) {
  const el = document.getElementById('sessions-list');
  try {
    const res = await fetch('/api/sessions');
    const sessions = await res.json();
    if (!sessions.length) {
      if (!silent) el.innerHTML = '<div class="empty-state">No sessions yet.<br>Make an LLM call through the proxy to get started.</div>';
      return;
    }
    el.innerHTML = sessions.map(s => {
      const c = cost(s.total_cost_usd);
      return `
      <div class="session-item ${s.session_id === activeSessionId ? 'active' : ''}"
           data-sid="${escHtml(s.session_id)}">
        <div style="display:flex;align-items:center;gap:4px">
          <div class="session-id" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(s.session_id)}</div>
          <button class="delete-session-btn" title="Delete session" data-sid="${escHtml(s.session_id)}"
            style="flex-shrink:0;background:none;border:1px solid #2a2a2a;border-radius:3px;cursor:pointer;color:#888;font-size:11px;padding:1px 5px;line-height:1.4"
            onmouseover="this.style.color='#ef4444';this.style.borderColor='#ef4444'" onmouseout="this.style.color='#888';this.style.borderColor='#2a2a2a'">✕</button>
        </div>
        <div class="session-meta">
          <span>${s.call_count} call${s.call_count !== 1 ? 's' : ''}</span>
          <span>${ms(s.total_latency_ms)}</span>
          ${c ? `<span>${c}</span>` : ''}
          <span>${timeAgo(s.started_at)}</span>
        </div>
        ${s.agent_name ? `<div class="session-meta"><span>${escHtml(s.agent_name)}</span></div>` : ''}
      </div>
      `;
    }).join('');
    el.querySelectorAll('.session-item').forEach(item => {
      item.addEventListener('click', () => loadSession(item.dataset.sid));
    });
    el.querySelectorAll('.delete-session-btn').forEach(btn => {
      btn.addEventListener('click', e => { e.stopPropagation(); deleteSession(btn.dataset.sid); });
    });
  } catch(e) {
    if (!silent) el.innerHTML = '<div class="empty-state">Failed to load sessions.</div>';
  }
}

// ── Session delete ───────────────────────────────────────────────────────────

async function deleteSession(sessionId) {
  if (!confirm(`Delete session "${sessionId}" and all its calls?`)) return;
  try {
    const res = await fetch('/api/sessions/' + encodeURIComponent(sessionId), { method: 'DELETE' });
    if (!res.ok) { alert('Failed to delete session.'); return; }
    if (activeSessionId === sessionId) {
      activeSessionId = null;
      document.getElementById('detail-body').innerHTML = '<div class="placeholder">Select a session to inspect</div>';
      document.getElementById('detail-header').innerHTML = '';
    }
    loadSessions();
  } catch(e) {
    alert('Failed to delete session.');
  }
}

// ── Session detail ───────────────────────────────────────────────────────────

async function loadSession(sessionId, silent = false) {
  if (sessionId !== activeSessionId) {
    if (sessionRefreshTimer) { clearInterval(sessionRefreshTimer); sessionRefreshTimer = null; }
    activeSessionId = sessionId;
    document.querySelectorAll('.session-item').forEach(el => {
      el.classList.toggle('active', el.querySelector('.session-id').textContent === sessionId);
    });
    const body = document.getElementById('detail-body');
    body.innerHTML = '<div class="placeholder">Loading…</div>';
    document.getElementById('detail-header').innerHTML =
      `<span class="detail-session-id">${escHtml(sessionId)}</span>`;
    sessionRefreshTimer = setInterval(() => loadSession(activeSessionId, true), 5000);
  }

  const header = document.getElementById('detail-header');
  const body = document.getElementById('detail-body');

  try {
    const res = await fetch('/session/' + encodeURIComponent(sessionId));
    if (!res.ok) { body.innerHTML = '<div class="placeholder">Session not found.</div>'; return; }
    const calls = await res.json();

    const totalMs = calls.reduce((s, c) => s + (c.latency_ms || 0), 0);
    const totalIn = calls.reduce((s, c) => s + (c.tokens_in || 0), 0);
    const totalOut = calls.reduce((s, c) => s + (c.tokens_out || 0), 0);
    const totalCost = calls.reduce((s, c) => s + (c.cost_usd || 0), 0);
    const errorCount = calls.filter(c => (c.status_code || 200) !== 200).length;
    const c = cost(totalCost || null);

    header.innerHTML = `
      <span class="detail-session-id">${escHtml(sessionId)}</span>
      <div class="detail-stats">
        <span><strong>${calls.length}</strong> calls${errorCount ? ` <span style="color:#f87171">(${errorCount} error${errorCount>1?'s':''})</span>` : ''}</span>
        <span><strong>${ms(totalMs)}</strong> total</span>
        <span><strong>${totalIn}</strong> / <strong>${totalOut}</strong> tokens</span>
        ${c ? `<span><strong>${c}</strong></span>` : ''}
      </div>
      <div class="detail-tabs">
        <button class="tab-btn ${activeTab==='calls'?'active':''}" onclick="switchTab('calls')">Calls</button>
        <button class="tab-btn ${activeTab==='flow'?'active':''}" onclick="switchTab('flow')">Flow</button>
        <button class="tab-btn ${activeTab==='trace'?'active':''}" onclick="switchTab('trace')">Trace</button>
      </div>
      <div class="export-btn">
        <a class="export-link" href="/export/${encodeURIComponent(sessionId)}" download>↓ JSON</a>
        <a class="export-link" href="/export/${encodeURIComponent(sessionId)}/report" target="_blank">Report</a>
      </div>
    `;

    renderFlowDAG(calls);
    renderTraceDAG(calls);

    const scrollTop = silent ? body.scrollTop : 0;
    body.innerHTML = calls.map((call, i) => renderCall(call, i + 1)).join('');
    bindParentLinks(body);
    body.scrollTop = scrollTop;
  } catch(e) {
    if (!silent) body.innerHTML = '<div class="placeholder">Failed to load session.</div>';
  }
}

function bindParentLinks(container) {
  container.querySelectorAll('[data-parent-action-id]').forEach(el => {
    el.addEventListener('click', () => scrollToAction(el.dataset.parentActionId));
  });
}

function renderCall(call, n) {
  const isError   = (call.status_code || 200) !== 200;
  const isWarning = !isError && (call.error_detail || '').startsWith('budget_warning:');
  const stopClass = (call.stop_reason || '').replace('_', '-');
  const input = lastUserMessage(call.messages);
  const hasTools = call.tool_calls && call.tool_calls.length > 0;
  const callCost = cost(call.cost_usd);

  const metaItems = [
    call.agent_name ? `<div class="meta-item"><span class="meta-label">Agent</span><span class="meta-value">${escHtml(call.agent_name)}</span></div>` : '',
    call.user_id    ? `<div class="meta-item"><span class="meta-label">User</span><span class="meta-value">${escHtml(call.user_id)}</span></div>` : '',
    call.app_id     ? `<div class="meta-item"><span class="meta-label">App</span><span class="meta-value">${escHtml(call.app_id)}</span></div>` : '',
    call.environment && call.environment !== 'development' ? `<div class="meta-item"><span class="meta-label">Env</span><span class="meta-value">${escHtml(call.environment)}</span></div>` : '',
    call.temperature != null ? `<div class="meta-item"><span class="meta-label">Temp</span><span class="meta-value">${call.temperature}</span></div>` : '',
    call.max_tokens  != null ? `<div class="meta-item"><span class="meta-label">Max tokens</span><span class="meta-value">${call.max_tokens}</span></div>` : '',
  ].filter(Boolean).join('');

  const systemSection = call.system_prompt ? `
    <div class="section-label">System prompt</div>
    <div class="message-bubble system-prompt">${escHtml(call.system_prompt)}</div>
  ` : '';

  const inputSection = input ? `
    <div class="section-label">Input</div>
    <div class="message-bubble">${escHtml(input)}</div>
  ` : '';

  const toolResultsSection = (call.tool_results && call.tool_results.length > 0) ? `
    <div class="section-label">Tool results</div>
    ${call.tool_results.map(tr => `
      <div class="tool-result">
        <div class="tool-result-id">${escHtml(tr.tool_call_id || tr.tool_use_id || '?')}</div>
        <div class="tool-result-content">${escHtml(
          typeof tr.content === 'string' ? tr.content :
          tr.content ? JSON.stringify(tr.content, null, 2) : ''
        )}</div>
      </div>
    `).join('')}
  ` : '';

  const toolSection = hasTools ? `
    <div class="section-label">Tool calls</div>
    ${call.tool_calls.map(tc => `
      <div class="tool-call">
        <div class="tool-name">${escHtml(tc.name || '?')}</div>
        <div class="tool-args">${escHtml(formatArgs(tc.arguments))}</div>
      </div>
    `).join('')}
  ` : '';

  const outputSection = call.content ? `
    <div class="section-label">Output</div>
    <div class="message-bubble output">${escHtml(call.content)}</div>
  ` : '';

  const errorSection = isError ? `
    <div class="section-label error-label">Error — HTTP ${escHtml(call.status_code)}</div>
    <div class="message-bubble error-bubble">${escHtml(call.error_detail || 'No details captured.')}</div>
  ` : '';

  const parentSection = call.parent_action_id ? `
    <div class="parent-link">
      Triggered by <a data-parent-action-id="${escHtml(call.parent_action_id)}" style="cursor:pointer">${shortId(call.parent_action_id)}</a>
    </div>
  ` : '';

  const handoffSection = (call.handoff_from || call.handoff_to) ? `
    <div class="handoff-badge">
      ${call.handoff_from ? escHtml(call.handoff_from) : ''}
      ${call.handoff_from && call.handoff_to ? '<span class="handoff-arrow">→</span>' : ''}
      ${call.handoff_to ? escHtml(call.handoff_to) : ''}
    </div>
  ` : '';

  return `
    <div class="call-card ${isError ? 'call-error' : isWarning ? 'call-warning' : ''}" id="call-${escHtml(call.action_id)}" data-agent="${escHtml(call.agent_name || '')}">
      <div class="call-card-header" onclick="toggleCallCard(this.closest('.call-card'))">
        <span class="call-number">${n}</span>
        <span class="call-model">${escHtml(call.model_id)}</span>
        ${call.agent_name ? `<span style="font-size:11px;color:#a78bfa;font-weight:600">${escHtml(call.agent_name)}</span>` : ''}
        <div class="call-badges">
          ${call.latency_ms != null ? `<span class="badge badge-latency">${ms(call.latency_ms)}</span>` : ''}
          ${(call.tokens_in != null && call.tokens_out != null) ? `<span class="badge badge-tokens">${call.tokens_in} / ${call.tokens_out}</span>` : ''}
          ${callCost ? `<span class="badge badge-cost">${callCost}</span>` : ''}
          ${isError ? `<span class="badge badge-error">HTTP ${escHtml(call.status_code)}</span>` : ''}
          ${isWarning ? `<span class="badge" style="background:#2a1a00;color:#fbbf24">⚠ budget</span>` : ''}
          ${!isError && !isWarning && call.stop_reason ? `<span class="badge badge-stop ${stopClass}">${escHtml(call.stop_reason)}</span>` : ''}
        </div>
        <span class="call-toggle">▼</span>
      </div>
      <div class="call-card-body">
        ${metaItems ? `<div class="call-meta-row">${metaItems}</div>` : ''}
        ${handoffSection}
        ${systemSection}
        ${inputSection}
        ${toolResultsSection}
        ${toolSection}
        ${outputSection}
        ${errorSection}
        ${parentSection}
      </div>
    </div>
  `;
}

function scrollToAction(actionId) {
  const el = document.getElementById('call-' + actionId);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── Tab switcher ──────────────────────────────────────────────────────────────

function switchTab(tab) {
  activeTab = tab;
  document.getElementById('detail-body').style.display  = tab === 'calls' ? '' : 'none';
  document.getElementById('flow-view').style.display    = tab === 'flow'  ? '' : 'none';
  document.getElementById('trace-view').style.display   = tab === 'trace' ? '' : 'none';
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.textContent.toLowerCase() === tab);
  });
  if (tab !== 'calls') clearAgentFilter();
  const filterBar = document.getElementById('agent-filter-bar');
  if (filterBar) filterBar.style.display = 'none';
}

function filterByAgentAndShowBar(agentName) {
  filterByAgent(agentName);
  const bar = document.getElementById('agent-filter-bar');
  if (bar) { bar.style.display = 'flex'; bar.querySelector('span').textContent = agentName; }
}

// ── Agent flow DAG ────────────────────────────────────────────────────────────

function renderFlowDAG(calls) {
  const svg = document.getElementById('flow-svg');
  const empty = document.getElementById('flow-empty');
  svg.innerHTML = '';

  // Build node map: agent_name → { calls, totalCost, totalMs, totalIn, totalOut }
  const nodes = new Map();   // id → node
  const edges = [];          // { from, to, label }

  const getNode = (name) => {
    if (!nodes.has(name)) {
      nodes.set(name, { id: name, calls: 0, totalCost: 0, totalMs: 0, totalIn: 0, totalOut: 0, errors: 0, warnings: 0 });
    }
    return nodes.get(name);
  };

  for (const call of calls) {
    const agent = call.agent_name || '(unknown)';
    const n = getNode(agent);
    n.calls++;
    n.totalCost += call.cost_usd || 0;
    n.totalMs   += call.latency_ms || 0;
    n.totalIn   += call.tokens_in || 0;
    n.totalOut  += call.tokens_out || 0;
    if ((call.status_code || 200) !== 200) n.errors++;
    if ((call.error_detail || '').startsWith('budget_warning:')) n.warnings++;

    if (call.handoff_from) {
      getNode(call.handoff_from);
      const key = `${call.handoff_from}→${agent}`;
      const ex = edges.find(e => e.key === key);
      if (ex) { ex.count++; } else { edges.push({ key, from: call.handoff_from, to: agent, count: 1 }); }
    }
    if (call.handoff_to) {
      getNode(call.handoff_to);
      const key = `${agent}→${call.handoff_to}`;
      const ex = edges.find(e => e.key === key);
      if (ex) { ex.count++; } else { edges.push({ key, from: agent, to: call.handoff_to, count: 1 }); }
    }
  }

  if (nodes.size === 0) {
    empty.style.display = '';
    svg.style.display = 'none';
    return;
  }
  empty.style.display = 'none';
  svg.style.display = '';

  // ── Cycle detection: separate forward edges from back-edges ─────────────────
  // DFS to find back-edges (edges that point to an ancestor in the DFS tree)
  const visited = new Set(), inStack = new Set();
  const backEdgeKeys = new Set();
  function dfs(id) {
    visited.add(id); inStack.add(id);
    for (const e of edges) {
      if (e.from !== id) continue;
      if (!visited.has(e.to)) { dfs(e.to); }
      else if (inStack.has(e.to)) { backEdgeKeys.add(e.key); }
    }
    inStack.delete(id);
  }
  [...nodes.keys()].forEach(id => { if (!visited.has(id)) dfs(id); });
  const forwardEdges = edges.filter(e => !backEdgeKeys.has(e.key));
  const backEdges    = edges.filter(e =>  backEdgeKeys.has(e.key));

  // ── Layout: topological layers (forward edges only) ─────────────────────────
  const nodeIds = [...nodes.keys()];
  const inDegree = new Map(nodeIds.map(id => [id, 0]));
  for (const e of forwardEdges) inDegree.set(e.to, (inDegree.get(e.to) || 0) + 1);

  // BFS layering
  const layer = new Map();
  const queue = nodeIds.filter(id => inDegree.get(id) === 0);
  queue.forEach(id => layer.set(id, 0));
  let head = 0;
  while (head < queue.length) {
    const id = queue[head++];
    for (const e of forwardEdges) {
      if (e.from !== id) continue;
      const next = e.to;
      const nextLayer = (layer.get(id) || 0) + 1;
      if (!layer.has(next) || layer.get(next) < nextLayer) {
        layer.set(next, nextLayer);
      }
      if (!queue.includes(next)) queue.push(next);
    }
  }
  // Nodes not reached (cycles/isolated) go to layer 0
  nodeIds.forEach(id => { if (!layer.has(id)) layer.set(id, 0); });

  // Group by layer
  const layerGroups = new Map();
  for (const [id, l] of layer) {
    if (!layerGroups.has(l)) layerGroups.set(l, []);
    layerGroups.get(l).push(id);
  }

  // ── Dimensions ─────────────────────────────────────────────────────────────
  const NODE_W = 180, NODE_H = 90, H_GAP = 60, V_GAP = 48;
  const maxPerLayer = Math.max(...[...layerGroups.values()].map(g => g.length));
  const layerCount = layerGroups.size;
  const totalW = layerCount * NODE_W + (layerCount - 1) * H_GAP + 80;
  const totalH = maxPerLayer * NODE_H + (maxPerLayer - 1) * V_GAP + 80;

  svg.setAttribute('width', totalW);
  svg.setAttribute('height', totalH);

  // Position nodes
  const pos = new Map();
  const sortedLayers = [...layerGroups.keys()].sort((a, b) => a - b);
  sortedLayers.forEach((l, li) => {
    const group = layerGroups.get(l);
    const x = 40 + li * (NODE_W + H_GAP);
    group.forEach((id, gi) => {
      const groupH = group.length * NODE_H + (group.length - 1) * V_GAP;
      const startY = (totalH - groupH) / 2;
      const y = startY + gi * (NODE_H + V_GAP);
      pos.set(id, { x, y });
    });
  });

  // ── SVG defs ───────────────────────────────────────────────────────────────
  const defs = `<defs>
    <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
      <path d="M0,0 L8,3 L0,6 Z" fill="#3b3b3b"/>
    </marker>
    <marker id="arrow-back" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
      <path d="M0,0 L8,3 L0,6 Z" fill="#78350f"/>
    </marker>
    <filter id="glow">
      <feGaussianBlur stdDeviation="2" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>`;

  // ── Draw edges ─────────────────────────────────────────────────────────────
  const edgeSvg = [
    // Forward edges — straight bezier
    ...forwardEdges.map(e => {
      const a = pos.get(e.from), b = pos.get(e.to);
      if (!a || !b) return '';
      const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2;
      const x2 = b.x,          y2 = b.y + NODE_H / 2;
      const cx = (x1 + x2) / 2;
      return `<path d="M${x1},${y1} C${cx},${y1} ${cx},${y2} ${x2},${y2}"
        fill="none" stroke="#2a2a2a" stroke-width="2"
        marker-end="url(#arrow)"/>`;
    }),
    // Back edges (cycles) — arc above nodes, dashed, amber colour with traversal count
    ...backEdges.map(e => {
      const a = pos.get(e.from), b = pos.get(e.to);
      if (!a || !b) return '';
      const x1 = a.x + NODE_W / 2, y1 = a.y;
      const x2 = b.x + NODE_W / 2, y2 = b.y;
      const cy = Math.min(y1, y2) - 60;
      const mx = (x1 + x2) / 2, my = cy + 10;
      const label = `↩ ${e.count}×`;
      const lw = label.length * 6 + 10;
      return `
        <path d="M${x1},${y1} C${x1},${cy} ${x2},${cy} ${x2},${y2}"
          fill="none" stroke="#78350f" stroke-width="1.5" stroke-dasharray="5,3"
          marker-end="url(#arrow-back)"/>
        <rect x="${mx - lw/2}" y="${my - 9}" width="${lw}" height="16" rx="4" fill="#1c0f00" stroke="#78350f" stroke-width="1"/>
        <text x="${mx}" y="${my + 3}" text-anchor="middle" fill="#f97316" font-size="10" font-weight="600">${label}</text>`;
    }),
  ].join('');

  // ── Draw nodes ─────────────────────────────────────────────────────────────
  const nodeSvg = nodeIds.map(id => {
    const n = nodes.get(id);
    const { x, y } = pos.get(id);
    const hasError   = n.errors > 0;
    const hasWarning = !hasError && n.warnings > 0;
    const borderColor = hasError ? '#7f1d1d' : hasWarning ? '#78350f' : '#2a2a2a';
    const headerBg    = hasError ? '#1a0808' : hasWarning ? '#120900' : '#141414';
    const c = n.totalCost > 0
      ? (n.totalCost < 0.001 ? '<$0.001' : '$' + n.totalCost.toFixed(4))
      : null;
    const latency = n.totalMs >= 1000
      ? (n.totalMs / 1000).toFixed(1) + 's'
      : Math.round(n.totalMs) + 'ms';

    return `
    <g transform="translate(${x},${y})" style="cursor:pointer" data-agent-id="${escHtml(id)}">
      <rect width="${NODE_W}" height="${NODE_H}" rx="8" fill="#111" stroke="${borderColor}" stroke-width="1.5"/>
      <rect width="${NODE_W}" height="32" rx="8" fill="${headerBg}" stroke="${borderColor}" stroke-width="1.5"/>
      <rect y="24" width="${NODE_W}" height="8" fill="${headerBg}"/>
      <text x="${NODE_W/2}" y="21" text-anchor="middle" fill="${hasError?'#f87171':hasWarning?'#fbbf24':'#c8b5f5'}"
            font-size="12" font-weight="600" font-family="SF Mono, Fira Code, monospace">${escHtml(id)}</text>
      <text x="14" y="52" fill="#666" font-size="10">calls</text>
      <text x="14" y="65" fill="#e0e0e0" font-size="13" font-weight="600">${n.calls}${hasError ? ` <tspan fill="#f87171" font-size="10">(${n.errors} err)</tspan>` : hasWarning ? ` <tspan fill="#fbbf24" font-size="10">(${n.warnings} ⚠)</tspan>` : ''}</text>
      <text x="${NODE_W/2+8}" y="52" fill="#666" font-size="10">latency</text>
      <text x="${NODE_W/2+8}" y="65" fill="#4ade80" font-size="12" font-weight="500">${latency}</text>
      ${c ? `<text x="14" y="82" fill="#86efac" font-size="11">${c}</text>` : ''}
      <text x="${NODE_W - 10}" y="82" text-anchor="end" fill="#444" font-size="10">${n.totalIn}↑ ${n.totalOut}↓ tok</text>
    </g>`;
  }).join('');

  svg.innerHTML = defs + edgeSvg + nodeSvg;
  svg.querySelectorAll('g[data-agent-id]').forEach(g => {
    g.addEventListener('click', () => filterByAgentAndShowBar(g.dataset.agentId));
  });
}

// ── Trace: Gantt / waterfall view ────────────────────────────────────────────
// Each call = a horizontal bar on a shared time axis.
// Parallel calls appear at the same x position — no instrumentation required.
// If parent_action_id is set, connector lines show explicit hierarchy.
// For uninstrumented calls, connectors are inferred from temporal proximity.
// Clicking any bar switches to the Calls tab and scrolls to that call.

function renderTraceDAG(calls) {
  const svg   = document.getElementById('trace-svg');
  const empty = document.getElementById('trace-empty');
  svg.innerHTML = '';

  if (!calls.length) {
    empty.style.display = '';
    svg.style.display   = 'none';
    return;
  }
  empty.style.display = 'none';
  svg.style.display   = '';

  // ── Parse timestamps, sort chronologically ────────────────────────────────
  const rows = calls.map(c => ({
    ...c,
    t0: new Date(c.timestamp).getTime(),
    t1: new Date(c.timestamp).getTime() + (c.latency_ms || 0),
  })).sort((a, b) => a.t0 - b.t0 || a.action_id.localeCompare(b.action_id));

  const sessionT0 = rows[0].t0;
  const sessionT1 = Math.max(...rows.map(r => r.t1));
  const durMs     = Math.max(sessionT1 - sessionT0, 1);

  // ── Layout constants ──────────────────────────────────────────────────────
  const ML       = 16;    // left margin
  const LABEL_W  = 164;   // agent name + model column
  const TIMELINE = 620;   // timeline bar area width
  const STATS_W  = 130;   // latency / tokens / cost column
  const MR       = 16;    // right margin
  const BAR_H    = 22;    // height of each bar
  const ROW_H    = 32;    // row pitch (bar + gap)
  const TICK_H   = 38;    // header height for time axis
  const SVG_W    = ML + LABEL_W + TIMELINE + STATS_W + MR;
  const SVG_H    = TICK_H + rows.length * ROW_H + 20;

  // ms offset → SVG x coordinate inside the timeline area
  const tx = ms => ML + LABEL_W + Math.round(Math.min(ms / durMs, 1) * TIMELINE);

  // ── Stable per-agent colour assignment (first-seen order) ─────────────────
  // Bright, clearly distinct colours optimised for dark backgrounds.
  const PALETTE  = ['#a78bfa','#60a5fa','#34d399','#fbbf24','#f87171','#e879f9','#2dd4bf','#fb923c'];
  const colorMap = new Map();
  let   ci       = 0;
  const agentCol = name => {
    if (!name) return '#3f3f3f';
    if (!colorMap.has(name)) colorMap.set(name, PALETTE[ci++ % PALETTE.length]);
    return colorMap.get(name);
  };
  rows.forEach(r => agentCol(r.agent_name));   // assign in chronological order

  // ── Time axis ticks ───────────────────────────────────────────────────────
  const NICE = [25,50,100,200,500,1000,2000,5000,10000,30000,60000];
  const tickStep = NICE.find(t => durMs / t <= 7) || 60000;
  const fmtMs = t => t === 0 ? '0' : t >= 1000 ? (t/1000).toFixed(t%1000===0?0:1)+'s' : t+'ms';

  let axisSvg = `<line x1="${ML+LABEL_W}" y1="${TICK_H}" x2="${ML+LABEL_W+TIMELINE}" y2="${TICK_H}" stroke="#1a1a1a" stroke-width="1"/>`;
  for (let t = 0; t <= durMs; t += tickStep) {
    const x = tx(t);
    axisSvg += `
      <line x1="${x}" y1="${TICK_H-5}" x2="${x}" y2="${TICK_H}"   stroke="#252525" stroke-width="1"/>
      <line x1="${x}" y1="${TICK_H}"   x2="${x}" y2="${SVG_H-8}"  stroke="#0f0f0f" stroke-width="1" stroke-dasharray="3,5"/>
      <text x="${x}" y="${TICK_H-9}" text-anchor="middle" fill="#333" font-size="9"
            font-family="SF Mono,Fira Code,monospace">${fmtMs(t)}</text>`;
  }

  // ── Parent → child connectors ──────────────────────────────────────────────
  // Priority: explicit parent_action_id > temporal inference.
  // Inference: for each call, the parent is the call that ended most recently
  // before it started, within a 2-second gap (covers spawn overhead).
  // This requires zero instrumentation and correctly surfaces parallel siblings
  // (two calls starting near the same time both point to the same predecessor).

  const byAction = new Map(rows.map((r, i) => [r.action_id, i]));
  const parentOf = new Map();   // action_id → parent action_id

  for (let i = 1; i < rows.length; i++) {
    const c = rows[i];
    // Explicit parent wins if it's in this session
    if (c.parent_action_id && byAction.has(c.parent_action_id)) {
      parentOf.set(c.action_id, { id: c.parent_action_id, explicit: true });
      continue;
    }
    // Infer: latest-ending predecessor that finished before c started (±50 ms tolerance
    // for spawn overhead). No gap cap — a disconnected root is more honest than a
    // wrong parent, but sessions with long pauses between calls are still rare and
    // the most-recent-predecessor heuristic remains the best available signal.
    let bestId  = null;
    let bestT1  = -Infinity;
    for (let j = 0; j < i; j++) {
      const p = rows[j];
      if (p.t1 <= c.t0 + 50 && p.t1 > bestT1) { bestT1 = p.t1; bestId = p.action_id; }
    }
    if (bestId !== null) {
      parentOf.set(c.action_id, { id: bestId, explicit: false });
    }
  }

  // Draw connectors (elbow: right from parent end, down, left to child start)
  let connSvg = '';
  for (const [childId, { id: parentId, explicit }] of parentOf) {
    const pi = byAction.get(parentId);
    const ci2 = byAction.get(childId);
    if (pi === undefined || ci2 === undefined || pi === ci2) continue;
    const pr = rows[pi];
    const cr = rows[ci2];
    // Connector departs from end of parent bar (clamped to timeline right edge)
    const depX  = Math.min(tx(pr.t1 - sessionT0), ML + LABEL_W + TIMELINE);
    const depY  = TICK_H + pi  * ROW_H + BAR_H / 2 + 5;
    const arrX  = tx(cr.t0 - sessionT0);
    const arrY  = TICK_H + ci2 * ROW_H + BAR_H / 2 + 5;
    const elbX  = Math.max(depX, arrX);   // elbow turns at the rightmost of the two
    const stroke = explicit ? '#2a2a2a' : '#181818';
    connSvg += `<polyline
      points="${depX},${depY} ${elbX},${depY} ${elbX},${arrY} ${arrX},${arrY}"
      fill="none" stroke="${stroke}" stroke-width="1" stroke-dasharray="3,4"
      marker-end="url(#tc-arr)"/>`;
  }

  // ── Row bars ──────────────────────────────────────────────────────────────
  let rowsSvg = '';
  for (let i = 0; i < rows.length; i++) {
    const r     = rows[i];
    const ry    = TICK_H + i * ROW_H;
    const by    = ry + 5;            // bar top y

    const isErr  = (r.status_code || 200) !== 200;
    const isWarn = !isErr && (r.error_detail || '').startsWith('budget_warning:');
    // Agent colour is always preserved — errors use red, warnings keep agent colour
    // with an amber border so agents remain visually distinct across the session.
    const col     = isErr ? '#dc2626' : agentCol(r.agent_name);
    const barStroke = isWarn ? 'stroke="#d97706" stroke-width="1.5"' : '';

    const bx = tx(r.t0 - sessionT0);
    const bw = Math.max(4, tx(r.t1 - sessionT0) - bx);

    const agent  = (r.agent_name || '—').slice(0, 21);
    const model  = (r.model_id   || '').split('/').pop().slice(0, 21);
    const lat    = r.latency_ms >= 1000 ? (r.latency_ms/1000).toFixed(1)+'s' : Math.round(r.latency_ms||0)+'ms';
    const tok    = `${r.tokens_in||0}↑ ${r.tokens_out||0}↓`;
    const cstr   = r.cost_usd ? '$'+(+r.cost_usd).toFixed(4) : '';
    const warnBadge = isWarn ? `<text x="${bx+bw+3}" y="${by+14}" fill="#d97706" font-size="9" pointer-events="none">⚠</text>` : '';

    // Alternating row background
    if (i % 2 === 0) rowsSvg += `<rect x="0" y="${ry}" width="${SVG_W}" height="${ROW_H}" fill="#080808"/>`;

    // Separator line
    rowsSvg += `<line x1="${ML+LABEL_W}" y1="${ry+ROW_H-1}" x2="${ML+LABEL_W+TIMELINE}" y2="${ry+ROW_H-1}" stroke="#0d0d0d" stroke-width="1"/>`;

    // Colour dot + agent name + model
    rowsSvg += `<circle cx="${ML+5}" cy="${by+BAR_H/2}" r="3.5" fill="${col}"/>`;
    rowsSvg += `<text x="${ML+13}" y="${by+11}" fill="${col}"
      font-size="11" font-weight="600" font-family="-apple-system,BlinkMacSystemFont,sans-serif">${escHtml(agent)}</text>`;
    rowsSvg += `<text x="${ML+13}" y="${by+21}" fill="#555"
      font-size="9" font-family="SF Mono,Fira Code,monospace">${escHtml(model)}</text>`;

    // Full-width timeline track (dark, shows full session span)
    rowsSvg += `<rect x="${ML+LABEL_W}" y="${by}" width="${TIMELINE}" height="${BAR_H}" rx="2" fill="#0b0b0b"/>`;

    // Actual call bar (clickable); amber border overlay for budget warnings
    rowsSvg += `<rect x="${bx}" y="${by}" width="${bw}" height="${BAR_H}" rx="3"
      fill="${col}" opacity="0.82" ${barStroke} style="cursor:pointer"
      data-trace-action-id="${escHtml(r.action_id)}"/>`;
    rowsSvg += warnBadge;

    // Duration label inside bar when wide enough
    if (bw > 34) {
      rowsSvg += `<text x="${bx + bw/2}" y="${by+14}" text-anchor="middle"
        fill="#fff" font-size="9" font-weight="600"
        font-family="SF Mono,Fira Code,monospace" pointer-events="none">${escHtml(lat)}</text>`;
    }

    // Stats column (right of timeline)
    const sx = ML + LABEL_W + TIMELINE + 8;
    rowsSvg += `<text x="${sx}"        y="${by+12}" fill="#4ade80" font-size="10" font-weight="500">${escHtml(lat)}</text>`;
    rowsSvg += `<text x="${sx}"        y="${by+22}" fill="#6b7280" font-size="9">${escHtml(tok)}</text>`;
    if (cstr) rowsSvg += `<text x="${sx+STATS_W-8}" y="${by+12}" text-anchor="end" fill="#86efac" font-size="10">${escHtml(cstr)}</text>`;
  }

  // ── Assemble SVG ──────────────────────────────────────────────────────────
  const defs = `<defs>
    <marker id="tc-arr" markerWidth="5" markerHeight="4" refX="4" refY="2" orient="auto">
      <path d="M0,0 L5,2 L0,4 Z" fill="#2a2a2a"/>
    </marker>
  </defs>`;

  svg.setAttribute('width',  SVG_W);
  svg.setAttribute('height', SVG_H);
  svg.innerHTML = defs + axisSvg + connSvg + rowsSvg;

  // Hover: brighten bar; click: jump to call detail
  svg.querySelectorAll('rect[data-trace-action-id]').forEach(el => {
    el.addEventListener('mouseenter', () => el.setAttribute('opacity', '1'));
    el.addEventListener('mouseleave', () => el.setAttribute('opacity', '0.82'));
    el.addEventListener('click', () => {
      switchTab('calls');
      scrollToAction(el.dataset.traceActionId);
    });
  });
}

let _agentFilter = null;

function filterByAgent(agentName) {
  switchTab('calls');
  _agentFilter = agentName;
  const cards = document.querySelectorAll('.call-card');
  let first = null;
  cards.forEach(card => {
    const match = card.dataset.agent === agentName;
    card.style.opacity = match ? '1' : '0.2';
    if (match) {
      card.classList.remove('collapsed'); // expand matched cards
      if (!first) first = card;
    }
  });
  if (first) first.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function clearAgentFilter() {
  _agentFilter = null;
  document.querySelectorAll('.call-card').forEach(c => c.style.opacity = '1');
}

// ── Collapsible call cards ────────────────────────────────────────────────────

function toggleCallCard(card) {
  card.classList.toggle('collapsed');
}

// ── Init ─────────────────────────────────────────────────────────────────────

loadSessions();
setInterval(loadSessions, 10000);
connectWS();
</script>
</body>
</html>"""


def get_dashboard_html() -> str:
    if _MASCOT_B64:
        img = f'<img src="data:image/jpeg;base64,{_MASCOT_B64}" style="height:36px;width:36px;object-fit:cover;border-radius:50%;flex-shrink:0;" alt="Agentic Ledger mascot">'
    else:
        img = ""
    return DASHBOARD_HTML.replace("{mascot_img}", img)
