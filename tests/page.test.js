// The page as a person sees it, in a real browser: the asbuilt command serves a plan, a save redraws it, a broken
// save shows the error over the last good chart, dimmed, and fixing the file brings the chart back.
// Only this test runs main.js, EventSource and the DOM; the others test the page's modules as functions.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
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
