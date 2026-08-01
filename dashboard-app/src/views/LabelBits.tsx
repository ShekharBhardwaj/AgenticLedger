import { useState } from "react";
import { setLabel } from "../api";

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

/** Filter dropdown shown only once projects exist. */
export function ProjectFilter({ projects, value, onChange }: {
  projects: string[]; value: string; onChange: (v: string) => void;
}) {
  if (projects.length === 0) return null;
  return (
    <select
      className="project-filter"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      title="Show only one project's work"
    >
      <option value="">all projects</option>
      {projects.map((p) => <option key={p} value={p}>{p}</option>)}
    </select>
  );
}

/** Pinned first, otherwise keep the incoming (recency) order. */
export function pinnedFirst<T extends { pinned: boolean }>(rows: T[]): T[] {
  return [...rows].sort((a, b) => Number(b.pinned) - Number(a.pinned));
}
