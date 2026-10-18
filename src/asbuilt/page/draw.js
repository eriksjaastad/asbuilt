// Draw laid-out boxes and arrows (the output of layout.js) as an ink-and-wash SVG string (D-006) with rough.js's
// generator, passed in. Every shape is seeded from its box or arrow id, never its place in the list, so a redraw
// never wobbles and adding a box does not move the others. Labels are plain escaped text, drawn last and crisp.
import { drawn, esc, INK, seedOf } from "./shapes.js";

const WASH = "rgba(170,158,135,0.20)";
const filter = (id, freq, scale, seed, w, h) =>
  `<filter id="${id}" filterUnits="userSpaceOnUse" x="0" y="0" width="${w}" height="${h}">` +
  `<feTurbulence type="fractalNoise" baseFrequency="${freq}" numOctaves="2" seed="${seed}" result="n"/>` +
  `<feDisplacementMap in="SourceGraphic" in2="n" scale="${scale}" xChannelSelector="R" yChannelSelector="G"/></filter>`;

export function draw(laid, gen) {
  const { width: w, height: h } = laid;
  const washes = laid.boxes.map((b) => {
    const o = { fill: WASH, fillStyle: "solid", stroke: "none", roughness: 2.4, seed: seedOf(b.id) };
    return `<g data-wash="${esc(b.id)}">${drawn(gen, gen.rectangle(b.x - 4, b.y + 3, b.w + 6, b.h + 2, o))}</g>`;
  });
  const inks = laid.boxes.map((b) => {
    const o = { stroke: INK, strokeWidth: b.level === 1 ? 2.8 : 2.2, roughness: 0.9, bowing: 0.7, seed: seedOf(b.id) + 1 };
    return `<g data-box="${esc(b.id)}">${drawn(gen, gen.rectangle(b.x, b.y, b.w, b.h, o))}</g>`;
  });
  const labels = laid.boxes.map(
    (b) => `<text x="${b.x + b.w / 2}" y="${b.y + 22}" text-anchor="middle" font-family="ui-monospace, Menlo, monospace" font-size="13" fill="${INK}">${esc(b.label)}</text>`,
  );
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w + 20} ${h + 20}" width="${w + 20}" height="${h + 20}">` +
    `<defs>${filter("wash", 0.035, 12, 7, w, h)}${filter("ink", 0.08, 2.2, 3, w, h)}</defs>` +
    `<g filter="url(#wash)">${washes.join("")}</g><g filter="url(#ink)">${inks.join("")}</g>${labels.join("")}</svg>`
  );
}
