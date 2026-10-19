// Lay out an already-parsed plan with ELK (passed in). Returns { width, height, boxes, arrows } in absolute
// coordinates; refusals come from prepare() and ELK. Read: id, labels[0].text, width, height, children, edges.
import { PADDING, prepare } from "./prepare.js";

const OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.hierarchyHandling": "INCLUDE_CHILDREN",
  "elk.padding": PADDING,
};
export async function layout(plan, elk) {
  const out = await elk.layout({ ...prepare(plan), layoutOptions: OPTIONS });
  const boxes = [];
  const origin = { [out.id]: [0, 0] };
  const walk = (box, level) => {
    for (const b of box.children ?? []) {
      const [x, y] = [origin[box.id][0] + b.x, origin[box.id][1] + b.y];
      origin[b.id] = [x, y];
      boxes.push({ id: b.id, x, y, w: b.width, h: b.height, level, label: b.labels?.[0]?.text ?? b.id });
      walk(b, level + 1);
    }
  };
  walk(out, 1);
  const lines = out.edges.map((e) => {
    const [s] = e.sections;
    const [dx, dy] = origin[e.container];
    const points = [s.startPoint, ...(s.bendPoints ?? []), s.endPoint].map((p) => [p.x + dx, p.y + dy]);
    return { id: e.id, from: e.sources[0], to: e.targets[0], points };
  });
  return { width: out.width, height: out.height, boxes, arrows: lines };
}
