// The overview page's modules as functions, then once in a real browser against `asbuilt DIR`.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { chromium } from "playwright-core";
import {
  NO_PATH_HINT,
  NO_PATH_LABEL,
  PACKAGES_TITLE,
  createApp,
  esc,
  initialState,
  renderHeader,
  renderOverview,
  renderPackages,
  safeUrl,
} from "../src/asbuilt/page/overview.js";

function samplePythonData() {
  return {
    project: "demo-py",
    skipped: ["tests", "build"],
    packages: [
      {
        name: "click",
        spec: ">=8.1",
        registry: "pypi",
        url: "https://pypi.org/project/click/",
        dev: false,
      },
      {
        name: "pytest",
        spec: ">=8.0",
        registry: "pypi",
        url: "https://pypi.org/project/pytest/",
        dev: true,
      },
    ],
    components: {
      groups: [
        { kind: "commands", components: ["demo"] },
        { kind: "scripts", components: ["worker"] },
      ],
      components: [
        {
          name: "demo",
          kind: "commands",
          ways_in: ["console script demo = cli:run"],
          roots: ["cli.py:run:3"],
          members: [
            { id: "cli.py:run:3", name: "run()", why: "entry point" },
          ],
        },
        {
          name: "worker",
          kind: "scripts",
          ways_in: ["python worker.py"],
          roots: ["worker.py:main:3"],
          members: [
            { id: "worker.py:main:3", name: "main()", why: "entry point" },
          ],
        },
      ],
      shared: [
        {
          name: "util.py",
          file: "sub/util.py",
          used_by: ["demo", "worker"],
          members: [
            {
              id: "sub/util.py:shared_helper:1",
              name: "shared_helper()",
              used_by: {
                demo: "reached from run()",
                worker: "reached from main()",
              },
            },
          ],
        },
      ],
      no_path_found: [
        { id: "sub/util.py:orphan_fn:5", name: "orphan_fn()" },
      ],
      arrows: [],
      entries_reaching_nothing: [],
      not_proven: [],
      files_not_read: [],
    },
    files: [
      { id: "cli.py", name: "cli.py", boxes: ["cli.py:run:3"], calls: [] },
      { id: "worker.py", name: "worker.py", boxes: ["worker.py:main:3"], calls: [] },
      {
        id: "sub/util.py",
        name: "util.py",
        boxes: ["sub/util.py:shared_helper:1", "sub/util.py:orphan_fn:5"],
        calls: [],
      },
    ],
    boxes: {
      "cli.py:run:3": {
        id: "cli.py:run:3",
        file: "cli.py",
        name: "run()",
        lines: [3, 8],
        plain: ["Reads the API token and delegates to shared_helper."],
        proven: "Calls shared_helper() and returns its result.",
        badges: ["env", "calls out"],
        security: [
          {
            fact: { kind: "env", how: "read", name: "API_TOKEN" },
            plain: "This reads API_TOKEN from the environment.",
            expert: "os.environ.get('API_TOKEN') reads API_TOKEN.",
          },
        ],
        text: "def run():\n    return shared_helper()\n",
        rest: null,
        calls: ["sub/util.py:shared_helper:1"],
      },
      "worker.py:main:3": {
        id: "worker.py:main:3",
        file: "worker.py",
        name: "main()",
        lines: [3, 6],
        plain: ["Runs the background job."],
        proven: null,
        badges: [],
        security: [],
        text: "def main():\n    return shared_helper()\n",
        rest: null,
        calls: ["sub/util.py:shared_helper:1"],
      },
      "sub/util.py:shared_helper:1": {
        id: "sub/util.py:shared_helper:1",
        file: "sub/util.py",
        name: "shared_helper()",
        lines: [1, 3],
        plain: ["Returns a constant value."],
        proven: null,
        badges: [],
        security: [],
        text: "def shared_helper():\n    return 1\n",
        rest: null,
        calls: [],
      },
      "sub/util.py:orphan_fn:5": {
        id: "sub/util.py:orphan_fn:5",
        file: "sub/util.py",
        name: "orphan_fn()",
        lines: [5, 7],
        plain: ["Standalone utility."],
        proven: null,
        badges: [],
        security: [],
        text: "def orphan_fn():\n    return 2\n",
        rest: null,
        calls: [],
      },
    },
  };
}

function sampleJsData() {
  return {
    project: "@acme/web-app",
    skipped: [],
    not_read: [],
    packages: [],
    components: null,
    js_components: {
      groups: [{ kind: "web pages", components: ["App"] }],
      components: [
        {
          name: "App",
          kind: "web pages",
          ways_in: ["createRoot render in src/app.tsx"],
          roots: ["src/app.tsx:greet:3"],
          members: [
            { id: "src/app.tsx:greet:3", name: "greet()", file: "src/app.tsx", why: "entry point" },
            { id: "src/util.ts:formatName:1", name: "formatName()", file: "src/util.ts", why: "reached from greet()" },
          ],
        },
      ],
      shared: [],
      no_path_found: [],
    },
    files: [
      { id: "src/app.tsx", boxes: ["src/app.tsx:greet:3"], imports: ["src/util.ts"] },
      { id: "src/util.ts", boxes: ["src/util.ts:formatName:1"], imports: [] },
    ],
    boxes: {
      "src/app.tsx:greet:3": {
        id: "src/app.tsx:greet:3", file: "src/app.tsx", name: "greet()", lines: [3, 5], plain: [], proven: null,
        badges: [], security: [], text: "export function greet(who) {}\n", rest: null, calls: ["src/util.ts:formatName:1"],
      },
      "src/util.ts:formatName:1": {
        id: "src/util.ts:formatName:1", file: "src/util.ts", name: "formatName()", lines: [1, 3], plain: [], proven: null,
        badges: [], security: [], text: "export function formatName(n) {}\n", rest: null, calls: [],
      },
    },
  };
}

// A root that records the click handler createApp installs, and a click on an element with these attributes.
function fakeRoot() {
  const listeners = new Map();
  const root = { innerHTML: "", addEventListener: (type, fn) => listeners.set(type, fn) };
  const click = (attrs) =>
    listeners.get("click")({ target: { closest: () => ({ getAttribute: (k) => attrs[k] ?? null }) } });
  return { root, click };
}

test("esc escapes HTML characters and safeUrl rejects non-http(s) URLs", () => {
  assert.equal(esc('<script>alert("x")&\'</script>'), "&lt;script&gt;alert(&quot;x&quot;)&amp;&#39;&lt;/script&gt;");
  assert.equal(safeUrl("https://pypi.org/project/click/"), "https://pypi.org/project/click/");
  assert.equal(safeUrl("javascript:alert(1)"), "");
  assert.equal(safeUrl("data:text/html,<h1>x</h1>"), "");
  assert.equal(safeUrl(null), "");
});

test("dynamic text is HTML-escaped across header, notices, functions, and packages", () => {
  const data = samplePythonData();
  data.project = '<img src=x onerror="alert(1)">';
  data.skipped = ["<bad-dir>"];
  data.not_read = ["<x>.py: why"];
  data.js_error = "<js-boom>";
  data.packages_unreadable = ["<p>.json"];
  data.boxes["cli.py:run:3"].plain = ["Uses <script>bad()</script> safely."];
  data.packages.push({ name: "<b>evil</b>", spec: "<1.0", registry: "pypi", url: "javascript:alert(1)", dev: false, tags: ["<t>"] });
  const html = renderOverview(data, { ...initialState(data), component: "demo", file: "cli.py" });
  for (const raw of ["<img src=x", "<script>bad()", "<b>evil</b>", "javascript:alert(1)", "<bad-dir>", "<x>.py", "<js-boom>", "<p>.json", "<t>"]) {
    assert.equal(html.includes(raw), false, raw);
  }
  assert.ok(html.includes("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"));
  assert.ok(html.includes("Uses &lt;script&gt;bad()&lt;/script&gt; safely."));
});

test("a Python component opens to its files, and a file to that component's functions in it", () => {
  const data = samplePythonData();
  const { root, click } = fakeRoot();
  createApp(root, data);
  // The first screen is the groups, not any group's components.
  assert.ok(root.innerHTML.includes('data-action="select-tab" data-tab="group:commands"'));
  assert.ok(root.innerHTML.includes("1 component"));
  assert.equal(root.innerHTML.includes('data-action="open-component"'), false);
  assert.ok(root.innerHTML.includes("Skipped (2):"));
  click({ "data-action": "select-tab", "data-tab": "group:commands" });
  assert.ok(root.innerHTML.includes('data-action="open-component" data-component="demo"'));

  click({ "data-action": "open-component", "data-component": "demo" });
  assert.ok(root.innerHTML.includes("console script demo = cli:run"));
  assert.ok(root.innerHTML.includes('data-action="open-file" data-file="cli.py"'));
  assert.equal(root.innerHTML.includes("ov-fn-card"), false, "functions wait for a file to be opened");

  click({ "data-action": "open-file", "data-file": "cli.py" });
  const html = root.innerHTML;
  assert.ok(html.includes('data-box-id="cli.py:run:3"'));
  assert.ok(html.includes("entry point"));
  assert.ok(html.includes("Security: 1 thing to know"));
  assert.ok(html.includes("shared_helper() (sub/util.py)"));
  assert.ok(html.includes("def run():"));

  click({ "data-action": "back" });
  assert.ok(root.innerHTML.includes('data-file="cli.py"'), "back from a file is the component");
  click({ "data-action": "back" });
  assert.ok(root.innerHTML.includes('data-component="demo"'), "back from a component is its group");
  click({ "data-action": "back" });
  assert.equal(root.innerHTML.includes('data-component="demo"'), false, "back from a group is the groups");

  click({ "data-action": "select-tab", "data-tab": "group:scripts" });
  assert.ok(root.innerHTML.includes('data-component="worker"'));
});

test("no_path_found uses the honest label and soft hint and never says dead or unreached", () => {
  const data = samplePythonData();
  const html = renderOverview(data, { ...initialState(data), tab: "no_path_found" });
  assert.ok(html.includes(NO_PATH_LABEL));
  assert.ok(html.includes(NO_PATH_HINT));
  assert.ok(html.includes("orphan_fn()"));
  assert.equal(/\bdead\b|\bunreached\b/i.test(html), false);
});

test("shared helpers list and open with which components use each helper", () => {
  const data = samplePythonData();
  const state = initialState(data);
  assert.ok(renderOverview(data, { ...state, tab: "shared" }).includes("Used by: demo, worker"));
  const detail = renderOverview(data, { ...state, tab: "shared", shared: "sub/util.py" });
  assert.ok(detail.includes("demo: reached from run()"));
  assert.ok(detail.includes("worker: reached from main()"));
});

test("a JavaScript project opens on its components, with the same component, file, function path", () => {
  const data = sampleJsData();
  const { root, click } = fakeRoot();
  createApp(root, data);
  assert.ok(root.innerHTML.includes("JavaScript / TypeScript"));
  click({ "data-action": "select-tab", "data-tab": "group:web pages" });
  assert.ok(root.innerHTML.includes('data-component="App"'));
  click({ "data-action": "open-component", "data-component": "App" });
  assert.ok(root.innerHTML.includes('data-file="src/util.ts"'));
  click({ "data-action": "open-file", "data-file": "src/util.ts" });
  assert.ok(root.innerHTML.includes("reached from greet()"));
  assert.equal(root.innerHTML.includes('data-box-id="src/app.tsx:greet:3"'), false);
});

test("a JavaScript shared helper names the components that use it", () => {
  const data = sampleJsData();
  data.js_components.shared = [
    { name: "util.ts", file: "src/util.ts", used_by: ["App", "Cli"], members: [{ id: "src/util.ts:formatName:1", name: "formatName()", used_by: ["App", "Cli"] }] },
  ];
  const html = renderOverview(data, { ...initialState(data), tab: "shared", shared: "src/util.ts" });
  assert.ok(html.includes("used by App, Cli"));
  assert.equal(/\b0: /.test(html), false);
});

test("a mixed project switches between its Python and JavaScript components", () => {
  const data = { ...samplePythonData(), js_components: sampleJsData().js_components };
  Object.assign(data.boxes, sampleJsData().boxes);
  const { root, click } = fakeRoot();
  createApp(root, data);
  assert.ok(root.innerHTML.includes("Python + JavaScript / TypeScript"));
  assert.ok(root.innerHTML.includes('data-action="select-section" data-section="js"'));
  assert.ok(root.innerHTML.includes('data-tab="group:commands"'));
  click({ "data-action": "select-section", "data-section": "js" });
  assert.ok(root.innerHTML.includes('data-tab="group:web pages"'));
  assert.equal(root.innerHTML.includes('data-tab="group:commands"'), false);
  click({ "data-action": "select-tab", "data-tab": "group:web pages" });
  assert.ok(root.innerHTML.includes('data-component="App"'));
});

test("what was not read is shown: unparsed files, a JavaScript reader failure, unreadable manifests", () => {
  const data = samplePythonData();
  data.not_read = ["broken.py: Python does not accept it"];
  data.js_error = "web: no parseable JavaScript/TypeScript files to chart";
  data.packages_unreadable = ["package.json"];
  const html = renderOverview(data);
  assert.ok(html.includes("Files asBuilt could not read (1):"));
  assert.ok(html.includes("broken.py: Python does not accept it"));
  assert.ok(html.includes("JavaScript / TypeScript was not charted:"));
  assert.ok(html.includes("web: no parseable JavaScript/TypeScript files to chart"));
  assert.ok(html.includes("<strong>Could not read:</strong> package.json"));
  data.components.entries_reaching_nothing = ["python setup.py"];
  data.components.not_proven = ["app.get route in web.py: an app or router made in this file"];
  const diag = renderOverview(data);
  assert.ok(diag.includes("Python: entry points that reach no charted function (1):"));
  assert.ok(diag.includes("python setup.py"));
  assert.ok(diag.includes("Python: entry points asBuilt could not prove (1):"));
  const clean = renderHeader({ ...samplePythonData(), skipped: [] });
  assert.equal(/Skipped|could not read|not charted/i.test(clean), false);
});

test("the packages panel says what it lists, links each package, and tags its section", () => {
  const html = renderPackages([
    ...samplePythonData().packages,
    { name: "fsevents", spec: "^2", registry: "npm", url: "https://www.npmjs.com/package/fsevents", dev: false, tags: ["optional"] },
  ]);
  assert.ok(html.includes(PACKAGES_TITLE));
  assert.equal(PACKAGES_TITLE, "Packages this project declares");
  assert.equal(/installed/i.test(html), false);
  assert.ok(html.includes('href="https://pypi.org/project/click/" target="_blank" rel="noopener noreferrer"'));
  assert.ok(html.includes('<span class="ov-tag-dev">dev</span>'));
  assert.ok(html.includes('<span class="ov-tag-reg">optional</span>'));
  assert.ok(renderPackages([], [], []).includes("No pyproject.toml, requirements.txt or package.json found."));
  assert.ok(renderPackages([], [], ["package.json"]).includes("No packages declared there."));
  assert.equal(renderPackages([], ["pyproject.toml"], []).includes("No packages declared"), false);
  const named = renderPackages([], [], ["pyproject.toml", "package.json"], ["requirements.txt: -r base.txt"]);
  assert.ok(named.includes("From pyproject.toml, package.json in the project folder only."));
  assert.ok(named.includes("Declared there but not read (1):"));
  assert.ok(named.includes("requirements.txt: -r base.txt"));
  const bare = renderPackages([{ name: "left-pad", spec: "1", registry: "npm", url: "https://www.npmjs.com/package/left-pad", dev: false }]);
  assert.equal(/undefined|null|pkg-summary/.test(bare), false, "a package with no summary shows none");
});

// The whole path a person takes: `asbuilt DIR` prints an address, the page opens on the components,
// and a click goes component -> file -> function. Headless; no window opens.
test("asbuilt DIR serves the overview, and a click walks component, file, function", { timeout: 60_000 }, async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "asbuilt-overview-"));
  mkdirSync(join(dir, "pkg"));
  writeFileSync(join(dir, "pkg", "__init__.py"), "");
  writeFileSync(join(dir, "pkg", "util.py"), "def helper():\n    return 1\n");
  writeFileSync(join(dir, "run.py"), 'from pkg.util import helper\n\ndef main():\n    return helper()\n\nif __name__ == "__main__":\n    main()\n');
  writeFileSync(join(dir, "broken.py"), "def oops(:\n");
  const run = spawn("uv", ["run", "-q", "asbuilt", dir, "--port", "0"], { stdio: ["ignore", "pipe", "pipe"], signal: t.signal });
  let err = "";
  run.stderr.on("data", (part) => (err += part));
  const address = new Promise((resolve, reject) => {
    let out = "";
    run.stdout.on("data", (part) => {
      out += part;
      const found = out.match(/http:\/\/127\.0\.0\.1:\d+\//);
      if (found) resolve(found[0]);
    });
    run.on("exit", (code) => reject(new Error(`asbuilt exited ${code} before printing its address: ${err}`)));
    run.on("error", reject);
  });
  address.catch(() => {});
  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(await address);
    await page.locator('[data-tab="group:scripts"]').click();
    await page.locator('[data-component="run"]').click();
    await page.locator('[data-file="pkg/util.py"]').click();
    await page.locator('[data-box-id="pkg/util.py:helper:1"]').waitFor();
    assert.ok((await page.locator("header").innerText()).includes("broken.py"));
    assert.ok(err.includes("could not read broken.py"), err);
    assert.deepEqual(errors, []);
  } finally {
    run.kill();
    await browser?.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

// A React project: the mounted App is the way in, and a component nothing renders sits under no path
// found, never as a way in. Headless; no window opens.
test("asbuilt DIR on a React project lists an unrendered component under no path found", { timeout: 60_000 }, async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "asbuilt-react-"));
  mkdirSync(join(dir, "src"));
  writeFileSync(join(dir, "package.json"), '{"name":"demo"}');
  writeFileSync(join(dir, "src", "Experience.tsx"), "export function Experience() { return <section/>; }\n");
  writeFileSync(join(dir, "src", "Hero.tsx"), "export function Hero() { return <h1/>; }\n");
  writeFileSync(join(dir, "src", "main.tsx"), "import { createRoot } from 'react-dom/client';\nimport { Hero } from './Hero';\nfunction App() { return <Hero/>; }\ncreateRoot(document.getElementById('root')!).render(<App/>);\n");
  const run = spawn("uv", ["run", "-q", "asbuilt", dir, "--port", "0"], { stdio: ["ignore", "pipe", "pipe"], signal: t.signal });
  let err = "";
  run.stderr.on("data", (part) => (err += part));
  const address = new Promise((resolve, reject) => {
    let out = "";
    run.stdout.on("data", (part) => {
      out += part;
      const found = out.match(/http:\/\/127\.0\.0\.1:\d+\//);
      if (found) resolve(found[0]);
    });
    run.on("exit", (code) => reject(new Error(`asbuilt exited ${code} before printing its address: ${err}`)));
    run.on("error", reject);
  });
  address.catch(() => {});
  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(await address);
    await page.locator('[data-tab="group:web pages"]').first().click();
    const ways = await page.locator("main, body").first().innerText();
    assert.ok(!ways.includes("Experience"), "Experience must not be a way in");
    await page.locator('[data-component="main"]').click();
    await page.locator('[data-file="src/Hero.tsx"]').waitFor();
    await page.goto(await address);
    await page.locator('[data-tab="no_path_found"]').first().click();
    await page.getByText("Experience", { exact: false }).first().waitFor();
    assert.deepEqual(errors, []);
  } finally {
    run.kill();
    await browser?.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
