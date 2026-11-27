// The part of a plan one box can reach. Arrows are followed outward only: a pipeline is what that box
// leads to, not everything that leads to it. A folder or a file has no arrows of its own, so starting
// there starts from every box inside it. A box that holds a reached box stays, as the box around it.

const isBox = (box) => box !== null && typeof box === "object" && !Array.isArray(box);
const childrenOf = (box) => (Array.isArray(box?.children) ? box.children.filter(isBox) : []);
const edgesOf = (box) => (Array.isArray(box?.edges) ? box.edges.filter(isBox) : []);

const index = (plan) => {
  const [parent, kids, edges] = [new Map(), new Map(), []];
  const walk = (box, from) => {
    if (!isBox(box)) return;
    if (from !== null && from !== undefined) parent.set(box.id, from);
    const children = childrenOf(box);
    kids.set(box.id, children);
    edges.push(...edgesOf(box));
    for (const child of children) walk(child, box.id);
  };
  walk(plan, null);
  return { parent, kids, edges };
};

const stable = (id) => {
  if (typeof id !== "string") return "";
  const bare = id.endsWith("/") ? id.slice(0, -1) : id;
  const cut = bare.lastIndexOf(":");
  if (cut > 0) {
    const [head, tail] = [bare.slice(0, cut), bare.slice(cut + 1)];
    if (/^\d+$/.test(tail) && head.includes(":")) return head;
  }
  return bare;
};

// Keep a focused box across reloads: exact id first, then a single unambiguous match without its
// trailing line number (e.g. file.py:fn:12 -> file.py:fn). Ambiguous or gone clears focus.
export function resolveFocus(plan, focus) {
  if (focus === null || focus === undefined || !isBox(plan)) return null;
  const ids = [];
  const walk = (box, top) => {
    if (!isBox(box)) return;
    if (!top && typeof box.id === "string") ids.push(box.id);
    for (const child of childrenOf(box)) walk(child, false);
  };
  walk(plan, true);
  if (plan.id === focus || ids.includes(focus)) return focus;
  const key = stable(focus);
  const matches = ids.filter((id) => stable(id) === key);
  return matches.length === 1 ? matches[0] : null;
}

const reached = ({ parent, kids, edges }, start) => {
  const next = new Map();
  for (const edge of edges) {
    if (edge.asbuilt?.mark === "dropped") continue;
    const [from, to] = [edge.sources?.[0], edge.targets?.[0]];
    if (!next.has(from)) next.set(from, []);
    next.get(from).push(to);
  }
  const keep = new Set();
  const todo = [];
  const add = (id) => {
    if (keep.has(id)) return;
    keep.add(id);
    todo.push(id);
    for (const child of kids.get(id) ?? []) add(child.id);
  };
  add(start);
  for (const id of todo) {
    for (const to of next.get(id) ?? []) add(to);
  }
  for (const id of [...keep]) for (let at = parent.get(id); at !== undefined && !keep.has(at); at = parent.get(at)) keep.add(at);
  return keep;
};

const copy = (box, keep) => {
  if (!isBox(box) || !keep.has(box.id)) return null;
  const children = childrenOf(box).map((child) => copy(child, keep)).filter(Boolean);
  const edges = edgesOf(box).filter((edge) => keep.has(edge.sources?.[0]) && keep.has(edge.targets?.[0]));
  const next = { ...box, ...(children.length ? { children } : {}) };
  if (children.length === 0) delete next.children;
  if (box.edges) {
    if (edges.length) next.edges = edges;
    else delete next.edges;
  }
  return next;
};

// The plan, or the part `start` reaches. An unknown start leaves the plan as it is.
export function pipeline(plan, start) {
  if (!isBox(plan)) return plan;
  const tree = index(plan);
  if (start === null || start === undefined || !tree.kids.has(start)) return plan;
  return copy(plan, reached(tree, start));
}
