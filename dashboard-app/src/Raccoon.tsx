import type { Run } from "./api";

/** The bookkeeper's face: one drawing for the logo, the favicon, and the
 *  Loop Lens mascot, so the product wears a single face everywhere.
 *  Geometry lives in a 24×22 viewBox; callers wrap it in their own <svg>. */
export type RaccoonMood = Run["status"] | "logo";

export function RaccoonHead({ mood }: { mood: RaccoonMood }) {
  const eyes =
    mood === "ended" ? (
      // asleep: gentle closed lids
      <g className="rac-eyes" stroke="var(--bg-panel)" strokeWidth="1.1"
         strokeLinecap="round" fill="none">
        <path d="M6.9,11.9 q1.2,0.9 2.4,0" />
        <path d="M14.7,11.9 q1.2,0.9 2.4,0" />
      </g>
    ) : mood === "complete" ? (
      // content: happy upward arcs
      <g className="rac-eyes" stroke="#fff" strokeWidth="1.1"
         strokeLinecap="round" fill="none">
        <path d="M6.9,12.1 q1.2,-1.1 2.4,0" />
        <path d="M14.7,12.1 q1.2,-1.1 2.4,0" />
      </g>
    ) : mood === "stopped" ? (
      // playing dead: little x eyes
      <g className="rac-eyes" stroke="#fff" strokeWidth="0.95"
         strokeLinecap="round" fill="none">
        <path d="M7.2,10.9 l1.8,1.8 M9,10.9 l-1.8,1.8" />
        <path d="M15,10.9 l1.8,1.8 M16.8,10.9 l-1.8,1.8" />
      </g>
    ) : (
      // awake (running / flagged / logo): round eyes catching the light
      <g className="rac-eyes" fill="#fff">
        <circle cx="8.1" cy="11.8" r="1.25" />
        <circle cx="15.9" cy="11.8" r="1.25" />
        <circle cx="8.45" cy="11.45" r="0.4" fill="var(--bg-panel)" />
        <circle cx="16.25" cy="11.45" r="0.4" fill="var(--bg-panel)" />
      </g>
    );
  // The logo wears the brighter body tone so it reads against the top bar;
  // the mascot keeps the muted tile tone. Same shapes either way.
  const body = mood === "logo" ? "var(--text)" : "var(--text-dim)";
  return (
    <g className="rac-head">
      {/* ears, with darker inner ear */}
      <path d="M4.2,8.2 L6.2,2.6 L10.4,5.9 Z" fill={body} />
      <path d="M19.8,8.2 L17.8,2.6 L13.6,5.9 Z" fill={body} />
      <path d="M5.6,7.2 L6.6,4.4 L8.7,6.1 Z" fill="var(--bg-panel)" />
      <path d="M18.4,7.2 L17.4,4.4 L15.3,6.1 Z" fill="var(--bg-panel)" />
      {/* head */}
      <ellipse cx="12" cy="13" rx="8.6" ry="7.6" fill={body} />
      {/* the mask */}
      <path d="M3.6,11.4 Q7,8.6 12,9.6 Q17,8.6 20.4,11.4
               Q19.6,15.2 15.6,14.4 Q12,13.6 8.4,14.4 Q4.4,15.2 3.6,11.4 Z"
            fill="var(--bg-panel)" opacity="0.92" />
      {eyes}
      {/* snout */}
      <ellipse cx="12" cy="17.1" rx="3.7" ry="2.7" fill="var(--text)" opacity="0.9" />
      <ellipse cx="12" cy="15.9" rx="1.35" ry="1.05" fill="var(--bg-panel)" />
    </g>
  );
}
