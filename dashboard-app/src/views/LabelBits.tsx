import { useState } from "react";
import { createProject, deleteProject, renameProject, setLabel } from "../api";

/** #47 — shared label controls for session and run cards: a ★ pin that
 *  keeps things findable, and a ✎ editor for the human name + project.
 *  Ids stay stable underneath; the label is just how humans refer to it. */

export function PinButton({ scope, refId, pinned, onSaved }: {
  scope: "session" | "run"; refId: string; pinned: boolean; onSaved: () => void;
}) {
  return (
    <button
      className={`card-pin ${pinned ? "on" : ""}`}
      title={pinned ? "Unpin" : "Pin — keeps it at the top of the list"}
      onClick={(e) => {
        e.stopPropagation();
        setLabel(scope, refId, { pinned: !pinned }).then(onSaved).catch(() => {});
      }}
    >
      {pinned ? "★" : "☆"}
    </button>
  );
}

export function LabelEditor({ scope, refId, label, project, projects, onSaved, onClose }: {
  scope: "session" | "run"; refId: string;
  label: string | null; project: string | null;
  projects: string[]; onSaved: () => void; onClose: () => void;
}) {
  const [name, setName] = useState(label ?? "");
  const [proj, setProj] = useState(project ?? "");
  const save = () => {
    setLabel(scope, refId, { name, project: proj })
      .then(() => { onSaved(); onClose(); })
      .catch(() => {});
  };
  return (
    <div className="label-edit" onClick={(e) => e.stopPropagation()}>
      <input
        autoFocus
        placeholder={`name this ${scope}…`}
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") onClose(); }}
      />
      <input
        placeholder="project (optional)"
        list="al-projects"
        value={proj}
        onChange={(e) => setProj(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") onClose(); }}
      />
      <datalist id="al-projects">
        {projects.map((p) => <option key={p} value={p} />)}
      </datalist>
      <div className="key-actions">
        <button className="link-btn" onClick={save}>Save</button>
        <button className="link-btn" onClick={onClose}>Cancel</button>
      </div>
    </div>
  );
}

export const STARRED = "__starred__";

/** Filter dropdown (two built-in views, then the user's projects) plus a
 *  "+ project" creator: name it now, optionally bind it to an app id so
 *  matching work — past and future — files itself. */
export function ProjectFilter({ projects, value, onChange, hasPinned, knownApps, onCreated, sessionCount }: {
  projects: string[]; value: string; onChange: (v: string) => void;
  hasPinned?: boolean; knownApps?: string[]; onCreated?: () => void;
  sessionCount?: number;   // how many items the current project filter shows
}) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [appId, setAppId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [managing, setManaging] = useState<"rename" | "delete" | null>(null);
  const [newName, setNewName] = useState("");
  const isProject = value !== "" && value !== STARRED;

  const doRename = () => {
    renameProject(value, newName.trim())
      .then(() => { setManaging(null); onChange(newName.trim()); onCreated?.(); })
      .catch((e) => setError(e.message));
  };
  const doDelete = (purge: boolean) => {
    deleteProject(value, purge)
      .then(() => { setManaging(null); onChange(""); onCreated?.(); })
      .catch((e) => setError(e.message));
  };

  const save = () => {
    createProject(name.trim(), appId.trim() || undefined)
      .then(() => {
        setCreating(false); setName(""); setAppId(""); setError(null);
        onCreated?.();
      })
      .catch((e) => setError(e.message));
  };

  return (
    <div className="project-filter-row">
      {(projects.length > 0 || hasPinned) && (
        <select
          className="project-filter"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          title="Narrow the list: everything, everything starred, or one project"
        >
          <option value="">all projects</option>
          <option value={STARRED}>★ all starred</option>
          {projects.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      )}
      {isProject && !creating && !managing && (
        <>
          <button className="link-btn" style={{ marginTop: 0 }} title="Rename this project everywhere"
                  onClick={() => { setNewName(value); setManaging("rename"); setError(null); }}>
            ✎
          </button>
          <button className="link-btn" style={{ marginTop: 0 }} title="Delete this project"
                  onClick={() => { setManaging("delete"); setError(null); }}>
            ×
          </button>
        </>
      )}
      {managing === "rename" && (
        <div className="label-edit" style={{ width: "100%" }}>
          <input autoFocus value={newName} onChange={(e) => setNewName(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter" && newName.trim()) doRename();
                                     if (e.key === "Escape") setManaging(null); }} />
          {error && <div className="key-status warn">{error}</div>}
          <div className="key-actions">
            <button className="link-btn" disabled={!newName.trim()} onClick={doRename}>Rename</button>
            <button className="link-btn" onClick={() => setManaging(null)}>Cancel</button>
          </div>
        </div>
      )}
      {managing === "delete" && (
        <div className="label-edit" style={{ width: "100%" }}>
          <div className="muted" style={{ fontSize: 12.5 }}>
            Delete “{value}”? Two very different things:
          </div>
          <button className="link-btn" onClick={() => doDelete(false)}>
            remove project — its {sessionCount ?? "…"} sessions survive, unfiled
          </button>
          <button className="link-btn project-purge" onClick={() => doDelete(true)}>
            ⚠ delete project AND its {sessionCount ?? "…"} sessions — calls and all, permanently
          </button>
          {error && <div className="key-status warn">{error}</div>}
          <div className="key-actions">
            <button className="link-btn" onClick={() => setManaging(null)}>Cancel</button>
          </div>
        </div>
      )}
      {!creating ? (
        <button className="link-btn project-new" style={{ marginTop: 0 }}
                title="Create a project — optionally bound to an app id so its sessions file themselves"
                onClick={() => setCreating(true)}>
          + project
        </button>
      ) : (
        <div className="label-edit" style={{ width: "100%" }}>
          <input autoFocus placeholder="project name…" value={name}
                 onChange={(e) => setName(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) save();
                                     if (e.key === "Escape") setCreating(false); }} />
          <input placeholder="auto-file app id (optional)" value={appId}
                 list="al-known-apps"
                 title="Sessions and runs carrying this app id file themselves under the project — including ones already captured"
                 onChange={(e) => setAppId(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) save();
                                     if (e.key === "Escape") setCreating(false); }} />
          <datalist id="al-known-apps">
            {(knownApps ?? []).map((a) => <option key={a} value={a} />)}
          </datalist>
          {error && <div className="key-status warn">{error}</div>}
          <div className="key-actions">
            <button className="link-btn" disabled={!name.trim()} onClick={save}>Create</button>
            <button className="link-btn" onClick={() => setCreating(false)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Does a row belong in the current filter view? */
export function matchesFilter<T extends { pinned: boolean; project: string | null }>(
  row: T, filter: string,
): boolean {
  if (!filter) return true;
  if (filter === STARRED) return row.pinned;
  return row.project === filter;
}

/** Pinned first, otherwise keep the incoming (recency) order. */
export function pinnedFirst<T extends { pinned: boolean }>(rows: T[]): T[] {
  return [...rows].sort((a, b) => Number(b.pinned) - Number(a.pinned));
}
