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
export async function layout(plan, elk) {
  const out = await elk.layout({ ...prepare(plan), layoutOptions: OPTIONS });
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
  const lines = out.edges.map((e) => {
    const [s] = e.sections;
    const [dx, dy] = origin[e.container];
    const points = [s.startPoint, ...(s.bendPoints ?? []), s.endPoint].map((p) => [p.x + dx, p.y + dy]);
    const l = e.labels?.[0];
    const label = l && { x: l.x + dx, y: l.y + dy, w: l.width, text: l.text };
    return { id: e.id, points, label, ...markOf(e) };
  });
  return { width: out.width, height: out.height, boxes, arrows: lines };
}
