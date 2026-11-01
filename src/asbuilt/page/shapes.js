// What drawing boxes and arrows share: escaping, seeding from an id, and turning a rough.js drawable into SVG paths.
export const INK = "#2F2E2C";
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
