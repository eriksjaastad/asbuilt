import assert from "node:assert/strict";
import test from "node:test";
import ELK from "elkjs/lib/elk.bundled.js";
import { LANES, lanes } from "../src/asbuilt/page/lanes.js";
import { layout, MAX_WIDTH } from "../src/asbuilt/page/layout.js";
import { prepare } from "../src/asbuilt/page/prepare.js";
import { arrow, big, leaf } from "./plans.js";

const elk = new ELK();
const inside = (p, b) => p[0] >= b.x - 1 && p[0] <= b.x + b.w + 1 && p[1] >= b.y - 1 && p[1] <= b.y + b.h + 1;

// Holds for any plan: every box and arrow comes back, children sit inside their parent, no box overlaps one that
// neither holds it nor is held by it, and each arrow starts on its source and ends on its target (all in absolute
// coordinates).
function assertSound(plan, laid, boxCount, arrowCount) {
  assert.equal(laid.boxes.length, boxCount);
  assert.equal(laid.arrows.length, arrowCount);
  const byId = Object.fromEntries(laid.boxes.map((b) => [b.id, b]));
  const [parents, ends] = [{}, {}];
  const mark = (box) => (box.edges?.forEach((e) => (ends[e.id] = [e.sources[0], e.targets[0]])), (box.children ?? []).forEach((c) => ((parents[c.id] = box.id), mark(c))));
  mark(plan);
  for (const b of laid.boxes) {
    const p = byId[parents[b.id]];
    assert.equal(b.level, p ? p.level + 1 : 1, b.id);
    if (p) assert.ok(inside([b.x, b.y], p) && inside([b.x + b.w, b.y + b.h], p), `${b.id} inside ${p.id}`);
  }
  const holds = (id, kid) => parents[kid] !== undefined && (parents[kid] === id || holds(id, parents[kid]));
  for (const a of laid.boxes) for (const b of laid.boxes) {
    if (a === b || holds(a.id, b.id) || holds(b.id, a.id)) continue;
    assert.ok(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y, `${a.id} clear of ${b.id}`);
  }
  for (const { id, points } of laid.arrows) {
    const [from, to] = ends[id];
    assert.ok(inside(points[0], byId[from]) && inside(points.at(-1), byId[to]), `${id} runs from ${from} to ${to}`);
  }
}

test("a 49-box plan with arrows inside and across groups comes back whole, nested and joined", async () => {
  assertSound(big, await layout(big, elk), 49, 41);
});

test("a box with arrows inside it, a bare leaf and a label without text are drawn", async () => {
  const plan = { id: "r", children: [{ id: "g", children: [{ id: "a" }, leaf("b")], edges: [arrow("e", "a", "b")] }] };
  const laid = await layout(plan, elk);
  assertSound(plan, laid, 3, 1);
  assert.deepEqual(laid.boxes.filter((b) => b.id === "a").map((b) => [b.w, b.h, b.label]), [[190, 70, "a"]]);
});

test("a label is the first label's text and a note the second's, or empty", async () => {
  const plan = { id: "r", children: [{ ...leaf("a"), labels: [{ text: "fn()" }, { text: "other" }] }, leaf("b")] };
  assert.deepEqual((await layout(plan, elk)).boxes.map((b) => [b.label, b.note]), [["fn()", "other"], ["b", ""]]);
});

test("an arrow that skips a box runs clear of it, and arrows keep their bend points", async () => {
  const plan = { id: "r", children: [leaf("a"), leaf("b"), leaf("c")], edges: [arrow("e1", "a", "b"), arrow("e2", "b", "c"), arrow("e3", "a", "c")] };
  const laid = await layout(plan, elk);
  assertSound(plan, laid, 3, 3);
  const b = laid.boxes.find((k) => k.id === "b");
  const { points } = laid.arrows.find((a) => a.id === "e3");
  const clear = (lo, hi, at, size) => Math.max(lo, hi) <= at || Math.min(lo, hi) >= at + size;
  assert.ok(points.slice(1).every((q, i) => clear(q[0], points[i][0], b.x, b.w) || clear(q[1], points[i][1], b.y, b.h)));
  assert.ok(laid.arrows.some((a) => a.points.length > 2));
});

test("a long label fits inside its box, whether the box is a bare leaf or holds other boxes", async () => {
  const text = "validate_credentials(user, password)";
  const plan = { id: "r", children: [{ id: "a", labels: [{ text }] }, { id: "g", labels: [{ text }], children: [leaf("b")] }] };
  const laid = await layout(plan, elk);
  assert.deepEqual(laid.boxes.filter((b) => b.label === text).map((b) => b.w >= 8 * text.length + 24), [true, true]);
});

test("every box that holds others, the plan included, keeps its top 36 px clear for the label", async () => {
  const plan = { id: "r", children: [{ id: "p", children: [{ id: "c", children: [leaf("k")] }] }] };
  const byId = Object.fromEntries((await layout(plan, elk)).boxes.map((b) => [b.id, b]));
  assert.deepEqual([byId.p.y, byId.c.y - byId.p.y, byId.k.y - byId.c.y], [36, 36, 36]);
});

const gap = ([x, y], [a, b], [c, d]) => {
  const t = Math.max(0, Math.min(1, ((x - a) * (c - a) + (y - b) * (d - b)) / ((c - a) ** 2 + (d - b) ** 2 || 1)));
  return Math.hypot(x - a - t * (c - a), y - b - t * (d - b));
};

test("an arrow that ends on a box lands well clear of an arrow crossing its border to a box inside", async () => {
  const plan = { id: "r", children: [leaf("f"), { id: "s", children: [leaf("k")] }, leaf("a")],
    edges: [arrow("in", "f", "k"), arrow("back", "a", "s"), arrow("out", "s", "a")] };
  const { arrows } = await layout(plan, elk);
  const [head, line] = [arrows.find((x) => x.id === "back").points.at(-1), arrows.find((x) => x.id === "in").points];
  assert.ok(Math.min(...line.slice(1).map((p, i) => gap(head, line[i], p))) >= 24);
});

// Every pair of level runs, from different arrows, that overlap by more than 20 px: [overlap, distance apart].
function sideBySide(arrows) {
  const flat = arrows.flatMap((a) =>
    a.points.slice(1).map((p, i) => [a.id, a.points[i], p]).filter(([, [, y0], [, y1]]) => Math.abs(y0 - y1) < 0.5));
  return flat.flatMap(([id, p, q], i) => flat.slice(i + 1).filter(([other]) => other !== id).map(([, r, s]) => [
    Math.min(Math.max(p[0], q[0]), Math.max(r[0], s[0])) - Math.max(Math.min(p[0], q[0]), Math.min(r[0], s[0])), Math.abs(p[1] - r[1])]))
    .filter(([overlap]) => overlap > 20);
}

test("arrows that run side by side, inside a box and across the plan, stay 20 px apart", async () => {
  const inside = { id: "r", children: [{ id: "g", children: [leaf("a"), leaf("b"), leaf("c")],
    edges: [arrow("e0", "a", "c"), arrow("e1", "a", "b"), arrow("e2", "a", "b")] }] };
  const across = { id: "r", children: [{ id: "g", children: [leaf("a")] }, leaf("x"), leaf("y"), { id: "h", children: [leaf("p"), leaf("q"), leaf("s")] }],
    edges: [arrow("e1", "a", "s"), arrow("e2", "a", "p"), arrow("e3", "a", "x"), arrow("e4", "a", "y")] };
  for (const plan of [inside, across]) {
    const side = sideBySide((await layout(plan, elk)).arrows);
    assert.ok(side.length >= 1);
    for (const [, apart] of side) assert.ok(apart >= 19.5, `${apart} px apart`);
  }
});

test("an arrow's label comes back beside its arrow, in absolute coordinates, clear of the boxes", async () => {
  const plan = { id: "r", children: [leaf("a"), { id: "g", children: [leaf("b"), leaf("c")] }],
    edges: [{ ...arrow("e1", "a", "b"), labels: [{ text: "daily" }] }, { ...arrow("e2", "b", "c"), labels: [{ text: "email link" }] }, arrow("e3", "a", "c")] };
  const { boxes, arrows } = await layout(plan, elk);
  const [a, g, b, c] = boxes;
  const clear = (l, k) => l.x + l.w <= k.x || l.x >= k.x + k.w || l.y + 16 <= k.y || l.y >= k.y + k.h;
  for (const [id, text, holder] of [["e1", "daily", { x: 0, y: 0, w: Infinity, h: Infinity }], ["e2", "email link", g]]) {
    const { label, points } = arrows.find((x) => x.id === id);
    assert.deepEqual([label.text, label.w], [text, 7 * text.length + 8]);
    assert.ok([a, b, c].every((k) => clear(label, k)) && inside([label.x, label.y], holder) && inside([label.x + label.w, label.y + 16], holder));
    assert.ok(points.some(([x]) => Math.abs(x - label.x) < 20 || Math.abs(x - label.x - label.w) < 20), id);
  }
  assert.equal(arrows.find((x) => x.id === "e3").label, undefined);
});

test("arrows into a group from a bare box and from another group do not cross", async () => {
  const plan = { id: "r", children: [leaf("c"), { id: "in", children: [leaf("p"), leaf("f")] }, { id: "out", children: [leaf("x"), leaf("w")] }],
    edges: [arrow("e0", "c", "x"), arrow("e1", "c", "w"), arrow("e2", "p", "x"), arrow("e3", "f", "w")] };
  const segs = (await layout(plan, elk)).arrows.flatMap((a) => a.points.slice(1).map((q, i) => [a.id, a.points[i], q]));
  const across = (a, b, c) => c > Math.min(a, b) && c < Math.max(a, b);
  const crossings = segs.filter(([a, p, q]) => p[1] === q[1] && segs.some(([b, s, t]) =>
    a !== b && s[0] === t[0] && across(p[0], q[0], s[0]) && across(s[1], t[1], p[1])));
  assert.deepEqual(crossings, []);
});

test("generated labels on a box, a group and an arrow: drawn with their words, or refused naming the first holder, never an ELK error", async () => {
  // fast-check is not a dependency; a seeded Park-Miller generator picks the inputs (same cases every run)
  let seed = 31_415;
  const rand = (n) => ((seed = (seed * 48271) % 2147483647), seed % n);
  const list = (make) => Array.from({ length: rand(4) }, make);
  const any = (d) => [() => `w${rand(9)}`, () => "", () => rand(201) - 100, () => rand(2) === 1, () => null,
    () => list(() => any(d + 1)), () => ({ text: any(d + 1) }), () => ({})][rand(d > 2 ? 5 : 8)]();
  const labels = () => (rand(5) ? list(() => (rand(10) ? { text: `t${rand(99)}` } : any(1))) : any(0));
  const readable = (l) => l === undefined || (Array.isArray(l) && l.every((x) => typeof x?.text === "string"));
  for (let i = 0; i < 400; i++) {
    const [la, lg, le] = [0, 0, 0].map(() => (rand(4) ? labels() : undefined));
    const plan = JSON.parse(JSON.stringify({ id: "r", children: [{ ...leaf("a"), labels: la }, { id: "g", labels: lg, children: [leaf("b")] }],
      edges: [{ ...arrow("e", "a", "b"), labels: le }] }));
    const why = JSON.stringify(plan);
    const refusal = [[le, 'box "r" at plan: has an arrow "e" with'], [la, 'box "a" at plan.children[0]: has'], [lg, 'box "g" at plan.children[1]: has']]
      .find(([l]) => !readable(l));
    if (refusal) {
      await assert.rejects(layout(plan, elk), { message: `${refusal[1]} labels that are not a list like [{"text": "..."}]` }, why);
      continue;
    }
    const { boxes, arrows } = await layout(plan, elk);
    for (const [b, l] of [[boxes[0], la], [boxes[1], lg]]) assert.deepEqual([b.label, b.note], [l?.[0]?.text ?? b.id, l?.[1]?.text ?? ""], why);
    assert.equal(arrows[0].label?.text, le?.[0]?.text || undefined, why);
  }
  assert.equal((await layout({ id: "r", children: [{ ...leaf("a"), labels: [] }] }, elk)).boxes[0].label, "a");
});

test("the mark compare writes on a box or an arrow comes through ELK; an item without one gets no mark key", async () => {
  const m = (item, mark) => ({ ...item, asbuilt: { mark, why: "" } });
  const plan = { id: "r", children: [{ id: "g", children: [m(leaf("a"), "dropped"), leaf("b")] }, m(leaf("c"), "added")], edges: [m(arrow("e", "a", "b"), "changed"), arrow("f", "b", "c")] };
  const laid = await layout(plan, elk);
  assert.deepEqual(laid.boxes.map((b) => [b.id, b.mark]), [["g", undefined], ["a", "dropped"], ["b", undefined], ["c", "added"]]);
  assert.deepEqual(laid.arrows.map((a) => [a.id, a.mark]), [["e", "changed"], ["f", undefined]]);
  assert.ok([...laid.boxes, ...laid.arrows].filter((x) => ["g", "b", "f"].includes(x.id)).every((x) => !("mark" in x)));
});

const counted = () => {
  const counter = { runs: 0, layout: (graph) => (counter.runs++, elk.layout(graph)) };
  return counter;
};
const boxes = (name, n) => Array.from({ length: n }, (_, i) => ({ ...leaf(`${name}${i}`), width: 190, height: 70 }));

test("a row too wide for a laptop folds down a layer, and the hidden arrows that fold it are not drawn", async () => {
  const plan = { id: "root", children: boxes("b", 14), edges: [arrow("e", "b0", "b13")] };
  const laid = await layout(plan, elk);
  assert.ok(laid.width <= MAX_WIDTH, `${laid.width} wide`);
  assert.ok(new Set(laid.boxes.map((b) => b.y)).size > 1, "more than one row");
  assertSound(plan, laid, 14, 1);
});

test("parts wide on their own, and parts ELK would set corner to corner, stay inside the width", async () => {
  const part = (id, n) => ({ id, labels: [{ text: id }], children: boxes(id, n) });
  const plan = { id: "root", children: [part("p", 9), { ...part("q", 3), children: [...boxes("q", 3), part("r", 10)] }, part("s", 8)], edges: [arrow("e1", "p8", "q0"), arrow("e2", "q2", "s7"), arrow("e3", "r9", "s0")] };
  const laid = await layout(plan, elk);
  assert.ok(laid.width <= MAX_WIDTH, `${laid.width} wide`);
  assertSound(plan, laid, 34, 3);
});

test("a chart that fits is laid out once, as it always was", async () => {
  const elk1 = counted();
  await layout(big, elk1);
  assert.equal(elk1.runs, 1);
});

test("one box too wide to fold gives up after a bounded number of layouts and still comes back whole", async () => {
  const plan = { id: "root", children: [leaf("x".repeat(400)), ...boxes("b", 3)] };
  const elk1 = counted();
  assertSound(plan, await layout(plan, elk1), 4, 0);
  assert.ok(elk1.runs <= 81, `${elk1.runs} layouts`);
});

test("a chart with every row inside the width, made too wide by arrows running past its boxes, still fits", async () => {
  // Found by search: 31 arrows, each four digits: part, box, then part, box.
  const links = "0030 2030 2042 0014 0215 3541 2432 1242 0411 2430 1141 1330 0210 3540 2034 2230 2032 2430 2545 1142 0415 3542 2032 2131 1031 3440 2343 0413 3145 3543 3142".split(" ");
  const parts = [6, 6, 6, 6, 6].map((n, i) => ({ id: `p${i}`, labels: [{ text: `p${i}` }], children: boxes(`p${i}b`, n) }));
  const edges = [...links.map(([a, b, c, d], i) => arrow(`l${i}`, `p${a}b${b}`, `p${c}b${d}`))];
  const laid = await layout({ id: "root", children: [{ id: "all", labels: [{ text: "all" }], children: parts }, { id: "top", width: 190, height: 70 }], edges: [...edges, arrow("te", "top", "p0b0")] }, elk);
  assert.ok(laid.width <= MAX_WIDTH, `${laid.width} wide`);
});

const part = (id, kids) => ({ id, labels: [{ text: id }], children: kids });
const placement = (box) => box.layoutOptions["elk.layered.nodePlacement.strategy"];

test("a chart too wide keeps arrows straight at every level, not only at the top", async () => {
  const seen = [];
  const plan = { id: "root", children: [part("p", [part("q", boxes("q", 9))]), ...boxes("b", 6)] };
  await layout(plan, { layout: (graph) => (seen.push(structuredClone(graph)), elk.layout(graph)) });
  assert.ok(seen.length > 1);
  for (const box of [seen[1], seen[1].children[0], seen[1].children[0].children[0]]) {
    assert.equal(placement(box), "NETWORK_SIMPLEX");
    assert.equal(box.layoutOptions["elk.layered.thoroughness"], "60");
  }
});

test("a long arrow past rows folded in its way runs without swinging from side to side", async () => {
  const rows = ["p", "q", "r", "s"].map((id) => part(id, boxes(id, 9)));
  const edges = rows.slice(1).flatMap((r, i) => [arrow(`a${i}`, `${rows[i].id}0`, `${r.id}0`), arrow(`b${i}`, `${rows[i].id}8`, `${r.id}8`)]);
  const laid = await layout({ id: "root", children: [...rows, ...boxes("t", 2)], edges: [...edges, arrow("long", "t0", "s4"), arrow("long2", "t1", "s5")] }, elk);
  assert.ok(laid.width <= MAX_WIDTH, `${laid.width} wide`);
  const swing = (a) => a.points.slice(1).reduce((sum, p, i) => sum + Math.abs(p[0] - a.points[i][0]), 0) - Math.abs(a.points.at(-1)[0] - a.points[0][0]);
  for (const a of laid.arrows) assert.ok(swing(a) < 400, `${a.id} swings ${swing(a)} px`);
});

test("a chart the straight layout cannot fit starts again centred, without the hidden arrows it tried", async () => {
  const seen = [];
  const wideWhileStraight = {
    layout: async (graph) => {
      seen.push(structuredClone(graph));
      const out = await elk.layout(graph);
      return placement(graph) === "NETWORK_SIMPLEX" && seen.length > 1 ? { ...out, width: MAX_WIDTH + 1 } : out;
    },
  };
  const plan = { id: "root", children: boxes("b", 14) };
  const laid = await layout(plan, wideWhileStraight);
  const first = seen.findIndex((g) => placement(g) === "SIMPLE");
  assert.ok(first > 1, "tried the straight layout first");
  assert.equal(seen[first].edges.length, 0);
  assert.ok(laid.width <= MAX_WIDTH, `${laid.width} wide`);
  assertSound(plan, laid, 14, 0);
});

// run and report share a group, and results, outside it, is written by one and read by the other: ELK alone turns
// one of the two arrows to point up.
const between = (extra = {}) => ({ id: "r", children: [{ id: "g", children: [leaf("run"), leaf("report")] }, leaf("results")],
  edges: [{ ...arrow("a", "run", "results"), ...extra }, { ...arrow("b", "results", "report"), ...extra }] });
const ups = (laid) => laid.arrows.filter((a) => a.points.at(-1)[1] < a.points[0][1]).map((a) => a.id);
const at = (laid, id) => laid.boxes.find((b) => b.id === id);

test("a box between two boxes of a group is drawn beside the group, and every arrow points down", async () => {
  const laid = await layout(between(), elk);
  assertSound(between(), laid, 4, 2);
  assert.deepEqual(ups(laid), []);
  const [g, run, results, report] = ["g", "run", "results", "report"].map((id) => at(laid, id));
  assert.ok(run.y + run.h <= results.y && results.y + results.h <= report.y, "run, then results, then report, down the page");
  // The group keeps a group's room round its boxes and no more: results is as near the box under it as the one over it.
  assert.deepEqual([run.x - g.x, run.y - g.y, g.y + g.h - report.y - report.h], [14, 36, 14]);
  assert.equal(report.y - results.y - results.h, results.y - run.y - run.h);
});

test("two groups whose boxes take turns stand side by side, each drawn once, with every arrow pointing down", async () => {
  const plan = { id: "r", children: [{ id: "steps", children: ["s1", "s2", "s3"].map(leaf) }, { id: "files", children: ["f1", "f2", "f3"].map(leaf) }],
    edges: [["s1", "f1"], ["f1", "s2"], ["s2", "f2"], ["f2", "s3"], ["s3", "f3"], ["s1", "s2"]].map(([a, b], i) => arrow(`e${i}`, a, b)) };
  const laid = await layout(plan, elk);
  assertSound(plan, laid, 8, 6);
  assert.deepEqual(ups(laid), []);
});

test("an arrow's label gets a row of its own between lanes, so labelled arrows point down too", async () => {
  const plan = between({ labels: [{ text: "rows" }] });
  plan.children.push(leaf("you"));
  plan.edges.push({ ...arrow("c", "you", "run"), labels: [{ text: "runs it" }] }, { ...arrow("d", "you", "report"), labels: [{ text: "reads it" }] });
  const laid = await layout(plan, elk);
  assertSound(plan, laid, 5, 4);
  assert.deepEqual(ups(laid), []);
  assert.deepEqual(laid.arrows.map((a) => a.label.text), ["rows", "rows", "runs it", "reads it"]);
});

test("an arrow that names a group cut into lanes lands on the group's top and leaves from its foot", async () => {
  const plan = between();
  plan.children.push(leaf("first"), leaf("last"));
  plan.edges.push(arrow("in", "first", "g"), arrow("out", "g", "last"), arrow("own", "report", "g"));
  const laid = await layout(plan, elk);
  assertSound(plan, laid, 6, 5);
  assert.deepEqual(ups(laid), []);
  const g = at(laid, "g");
  const [into, from] = ["in", "out"].map((id) => laid.arrows.find((a) => a.id === id).points);
  assert.deepEqual([into.at(-1)[1], from[0][1]], [g.y, g.y + g.h]);
});

test("a group is cut only where it must be: not for a box the knot's arrows miss, nor for one they reach through its own group", async () => {
  const groups = (other) => [{ id: "h", children: [leaf("mid"), leaf(other)] }, { id: "g", children: [leaf("first"), leaf("last")] }, leaf("out")];
  const knot = [arrow("a", "first", "mid"), arrow("b", "mid", "last")];
  for (const plan of [{ id: "r", children: groups("apart"), edges: [...knot, arrow("c", "apart", "out")] }, { id: "r", children: groups("pre"), edges: [...knot, arrow("c", "pre", "mid")] }]) {
    const laid = await layout(plan, elk);
    assertSound(plan, laid, 7, 3);
    assert.deepEqual(ups(laid), []);
    assert.deepEqual([...lanes(prepare(plan)).parts.values()].map((part) => part.group.id), ["g", "g"], "only g is cut");
  }
});

test("a plan with no knot, one whose arrows go round in a ring, and a knot of three are left to ELK", () => {
  const [ring, three] = [between(), between()];
  ring.edges.push(arrow("c", "report", "run"));
  three.children.push(leaf("more"));
  three.edges = [arrow("a", "run", "results"), arrow("b", "results", "more"), arrow("c", "more", "report")];
  assert.deepEqual([big, ring, three].map((plan) => lanes(prepare(plan))), [null, null, null]);
});

test("lanes too wide for a laptop fold inside themselves: the chart fits and every arrow still points down", async () => {
  const [steps, early, late] = [boxes("s", 3), boxes("e", 7), boxes("l", 7)];
  const plan = { id: "root", children: [part("steps", steps), part("files", [...early, ...late])],
    edges: [...early.map((b, i) => arrow(`a${i}`, "s0", b.id)), arrow("b", "e0", "s1"), ...late.map((b, i) => arrow(`c${i}`, "s1", b.id)), arrow("d", "l0", "s2")] };
  const laid = await layout(plan, elk);
  assert.ok(laid.width <= MAX_WIDTH, `${laid.width} wide`);
  assertSound(plan, laid, 19, 16);
  assert.deepEqual(ups(laid), []);
});

test("two lanes too wide together give up a share each, and a group that fits beside them keeps its row", async () => {
  const [early, files, tools] = [boxes("e", 4), boxes("f", 4), boxes("t", 6)];
  const plan = { id: "root", children: [part("steps", [...early, ...boxes("z", 1)]), part("files", files), part("tools", tools)],
    edges: [...files.map((f, i) => arrow(`a${i}`, `e${i}`, f.id)), arrow("b", "f0", "z0"), arrow("c", "z0", "t0")] };
  const laid = await layout(plan, elk);
  assert.ok(laid.width <= MAX_WIDTH, `${laid.width} wide`);
  assertSound(plan, laid, 18, 6);
  assert.deepEqual(ups(laid), []);
  assert.equal(new Set(tools.map((t) => at(laid, t.id).y)).size, 1, "tools is still one row");
});

test("a knot whose lanes cannot fit is not folded lane under lane: three tries, then ELK's own layout", async () => {
  const plan = between();
  plan.children[1] = { id: "results", labels: [{ text: "results".padEnd(180, "-") }] };
  let tries = 0;
  const counting = { layout: (graph) => ((tries += graph.children.some((c) => c.id.startsWith(LANES))), elk.layout(graph)) };
  assertSound(plan, await layout(plan, counting), 4, 2);
  assert.equal(tries, 3, "as it is, kept straight, and centred");
});

test("lanes that come back with a box in the wrong lane, or too wide, are dropped for ELK's own layout", async () => {
  const spoilt = (spoil) => ({ layout: async (graph) => {
    const out = await elk.layout(graph);
    const knot = out.children.find((c) => c.id.startsWith(LANES));
    if (knot) spoil(out, knot.children);
    return out;
  } });
  const intoTheGroup = (out, kids) => (kids.find((k) => k.id === "results").x = kids.find((k) => k.children).x);
  const tooWide = (out) => (out.width = MAX_WIDTH + 1);
  for (const spoil of [intoTheGroup, tooWide]) {
    const laid = await layout(between(), spoilt(spoil));
    assertSound(between(), laid, 4, 2);
    assert.equal(ups(laid).length, 1, spoil.name);
  }
});

test("when a chart too wide starts again centred, a knot's lanes keep the placement that holds their rails straight", async () => {
  const seen = [];
  const wideWhileStraight = { layout: async (graph) => {
    seen.push(structuredClone(graph));
    const out = await elk.layout(graph);
    return placement(graph) === "NETWORK_SIMPLEX" ? { ...out, width: MAX_WIDTH + 1 } : out;
  } };
  await layout(between(), wideWhileStraight);
  const centred = seen.find((graph) => placement(graph) === "SIMPLE");
  assert.equal(placement(centred.children.find((c) => c.id.startsWith(LANES))), "NETWORK_SIMPLEX");
});

test("generated plans: every box comes back inside its own group and clear of the others, and a knot of two leaves no arrow pointing up", async () => {
  // fast-check is not a dependency; a seeded Park-Miller generator picks the plans (same cases every run)
  let seed = 27_182;
  const rand = (n) => ((seed = (seed * 48271) % 2147483647), seed % n);
  let knotted = 0;
  for (let i = 0; i < 300; i++) {
    // Two groups and up to two loose boxes, then arrows that only run forward through a shuffled order of the boxes.
    const groups = [0, 1].map((g) => ({ id: `g${g}`, children: Array.from({ length: 1 + rand(4) }, (_, k) => leaf(`g${g}k${k}`)) }));
    const loose = Array.from({ length: rand(3) }, (_, k) => leaf(`x${k}`));
    const order = [...groups.flatMap((g) => g.children), ...loose].map((b) => b.id);
    for (let k = order.length - 1; k > 0; k--) {
      const j = rand(k + 1);
      [order[k], order[j]] = [order[j], order[k]];
    }
    const edges = Array.from({ length: 3 + rand(7) }, (_, e) => {
      const from = rand(order.length - 1);
      return { ...arrow(`e${e}`, order[from], order[from + 1 + rand(order.length - from - 1)]), ...(rand(3) ? {} : { labels: [{ text: `l${e}` }] }) };
    });
    const plan = JSON.parse(JSON.stringify({ id: "r", children: [...groups, ...loose], edges }));
    const laid = await layout(plan, elk);
    assertSound(plan, laid, order.length + 2, edges.length);
    if (lanes(prepare(plan))) assert.deepEqual([++knotted && ups(laid)], [[]], JSON.stringify(plan));
  }
  assert.ok(knotted >= 60, `${knotted} of the plans had a knot of two`);
});
