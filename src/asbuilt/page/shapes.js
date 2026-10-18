// What drawing boxes and arrows share: escaping, seeding from an id, and turning a rough.js drawable into SVG paths.
export const INK = "#2F2E2C";
export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
// FNV-1a over the id's characters, so neighbouring ids get unrelated seeds. rough.js reads seed 0 as "pick a
// random one", so a seed is never 0.
const fnv = (id) => [...id].reduce((h, c) => Math.imul(h ^ c.codePointAt(0), 16777619), 2166136261);
export const seedOf = (id) => (fnv(id) >>> 1) + 1;
export const drawn = (gen, drawable) =>
  gen.toPaths(drawable).map((p) => `<path d="${p.d}" fill="${p.fill}" stroke="${p.stroke}" stroke-width="${p.strokeWidth}"/>`).join("");
