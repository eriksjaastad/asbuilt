import assert from "node:assert/strict";
import test from "node:test";
import { latest } from "../src/asbuilt/page/latest.js";

// A state is just a name; its work finishes when finish[name]() is called, so a test sets the order.
const setup = () => {
  const shown = [];
  const finish = {};
  const work = (s) => new Promise((resolve) => (finish[s] = () => resolve(s)));
  return { shown, finish, go: latest(work, (r) => shown.push(r)) };
};

test("an older state that finishes after a newer one has started is never shown", async () => {
  const { shown, finish, go } = setup();
  const first = go("one");
  const second = go("two");
  finish.two();
  finish.one();
  await Promise.all([first, second]);
  assert.deepEqual(shown, ["two"]);
});

test("an older state that finishes before the newer one starts is shown, then the newer", async () => {
  const { shown, finish, go } = setup();
  const first = go("one");
  finish.one();
  await first;
  const second = go("two");
  finish.two();
  await second;
  assert.deepEqual(shown, ["one", "two"]);
});

test("an error thrown by work reaches the caller and shows nothing", async () => {
  const shown = [];
  const go = latest(async () => { throw new Error("boom"); }, (r) => shown.push(r));
  await assert.rejects(go("x"), /boom/);
  assert.deepEqual(shown, []);
});
