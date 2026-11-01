import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const dir = new URL("../src/asbuilt/page/vendor/", import.meta.url);
const read = (name) => readFileSync(new URL(name, dir));
const { files } = JSON.parse(read("manifest.json"));
const lock = JSON.parse(readFileSync(new URL("../package-lock.json", import.meta.url))).packages;
// Locked for the tests alone and never shipped to the page, so not vendored: the browser test's driver.
const TEST_ONLY = ["playwright-core"];

test("every vendored file is exactly the file the manifest names, by sha256", () => {
  for (const f of files) assert.equal(createHash("sha256").update(read(f.file)).digest("hex"), f.sha256, f.file);
});

test("the manifest names the version and tarball integrity that package-lock.json pins, for every package", () => {
  for (const f of files) {
    const pinned = lock[`node_modules/${f.package}`];
    assert.match(f.integrity, /^sha512-/, f.file);
    assert.deepEqual({ version: f.version, integrity: f.integrity }, { version: pinned?.version, integrity: pinned?.integrity }, f.file);
  }
});

test("nothing is vendored that the manifest does not list, and every shipped package has its licence", () => {
  const found = readdirSync(dir, { recursive: true, withFileTypes: true }).filter((e) => e.isFile()).map((e) => (e.parentPath.endsWith("licenses") ? "licenses/" : "") + e.name);
  assert.deepEqual(found.sort(), [...files.map((f) => f.file), "manifest.json"].sort());
  for (const key of Object.keys(lock).filter((k) => k)) {
    const name = key.slice("node_modules/".length);
    if (TEST_ONLY.includes(name)) continue;
    assert.ok(files.some((f) => f.package === name && f.file.startsWith("licenses/")), name);
  }
});

test("the two libraries define the globals the page expects", () => {
  const sandbox = { window: {} };
  for (const name of ["elk.js", "rough.js"]) vm.runInNewContext(read(name).toString(), sandbox);
  assert.equal(typeof sandbox.window.ELK, "function");
  assert.equal(typeof sandbox.rough.generator, "function");
});
