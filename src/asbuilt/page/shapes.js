// What drawing boxes and arrows share: escaping, seeding from an id, and turning a rough.js drawable into SVG paths.
export const INK = "#2F2E2C";
// How a mark from python -m asbuilt.compare is drawn. Each has a tag, and dropped and changed a dash, so a mark
// never rests on colour alone; the hue is a muted Okabe-Ito one, and a dropped item is faded. "planned", no mark
// or a mark not in this table draws as it always has.
export const MARKS = {
  added: { tag: "new", hue: "#2E8B6E", dash: "", fade: 1 },
  dropped: { tag: "dropped", hue: "#B8622C", dash: "9 6", fade: 0.5 },
  changed: { tag: "changed", hue: "#33739F", dash: "2 5", fade: 1 },
};
export const cueOf = (mark) => (typeof mark === "string" && Object.hasOwn(MARKS, mark) ? MARKS[mark] : undefined);
// The attributes a marked group carries; its paths set no dash or opacity of their own, so they take these.
export const cued = (cue) => (cue?.dash ? ` stroke-dasharray="${cue.dash}"` : "") + (cue && cue.fade < 1 ? ` opacity="${cue.fade}"` : "");
// Text and attribute values survive the browser's parsing exactly: the markup characters, and the ones a parser
// rewrites (a carriage return becomes a line feed, a tab or line feed in an attribute becomes a space), are written
// as numeric references. Characters no XML text can hold (controls, lone surrogates, U+FFFE, U+FFFF) show as U+FFFD.
export const esc = (s) =>
  String(s)
    .replace(/[\0-\b\v\f\x0E-\x1F\p{Cs}\uFFFE\uFFFF]/gu, "\uFFFD")
    .replace(/[&<>"'\t\n\r]/g, (c) => `&#${c.charCodeAt(0)};`);
// FNV-1a over the id's characters, so neighbouring ids get unrelated seeds. rough.js reads seed 0 as "pick a
// random one", so a seed is never 0.
const fnv = (id) => [...id].reduce((h, c) => Math.imul(h ^ c.codePointAt(0), 16777619), 2166136261);
export const seedOf = (id) => (fnv(id) >>> 1) + 1;
export const drawn = (gen, drawable) =>
  gen.toPaths(drawable).map((p) => `<path d="${p.d}" fill="${p.fill}" stroke="${p.stroke}" stroke-width="${p.strokeWidth}"/>`).join("");
