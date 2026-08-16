#!/usr/bin/env node
// The input guards on the two helpers that can kill a process.
//
// `defaultKillOnePid` and `defaultResolveListenerPids` were named by no test. Only their REFUSAL
// paths are exercised here, and that is deliberate: the accepting path of one of them terminates a
// live process, and the other shells out to PowerShell or lsof. A test that reached either would be
// running the dangerous thing to prove the safe thing.
//
// THE VALIDATION IS THE SAFETY FEATURE, NOT A TIDINESS CHECK. On POSIX `process.kill(0, SIGTERM)`
// signals THE ENTIRE PROCESS GROUP — the wrapper, the bridge, and every sibling the operator's
// shell started — and a negative pid signals a process group by number. So `Number.isInteger(n) &&
// n > 0` is the line standing between a stale marker file and a shell full of dead processes. It is
// asserted over every shape a bad pid actually arrives in: a marker file that was empty, a JSON
// null, a float from a parsed string, a pid-shaped string.
//
// The `if (!port) return []` guard is the same shape one layer up: port 0 means "any port" to a
// socket API, and resolving it would return listeners the caller never asked about — which
// `defaultKillByPort` would then hand to the killer.
//
// TWO MUTATIONS STILL SURVIVE ON THIS HOST AND ARE RECORDED RATHER THAN PAPERED OVER: making
// `defaultKillOnePid` skip the predicate, and dropping the port guard. On win32 the platform refuses
// first either way — `Stop-Process -Id 0` errors, and `Get-NetTCPConnection -LocalPort 0` finds
// nothing — so the difference is invisible here. They are provable on POSIX, or by making the
// platform branch injectable, which is a change to the module rather than to this file. The
// predicate above is the part that could be pulled out and proved anywhere, and it is the part that
// matters: on POSIX the unguarded call signals a process group.

import assert from "node:assert/strict";
import test from "node:test";

import { defaultKillOnePid, defaultResolveListenerPids, isKillablePid } from "../hermes-daemon.js";

//: Every non-pid a caller can hand these. `0` and negatives lead the list because on POSIX they are
//: not invalid — they are process-GROUP signals, which is the accident this guard prevents.
const NOT_A_PID = [0, -1, -1234, null, undefined, "", "  ", "abc", "12abc", 1.5, NaN, Infinity,
                   {}, [], true, false];

test("the PREDICATE refuses every non-pid, on any platform", () => {
  // THE ONE THAT ACTUALLY PROVES THE GUARD. The async helper below cannot: on Windows, removing the
  // check changes nothing observable — `Stop-Process -Id 0` errors and the helper still returns
  // false — so every mutation weakening it survived until the rule was pulled out into a predicate.
  // Measured, not assumed: four mutations, all uncaught, before this existed.
  for (const value of NOT_A_PID) {
    assert.equal(isKillablePid(value), false, `${JSON.stringify(String(value))} read as killable`);
  }
  assert.equal(isKillablePid("123"), false, "a pid-shaped string came from text nobody re-checked");
});

test("the predicate accepts an ordinary pid", () => {
  for (const value of [1, 2, 4321, 999999]) {
    assert.equal(isKillablePid(value), true, `${value} was refused`);
  }
});

test("no non-pid value reaches the killer", async () => {
  // The end-to-end contract: false, never a throw. On win32 this holds even without the
  // predicate, because the platform refuses first — which is exactly why the predicate test
  // above exists rather than this one standing alone.
  for (const value of NOT_A_PID) {
    const result = await defaultKillOnePid(value);
    assert.equal(
      result, false,
      `${JSON.stringify(String(value))} was accepted as a pid — on POSIX 0 and negatives signal a `
        + "whole process group",
    );
  }
});

test("a pid-shaped STRING is not accepted either", async () => {
  // `Number("123")` is 123, so a looser check would take it. It is refused because a pid arriving
  // as a string means it came from a file or a command's output unparsed, and the caller has not
  // established that it is still the process it thinks.
  assert.equal(await defaultKillOnePid("123"), false);
});

test("the guard answers false rather than throwing", async () => {
  // Its callers treat it as best-effort and do not wrap it. A throw here would abort a teardown
  // partway, leaving exactly the half-dead state the teardown exists to clear.
  await assert.doesNotReject(() => defaultKillOnePid(undefined));
  await assert.doesNotReject(() => defaultKillOnePid("nonsense"));
});

test("resolving listeners on no port returns nothing and shells out to nothing", async () => {
  // Port 0 means "any port" to a socket API. Resolving it would hand `defaultKillByPort` listeners
  // nobody asked about — and it kills what it is handed, subject to its own cmdline check.
  for (const port of [0, null, undefined, "", NaN, false]) {
    assert.deepEqual(
      await defaultResolveListenerPids(port), [],
      `port ${JSON.stringify(port)} was resolved instead of refused`,
    );
  }
});

test("an unusable port resolves to an empty list, not an error", async () => {
  // Same contract as the killer: never throw, so a caller mid-teardown is not left half-done. This
  // one DOES shell out for a plausible-looking port, so the values here are ones the guard rejects
  // before any command runs.
  await assert.doesNotReject(() => defaultResolveListenerPids(undefined));
  assert.deepEqual(await defaultResolveListenerPids(0), []);
});
