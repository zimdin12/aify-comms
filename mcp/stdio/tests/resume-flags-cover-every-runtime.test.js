#!/usr/bin/env node
// Every runtime's resume flags are in the table the command reader is built from, and it reads them.
//
// The bridge parses a launch command string to find `--resume <handle>`, with per-runtime regexes
// built from one flag table. That parse has already shipped a defect -- codex's and opencode's forms
// went unrecognised, so the heal path could never fire and workers were handed a blank
// CODEX_THREAD_ID. The table is the thing to keep complete.

import assert from "node:assert/strict";
import { test } from "node:test";

import { extractRuntimeSessionHandleFromCommand, resumeFlagsForRuntime } from "../runtimes.js";

const RUNTIMES = ["claude-code", "codex", "hermes", "opencode", "pi"];
const HANDLE = "sess-9f3a";

test("every runtime declares resume flags, so none is silently unparseable", () => {
  for (const runtime of RUNTIMES) {
    assert.ok(resumeFlagsForRuntime(runtime).length > 0, `${runtime} declares no resume flags`);
  }
});

test("the command reader finds the handle after every flag in the table", () => {
  for (const runtime of RUNTIMES) {
    for (const flag of resumeFlagsForRuntime(runtime)) {
      const command = `${runtime}-aify --aify-agent a1 ${flag} ${HANDLE}`;
      assert.equal(extractRuntimeSessionHandleFromCommand(runtime, command), HANDLE, `${runtime} ${flag}`);
    }
  }
});

test("no resume flag means no handle, not a wrong one", () => {
  for (const runtime of RUNTIMES) {
    assert.equal(extractRuntimeSessionHandleFromCommand(runtime, `${runtime}-aify --aify-agent a1`), "");
  }
});
