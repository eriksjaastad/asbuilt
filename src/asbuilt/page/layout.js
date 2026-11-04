// Lay out an already-parsed plan with ELK (passed in). Returns { width, height, boxes, arrows } in absolute
// coordinates; refusals come from prepare() and ELK. Read: id, labels[0].text and labels[1].text, width, height, children, edges
// (and an arrow's labels[0].text), and asbuilt.mark when an item has one (the mark compare writes).
import { ARROW_ROOM, noteOf, PADDING, prepare } from "./prepare.js";

const markOf = (item) => (item.asbuilt?.mark === undefined ? {} : { mark: item.asbuilt.mark });
const OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.hierarchyHandling": "INCLUDE_CHILDREN",
  // Lines boxes up along their arrows, so on real plans fewer arrows cross than with the default.
  "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
  "elk.padding": PADDING,
  ...ARROW_ROOM,
};
// A chart is at most a 1440 px laptop screen wide, less the 10 px draw.js leaves on each side, and grows downward.
export const MAX_WIDTH = 1420;
const NESTED = 28; // a box's padding, left and right, taken from the room inside it
const MAX_LAYOUTS = 40;
const [ROOM_STEP, MIN_ROOM] = [50, 600];

// The hidden arrows that push what runs past `room` down a layer. ELK has no limit on width, and unjoined boxes share a
// layer however many there are. Goes deepest first, since a part's width counts towards the row it sits in.
const folds = (box, room, tried) => {
  const kids = box.children ?? [];
  for (const kid of kids) {
    const found = folds(kid, room - NESTED, tried);
    if (found.length) return found;
  }
  if (box.width <= room) return [];
  const rows = [];
  for (const kid of [...kids].sort((a, b) => a.y - b.y)) {
    const row = rows.at(-1);
    if (row && kid.y < row.bottom) (row.kids.push(kid), (row.bottom = Math.max(row.bottom, kid.y + kid.height)));
    else rows.push({ kids: [kid], bottom: kid.y + kid.height });
  }
  for (const { kids: row } of rows) {
    const left = Math.min(...row.map((k) => k.x));
    const fits = row.filter((k) => k.x + k.width - left <= room).sort((a, b) => a.x - b.x);
    const over = row.filter((k) => !fits.includes(k));
    const more = fits.length ? over.map((k) => ({ id: `\0fold ${fits.at(-1).id} ${k.id}`, sources: [fits.at(-1).id], targets: [k.id] })).filter((e) => !tried.has(e.id)) : [];
    if (more.length) return more;
  }
  return [];
};

// ELK's placement staggers boxes to keep arrows straight, which can put two parts corner to corner; this one centres
// each layer, so a chart is as wide as its widest layer. Set on every box that holds others, or it holds only at the top.
const CENTRED = { "elk.layered.nodePlacement.strategy": "SIMPLE" };
const centre = (box) => {
  box.layoutOptions = { ...box.layoutOptions, ...CENTRED };
  (box.children ?? []).filter((c) => c.children?.length).forEach(centre);
};

async function narrow(prepared, elk, hidden) {
  let [out, room] = [null, MAX_WIDTH];
  for (let n = 0; n < MAX_LAYOUTS; n++) {
    const graph = structuredClone(prepared);
    centre(graph);
    out = await elk.layout({ ...graph, edges: [...(graph.edges ?? []), ...hidden.values()], layoutOptions: { ...OPTIONS, ...CENTRED } });
    if (out.width <= MAX_WIDTH) break;
    // Arrows running past the boxes take room of their own, so a chart can be too wide with every row inside `room`.
    let more = folds(out, room, hidden);
    while (!more.length && room > MIN_ROOM) more = folds(out, (room -= ROOM_STEP), hidden);
    if (!more.length) break;
    for (const e of more) hidden.set(e.id, e);
  }
  return out;
}

export async function layout(plan, elk) {
  const prepared = prepare(plan);
  const hidden = new Map();
  let out = await elk.layout({ ...structuredClone(prepared), layoutOptions: OPTIONS });
  if (out.width > MAX_WIDTH) out = await narrow(prepared, elk, hidden);
  const boxes = [];
  const origin = { [out.id]: [0, 0] };
  const walk = (box, level) => {
    for (const b of box.children ?? []) {
      const [x, y] = [origin[box.id][0] + b.x, origin[box.id][1] + b.y];
      origin[b.id] = [x, y];
      boxes.push({ id: b.id, x, y, w: b.width, h: b.height, level, label: b.labels?.[0]?.text ?? b.id, note: noteOf(b), ...markOf(b) });
      walk(b, level + 1);
    }
  };
  walk(out, 1);
  const lines = out.edges.filter((e) => !hidden.has(e.id)).map((e) => {
    const [s] = e.sections;
    const [dx, dy] = origin[e.container];
    const points = [s.startPoint, ...(s.bendPoints ?? []), s.endPoint].map((p) => [p.x + dx, p.y + dy]);
    const l = e.labels?.[0];
    const label = l && { x: l.x + dx, y: l.y + dy, w: l.width, text: l.text };
    return { id: e.id, points, label, ...markOf(e) };
  });
  return { width: out.width, height: out.height, boxes, arrows: lines };
}
