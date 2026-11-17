// Lay out an already-parsed plan with ELK (passed in). Returns { width, height, boxes, arrows } in absolute
// coordinates; refusals come from prepare() and ELK. Read: id, labels[0].text and labels[1].text, width, height, children, edges
// (and an arrow's labels[0].text), and asbuilt.mark when an item has one (the mark compare writes).
import { LANES, lanes } from "./lanes.js";
import { ARROW_ROOM, noteOf, PADDING, prepare } from "./prepare.js";

const markOf = (item) => (item.asbuilt?.mark === undefined ? {} : { mark: item.asbuilt.mark });
const badgesOf = (item) => (Array.isArray(item.asbuilt?.badges) ? { badges: item.asbuilt.badges } : {});
const OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.hierarchyHandling": "INCLUDE_CHILDREN",
  // Lines boxes up along their arrows, so on real plans fewer arrows cross than with the default.
  "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
  "elk.padding": PADDING,
  ...ARROW_ROOM,
};
// Target a laptop's width; draw.js adds room for ink, and the page scales any overflow to its viewport.
export const MAX_WIDTH = 1420;
const NESTED = 28; // a box's padding, left and right, taken from the room inside it
const MAX_LAYOUTS = 40;
const [ROOM_STEP, MIN_ROOM] = [100, 300];

// The hidden arrows that push what runs past `room` down a layer. ELK has no limit on width, and unjoined boxes share a
// layer however many there are. Goes deepest first, since a part's width counts towards the row it sits in.
// `all` folds every wide box in this pass, not only the deepest: one file a little over its room was holding the
// chart at the same width while the folder around it was the part that was actually too wide.
const folds = (box, room, tried, limit, all) => {
  const kids = box.children ?? [];
  // Lanes stand side by side and cannot fold under each other: each gives up its share of what they run over by.
  const abreast = box.id.startsWith(LANES);
  const found = [];
  for (const kid of kids) {
    const inner = folds(kid, abreast ? kid.width * Math.min(1, room / box.width) : room - NESTED, tried, limit, all);
    if (!inner.length) continue;
    if (!all) return inner;
    found.push(...inner);
  }
  if (box.width <= room || abreast) return found;
  const rows = [];
  for (const kid of [...kids].sort((a, b) => a.y - b.y)) {
    const row = rows.at(-1);
    if (row && kid.y < row.bottom) (row.kids.push(kid), (row.bottom = Math.max(row.bottom, kid.y + kid.height)));
    else rows.push({ kids: [kid], bottom: kid.y + kid.height });
  }
  const more = [];
  for (const { kids: row } of rows) {
    const left = Math.min(...row.map((k) => k.x));
    const fits = row.filter((k) => k.x + k.width - left <= room).sort((a, b) => a.x - b.x);
    const over = row.filter((k) => !fits.includes(k));
    if (fits.length) more.push(...over.map((k) => ({ id: `\0fold ${fits.at(-1).id} ${k.id}`, sources: [fits.at(-1).id], targets: [k.id] })).filter((e) => !tried.has(e.id)));
    if (more.length >= limit) break;
  }
  return found.concat(more);
};

// How wide one box would be if its own leaves sat on a single row. Nested boxes count through their widest child.
const spread = (box) => {
  const kids = box.children ?? [];
  const across = kids.reduce((sum, kid) => sum + (kid.children?.length ? 0 : kid.width), 0);
  return Math.max(across, ...kids.map(spread), 0);
};

// How a chart too wide is laid out, tried in turn. Each is set on every box that holds others, or it holds only at the
// top. STRAIGHT keeps long arrows straight past the rows folded in their way, and more of ELK's crossing search lets it,
// but its boxes stagger, so a dense chart can stay too wide. CENTRED centres each layer, so a chart is as wide as its
// widest layer, at the price of arrows that swing from side to side.
const PLACEMENT = "elk.layered.nodePlacement.strategy";
const STRAIGHT = { [PLACEMENT]: "NETWORK_SIMPLEX", "elk.layered.thoroughness": "60" };
const CENTRED = { [PLACEMENT]: "SIMPLE" };
// Start very wide charts with bounded layers instead of an overflowing row. MinWidth weighs boxes and the
// arrows passing them; three average boxes leave routing room, and each of a knot's lanes gets a smaller share.
const COMPACT = { ...CENTRED, "elk.layered.layering.strategy": "MIN_WIDTH", "elk.layered.layering.minWidth.upperBoundOnWidth": "3", "elk.layered.thoroughness": "3" };
// Compact a fitted chart to remove the tall gaps left by bounded layers. SCANLINE throws on valid nested lanes.
const FINISH = { "elk.layered.compaction.postCompaction.strategy": "LEFT_RIGHT_CONNECTION_LOCKING", "elk.layered.compaction.postCompaction.constraints": "QUADRATIC" };
// A bounded layer is a poor starting point for dense, large programs: their long arrow bundles make it taller
// and slower. Keep their existing search, without paying for a speculative layout first.
const COMPACT_ITEMS = 512;
const boxCount = (box) => 1 + (box.children ?? []).reduce((n, child) => n + boxCount(child), 0);
const setOn = (box, options, inLane = false) => {
  box.layoutOptions = { ...box.layoutOptions, ...options, ...(box.id.startsWith(LANES) ? box.layoutOptions : {}) }; // lanes keep their rails straight
  if (options === COMPACT && inLane) box.layoutOptions["elk.layered.layering.minWidth.upperBoundOnWidth"] = "1";
  (box.children ?? []).filter((c) => c.children?.length).forEach((c) => setOn(c, options, inLane || box.id.startsWith(LANES)));
};

const laidOut = (prepared, elk, hidden, options, finish = false) => {
  const graph = structuredClone(prepared);
  setOn(graph, options);
  if (finish) setOn(graph, FINISH);
  // draw() already leaves a margin around the chart. The undrawn root needs no extra side padding when fitting.
  const sides = options === COMPACT ? { "elk.padding": "[top=36,left=0,bottom=14,right=0]" } : {};
  return elk.layout({ ...graph, edges: [...(graph.edges ?? []), ...hidden.values()], layoutOptions: { ...OPTIONS, ...options, ...sides, ...(finish ? FINISH : {}) } });
};

async function narrow(prepared, elk, hidden, options) {
  let [out, room, best, stall] = [null, MAX_WIDTH, Infinity, 0];
  for (let n = 0; n < MAX_LAYOUTS; n++) {
    out = await laidOut(prepared, elk, hidden, options);
    // Compaction can widen the result; if so, the next fold uses its actual geometry and tries again.
    if (options === COMPACT && out.width <= MAX_WIDTH) out = await laidOut(prepared, elk, hidden, options, true);
    if (out.width <= MAX_WIDTH) break;
    // Still more than twice a laptop after a straight try: more of those will not fit it. The centred pass will.
    if (options === STRAIGHT && out.width > MAX_WIDTH * 2) break;
    // Bounded layers can fold every wide holder at once. The original search broadens only after a stall.
    const limit = options === COMPACT ? Infinity : 1 + stall * 2;
    const stalled = !(out.width < best - 1);
    if (stalled) stall = Math.min(stall + 1, 4);
    else best = out.width, stall = 0;
    // Arrows running past the boxes take room of their own, so a chart can be too wide with every row inside `room`.
    let more = folds(out, room, hidden, limit, stalled || options === COMPACT);
    while (!more.length && room > MIN_ROOM) more = folds(out, (room -= ROOM_STEP), hidden, limit, stalled || options === COMPACT);
    if (!more.length) break;
    for (const e of more) hidden.set(e.id, e);
  }
  return out;
}

async function fitted(prepared, elk, hidden, firstWidth, compact) {
  if (compact) return narrow(prepared, elk, hidden, COMPACT);
  // A first layout already far past a laptop never came back inside one with the straight search,
  // and that search was almost all of the time. Go straight to the centred pass.
  if (!(firstWidth > MAX_WIDTH * 3)) {
    const out = await narrow(prepared, elk, hidden, STRAIGHT);
    if (out.width <= MAX_WIDTH) return out;
    hidden.clear();
  }
  return narrow(prepared, elk, hidden, CENTRED);
}

// One chart from a plan ready for ELK: `hide` holds the ids of what ELK lays out and the page does not draw, and
// `parts` says which group each part of a lane belongs to. Boxes and arrows come back in absolute coordinates.
async function chart(prepared, elk, hide, parts, compact) {
  const hidden = new Map();
  // A file of many leaves on one row is already far past a laptop. The opening layout and the straight search
  // were thrown away, and that search was almost all of the time.
  const wide = spread(prepared);
  let out = wide > MAX_WIDTH * 3 ? null : await elk.layout({ ...structuredClone(prepared), layoutOptions: OPTIONS });
  if (!out || out.width > MAX_WIDTH) out = await fitted(prepared, elk, hidden, out?.width ?? wide, compact);
  const [boxes, cut, held, spot, shown] = [[], new Map(), new Map(), new Map(), new Map()];
  const origin = { [out.id]: [0, 0] };
  const walk = (box, level, holders) => {
    for (const b of box.children ?? []) {
      const [x, y] = [origin[box.id][0] + b.x, origin[box.id][1] + b.y];
      origin[b.id] = [x, y];
      spot.set(b.id, { x, w: b.width });
      const part = parts.get(b.id);
      if (hide.has(b.id) && !part) {
        walk(b, level, holders); // a knot's wrapper: its boxes are drawn where the plan has them
        continue;
      }
      const whole = part?.group ?? b;
      let drawn = cut.get(whole.id)?.drawn;
      if (drawn) drawn.h = y + b.height - drawn.y; // a further part of a group: the group's box runs down to its foot
      else {
        drawn = { id: whole.id, x, y, w: b.width, h: b.height, level, label: whole.labels?.[0]?.text ?? whole.id, note: noteOf(whole), ...markOf(whole), ...badgesOf(whole) };
        boxes.push(drawn);
        held.set(drawn, holders);
        if (part) cut.set(whole.id, { drawn, part });
      }
      shown.set(b.id, drawn);
      walk(b, level + 1, [...holders, drawn]);
    }
  };
  walk(out, 1, []);
  // A group cut into parts is drawn as its whole lane: from the rail on its left, or the knot's edge, to the one on its right.
  for (const { drawn, part: { wrapper, rails: [before, after] } } of cut.values()) {
    const [left, right] = [spot.get(before ?? wrapper), spot.get(after ?? wrapper)];
    drawn.x = before ? left.x + left.w : left.x;
    drawn.w = (after ? right.x : right.x + right.w) - drawn.x;
  }
  const drawnEdges = out.edges.filter((e) => !hidden.has(e.id) && !hide.has(e.id));
  const lines = drawnEdges.map((e) => {
    const [s] = e.sections;
    const [dx, dy] = origin[e.container];
    const points = [s.startPoint, ...(s.bendPoints ?? []), s.endPoint].map((p) => [p.x + dx, p.y + dy]);
    const l = e.labels?.[0];
    const label = l && { x: l.x + dx, y: l.y + dy, w: l.width, text: l.text };
    return { id: e.id, points, label, ...markOf(e) };
  });
  // Lanes are kept only when the chart still fits a laptop, each lane holds its own boxes and no others, and no arrow
  // runs through a lane's group without an end in it. If not, the caller tries another way.
  const inside = (b, g) => b.x >= g.x && b.y >= g.y && b.x + b.w <= g.x + g.w && b.y + b.h <= g.y + g.h;
  const apart = (a, b) => a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;
  const within = (b, g) => b === g || held.get(b).includes(g);
  const across = ([px, py], [qx, qy], g) => Math.max(px, qx) > g.x + 1 && Math.min(px, qx) < g.x + g.w - 1 && Math.max(py, qy) > g.y + 1 && Math.min(py, qy) < g.y + g.h - 1;
  const sound = out.width <= MAX_WIDTH && [...cut.values()].every(({ drawn: g }) =>
    boxes.every((b) => (within(b, g) ? inside(b, g) : within(g, b) || apart(g, b))) &&
    drawnEdges.every((e, i) => [e.sources[0], e.targets[0]].some((id) => within(shown.get(id), g)) || !lines[i].points.slice(1).some((q, k) => across(lines[i].points[k], q, g))));
  return { sound, laid: { width: out.width, height: out.height, boxes, arrows: lines } };
}

export async function layout(plan, elk) {
  const prepared = prepare(plan);
  const compact = spread(prepared) > MAX_WIDTH * 3 && boxCount(prepared) + prepared.edges.length <= COMPACT_ITEMS;
  // A knot's two lanes can stand either way round: the plan's way is tried first, then the other.
  for (const turned of [false, true]) {
    const laned = lanes(prepared, turned);
    if (!laned) break;
    const made = await chart(laned.graph, elk, laned.hidden, laned.parts, compact);
    if (made.sound) return made.laid;
  }
  return (await chart(prepared, elk, new Set(), new Map(), compact)).laid;
}
