#!/usr/bin/env node

import assert from "node:assert/strict";
import { test } from "node:test";

test("Codex receipt marker keeps Codex terminal styling, not Hermes box styling", async () => {
  const { codexAifyReceiptFrame } = await import("../aify-console-markers.js");
  const frame = codexAifyReceiptFrame();

  assert.match(frame, /\[codex\] aify-comms message received/);
  assert.match(frame, /\x1b\[2m/, "Codex marker should use its native dim terminal styling");
  assert.doesNotMatch(frame, /^\+-+\+$/m, "Codex should not use Hermes box styling");
});
