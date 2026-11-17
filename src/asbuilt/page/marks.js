// The boxes whose own code proves one of the chosen work marks, and the arrows between those boxes.
// A folder or a file with no mark of its own stays when it holds a box that does, so the box is still inside it.
// Anything else is left out. No chosen mark means the plan itself, not a copy.
import { pipeline } from "./pipeline.js";

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

const names = (kinds) => KINDS.filter((kind) => (kinds ?? []).includes(kind));

// What the page lays out. Null means the plan itself: no pipeline and no mark. A copy is never
// returned for that case, so All stays byte-identical to the chart with nothing chosen.
export function shown(plan, focus, kinds) {
  if (!plan) return null;
  const base = focus ? pipeline(plan, focus) : plan;
  const filtered = byMarks(base, names(kinds));
  return filtered === plan ? null : filtered;
}

// How many boxes in this view prove each mark. The count is the view before a mark is chosen,
// so a button can read "model 0" before anyone clicks it.
export function markCounts(plan) {
  const tally = Object.fromEntries(KINDS.map((kind) => [kind, 0]));
  const walk = (box) => {
    for (const badge of badgesOf(box)) if (badge in tally) tally[badge] += 1;
    for (const child of childrenOf(box)) walk(child);
  };
  if (plan) walk(plan);
  return tally;
}

const what = (kinds) => {
  const list = names(kinds);
  return list.length > 1 ? `any of: ${list.join(", ")}` : list[0];
};

// A view with no matching box. `reached` is the label of the pipeline's start, when one is on.
export function carries(kinds, reached) {
  const where = reached ? `in what ${reached} reaches` : "here";
  return `No box ${where} carries ${what(kinds)}.`;
}

const bit = (count, one, many) => (count ? `${count} ${count === 1 ? one : many}` : "");

// Several marks mean any of them. The rest of the line is how many boxes and arrows that hides.
export function showing(kinds, boxes, arrows) {
  const hidden = [bit(boxes, "box", "boxes"), bit(arrows, "arrow", "arrows")].filter(Boolean);
  const rest = hidden.length ? `Hiding ${hidden.join(" and ")}.` : "Hiding nothing.";
  return `Showing boxes with ${what(kinds)}. ${rest}`;
}
