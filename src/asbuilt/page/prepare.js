// Check an already-parsed plan (an object, never a file) and copy it ready for ELK. Throws an Error that
// names the problem, so the page can show it. The plan passed in is not changed.
// ELK writes into what it is given and silently accepts what we cannot draw (no size, a repeated id, a
// bad width), so each box is checked here, a leaf without a size gets one, a box is made wide enough for its
// label, and every arrow moves to the root.
const LEAF = { width: 190, height: 70 };
// Room inside a box that holds others: its label sits in the top 36 px (draw.js writes it 22 px down).
export const PADDING = "[top=36,left=14,bottom=14,right=14]";
const CHAR = 8; // about the width of a character of the 13px monospace label
const fail = (where, why) => {
  throw new Error(`${where}: ${why}`);
};

function check(box, where, ids, arrows) {
  if (typeof box !== "object" || box === null || Array.isArray(box)) fail(where, "is not an object");
  // Name the box when it has a name: in a real plan, counting children by index to find it is slow.
  const at = typeof box.id === "string" ? `box ${JSON.stringify(box.id)} at ${where}` : where;
  for (const key of ["children", "edges"]) {
    if (key in box && !Array.isArray(box[key])) fail(at, `${key} is not a list`);
  }
  for (const item of [box, ...(box.edges ?? [])]) {
    if (typeof item?.id !== "string") fail(at, item === box ? "has an id that is not text" : "has an arrow whose id is not text");
    if (ids.has(item.id)) fail(at, `id ${JSON.stringify(item.id)} is used twice`);
    ids.add(item.id);
  }
  arrows.push(...(box.edges ?? []));
  delete box.edges;
  for (const key of ["width", "height"]) {
    if (key in box && !(Number.isFinite(box[key]) && box[key] > 0)) fail(at, `${key} is not a number above 0`);
  }
  const wide = CHAR * [...String(box.labels?.[0]?.text ?? box.id)].length + 24; // so the label stays inside its box
  // ELK reads a box's minimum size along the layout direction, which layout.js sets to DOWN: the width goes second.
  if (box.children?.length) box.layoutOptions = { "elk.padding": PADDING, "elk.nodeSize.constraints": "MINIMUM_SIZE", "elk.nodeSize.minimum": `(0,${wide})`, ...box.layoutOptions };
  else Object.assign(box, { ...LEAF, width: Math.max(LEAF.width, wide), ...box, children: undefined });
  box.children?.forEach((kid, i) => check(kid, `${where}.children[${i}]`, ids, arrows));
}

export function prepare(plan) {
  const root = structuredClone(plan);
  const [arrows, ids] = [[], new Set()];
  check(root, "plan", ids, arrows);
  if (!root.children) fail("plan", "has no boxes");
  // The page draws an arrow from one drawn box to another; ELK would also take ports, several ends, or the plan
  // itself, and the arrow would then be drawn loose or not at all.
  const boxes = new Set(ids);
  [root, ...arrows].forEach((x) => boxes.delete(x.id));
  // Each refusal names the end and the id, so a box renamed in the plan but not in its arrows is found at once.
  for (const a of arrows) {
    for (const [key, end] of [["sources", "source"], ["targets", "target"]]) {
      const ends = a[key];
      const where = `arrow ${JSON.stringify(a.id)}`;
      if (!Array.isArray(ends) || ends.length !== 1) fail(where, `${key} must be a list of one box id`);
      if (!boxes.has(ends[0])) fail(where, `${end} ${JSON.stringify(ends[0])} is not a box in the plan`);
    }
  }
  return { ...root, edges: arrows };
}
