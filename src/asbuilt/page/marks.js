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
// so a button can show 0, and be disabled, before anyone clicks it.
export function markCounts(plan) {
  const tally = Object.fromEntries(KINDS.map((kind) => [kind, 0]));
  const walk = (box) => {
    for (const badge of badgesOf(box)) if (badge in tally) tally[badge] += 1;
    for (const child of childrenOf(box)) walk(child);
  };
  if (plan) walk(plan);
  return tally;
}

// What each mark means in everyday words, as the end of "a box whose own code ...". Each phrase
// claims only what marks.py checks: os.environ or os.getenv; input(); a file opened or written to
// write; requests, httpx, urllib.request, http.client, socket, subprocess or os.system; openai or anthropic.
export const SAYS = {
  env: "uses environment settings",
  input: "asks a person to type",
  writes: "writes files",
  "calls out": "talks to the network or runs programs",
  model: "uses an AI library",
};

const what = (kinds) => names(kinds).map((kind) => SAYS[kind]).join(" or ");

// A view with no matching box. `reached` is the label of the pipeline's start, when one is on.
export function carries(kinds, reached) {
  const where = reached ? `in what ${reached} reaches` : "here";
  return `No box ${where} has its own code that ${what(kinds)}.`;
}

const SHOWN = 6;

// Several marks mean any of them. Then which boxes that hides and why, and how many arrows.
// A box calling another box that does it is hidden too: a mark comes from a box's own code.
export function showing(kinds, hidden, arrows) {
  const head = `Showing boxes whose own code ${what(kinds)}.`;
  if (!hidden.length && !arrows) return `${head} Hiding nothing.`;
  const more = hidden.length > SHOWN ? `, and ${hidden.length - SHOWN} more` : "";
  const boxes = hidden.length ? ` Hiding ${hidden.length === 1 ? "1 box" : `${hidden.length} boxes`} whose own code does not: ${hidden.slice(0, SHOWN).join(", ")}${more}.` : "";
  const lines = arrows ? ` Also hiding ${arrows === 1 ? "1 arrow" : `${arrows} arrows`} to or from a box whose own code does not.` : "";
  return `${head}${boxes}${lines}`;
}
