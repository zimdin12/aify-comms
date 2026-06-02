import assert from "assert";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// pure-event-status change #4: notify-check.js (the claude PostToolUse/notify
// hook) must NO LONGER re-pulse turn_busy. The PostToolUse turn_busy re-pulse was
// the window-defeat signal that kept an agent `working` past the (now removed)
// short status window; with STATUS pure-event it would re-arm turn_busy on every
// tool call. The transcript-growth signal is now repurposed as the #1
// turn-END detector; PostToolUse keeps ONLY the unconditional liveness heartbeat.

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(path.join(__dirname, "..", "notify-check.js"), "utf-8");

test("notify-check.js no longer posts a turnBusy heartbeat body", () => {
  assert.ok(
    !/turnBusy\s*:\s*true/.test(SRC),
    "notify-check.js must NOT send a {turnBusy:true} heartbeat body (pure-event #4 removes the PostToolUse re-pulse)",
  );
});

test("notify-check.js does not special-case PostToolUse to re-pulse turn_busy", () => {
  assert.ok(
    !/PostToolUse[\s\S]{0,120}turnBusy/.test(SRC),
    "notify-check.js must not branch on PostToolUse to assert turn_busy",
  );
});
