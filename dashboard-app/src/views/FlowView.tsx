import { Call, fmtUsd } from "../api";
import { agentColor } from "./TraceView";

interface Node {
  name: string;
  calls: number;
  cost: number;
  latency: number;
  layer: number;
  slot: number;
}

interface Edge {
  from: string;
  to: string;
  count: number;
  back: boolean;
  inferred: boolean;
}

/** Agent-level flow DAG built from handoff metadata: nodes aggregate cost,
 *  calls, and latency per agent; cycles render as dashed back-edges with a
 *  traversal count. */
export default function FlowView({ calls }: { calls: Call[] }) {
  const nodes = new Map<string, Node>();
  const edgeCount = new Map<string, number>();

  for (const c of calls) {
    const name = c.agent_name ?? "unattributed";
    const node = nodes.get(name) ?? { name, calls: 0, cost: 0, latency: 0, layer: 0, slot: 0 };
    node.calls += 1;
    node.cost += c.cost_usd ?? 0;
    node.latency += c.latency_ms ?? 0;
    nodes.set(name, node);
    if (c.handoff_from && c.handoff_to && c.handoff_from !== c.handoff_to) {
      for (const end of [c.handoff_from, c.handoff_to]) {
        if (!nodes.has(end)) nodes.set(end, { name: end, calls: 0, cost: 0, latency: 0, layer: 0, slot: 0 });
      }
      const key = `${c.handoff_from}→${c.handoff_to}`;
      edgeCount.set(key, (edgeCount.get(key) ?? 0) + 1);
    }
  }

  // Inferred handoffs: explicit handoff metadata is rare — most agent
  // changes are detected (claude-code invokes a BMAD skill and the next
  // call is bmad:spec). One conversation must not render as islands, so a
  // change of agent between consecutive calls becomes a dotted edge —
  // BUT only within one thread of conversation. Two independent threads
  // merely interleaving in time is not a handoff, and drawing one would
  // be fiction; when both calls carry thread ids, they must match.
  const inferredKeys = new Set<string>();
  for (let i = 1; i < calls.length; i++) {
    const a = calls[i - 1];
    const b = calls[i];
    const prev = a.agent_name ?? "unattributed";
    const cur = b.agent_name ?? "unattributed";
    if (prev === cur) continue;
    if (a.thread_id && b.thread_id && a.thread_id !== b.thread_id) continue;
    const key = `${prev}→${cur}`;
    if (!edgeCount.has(key)) {
      inferredKeys.add(key);
      edgeCount.set(key, 1);
    } else if (inferredKeys.has(key)) {
      edgeCount.set(key, (edgeCount.get(key) ?? 0) + 1);
    }
  }
  if (nodes.size === 0) return <div className="empty">No calls in this session.</div>;

  const adj = new Map<string, string[]>();
  for (const key of edgeCount.keys()) {
    const [from, to] = key.split("→");
    adj.set(from, [...(adj.get(from) ?? []), to]);
  }

  // Cycle detection (DFS): edges that would close a cycle become back-edges.
  const back = new Set<string>();
  const state = new Map<string, 1 | 2>(); // 1 = on stack, 2 = done
  const dfs = (n: string) => {
    state.set(n, 1);
    for (const m of adj.get(n) ?? []) {
      if (state.get(m) === 1) back.add(`${n}→${m}`);
      else if (!state.has(m)) dfs(m);
    }
    state.set(n, 2);
  };
  for (const n of nodes.keys()) if (!state.has(n)) dfs(n);

  // Layering (BFS over forward edges from the roots).
  const hasIncoming = new Set(
    [...edgeCount.keys()].filter((k) => !back.has(k)).map((k) => k.split("→")[1]),
  );
  const roots = [...nodes.keys()].filter((n) => !hasIncoming.has(n));
  const queue = [...(roots.length ? roots : [nodes.keys().next().value as string])];
  const seen = new Set(queue);
  while (queue.length) {
    const n = queue.shift()!;
    for (const m of adj.get(n) ?? []) {
      if (back.has(`${n}→${m}`)) continue;
      const cand = nodes.get(n)!.layer + 1;
      if (cand > nodes.get(m)!.layer) nodes.get(m)!.layer = cand;
      if (!seen.has(m)) {
        seen.add(m);
        queue.push(m);
      }
    }
  }
  // Anything unreachable (isolated agents) sits on layer 0.
  const byLayer = new Map<number, Node[]>();
  for (const n of nodes.values()) {
    n.slot = (byLayer.get(n.layer) ?? []).length;
    byLayer.set(n.layer, [...(byLayer.get(n.layer) ?? []), n]);
  }

  const BOX_W = 158, BOX_H = 60, GAP_X = 210, GAP_Y = 84, PAD = 24;
  const px = (n: Node) => PAD + n.layer * GAP_X;
  const py = (n: Node) => PAD + n.slot * GAP_Y;
  const W = PAD * 2 + (Math.max(...[...nodes.values()].map((n) => n.layer)) + 1) * GAP_X - (GAP_X - BOX_W);
  const H = PAD * 2 + Math.max(...[...byLayer.values()].map((l) => l.length)) * GAP_Y - (GAP_Y - BOX_H);

  const edges: Edge[] = [...edgeCount.entries()].map(([key, count]) => {
    const [from, to] = key.split("→");
    return { from, to, count, back: back.has(key), inferred: inferredKeys.has(key) };
  });

  return (
    <div className="svg-scroll">
      <svg viewBox={`0 0 ${Math.max(W, 400)} ${Math.max(H, 120)}`} width="100%" style={{ minWidth: 500, maxWidth: W * 1.4 }}>
        <defs>
          <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0 0 L8 4 L0 8 Z" fill="var(--text-dim)" />
          </marker>
        </defs>

        {edges.map((e) => {
          const a = nodes.get(e.from)!;
          const b = nodes.get(e.to)!;
          if (e.back) {
            const x1 = px(a);
            const x2 = px(b) + BOX_W;
            const dip = Math.max(py(a), py(b)) + BOX_H + 26;
            return (
              <g key={`${e.from}→${e.to}`}>
                <title>{e.inferred
                  ? `${e.from} → ${e.to} — cycles back; inferred from call order within a thread`
                  : `${e.from} → ${e.to} — cycles back (explicit handoff)`}</title>
                <path
                  d={`M ${x1 + BOX_W / 2} ${py(a) + BOX_H} C ${x1 + BOX_W / 2} ${dip}, ${x2 - BOX_W / 2} ${dip}, ${x2 - BOX_W / 2} ${py(b) + BOX_H}`}
                  stroke="var(--amber)" strokeWidth="1.4" strokeDasharray="5 4" fill="none" markerEnd="url(#arrow)"
                />
                <text x={(x1 + x2) / 2} y={dip - 4} fill="var(--amber)" fontSize="10" textAnchor="middle" fontFamily="var(--mono)">
                  ↩ {e.count}×{e.inferred ? " ·inferred" : ""}
                </text>
              </g>
            );
          }
          return (
            <g key={`${e.from}→${e.to}`}>
              <title>{e.inferred
                ? `${e.from} → ${e.to} — inferred from call order (the agent changed between consecutive calls)`
                : `${e.from} → ${e.to} — explicit handoff`}</title>
              <line
                x1={px(a) + BOX_W} y1={py(a) + BOX_H / 2}
                x2={px(b) - 3} y2={py(b) + BOX_H / 2}
                stroke="var(--text-dim)" strokeWidth="1.4"
                strokeDasharray={e.inferred ? "2 4" : undefined}
                markerEnd="url(#arrow)"
              />
              {e.count > 1 && (
                <text
                  x={(px(a) + BOX_W + px(b)) / 2} y={(py(a) + py(b)) / 2 + BOX_H / 2 - 6}
                  fill="var(--text-dim)" fontSize="10" textAnchor="middle" fontFamily="var(--mono)"
                >
                  {e.count}×
                </text>
              )}
            </g>
          );
        })}

        {[...nodes.values()].map((n) => (
          <g key={n.name}>
            <rect
              x={px(n)} y={py(n)} width={BOX_W} height={BOX_H} rx={10}
              fill="var(--bg-card)" stroke={agentColor(n.name)} strokeWidth="1.6"
            />
            <circle cx={px(n) + 14} cy={py(n) + 16} r={4} fill={agentColor(n.name)} />
            <text x={px(n) + 24} y={py(n) + 20} fill="var(--text)" fontSize="12" fontWeight="600" fontFamily="var(--mono)">
              {n.name.length > 15 ? n.name.slice(0, 14) + "…" : n.name}
            </text>
            <text x={px(n) + 14} y={py(n) + 40} fill="var(--text-dim)" fontSize="10" fontFamily="var(--mono)">
              {n.calls} calls · {fmtUsd(n.cost)}
            </text>
            <text x={px(n) + 14} y={py(n) + 52} fill="var(--text-dim)" fontSize="10" fontFamily="var(--mono)">
              avg {n.calls ? Math.round(n.latency / n.calls) : 0}ms
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}
