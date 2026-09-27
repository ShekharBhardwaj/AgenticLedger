import { useEffect, useRef, type CSSProperties, type KeyboardEvent } from "react";
import {
  Asterisk, Book, Braces, Brain, Briefcase, ChartColumn, CircleDashed, CircleDollarSign,
  Dumbbell, Earth, FlaskConical, Flower, Folder, Globe, GraduationCap, Heart, Music,
  Notebook, Palette, PawPrint, Pencil, PenTool, Plane, Popcorn, Scale, Sprout,
  SquareTerminal, Stethoscope, WandSparkles, Weight, Wrench, type LucideIcon,
} from "lucide-react";

/** Marks: an icon and a color a person pins on a run or a session so their
 *  loops tell apart at a glance. The two lists are the client half of a
 *  shared contract: agenticledger/proxy/marks.py holds the server half and
 *  tests/test_labels.py reads this file to assert the two never drift.
 *  Order matters (it is the picker's order), so keep the literals plain. */

export const LABEL_COLORS = [
  "gray", "red", "orange", "yellow", "green", "blue", "purple", "pink",
];

export const LABEL_ICONS = [
  "folder", "dollar", "book", "graduation-cap", "pencil", "pen-tool",
  "braces", "terminal", "music", "popcorn", "wand", "palette",
  "stethoscope", "asterisk", "flower", "briefcase", "chart", "kettlebell",
  "dumbbell", "notebook", "scale", "globe-stand", "plane", "globe",
  "wrench", "paw", "flask", "brain", "heart", "plant",
];

// Contract name -> lucide glyph. Named imports keep the bundle to these 31.
const GLYPHS: Record<string, LucideIcon> = {
  "folder": Folder,
  "dollar": CircleDollarSign,
  "book": Book,
  "graduation-cap": GraduationCap,
  "pencil": Pencil,
  "pen-tool": PenTool,
  "braces": Braces,
  "terminal": SquareTerminal,
  "music": Music,
  "popcorn": Popcorn,
  "wand": WandSparkles,
  "palette": Palette,
  "stethoscope": Stethoscope,
  "asterisk": Asterisk,
  "flower": Flower,
  "briefcase": Briefcase,
  "chart": ChartColumn,
  "kettlebell": Weight,
  "dumbbell": Dumbbell,
  "notebook": Notebook,
  "scale": Scale,
  "globe-stand": Earth,
  "plane": Plane,
  "globe": Globe,
  "wrench": Wrench,
  "paw": PawPrint,
  "flask": FlaskConical,
  "brain": Brain,
  "heart": Heart,
  "plant": Sprout,
};

const STROKE = 1.75;
// The picker grid's column count; .mark-grid in facelift.css lays it out.
const GRID_COLS = 6;

/** The CSS token behind a color name; unknown names fall back to gray. */
export function colorVar(name: string): string {
  return `var(--mark-${LABEL_COLORS.includes(name) ? name : "gray"})`;
}

export interface Mark { icon: string | null; color: string | null }

/** The mark as it rides a name: icon in its color, a colored dot when only
 *  a color is set, the icon in the text color when only an icon is set,
 *  nothing when neither. Decorative: the name beside it carries meaning. */
export function LabelMark({ icon, color, size = 16 }: {
  icon?: string | null; color?: string | null; size?: number;
}) {
  const Glyph = icon ? GLYPHS[icon] : undefined;
  const tint = color && LABEL_COLORS.includes(color) ? color : null;
  if (!Glyph && !tint) return null;
  return (
    <span className="label-mark" aria-hidden="true"
          data-icon={Glyph ? icon : undefined} data-color={tint ?? undefined}
          style={{ width: size, height: size, color: tint ? colorVar(tint) : undefined }}>
      {Glyph ? <Glyph size={size} strokeWidth={STROKE} /> : <i className="label-mark-dot" />}
    </span>
  );
}

/** The empty slot on the editor's "Add icon" button. */
export function MarkPlaceholder({ size = 16 }: { size?: number }) {
  return (
    <span className="label-mark" aria-hidden="true" style={{ width: size, height: size }}>
      <CircleDashed size={size} strokeWidth={STROKE} />
    </span>
  );
}

/** The picker: a row of color swatches (a radiogroup), a grid of icons
 *  (toggle buttons), Clear, Done. It renders in flow under its
 *  button, so it stays inside the card and never leaves a phone screen. */
export function IconPicker({ value, onChange, onClose }: {
  value: Mark; onChange: (next: Mark) => void; onClose: () => void;
}) {
  const swatches = useRef<HTMLDivElement>(null);
  const grid = useRef<HTMLDivElement>(null);
  // Land keyboard focus on the checked swatch (or the first) on open, so
  // Escape and the arrow keys work without a click.
  useEffect(() => {
    const checked = swatches.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]')
      ?? swatches.current?.querySelector<HTMLButtonElement>("button");
    checked?.focus();
  }, []);
  const pickColor = (c: string) => onChange({ ...value, color: value.color === c ? null : c });
  const pickIcon = (i: string) => onChange({ ...value, icon: value.icon === i ? null : i });
  // Roving focus for the radiogroup: arrows move and select in one step.
  const onSwatchKey = (e: KeyboardEvent, i: number) => {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1
      : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = (i + step + LABEL_COLORS.length) % LABEL_COLORS.length;
    onChange({ ...value, color: LABEL_COLORS[next] });
    swatches.current?.querySelectorAll<HTMLButtonElement>("button")[next]?.focus();
  };
  const tabStop = value.color && LABEL_COLORS.includes(value.color) ? value.color : LABEL_COLORS[0];
  // Roving focus for the grid too: one tab stop, arrows move by one or by
  // a row, Home/End jump to the ends. Moving only focuses; Enter and Space
  // toggle through the button itself, so an arrow never changes the pick.
  const onIconKey = (e: KeyboardEvent, i: number) => {
    const n = LABEL_ICONS.length;
    const next = e.key === "ArrowRight" ? (i + 1) % n
      : e.key === "ArrowLeft" ? (i - 1 + n) % n
      : e.key === "ArrowDown" ? (i + GRID_COLS < n ? i + GRID_COLS : i)
      : e.key === "ArrowUp" ? (i >= GRID_COLS ? i - GRID_COLS : i)
      : e.key === "Home" ? 0
      : e.key === "End" ? n - 1 : null;
    if (next === null) return;
    e.preventDefault();
    grid.current?.querySelectorAll<HTMLButtonElement>("button")[next]?.focus();
  };
  const iconStop = value.icon && LABEL_ICONS.includes(value.icon) ? value.icon : LABEL_ICONS[0];
  return (
    <div className="mark-picker" role="dialog" aria-label="Icon and color"
         onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } }}>
      <div className="mark-swatches" role="radiogroup" aria-label="Color" ref={swatches}>
        {LABEL_COLORS.map((c, i) => (
          <button key={c} type="button" role="radio" className="mark-swatch"
                  aria-label={c} aria-checked={value.color === c}
                  tabIndex={c === tabStop ? 0 : -1}
                  style={{ "--swatch": colorVar(c) } as CSSProperties}
                  onKeyDown={(e) => onSwatchKey(e, i)}
                  onClick={() => pickColor(c)} />
        ))}
      </div>
      <div className="mark-grid" ref={grid}
           style={{ color: value.color ? colorVar(value.color) : undefined }}>
        {LABEL_ICONS.map((name, i) => {
          const Glyph = GLYPHS[name];
          return (
            <button key={name} type="button" className="mark-icon"
                    aria-label={name.replace(/-/g, " ")} aria-pressed={value.icon === name}
                    tabIndex={name === iconStop ? 0 : -1}
                    onKeyDown={(e) => onIconKey(e, i)}
                    onClick={() => pickIcon(name)}>
              <Glyph size={18} strokeWidth={STROKE} aria-hidden="true" />
            </button>
          );
        })}
      </div>
      <div className="mark-foot">
        <button type="button" className="mark-clear"
                disabled={!value.icon && !value.color}
                onClick={() => onChange({ icon: null, color: null })}>
          Clear
        </button>
        <button type="button" className="link-btn" onClick={onClose}>Done</button>
      </div>
    </div>
  );
}
