// Draw laid-out boxes and arrows (the output of layout.js) as an ink-and-wash SVG string with rough.js's
// generator, passed in. Every shape's rough strokes are seeded from its box or arrow id, never its place in the
// list, so a redraw never wobbles and adding a box leaves the other shapes' strokes as they were. That is the seeds
// only: where each box sits is up to layout.js. Labels, a box's note under its label and an arrow's label beside
// it are plain escaped text, drawn last and crisp, each on a paper-coloured halo so an arrow under one cannot hide it.
// A marked box or arrow (see MARKS in shapes.js) takes its mark's hue, dash and fade, and its tag: a box's above
// its top right corner, an arrow's beside the middle of its line.
import { arrows } from "./arrows.js";
import { cued, cueOf, drawn, esc, INK, seedOf } from "./shapes.js";

const WASH = "rgba(170,158,135,0.20)";
const PAPER = "#F9F5EB"; // the page background in index.html
const filter = (id, freq, scale, seed, w, h) =>
  `<filter id="${id}" filterUnits="userSpaceOnUse" x="0" y="0" width="${w}" height="${h}">` +
  `<feTurbulence type="fractalNoise" baseFrequency="${freq}" numOctaves="2" seed="${seed}" result="n"/>` +
  `<feDisplacementMap in="SourceGraphic" in2="n" scale="${scale}" xChannelSelector="R" yChannelSelector="G"/></filter>`;

export function draw(laid, gen) {
  const { width: w, height: h } = laid;
  const washes = laid.boxes.map((b) => {
    const o = { fill: WASH, fillStyle: "solid", stroke: "none", roughness: 2.4, seed: seedOf(b.id) };
    return `<g data-wash="${esc(b.id)}"${cued(cueOf(b.mark))}>${drawn(gen, gen.rectangle(b.x - 4, b.y + 3, b.w + 6, b.h + 2, o))}</g>`;
  });
  const inks = laid.boxes.map((b) => {
    const cue = cueOf(b.mark);
    const o = { stroke: cue?.hue ?? INK, strokeWidth: b.level === 1 ? 2.8 : 2.2, roughness: 0.9, bowing: 0.7, seed: seedOf(b.id) + 1 };
    return `<g data-box="${esc(b.id)}"${cued(cue)}>${drawn(gen, gen.rectangle(b.x, b.y, b.w, b.h, o))}</g>`;
  });
  const text = (b, dy, size, s, fill = INK, x = b.x + b.w / 2, anchor = "middle") =>
    `<text x="${x}" y="${b.y + dy}" text-anchor="${anchor}" font-family="ui-monospace, Menlo, monospace" font-size="${size}" fill="${fill}" stroke="${PAPER}" stroke-width="4" stroke-linejoin="round" paint-order="stroke">${esc(s)}</text>`;
  const labels = laid.boxes.map((b) => text(b, 22, 13, b.label) + (b.note ? text(b, 40, 11, b.note) : ""));
  for (const { label } of laid.arrows) if (label) labels.push(text(label, 12, 11, label.text));
  const tag = (cue, x, y, anchor) => cue && labels.push(text({ x, y }, 0, 10, cue.tag, cue.hue, x, anchor));
  for (const b of laid.boxes) tag(cueOf(b.mark), b.x + b.w, b.y - 5, "end");
  for (const a of laid.arrows) {
    const i = Math.floor((a.points.length - 1) / 2);
    const [[x0, y0], [x1, y1]] = [a.points[i], a.points[i + 1]];
    tag(cueOf(a.mark), (x0 + x1) / 2 + 6, (y0 + y1) / 2 + 4, "start");
  }
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w + 20} ${h + 20}" width="${w + 20}" height="${h + 20}">` +
    `<defs>${filter("wash", 0.035, 12, 7, w, h)}${filter("ink", 0.08, 2.2, 3, w, h)}</defs>` +
    `<g filter="url(#wash)">${washes.join("")}</g><g filter="url(#ink)">${inks.join("")}${arrows(laid.arrows, gen)}</g>${labels.join("")}</svg>`
  );
}
