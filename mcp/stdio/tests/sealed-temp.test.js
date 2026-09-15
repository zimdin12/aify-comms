// tests/_sealed-temp.mjs: a test file run on its own writes hermes markers into a temp directory of its own.
import { SEALED_TEMP } from "./_sealed-temp.mjs";

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { defaultMarkerTmpDir, writeSessionIdMarker } from "../hermes-endpoint.js";

test("the marker directory a hermes module resolves is the sealed one, not the host's TEMP", () => {
  assert.equal(defaultMarkerTmpDir(), SEALED_TEMP, "a marker writer would still reach the real TEMP");
  assert.equal(os.tmpdir(), SEALED_TEMP);
  // A real write lands there.
  assert.ok(writeSessionIdMarker("sealed-probe", "sess-1"));
  assert.ok(fs.existsSync(path.join(SEALED_TEMP, "aify-hermes-session-sealed-probe")), "control: the write happened, in the sealed directory");
});
