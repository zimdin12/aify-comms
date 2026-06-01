import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const htmlPath = path.join(__dirname, "..", "dashboard.html");
const source = fs.readFileSync(htmlPath, "utf8");
const fnMatch = source.match(/\nfunction isNoteworthyDeliveryRun\([\s\S]*?\n\}\n/);
if (!fnMatch) throw new Error("isNoteworthyDeliveryRun not found in dashboard.html");
const isNoteworthyDeliveryRun = new Function(`${fnMatch[0]}\nreturn isNoteworthyDeliveryRun;`)();

test("routine delivered run with boilerplate summary is NOT noteworthy", () => {
  // Legacy / not-yet-restarted bridges may still write the old boilerplate; the
  // gate must keep treating it as noise even after D2 (#162).
  assert.equal(isNoteworthyDeliveryRun({ run: { status: "delivered", summary: "Delivered to Claude resident session; awaiting explicit reply" } }), false);
});
test("routine delivered run with EMPTY summary (D2 #162) is NOT noteworthy", () => {
  // D2: bridges now blank the routine delivery summary at the source.
  assert.equal(isNoteworthyDeliveryRun({ run: { status: "delivered", summary: "" } }), false);
});
test("completed run WITHOUT a reply is not noteworthy", () => {
  assert.equal(isNoteworthyDeliveryRun({ run: { status: "completed" } }), false);
});
test("failed run is noteworthy", () => {
  assert.equal(isNoteworthyDeliveryRun({ run: { status: "failed", error: "boom" } }), true);
});
test("cancelled run is noteworthy", () => {
  assert.equal(isNoteworthyDeliveryRun({ run: { status: "cancelled" } }), true);
});
test("steer control is noteworthy", () => {
  assert.equal(isNoteworthyDeliveryRun({ run: { status: "running" }, control: { action: "steer", status: "completed" } }), true);
});
test("queued-behind run is noteworthy", () => {
  assert.equal(isNoteworthyDeliveryRun({ run: { status: "queued", queuedBehindActiveRun: { runId: "r1" } } }), true);
});
test("run with a linked reply is noteworthy", () => {
  assert.equal(isNoteworthyDeliveryRun({ run: { status: "completed", resultMessageId: "m1" } }), true);
});
test("empty / no run is not noteworthy", () => {
  assert.equal(isNoteworthyDeliveryRun({}), false);
  assert.equal(isNoteworthyDeliveryRun({ run: null }), false);
});
