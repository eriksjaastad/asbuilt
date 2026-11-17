import assert from "node:assert/strict";
import test from "node:test";
import { find, html, titled } from "../src/asbuilt/page/code.js";

const sentence = "This function calls one other function and returns whatever that returns.";

test("the panel is that function's lines and the proven sentence, and a box with no lines is empty", () => {
  const plan = { id: "r", children: [{ id: "t.py", children: [{ id: "one", labels: [{ text: "one()" }], asbuilt: {
    text: "def one():\n    return other()\n", proven: sentence, rest: "the rest is in the file, line 9.",
  } }] }] };
  const out = html(find(plan, "one"));
  assert.ok(out.includes("def one():") && out.includes("return other()") && out.includes(sentence));
  assert.ok(out.includes("line 9"));
  assert.equal(html(find(plan, "t.py")), "");
  assert.equal(find(plan, "missing"), null);
  assert.equal(html({ id: "e", asbuilt: { text: "<script>" } }).includes("<script>"), false);
});

test("a model note is labeled model, and the proven sentence is not", () => {
  const out = html({
    id: "f",
    labels: [{ text: "f()" }],
    asbuilt: {
      text: "def f():\n    return g()\n",
      proven: sentence,
      model: [{ label: "tricky part", text: "the name hides the call" }],
    },
  });
  const [before] = out.split('<p class="model">');
  assert.ok(before.includes(sentence));
  assert.equal(before.includes("model"), false);
  assert.match(out, /<b>model<\/b> tricky part: the name hides the call/);
});

test("an everyday note comes before the security plain and expert sentences, and they are escaped", () => {
  const out = html({
    id: "f",
    labels: [{ text: "f()" }],
    asbuilt: {
      text: "def f():\n    pass\n",
      plain: ["It sends a prompt to <host>."],
      security: [{ plain: "This reads <K> from the environment.", expert: "os.environ.get(KEY) reads <K>." }],
    },
  });
  assert.match(out, /<p class="plain">It sends a prompt to &#60;host&#62;\.<\/p>/);
  assert.match(out, /<b>Plain \(everyday words\):<\/b> This reads &#60;K&#62; from the environment\./);
  assert.match(out, /<b>Expert \(the code\):<\/b> os\.environ\.get\(KEY\) reads &#60;K&#62;\./);
  assert.ok(out.indexOf('class="plain"') < out.indexOf("<b>Expert"));
  assert.equal(out.includes("<K>"), false);
  assert.equal(out.includes("<host>"), false);
  const bare = html({ id: "g", labels: [{ text: "g()" }], asbuilt: { text: "def g():\n    pass\n" } });
  assert.equal(bare.includes("Plain"), false);
  assert.equal(bare.includes("Expert"), false);
  assert.equal(bare.includes("Security"), false);
});

test("security facts get a counted heading and one numbered, named entry each, with the sentences unchanged", () => {
  const facts = [
    { fact: { kind: "env", how: "read", name: "K" }, plain: "This reads K from the environment: os.environ.get(K).", expert: "os.environ.get(K) reads K from the environment." },
    { fact: { kind: "calls out", way: "command" }, plain: "This runs a command outside this program: subprocess.run(...).", expert: "subprocess.run(...). This call is not inside try." },
  ];
  const out = html({ id: "f", labels: [{ text: "f()" }], asbuilt: { text: "def f():\n    pass\n", security: facts } });
  assert.ok(out.includes('<p class="security-head">Security: 2 things to know</p>'));
  assert.ok(out.includes('<p class="fact">1. Reads an environment setting</p>'));
  assert.ok(out.includes('<p class="fact">2. Runs a program outside this one</p>'));
  for (const note of facts) assert.ok(out.includes(`${note.plain}</p>`) && out.includes(`${note.expert}</p>`));
  assert.ok(out.indexOf("1. Reads") < out.indexOf(facts[0].expert) && out.indexOf(facts[0].expert) < out.indexOf("2. Runs"));
  const one = html({ id: "g", asbuilt: { text: "", security: [facts[0]] } });
  assert.ok(one.includes("Security: 1 thing to know"));
});

test("a fact's name follows only its stored kind, how and way", () => {
  assert.equal(titled({ kind: "input" }), "Reads what a person types");
  assert.equal(titled({ kind: "env", how: "check" }), "Checks an environment setting");
  for (const how of ["remove", "default", "set", "write"]) assert.equal(titled({ kind: "env", how }), "Changes an environment setting");
  assert.equal(titled({ kind: "env" }), "Reads an environment setting");
  assert.equal(titled({ kind: "calls out", way: "connect" }), "Opens a network connection");
  assert.equal(titled({ kind: "calls out", way: "send" }), "Makes a network request");
  assert.equal(titled(undefined), "Something to know");
});
