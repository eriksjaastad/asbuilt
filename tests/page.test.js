// The page as a person sees it, in a real browser: the asbuilt command serves a plan, a save redraws it, a broken
// save shows the error over the last good chart, dimmed, and fixing the file brings the chart back.
// Only this test runs main.js, EventSource and the DOM; the others test the page's modules as functions.
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
function serve(file) {
  const run = spawn("uv", ["run", "-q", "asbuilt", file, "--port", "0"], { stdio: ["ignore", "pipe", "pipe"] });
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

// The timeout bounds every wait, including a server that never prints its address; finally stops the server
// and the browser whatever fails, so a missing browser fails the run instead of hanging it.
test("a save redraws the page, a broken save keeps the last good chart dimmed, and a fix brings it back", { timeout: 60_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), "asbuilt-page-"));
  const file = join(dir, "plan.json");
  writeFileSync(file, plan(["read", "draw"]));
  const { run, address } = serve(file);
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
