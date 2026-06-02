import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Unit-tests the non-secure-context clipboard fallback that powers the
// dashboard Console "Copy" button + Ctrl+Shift+C. The dashboard is typically
// served over plain http://192.168.x:8800 where navigator.clipboard is
// undefined, so copy MUST work via the legacy execCommand path.
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const htmlPath = path.join(__dirname, "..", "dashboard.html");
const source = fs.readFileSync(htmlPath, "utf8");

const fnMatch = source.match(/\nfunction legacyCopyText\([\s\S]*?\n\}\n/);
if (!fnMatch) throw new Error("legacyCopyText not found in dashboard.html");

// Build a fake DOM that records what legacyCopyText does.
function makeHarness({ execCommandResult = true } = {}) {
  const calls = { created: [], appended: [], removed: [], execCommand: [], selected: false };
  const fakeTextarea = {
    style: {},
    value: "",
    setAttribute() {},
    focus() {},
    select() { calls.selected = true; },
    setSelectionRange() {},
  };
  const document = {
    createElement(tag) { calls.created.push(tag); return fakeTextarea; },
    body: {
      appendChild(node) { calls.appended.push(node); },
      removeChild(node) { calls.removed.push(node); },
    },
    execCommand(cmd) { calls.execCommand.push(cmd); return execCommandResult; },
  };
  const fn = new Function("document", `${fnMatch[0]}\nreturn legacyCopyText;`)(document);
  return { fn, calls, fakeTextarea };
}

test("legacyCopyText copies via a hidden textarea + execCommand('copy')", () => {
  const { fn, calls, fakeTextarea } = makeHarness({ execCommandResult: true });
  const ok = fn("hello world");
  assert.equal(ok, true);
  assert.deepEqual(calls.created, ["textarea"]);
  assert.equal(fakeTextarea.value, "hello world");
  assert.equal(calls.selected, true);
  assert.deepEqual(calls.execCommand, ["copy"]);
  // Must always clean up the temporary element.
  assert.equal(calls.appended.length, 1);
  assert.equal(calls.removed.length, 1);
});

test("legacyCopyText returns false when execCommand reports failure", () => {
  const { fn, calls } = makeHarness({ execCommandResult: false });
  const ok = fn("nope");
  assert.equal(ok, false);
  // Still cleaned up even on failure.
  assert.equal(calls.removed.length, 1);
});

test("legacyCopyText tolerates an empty value", () => {
  const { fn, fakeTextarea } = makeHarness({ execCommandResult: true });
  const ok = fn();
  assert.equal(ok, true);
  assert.equal(fakeTextarea.value, "");
});
