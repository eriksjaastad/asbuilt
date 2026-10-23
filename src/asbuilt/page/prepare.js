// Check an already-parsed plan (an object, never a file) and copy it ready for ELK. Throws an Error that
// names the problem, so the page can show it. The plan passed in is not changed.
// ELK writes into what it is given and silently accepts what we cannot draw (no size, a repeated id, a
// bad width), so each box is checked here, a leaf without a size gets one, a box is made wide enough for its
// label, and every arrow moves to the root.
const LEAF = { width: 190, height: 70 };
// Room inside a box that holds others: its label sits in the top 36 px (draw.js writes it 22 px down).
export const PADDING = "[top=36,left=14,bottom=14,right=14]";
// Room between arrows, 10 px each by ELK's default: two arrows running side by side blur into one line, and an arrow
// ending on a box that holds others, next to one crossing its border to a box inside, reads as going inside.
// layout.js sets it for the plan, and each box that holds others gets it here.
export const ARROW_ROOM = { "elk.spacing.portPort": "24", "elk.layered.spacing.edgeEdgeBetweenLayers": "20" };
const PADDING_NOTE = "[top=54,left=14,bottom=14,right=14]"; // and the note under it, written 40 px down
const CHAR = 8; // about the width of a character of the 13px monospace label
const NOTE_CHAR = 7; // and of the 11px note
// A box's note: its second label's text, drawn under the first in smaller type (D-013). "" when there is none.
export const noteOf = (box) => String(box.labels?.[1]?.text ?? "");
// Labels, when given, are a list of {"text": "..."}. Other shapes lose their words without a message (a box is drawn
// under its id, an arrow with no label, a bare-string note is dropped) or make ELK fail naming nothing, so check()
// refuses them.
const readable = (labels) => Array.isArray(labels) && labels.every((l) => typeof l?.text === "string");
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
    if ("labels" in item && !readable(item.labels)) {
      fail(at, `${item === box ? "has" : `has an arrow ${JSON.stringify(item.id)} with`} labels that are not a list like [{"text": "..."}]`);
    }
  }
  arrows.push(...(box.edges ?? []));
  delete box.edges;
  for (const key of ["width", "height"]) {
    if (key in box && !(Number.isFinite(box[key]) && box[key] > 0)) fail(at, `${key} is not a number above 0`);
  }
  const [label, note] = [String(box.labels?.[0]?.text ?? box.id), noteOf(box)];
  const wide = Math.max(CHAR * [...label].length, NOTE_CHAR * [...note].length) + 24; // so both stay inside the box
  // ELK reads a box's minimum size along the layout direction, which layout.js sets to DOWN: the width goes second.
  if (box.children?.length) box.layoutOptions = { "elk.padding": note ? PADDING_NOTE : PADDING, ...ARROW_ROOM, "elk.nodeSize.constraints": "MINIMUM_SIZE", "elk.nodeSize.minimum": `(0,${wide})`, ...box.layoutOptions };
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
    const where = `arrow ${JSON.stringify(a.id)}`;
    for (const [key, end] of [["sources", "source"], ["targets", "target"]]) {
      const ends = a[key];
      if (!Array.isArray(ends) || ends.length !== 1) fail(where, `${key} must be a list of one box id`);
      if (!boxes.has(ends[0])) fail(where, `${end} ${JSON.stringify(ends[0])} is not a box in the plan`);
    }
    // An arrow's first label is drawn beside it in the note's type; ELK makes room for a label only when it has a size.
    const text = String(a.labels?.[0]?.text ?? "");
    if (text) a.labels = [{ text, width: NOTE_CHAR * [...text].length + 8, height: 16 }];
    else delete a.labels;
  }
  return { ...root, edges: arrows };
}
