// The part of a plan one box can reach. Arrows are followed outward only: a pipeline is what that box
// leads to, not everything that leads to it. A folder or a file has no arrows of its own, so starting
// there starts from every box inside it. A box that holds a reached box stays, as the box around it.

const childrenOf = (box) => box.children ?? [];

const index = (plan) => {
  const [parent, kids, edges] = [new Map(), new Map(), []];
  const walk = (box, from) => {
    if (from) parent.set(box.id, from);
    kids.set(box.id, childrenOf(box));
    edges.push(...(box.edges ?? []));
    for (const child of childrenOf(box)) walk(child, box.id);
  };
  walk(plan, null);
  return { parent, kids, edges };
};

const reached = ({ parent, kids, edges }, start) => {
  const seeds = new Set();
  const fill = (id) => {
    if (seeds.has(id)) return;
    seeds.add(id);
    for (const child of kids.get(id) ?? []) fill(child.id);
  };
  fill(start);
  const next = new Map();
  for (const edge of edges) {
    const [from, to] = [edge.sources?.[0], edge.targets?.[0]];
    if (!next.has(from)) next.set(from, []);
    next.get(from).push(to);
  }
  const keep = new Set(seeds);
  const todo = [...seeds];
  for (const id of todo) {
    for (const to of next.get(id) ?? []) if (!keep.has(to)) keep.add(to), todo.push(to);
  }
  for (const id of [...keep]) for (let at = parent.get(id); at; at = parent.get(at)) keep.add(at);
  return keep;
};

const copy = (box, keep) => {
  if (!keep.has(box.id)) return null;
  const children = childrenOf(box).map((child) => copy(child, keep)).filter(Boolean);
  const edges = (box.edges ?? []).filter((edge) => keep.has(edge.sources?.[0]) && keep.has(edge.targets?.[0]));
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
  const tree = index(plan);
  if (!start || !tree.kids.has(start)) return plan;
  return copy(plan, reached(tree, start));
}
