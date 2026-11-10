// Lanes: how a box outside a group is drawn between two boxes inside it, with every arrow still pointing down.
// ELK lays out each box's insides on their own, so a group is one row of the box that holds it. When run.py and
// report.py share a group, and results.json, outside it, is written by one and read by the other, the group and
// the file each come before the other, and ELK turns one arrow round to point up. Such a knot of two boxes is laid
// out here as two lanes side by side: a group is cut into parts, one for each run of its boxes that nothing in the
// other lane has to sit between, the parts are stacked in the order the arrows give, and the other lane's box sits
// beside the gap. A part is a box ELK sees and the page does not: layout.js draws a group as one box down its whole
// lane, so every box is still inside the group the plan puts it in and nothing else is.
// Takes a plan from prepare() and returns a copy to lay out in its place with what layout.js needs to draw it, or
// null for a plan with no such knot. `turned` stands each knot's two lanes the other way round. ELK still places
// every box and routes every arrow.
export const LANES = "\0lanes ";
const GAP = 16; // between two lanes: the width of the rail that keeps them apart
const EDGE = "[top=0,left=14,bottom=0,right=14]"; // round a knot's lanes, so an arrow never runs along a group's side
const trim = (padding, sides) => sides.reduce((p, side) => p.replace(new RegExp(`${side}=\\d+`), `${side}=0`), padding);

// The sets of ids that can each reach the other along `next` (a map from an id to the ids after it), two or more to a set.
function knots(next) {
  const reach = new Map();
  for (const start of next.keys()) {
    const seen = new Set();
    const go = (id) => next.get(id).forEach((to) => seen.has(to) || (seen.add(to), go(to)));
    go(start);
    reach.set(start, seen);
  }
  const [found, taken] = [[], new Set()];
  for (const id of next.keys()) {
    if (taken.has(id)) continue;
    const knot = [...next.keys()].filter((other) => other === id || (reach.get(id).has(other) && reach.get(other).has(id)));
    knot.forEach((k) => taken.add(k));
    if (knot.length > 1) found.push(knot);
  }
  return found;
}

export function lanes(prepared, turned = false) {
  const graph = structuredClone(prepared);
  const [hidden, parts, holder] = [new Set(), new Map(), new Map()];
  const index = (box) => box.children?.forEach((kid) => (holder.set(kid.id, box), index(kid)));
  index(graph);
  // The box straight inside `box` that `id` is, or is somewhere inside.
  const under = (box, id) => {
    let at = id;
    while (holder.has(at) && holder.get(at) !== box) at = holder.get(at).id;
    return holder.get(at) === box ? at : undefined;
  };
  // ELK orders the boxes of a row by the x of their "elk.position" when the box that holds them asks it to: the lane.
  const place = (node, lane) => Object.assign(node, { layoutOptions: { ...node.layoutOptions, "elk.position": `(${lane},0)` } });

  function wrap(box, knot) {
    const inKnot = box.children.filter((kid) => knot.includes(kid.id));
    const first = inKnot[0];
    if (turned) inKnot.reverse();
    // A unit is what a lane is cut between: a box straight inside a group, or a lane that holds nothing.
    const units = inKnot.flatMap((lane) => (lane.children?.length ? lane.children : [lane]).map((unit) => ({ unit, lane })));
    const ends = (id) => {
      const lane = inKnot.find((l) => l.id === under(box, id));
      if (!lane) return [];
      return units.filter((u) => u.lane === lane && (id === lane.id || u.unit.id === under(lane, id)));
    };
    // For each unit, the units an arrow brings it after, and how many rows on: an arrow's label takes a row of its own.
    const before = new Map(units.map((u) => [u, new Map()]));
    for (const { sources: [from], targets: [to], labels } of graph.edges) {
      for (const u of ends(from)) for (const v of ends(to)) {
        if (u !== v && !(u.lane === v.lane && [from, to].includes(u.lane.id))) before.get(v).set(u, Math.max(before.get(v).get(u) ?? 1, labels ? 2 : 1));
      }
    }
    // Cut the lanes into runs: go as far down one lane as the arrows allow, then turn to the other. Starting from each
    // lane in turn, the start that cuts the groups into the fewest parts is kept.
    const cutFrom = (start) => {
      const [runs, done] = [[], new Set()];
      const ready = () => units.filter((u) => !done.has(u) && [...before.get(u).keys()].every((b) => done.has(b)));
      while (done.size < units.length) {
        const open = ready().map((u) => u.lane);
        const lane = !runs.length && open.includes(start) ? start : open[0];
        if (!lane) return undefined; // the arrows go round in a ring, which no order can draw downwards
        const run = { lane, units: [] };
        for (let next; (next = ready().find((u) => u.lane === lane)); done.add(next)) run.units.push(next);
        const above = runs.flatMap((r) => run.units.flatMap((v) => r.units.map((u) => r.row + (before.get(v).get(u) ?? -Infinity))));
        runs.push({ ...run, row: Math.max(0, ...above) });
      }
      return runs;
    };
    const runs = inKnot.map(cutFrom).filter(Boolean).sort((a, b) => a.length - b.length)[0];
    if (!runs) return; // a ring: ELK's layout stands
    const wrapper = { id: `${LANES}${first.id}`, children: [], layoutOptions: { "elk.padding": EDGE, "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.layered.crossingMinimization.semiInteractive": "true" } };
    hidden.add(wrapper.id);
    const rows = Math.max(...runs.map((r) => r.row));
    const rail = (at) => (at >= 0 && at < inKnot.length - 1 ? `\0rail ${inKnot[at].id} ` : undefined);
    inKnot.forEach((lane, at) => {
      const mine = runs.filter((r) => r.lane === lane);
      const nodes = mine.map((run, n) => {
        if (mine.length === 1) return lane;
        const padding = trim(lane.layoutOptions["elk.padding"], ["left", "right", ...(n > 0 ? ["top"] : []), ...(n < mine.length - 1 ? ["bottom"] : [])]);
        const part = { id: `\0part ${lane.id} ${n}`, children: run.units.map((u) => u.unit), layoutOptions: { ...lane.layoutOptions, "elk.padding": padding } };
        hidden.add(part.id);
        parts.set(part.id, { group: lane, wrapper: wrapper.id, rails: [rail(at - 1), rail(at)].map((r) => r && `${r}0`) });
        part.children.forEach((kid) => holder.set(kid.id, part));
        return part;
      });
      nodes.forEach((node) => (holder.set(node.id, wrapper), wrapper.children.push(place(node, 2 * at))));
      // An arrow that names a group cut into parts comes in at its top and leaves from its foot; one from a box
      // inside the group stays in that box's part.
      for (const arrow of graph.edges) {
        if (arrow.targets[0] === lane.id) arrow.targets = [(nodes.find((node) => under(node, arrow.sources[0])) ?? nodes[0]).id];
        if (arrow.sources[0] === lane.id) arrow.sources = [nodes.at(-1).id];
      }
      // A rail: a hidden box in every row, joined in one straight line, with this lane kept to its left and the next
      // to its right.
      for (let row = 0; rail(at) && row <= rows; row++) {
        const id = `${rail(at)}${row}`;
        hidden.add(id);
        wrapper.children.push(place({ id, width: GAP, height: 1 }, 2 * at + 1));
        if (row > 0) (hidden.add(`\0line ${id}`), graph.edges.push({ id: `\0line ${id}`, sources: [`${rail(at)}${row - 1}`], targets: [id], layoutOptions: { "elk.layered.priority.straightness": "1000" } }));
      }
    });
    box.children = box.children.flatMap((kid) => (kid === first ? [wrapper] : knot.includes(kid.id) ? [] : [kid]));
    holder.set(wrapper.id, box);
  }

  const visit = (box) => {
    box.children?.forEach(visit);
    if (!box.children) return;
    const next = new Map(box.children.map((kid) => [kid.id, new Set()]));
    for (const { sources: [from], targets: [to] } of graph.edges) {
      const [a, b] = [under(box, from), under(box, to)];
      if (a && b && a !== b) next.get(a).add(b);
    }
    for (const knot of knots(next)) if (knot.length === 2) wrap(box, knot); // a bigger knot needs an order for its lanes, so ELK's layout stands
  };
  visit(graph);
  return hidden.size ? { graph, hidden, parts } : null;
}
