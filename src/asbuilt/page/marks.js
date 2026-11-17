// The boxes whose own code proves one of the chosen work marks, and the arrows between those boxes.
// A folder or a file with no mark of its own stays when it holds a box that does, so the box is still inside it.
// Anything else is left out. No chosen mark means the plan itself, not a copy.

const KINDS = ["env", "input", "writes", "calls out", "model"];

const childrenOf = (box) => box.children ?? [];
const badgesOf = (box) => (Array.isArray(box.asbuilt?.badges) ? box.asbuilt.badges : []);

const index = (plan) => {
  const parent = new Map();
  const walk = (box, from) => {
    if (from) parent.set(box.id, from);
    for (const child of childrenOf(box)) walk(child, box.id);
  };
  walk(plan, null);
  return parent;
};

const copy = (box, keep, match) => {
  if (!keep.has(box.id)) return null;
  const children = childrenOf(box).map((child) => copy(child, keep, match)).filter(Boolean);
  const edges = (box.edges ?? []).filter((edge) => match.has(edge.sources?.[0]) && match.has(edge.targets?.[0]));
  const next = { ...box, ...(children.length ? { children } : {}) };
  if (!children.length) delete next.children;
  if (box.edges) {
    if (edges.length) next.edges = edges;
    else delete next.edges;
  }
  return next;
};

// `kinds` empty, or none of the real marks, is the plan unchanged. Otherwise every box that proves
// one of them stays, with the arrows that run from one such box to another.
export function byMarks(plan, kinds) {
  const chosen = new Set((kinds ?? []).filter((kind) => KINDS.includes(kind)));
  if (!chosen.size) return plan;
  const match = new Set();
  const scan = (box) => {
    if (badgesOf(box).some((badge) => chosen.has(badge))) match.add(box.id);
    for (const child of childrenOf(box)) scan(child);
  };
  scan(plan);
  const keep = new Set(match);
  if (plan.id !== undefined) keep.add(plan.id);
  const parent = index(plan);
  for (const id of match) for (let at = parent.get(id); at; at = parent.get(at)) keep.add(at);
  return copy(plan, keep, match);
}
