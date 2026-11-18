import assert from "node:assert/strict";
import test from "node:test";
import { byMarks, carries, hiddenBoxes, looked, markCounts, showing, shown } from "../src/asbuilt/page/marks.js";
import { pipeline } from "../src/asbuilt/page/pipeline.js";

// The shape marks.py LOOKS_FOR sends. Its names are checked against marks.py in test_marks.py.
const looks = {
  env: ["os.environ", "os.getenv"],
  input: ["input()"],
  writes: ["open() with a write mode", "os.fdopen() with a write mode", "a .open() method with a write mode", ".write_text()", ".write_bytes()"],
  "calls out": ["requests", "httpx", "urllib.request", "http.client", "os.system", "subprocess.run"],
  model: ["openai", "anthropic"],
};

const badge = (id, badges) => ({ id, asbuilt: { badges } });
const plan = {
  id: "app",
  children: [{
    id: "a.py",
    children: [badge("read", ["env", "input"]), badge("post", ["calls out"]), { id: "local" }],
    edges: [
      { id: "uses", sources: ["read"], targets: ["post"] },
      { id: "aside", sources: ["read"], targets: ["local"] },
    ],
  }, {
    id: "b.py",
    children: [badge("ask", ["model"])],
  }, badge("far", ["model"])],
  edges: [{ id: "out", sources: ["post"], targets: ["ask"] }],
};

const ids = (box, into = []) => (into.push(box.id), (box.children ?? []).forEach((child) => ids(child, into)), into);
const arrows = (box, into = []) => ((box.edges ?? []).forEach((edge) => into.push(edge.id)), (box.children ?? []).forEach((child) => arrows(child, into)), into);

test("no chosen mark is the plan itself", () => {
  assert.equal(byMarks(plan, []), plan);
  assert.equal(byMarks(plan, null), plan);
  assert.equal(byMarks(plan, ["nope"]), plan);
  assert.equal(shown(plan, null, []), null);
  assert.equal(shown(plan, null, ["nope"]), null);
});

test("chosen marks keep every matching box and the arrows between them, and nothing else", () => {
  const full = ids(plan);
  const fullArrows = arrows(plan);
  const out = byMarks(plan, ["env", "calls out"]);
  const kept = ids(out);
  const joined = arrows(out);
  assert.deepEqual(kept.sort(), ["a.py", "app", "post", "read"]);
  assert.deepEqual(joined, ["uses"]);
  assert.ok(kept.every((id) => full.includes(id)));
  assert.ok(joined.every((id) => fullArrows.includes(id)));
  for (const id of ["read", "post"]) assert.ok(kept.includes(id), id);
  assert.equal(kept.includes("local"), false);
  assert.equal(kept.includes("ask"), false);
  assert.equal(joined.includes("aside"), false);
  assert.equal(joined.includes("out"), false);
});

test("a mark on its own still keeps that box when another mark is not chosen", () => {
  const out = byMarks(plan, ["model"]);
  assert.deepEqual(ids(out).sort(), ["app", "ask", "b.py", "far"]);
  assert.deepEqual(arrows(out), []);
});

test("marks and the pipeline both apply: a box has to be reached and carry a chosen mark", () => {
  const reached = pipeline(plan, "read");
  const out = byMarks(reached, ["calls out"]);
  assert.ok(ids(reached).includes("local") && ids(reached).includes("ask"));
  assert.equal(ids(reached).includes("far"), false);
  assert.deepEqual(ids(out).sort(), ["a.py", "app", "post"]);
  assert.deepEqual(arrows(out), []);
  const both = shown(plan, "read", ["calls out", "model"]);
  assert.deepEqual(arrows(both), ["out"]);
  assert.equal(ids(both).includes("ask"), true);
  assert.equal(ids(both).includes("far"), false);
  assert.equal(ids(both).includes("local"), false);
});

test("the line says in plain words what is shown, which boxes are hidden and why", () => {
  const fn = (name) => ({ name, checked: true });
  assert.equal(showing(["env"], [fn("a()"), fn("b()")], 1, looks), "Showing functions and classes whose own code uses environment settings, and the boxes that hold them. Hiding 2 functions and classes where asBuilt found none of it: a(), b(). It looks only in functions and classes, for: os.environ, os.getenv. Also hiding 1 arrow that does not join two shown functions or classes.");
  assert.equal(showing(["calls out", "env"], [fn("a()")], 0, looks), "Showing functions and classes whose own code uses environment settings or talks to the network or runs programs, and the boxes that hold them. Hiding 1 function or class where asBuilt found none of it: a(). It looks only in functions and classes, for: os.environ, os.getenv; requests, httpx, urllib.request, http.client, os.system, subprocess.run.");
  assert.equal(showing(["env"], [], 0, looks), "Showing functions and classes whose own code uses environment settings, and the boxes that hold them. Hiding nothing.");
  assert.equal(showing(["writes"], ["a", "b", "c", "d", "e", "f", "g", "h"].map(fn), 2, looks), "Showing functions and classes whose own code writes files, and the boxes that hold them. Hiding 8 functions and classes where asBuilt found none of it: a, b, c, d, e, f, and 2 more. It looks only in functions and classes, for: open() with a write mode, os.fdopen() with a write mode, a .open() method with a write mode, .write_text(), .write_bytes(). Also hiding 2 arrows that do not join two shown functions or classes.");
  assert.equal(carries(["input"], null, looks), "asBuilt found no function or class here whose own code asks a person to type. It looks only in functions and classes, for: input().");
  assert.equal(carries(["env", "calls out"], null, looks), "asBuilt found no function or class here whose own code uses environment settings or talks to the network or runs programs. It looks only in functions and classes, for: os.environ, os.getenv; requests, httpx, urllib.request, http.client, os.system, subprocess.run.");
  assert.equal(carries(["model"], "main()", looks), "asBuilt found no function or class in what main() reaches whose own code uses the OpenAI or Anthropic library. It looks only in functions and classes, for: openai, anthropic.");
});

test("a file whose top-level code was never checked is not said to lack the mark", () => {
  // top.py has KEY = os.environ["K"] at top level: no box carries that code, so no mark is proven for it.
  const files = {
    id: "app",
    children: [
      { id: "top.py", labels: [{ text: "top.py" }], children: [{ id: "f", labels: [{ text: "f()" }], asbuilt: { text: "def f():\n    pass\n" } }] },
      { id: "env.py", labels: [{ text: "env.py" }], children: [{ id: "g", labels: [{ text: "g()" }], asbuilt: { text: "def g():\n    os.environ['K']\n", badges: ["env"] } }] },
    ],
  };
  const hidden = hiddenBoxes(files, ["env.py", "g"]);
  assert.deepEqual(hidden, [{ name: "top.py", checked: false }, { name: "f()", checked: true }]);
  const line = showing(["env"], hidden, 0, looks);
  assert.equal(line, "Showing functions and classes whose own code uses environment settings, and the boxes that hold them. Hiding 1 function or class where asBuilt found none of it: f(). Hiding 1 other box with no such function or class inside: top.py. It looks only in functions and classes, for: os.environ, os.getenv.");
  assert.equal(line.includes("does not"), false);
});

test("hidden boxes that share a label are told apart by the box that holds them", () => {
  const run = (id) => ({ id, labels: [{ text: "run()" }], asbuilt: { text: "def run():\n    pass\n" } });
  const files = { id: "app", children: [{ id: "a.py", children: [run("a")] }, { id: "b.py", children: [run("b"), { id: "solo", labels: [{ text: "solo()" }], asbuilt: { text: "" } }] }] };
  assert.deepEqual(hiddenBoxes(files, ["a.py", "b.py"]).map((h) => h.name), ["run() in a.py", "run() in b.py", "solo()"]);
  files.children[1].children.push(run("b2"));
  assert.match(showing(["env"], hiddenBoxes(files, ["a.py", "b.py"]), 0, looks), /where asBuilt found none of it: run\(\) in a\.py, run\(\) in b\.py \(2\), solo\(\)\./);
});

test("what asBuilt does not look for is never said to be absent from the code", () => {
  // The shape asbuilt.built gives this probe: os.popen, aiohttp, f.write, shutil.copy and urllib3
  // are not looked for, so a() to e() carry no mark; g() reads os.getenv.
  const fn = (id, body, badges) => ({ id, labels: [{ text: `${id}()` }], asbuilt: { text: `def ${id}():\n    ${body}\n`, ...(badges ? { badges } : {}) } });
  const probe = { id: "app", children: [{ id: "probe.py", labels: [{ text: "probe.py" }], children: [
    fn("a", 'os.popen("ls")'), fn("b", "aiohttp.ClientSession()"), fn("c", 'f.write("hi")'),
    fn("d", 'shutil.copy("x", "y")'), fn("e", "urllib3.PoolManager()"), fn("g", 'os.getenv("K")', ["env"]),
  ] }] };
  assert.deepEqual(markCounts(probe), { env: 1, input: 0, writes: 0, "calls out": 0, model: 0 });
  const texts = [
    showing(["env"], hiddenBoxes(probe, ["probe.py", "g"]), 0, looks),
    carries(["calls out"], null, looks), carries(["writes"], null, looks), looked(["calls out"], looks), looked(["writes"], looks),
  ];
  for (const text of texts) assert.doesNotMatch(text, /does not|doesn't|do not|does this|has no/, text);
  assert.match(texts[0], /where asBuilt found none of it: a\(\), b\(\), c\(\), d\(\), e\(\)\./);
  assert.match(texts[1], /^asBuilt found no function or class here/);
  assert.equal(looked(["calls out"], {}), "It looks only in functions and classes.");
  assert.ok(texts[3].includes("requests, httpx, urllib.request, http.client, os.system, subprocess.run"));
});

test("a mark button's count is how many boxes in this view prove it", () => {
  assert.deepEqual(markCounts(plan), { env: 1, input: 1, writes: 0, "calls out": 1, model: 2 });
  assert.deepEqual(markCounts(pipeline(plan, "read")).model, 1);
});
