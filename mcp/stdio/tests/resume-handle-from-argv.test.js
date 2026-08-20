#!/usr/bin/env node
// Recovering the resume handle from ARGV instead of by regex over a shell string.
//
// The bridge has always parsed the command string to find `--resume <handle>`: per-runtime regexes
// plus shell unquoting. That parse has already shipped a defect -- codex's and opencode's forms went
// unrecognised, so the heal path could never fire and workers were handed a blank CODEX_THREAD_ID.
//
// v0.6 Phase 8 puts `argv` on the terminal row beside the command. Where it is present the handle can
// be read structurally: find the flag, take the next element. No regex, no unquoting, and nothing a
// quote in a workspace path can defeat.
//
// AGREEMENT IS THE PROPERTY UNDER TEST, not the extraction. Both readers will be live at once -- rows
// created before the column exists carry no argv and must keep using the string -- so the two must
// answer identically on every launch this project generates. A test of the new one alone would pass
// while the pair disagreed.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  extractRuntimeSessionHandleFromArgv,
  extractRuntimeSessionHandleFromCommand,
  resumeFlagsForRuntime,
} from "../runtimes.js";

const RUNTIMES = ["claude-code", "codex", "hermes", "opencode", "pi"];
const HANDLE = "sess-9f3a";

test("every runtime declares resume flags, so none is silently unparseable", () => {
  // The defect that motivated this: two runtimes were missing from the flag table and nothing said so.
  for (const runtime of RUNTIMES) {
    assert.ok(resumeFlagsForRuntime(runtime).length > 0, `${runtime} declares no resume flags`);
  }
});

test("argv and the joined string agree on the handle, for every runtime and flag", () => {
  for (const runtime of RUNTIMES) {
    for (const flag of resumeFlagsForRuntime(runtime)) {
      const argv = [`${runtime}-aify`, "--aify-agent", "a1", flag, HANDLE];
      assert.equal(
        extractRuntimeSessionHandleFromArgv(runtime, argv),
        HANDLE,
        `${runtime} ${flag}: argv reader missed the handle`,
      );
      assert.equal(
        extractRuntimeSessionHandleFromArgv(runtime, argv),
        extractRuntimeSessionHandleFromCommand(runtime, argv.join(" ")),
        `${runtime} ${flag}: the two readers disagree`,
      );
    }
  }
});

test("the `--flag=value` spelling is read too", () => {
  for (const runtime of RUNTIMES) {
    const flag = resumeFlagsForRuntime(runtime)[0];
    const argv = [`${runtime}-aify`, `${flag}=${HANDLE}`];
    assert.equal(extractRuntimeSessionHandleFromArgv(runtime, argv), HANDLE, `${runtime} ${flag}=`);
  }
});

test("codex's positional subcommand form is read", () => {
  // The dashboard renders `codex --no-alt-screen resume --include-non-interactive <handle>`, where the
  // load-bearing part is the POSITIONAL id rather than a flag.
  assert.equal(
    extractRuntimeSessionHandleFromArgv("codex", ["codex", "--no-alt-screen", "resume", "--include-non-interactive", HANDLE]),
    HANDLE,
  );
  assert.equal(extractRuntimeSessionHandleFromArgv("codex", ["codex", "resume", HANDLE]), HANDLE);
});

test("no resume flag means no handle, not a wrong one", () => {
  for (const runtime of RUNTIMES) {
    assert.equal(extractRuntimeSessionHandleFromArgv(runtime, [`${runtime}-aify`, "--aify-agent", "a1"]), "");
  }
});

test("a flag with nothing after it yields no handle rather than reading past the end", () => {
  assert.equal(extractRuntimeSessionHandleFromArgv("claude-code", ["claude-aify", "--resume"]), "");
});

test("anything that is not an array of strings yields no handle", () => {
  // Callers get argv from a JSON column. It fails closed on every shape that is not one.
  for (const bad of [null, undefined, "", "--resume x", 42, {}, [1, 2]]) {
    assert.equal(extractRuntimeSessionHandleFromArgv("claude-code", bad), "");
  }
});

test("a quoted handle survives argv where the string reader has to unquote it", () => {
  // The point of the structural form: argv elements are already separated, so nothing has to guess
  // where a token ends. A workspace path with a space in it is the case that breaks a regex.
  const argv = ["claude-aify", "--resume", "sess with space"];
  assert.equal(extractRuntimeSessionHandleFromArgv("claude-code", argv), "sess with space");
});
