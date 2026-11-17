// The page as a person sees it, in a real browser: the asbuilt command serves a plan, a save redraws it, a broken
// save shows the error over the last good chart, dimmed, and fixing the file brings the chart back.
// Only this test runs main.js, EventSource and the DOM; the others test the page's modules as functions.
import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { chromium } from "playwright-core";

const plan = (ids) => JSON.stringify({
  id: "root",
  children: ids.map((id) => ({ id, labels: [{ text: id }] })),
  edges: [{ id: "e1", sources: [ids[0]], targets: [ids[1]] }],
});

// Resolves with the page address once asbuilt prints it; rejects if asbuilt exits first.
// The test's signal kills it at the test's deadline: a timed-out test is marked failed but keeps running.
function serve(file, signal) {
  const run = spawn("uv", ["run", "-q", "asbuilt", file, "--port", "0"], { stdio: ["ignore", "pipe", "pipe"], signal });
  const address = new Promise((resolve, reject) => {
    let out = "", err = "";
    run.stdout.on("data", (part) => {
      out += part;
      const found = out.match(/http:\/\/127\.0\.0\.1:\d+\//);
      if (found) resolve(found[0]);
    });
    run.stderr.on("data", (part) => (err += part));
    run.on("exit", (code) => reject(new Error(`asbuilt exited ${code} before printing its address: ${err}`)));
    run.on("error", reject);
  });
  return { run, address };
}

test("the repo-root chart draws all parsed boxes and arrows in a few layouts", { timeout: 60_000 }, async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "asbuilt-root-"));
  const file = join(dir, "plan.json");
  let server, browser;
  try {
    execFileSync("uv", ["run", "--offline", "python", "-m", "asbuilt.built", ".", file], { timeout: 30_000 });
    const plan = JSON.parse(readFileSync(file, "utf8"));
    const [boxes, arrows] = [[], []];
    const walk = (box) => {
      for (const kid of box.children ?? []) { boxes.push(kid.id); walk(kid); }
      arrows.push(...(box.edges ?? []).map((e) => e.id));
    };
    walk(plan);
    const serving = serve(file, t.signal);
    server = serving.run;
    serving.address.catch(() => {});
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.addInitScript(() => {
      let engine;
      window.layoutCalls = 0;
      Object.defineProperty(window, "ELK", {
        get: () => engine,
        set: (Base) => { engine = class extends Base {
          layout(...args) { window.layoutCalls++; return super.layout(...args); }
        }; },
      });
    });
    await page.goto(await serving.address);
    await page.locator("#chart > svg").waitFor();
    const drawn = await page.evaluate(() => ({
      boxes: [...document.querySelectorAll("[data-box]")].map((b) => b.dataset.box).sort(),
      arrows: [...document.querySelectorAll("[data-arrow]")].map((a) => a.dataset.arrow).sort(),
      calls: window.layoutCalls,
      height: document.querySelector("#chart > svg").viewBox.baseVal.height,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    }));
    assert.deepEqual(drawn.boxes, boxes.sort());
    assert.deepEqual(drawn.arrows, arrows.sort());
    assert.ok(drawn.calls <= 8, `${drawn.calls} layouts`);
    assert.ok(drawn.height < boxes.length * 140, "the chart has not become a single long column");
    assert.equal(drawn.overflow, 0);
  } finally {
    server?.kill();
    await browser?.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

// At the timeout the server is killed, so a server that never prints its address fails the run; Playwright's waits
// end on their own timeouts. finally stops the server and the browser whatever fails.
test("a save redraws the page, a broken save keeps the last good chart dimmed, and a fix brings it back", { timeout: 60_000 }, async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "asbuilt-page-"));
  const file = join(dir, "plan.json");
  writeFileSync(file, plan(["read", "draw"]));
  const { run, address } = serve(file, t.signal);
  address.catch(() => {}); // awaited below; once the server is stopped, its exit is not a second failure
  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(await address);

    await page.locator("#chart > svg [data-box]").nth(1).waitFor();
    await page.locator("#chart > svg [data-arrow='e1']").waitFor();

    writeFileSync(file, '{"id": "root", "children": [');
    await page.locator("#chart .error").waitFor();
    const stale = page.locator("#chart .stale svg");
    await stale.locator("[data-box='draw']").waitFor();
    const opacity = Number(await stale.evaluate((svg) => getComputedStyle(svg).opacity));
    if (!(opacity < 1)) throw new Error(`the last good chart is not dimmed: opacity ${opacity}`);

    writeFileSync(file, plan(["read", "draw", "show"]));
    await page.locator("#chart > svg [data-box='show']").waitFor();
    await page.locator("#chart .error").waitFor({ state: "detached" });
    if (errors.length) throw new Error(`the page threw: ${errors.join("; ")}`);
  } finally {
    await browser?.close();
    run.kill();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("no chart scrolls sideways: not a wide plan on a laptop, and not a box too wide to fold in a small window", { timeout: 60_000 }, async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "asbuilt-page-"));
  const file = join(dir, "plan.json");
  writeFileSync(file, plan(Array.from({ length: 14 }, (_, i) => `box${i}`)));
  const { run, address } = serve(file, t.signal);
  address.catch(() => {});
  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(await address);
    await page.locator("svg").waitFor();
    const sideways = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.equal(await sideways(), 0);
    writeFileSync(file, plan(["x".repeat(300), "draw"]));
    await page.locator("text=draw").waitFor();
    await page.setViewportSize({ width: 800, height: 600 });
    assert.equal(await sideways(), 0);
  } finally {
    run.kill();
    await browser?.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("a box between two boxes of a group is drawn beside the group: below the first, above the second, outside the group", { timeout: 60_000 }, async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "asbuilt-page-"));
  const file = join(dir, "plan.json");
  writeFileSync(file, JSON.stringify({
    id: "root",
    children: [{ id: "tests", children: [{ id: "run" }, { id: "report" }] }, { id: "results" }],
    edges: [{ id: "e1", sources: ["run"], targets: ["results"] }, { id: "e2", sources: ["results"], targets: ["report"] }],
  }));
  const { run, address } = serve(file, t.signal);
  address.catch(() => {});
  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(await address);
    await page.locator("#chart > svg [data-arrow='e2']").waitFor();
    // Each drawn box as [left, top, right, bottom].
    const drawn = (id) => page.locator(`#chart > svg [data-box='${id}']`).evaluate((g) => {
      const { x, y, width, height } = g.getBBox();
      return [x, y, x + width, y + height];
    });
    const [group, first, between, second] = await Promise.all(["tests", "run", "results", "report"].map(drawn));
    assert.ok(first[3] < between[1] && between[3] < second[1], "run, then results, then report, down the page");
    assert.ok(between[0] > group[2] || between[2] < group[0], "results is beside the group");
    assert.deepEqual(errors, []);
  } finally {
    run.kill();
    await browser?.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("clicking a function opens its code beside the chart, which stays put, with no sideways scroll", { timeout: 60_000 }, async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "asbuilt-page-"));
  const file = join(dir, "plan.json");
  const sentence = "This function calls one other function and returns whatever that returns.";
  const child = (id, text, proven) => ({ id, labels: [{ text: `${id}()` }], asbuilt: { text, ...(proven ? { proven } : {}) } });
  writeFileSync(file, JSON.stringify({ id: "built", children: [{ id: "t.py", children: [
    child("one", "def one():\n    return other()\n", sentence), child("two", "def two():\n    return 1\n"),
  ] }] }));
  const { run, address } = serve(file, t.signal);
  address.catch(() => {});
  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(await address);
    await page.locator("[data-box='two']").waitFor();
    await page.getByText("one()", { exact: true }).click();
    await page.locator("#code").waitFor();
    const panel = await page.locator("#code").innerText();
    assert.ok(panel.includes("def one():") && panel.includes(sentence));
    await page.locator("#chart > svg [data-box='two']").waitFor();
    const placed = () => page.evaluate(() => {
      const code = document.getElementById("code").getBoundingClientRect();
      const svg = document.querySelector("#chart > svg").getBoundingClientRect();
      const scroll = document.documentElement.scrollWidth - document.documentElement.clientWidth;
      return [code.left >= svg.right - 1 && code.top < svg.bottom, code.top >= svg.bottom - 1, scroll];
    });
    const wide = await placed();
    assert.deepEqual([wide[0], wide[2]], [true, 0]);
    await page.setViewportSize({ width: 700, height: 800 });
    const narrow = await placed();
    assert.deepEqual([narrow[1], narrow[2]], [true, 0]);
  } finally {
    run.kill();
    await browser?.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("choosing a box hides what its arrows cannot reach, and whole chart brings it back", { timeout: 60_000 }, async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "asbuilt-page-"));
  const file = join(dir, "plan.json");
  const run = { id: "run", labels: [{ text: "run()" }], asbuilt: { text: "def run():\n    pass\n" } };
  writeFileSync(file, JSON.stringify({
    id: "root",
    children: [{ id: "tests", children: [run, { id: "report" }] }, { id: "results" }, { id: "aside" }],
    edges: [{ id: "e1", sources: ["run"], targets: ["results"] }, { id: "e2", sources: ["results"], targets: ["report"] }],
  }));
  const { run: server, address } = serve(file, t.signal);
  address.catch(() => {});
  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(await address);
    await page.locator("[data-box='aside']").waitFor();
    await page.getByText("run()", { exact: true }).click();
    await page.locator("#pipeline").click();
    await page.locator("[data-box='aside']").waitFor({ state: "detached" });
    await page.locator("[data-box='results']").waitFor();
    await page.locator("[data-box='report']").waitFor();
    await page.locator("[data-box='tests']").waitFor();
    await page.locator("#whole").click();
    await page.locator("[data-box='aside']").waitFor();
  } finally {
    server.kill();
    await browser?.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("a file box offers its pipeline with no code, and choosing it keeps what that file reaches", { timeout: 60_000 }, async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "asbuilt-page-"));
  const file = join(dir, "plan.json");
  writeFileSync(file, JSON.stringify({
    id: "app",
    children: [{ id: "a.py", children: [{ id: "load" }, { id: "skip" }] }, { id: "other" }, { id: "aside" }],
    edges: [{ id: "e", sources: ["load"], targets: ["other"] }],
  }));
  const { run: server, address } = serve(file, t.signal);
  address.catch(() => {});
  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(await address);
    await page.locator("[data-box='aside']").waitFor();
    await page.getByText("a.py", { exact: true }).click();
    await page.locator("#pipeline").waitFor();
    assert.equal(await page.locator("#code pre").count(), 0);
    await page.locator("#pipeline").click();
    await page.locator("[data-box='aside']").waitFor({ state: "detached" });
    for (const id of ["a.py", "load", "skip", "other"]) await page.locator(`[data-box='${id}']`).waitFor();
  } finally {
    server.kill();
    await browser?.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("a save that drops the focused box returns the whole chart and hides whole chart", { timeout: 60_000 }, async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "asbuilt-page-"));
  const file = join(dir, "plan.json");
  const run = { id: "run", labels: [{ text: "run()" }], asbuilt: { text: "def run():\n    pass\n" } };
  writeFileSync(file, JSON.stringify({
    id: "root",
    children: [run, { id: "kept" }, { id: "aside" }],
    edges: [{ id: "e", sources: ["run"], targets: ["kept"] }],
  }));
  const { run: server, address } = serve(file, t.signal);
  address.catch(() => {});
  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(await address);
    await page.locator("[data-box='aside']").waitFor();
    await page.getByText("run()", { exact: true }).click();
    await page.locator("#pipeline").click();
    await page.locator("[data-box='aside']").waitFor({ state: "detached" });
    await page.locator("#whole").waitFor();
    writeFileSync(file, JSON.stringify({ id: "root", children: [{ id: "kept" }, { id: "aside" }, { id: "fresh" }] }));
    await page.locator("[data-box='fresh']").waitFor();
    await page.locator("[data-box='aside']").waitFor();
    await page.locator("[data-box='run']").waitFor({ state: "detached" });
    assert.equal(await page.locator("#whole").evaluate((button) => button.hidden), true);
  } finally {
    server.kill();
    await browser?.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("closing the code while a filter is on leaves whole chart visible", { timeout: 60_000 }, async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "asbuilt-page-"));
  const file = join(dir, "plan.json");
  const run = { id: "run", labels: [{ text: "run()" }], asbuilt: { text: "def run():\n    pass\n" } };
  writeFileSync(file, JSON.stringify({
    id: "root",
    children: [run, { id: "kept" }, { id: "aside" }],
    edges: [{ id: "e", sources: ["run"], targets: ["kept"] }],
  }));
  const { run: server, address } = serve(file, t.signal);
  address.catch(() => {});
  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(await address);
    await page.locator("[data-box='aside']").waitFor();
    await page.locator("#chart").getByText("run()", { exact: true }).click();
    await page.locator("#pipeline").click();
    await page.locator("[data-box='aside']").waitFor({ state: "detached" });
    await page.locator("#chart").getByText("run()", { exact: true }).click();
    await page.locator("#whole").waitFor();
    assert.equal(await page.locator("#pipeline").evaluate((button) => button.hidden), true);
    assert.equal(await page.locator("#code").evaluate((el) => el.hidden), true);
    await page.locator("[data-box='aside']").waitFor({ state: "detached" });
    await page.locator("[data-box='kept']").waitFor();
  } finally {
    server.kill();
    await browser?.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("choosing marks keeps those boxes and the arrows between them, and all brings the chart back", { timeout: 60_000 }, async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "asbuilt-page-"));
  const file = join(dir, "plan.json");
  const badge = (id, badges) => ({ id, labels: [{ text: `${id}()` }], asbuilt: { badges } });
  writeFileSync(file, JSON.stringify({
    id: "app",
    children: [{
      id: "a.py",
      children: [badge("read", ["env"]), badge("post", ["calls out"]), { id: "local", labels: [{ text: "local()" }] }],
      edges: [
        { id: "uses", sources: ["read"], targets: ["post"] },
        { id: "aside", sources: ["read"], targets: ["local"] },
      ],
    }, badge("ask", ["model"]), badge("far", ["model"])],
    edges: [{ id: "out", sources: ["post"], targets: ["ask"] }, { id: "gone", sources: ["far"], targets: ["local"] }],
  }));
  const { run: server, address } = serve(file, t.signal);
  address.catch(() => {});
  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(await address);
    await page.locator("[data-box='local']").waitFor();
    await page.locator("[data-arrow='aside']").waitFor();
    assert.equal(await page.locator("#mark-all").getAttribute("aria-pressed"), "true");
    assert.equal(await page.locator("[data-mark='env']").innerText(), "env 1");
    assert.equal(await page.locator("[data-mark='model']").innerText(), "model 2");
    assert.equal(await page.locator("[data-mark='input']").innerText(), "input 0");
    await page.locator("[data-mark='env']").click();
    await page.locator("[data-box='local']").waitFor({ state: "detached" });
    assert.equal(await page.locator("[data-mark='env']").getAttribute("aria-pressed"), "true");
    assert.equal(await page.locator("#mark-all").getAttribute("aria-pressed"), "false");
    await page.locator("[data-mark='env']").click();
    await page.locator("[data-box='local']").waitFor();
    assert.equal(await page.locator("[data-mark='env']").getAttribute("aria-pressed"), "false");
    assert.equal(await page.locator("#mark-all").getAttribute("aria-pressed"), "true");
    await page.locator("[data-mark='env']").click();
    await page.locator("[data-mark='calls out']").click();
    await page.locator("[data-box='local']").waitFor({ state: "detached" });
    await page.locator("[data-box='ask']").waitFor({ state: "detached" });
    await page.locator("[data-arrow='aside']").waitFor({ state: "detached" });
    await page.locator("[data-arrow='out']").waitFor({ state: "detached" });
    await page.locator("[data-box='read']").waitFor();
    await page.locator("[data-box='post']").waitFor();
    await page.locator("[data-arrow='uses']").waitFor();
    await page.locator("[data-box='a.py']").waitFor();
    assert.equal(await page.locator("#marks-note").innerText(), "Showing boxes with any of: env, calls out. Hiding 3 boxes and 3 arrows.");
    await page.locator("#mark-all").click();
    await page.locator("[data-box='local']").waitFor();
    await page.locator("[data-arrow='aside']").waitFor();
    assert.equal(await page.locator("#marks-note").innerText(), "");
    await page.getByText("read()", { exact: true }).click();
    await page.locator("#pipeline").click();
    await page.locator("[data-box='far']").waitFor({ state: "detached" });
    assert.equal(await page.locator("[data-mark='model']").innerText(), "model 1");
    assert.equal(await page.locator("[data-mark='env']").innerText(), "env 1");
    await page.locator("[data-mark='calls out']").click();
    await page.locator("[data-mark='model']").click();
    await page.locator("[data-box='local']").waitFor({ state: "detached" });
    await page.locator("[data-box='read']").waitFor({ state: "detached" });
    await page.locator("[data-box='far']").waitFor({ state: "detached" });
    await page.locator("[data-box='ask']").waitFor();
    await page.locator("[data-box='post']").waitFor();
    await page.locator("[data-arrow='out']").waitFor();
    await page.locator("[data-arrow='uses']").waitFor({ state: "detached" });
    assert.equal(await page.locator("#marks-note").innerText(), "Showing boxes with any of: calls out, model. Hiding 2 boxes and 2 arrows.");
    assert.equal(await page.locator("[data-mark='model']").innerText(), "model 1");
    assert.equal(await page.locator("[data-mark='env']").innerText(), "env 1");
  } finally {
    server.kill();
    await browser?.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("a mark no box carries is a plain empty chart, not an error over the old one", { timeout: 60_000 }, async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "asbuilt-page-"));
  const file = join(dir, "plan.json");
  writeFileSync(file, JSON.stringify({
    id: "app",
    children: [{ id: "read", labels: [{ text: "read()" }], asbuilt: { badges: ["env"], text: "def read():\n    pass\n" } }, { id: "local", labels: [{ text: "local()" }] }],
    edges: [{ id: "e", sources: ["read"], targets: ["local"] }],
  }));
  const { run: server, address } = serve(file, t.signal);
  address.catch(() => {});
  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(await address);
    await page.locator("[data-box='local']").waitFor();
    assert.equal(await page.locator("[data-mark='input']").innerText(), "input 0");
    assert.equal(await page.locator("[data-mark='model']").innerText(), "model 0");
    assert.equal(await page.locator("[data-mark='env']").innerText(), "env 1");
    await page.locator("[data-mark='input']").click();
    await page.locator(".empty").waitFor();
    assert.equal(await page.locator(".empty").innerText(), "No box here carries input.");
    assert.equal(await page.locator("#marks-note").innerText(), "");
    assert.equal(await page.locator(".error").count(), 0);
    assert.equal(await page.locator(".stale").count(), 0);
    assert.equal(await page.locator("[data-box]").count(), 0);
    assert.equal(await page.locator("[data-mark='env']").innerText(), "env 1");
    await page.locator("#mark-all").click();
    await page.locator("[data-box='read']").waitFor();
    await page.getByText("read()", { exact: true }).click();
    await page.locator("#pipeline").click();
    await page.locator("[data-mark='model']").click();
    await page.locator(".empty").waitFor();
    assert.equal(await page.locator(".empty").innerText(), "No box in what read() reaches carries model.");
    assert.equal(await page.locator("#marks-note").innerText(), "");
    assert.equal(await page.locator(".error").count(), 0);
  } finally {
    server.kill();
    await browser?.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("a bad plan still shows the error while an empty mark is on", { timeout: 60_000 }, async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "asbuilt-page-"));
  const file = join(dir, "plan.json");
  writeFileSync(file, JSON.stringify({
    id: "app",
    children: [{ id: "read", labels: [{ text: "read()" }], asbuilt: { badges: ["env"] } }],
  }));
  const { run: server, address } = serve(file, t.signal);
  address.catch(() => {});
  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(await address);
    await page.locator("[data-box='read']").waitFor();
    await page.locator("[data-mark='input']").click();
    await page.locator("#chart > .empty").waitFor();
    writeFileSync(file, "{ not json");
    await page.locator("#chart > .error").waitFor();
    assert.match(await page.locator("#chart > .error").innerText(), /is not valid JSON/);
    assert.equal(await page.locator("#chart > .empty").count(), 0);
  } finally {
    server.kill();
    await browser?.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("a dropped server still says the connection was lost while an empty mark is on", { timeout: 60_000 }, async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "asbuilt-page-"));
  const file = join(dir, "plan.json");
  writeFileSync(file, JSON.stringify({
    id: "app",
    children: [{ id: "read", labels: [{ text: "read()" }], asbuilt: { badges: ["env"] } }],
  }));
  const { run: server, address } = serve(file, t.signal);
  address.catch(() => {});
  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(await address);
    await page.locator("[data-box='read']").waitFor();
    await page.locator("[data-mark='input']").click();
    await page.locator("#chart > .empty").waitFor();
    server.kill();
    await page.locator("#chart > .error").waitFor();
    assert.match(await page.locator("#chart > .error").innerText(), /Lost the connection to asbuilt/);
    assert.equal(await page.locator("#chart > .empty").count(), 0);
  } finally {
    server.kill();
    await browser?.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("opening a box shows the everyday note and both security sentences", { timeout: 60_000 }, async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "asbuilt-page-"));
  const file = join(dir, "plan.json");
  const note = "OpenRouter sends a prompt to openrouter.ai over the internet.";
  const plain = "This reads OPENROUTER_API_KEY from the environment: os.environ.get(KEY_NAME).";
  const expert = "os.environ.get(KEY_NAME) reads OPENROUTER_API_KEY from the environment.";
  writeFileSync(file, JSON.stringify({
    id: "app",
    children: [{
      id: "read",
      labels: [{ text: "read()" }],
      asbuilt: {
        text: "def read():\n    return os.environ.get(KEY_NAME)\n",
        plain: [note],
        security: [{ plain, expert }],
      },
    }],
  }));
  const { run: server, address } = serve(file, t.signal);
  address.catch(() => {});
  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(await address);
    await page.getByText("read()", { exact: true }).click();
    await page.locator("#code .plain").waitFor();
    assert.equal(await page.locator("#code .plain").innerText(), note);
    const panel = await page.locator("#code").innerText();
    const at = panel.indexOf(note);
    assert.ok(at >= 0);
    assert.ok(at < panel.indexOf("Expert"));
    assert.ok(panel.includes(plain));
    assert.ok(panel.includes(expert));
  } finally {
    server.kill();
    await browser?.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
