// Arrows (from layout.js) as rough.js lines through their bend points, with a head at the target end.
import { cued, cueOf, drawn, esc, INK, seedOf } from "./shapes.js";

export const arrows = (list, gen) =>
  list.map((a) => {
    const cue = cueOf(a.mark);
    const o = { stroke: cue?.hue ?? INK, strokeWidth: 1.4, roughness: 1, bowing: 1.2, seed: seedOf(a.id) };
    const [[x0, y0], [x1, y1]] = [a.points.at(-2), a.points.at(-1)];
    const turn = Math.atan2(y1 - y0, x1 - x0);
    const barb = (d) => [x1 - 10 * Math.cos(turn + d), y1 - 10 * Math.sin(turn + d)];
    const head = gen.linearPath([barb(0.45), [x1, y1], barb(-0.45)], { ...o, seed: o.seed + 1 });
    return `<g data-arrow="${esc(a.id)}"${cued(cue)}>${drawn(gen, gen.linearPath(a.points, o))}${drawn(gen, head)}</g>`;
  }).join("");
