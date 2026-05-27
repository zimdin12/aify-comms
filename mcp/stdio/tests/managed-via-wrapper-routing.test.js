#!/usr/bin/env node
// Pins Task A4 of the unified-backing refactor: supportedExecutionModes
// excludes 'managed' from the main bridge's claim list when the runtime
// is wrapper-backed (managed_via_wrapper setting includes it). The
// wrapper's child bridge (loaded as MCP inside *-aify) claims via its
// resident-run capability instead. Without this gate, both bridges
// race to claim the same dispatch_run.

import assert from "node:assert/strict";
import { test } from "node:test";
import { supportedExecutionModes } from "../dispatch-execution.js";

test("default behavior: managed mode included for native-managed runtimes", () => {
  const info = {
    sessionMode: "managed",
    runtime: "hermes",
    capabilities: ["managed-run", "native-managed-run", "resume", "interrupt", "spawn"],
    runtimeConfig: {},
  };
  const modes = supportedExecutionModes(info);
  assert.ok(modes.includes("managed"), `default: managed included; got ${JSON.stringify(modes)}`);
});

test("wrapper-backed hermes: main bridge must NOT claim managed", () => {
  const info = {
    sessionMode: "managed",
    runtime: "hermes",
    capabilities: ["managed-run", "native-managed-run", "resume", "interrupt", "spawn"],
    runtimeConfig: {},
  };
  const modes = supportedExecutionModes(info, {
    managedViaWrapperRuntimes: new Set(["hermes"]),
  });
  assert.ok(!modes.includes("managed"),
    `wrapper-backed hermes: main bridge must not advertise managed; got ${JSON.stringify(modes)}`);
});

test("wrapper-backed codex: main bridge leaves claims to the wrapper child", () => {
  const info = {
    sessionMode: "managed",
    runtime: "codex",
    capabilities: ["managed-run", "native-managed-run", "resume", "interrupt", "spawn"],
    runtimeConfig: {},
  };
  const modes = supportedExecutionModes(info, {
    managedViaWrapperRuntimes: new Set(["codex"]),
  });
  assert.deepEqual(modes, [], `wrapper-backed codex: main bridge must not claim; got ${JSON.stringify(modes)}`);
  assert.ok(!modes.includes("managed"),
    `wrapper-backed codex: main bridge must not advertise managed; got ${JSON.stringify(modes)}`);
});

test("wrapper-backed hermes child bridge still claims resident via its resident-run capability", () => {
  // When *-aify spawns the wrapper, the wrapper's in-process bridge runs
  // as a resident-mode session (AIFY_SESSION_MODE=resident is set by the
  // wrapper for itself). The child bridge's supportedExecutionModes for
  // its OWN agent still returns 'resident' so it can claim.
  const info = {
    sessionMode: "resident",
    runtime: "hermes",
    capabilities: ["resident-run", "resume", "interrupt", "steer"],
    runtimeConfig: { gatewayUrl: "ws://127.0.0.1:9119/api/ws?token=x" },
  };
  const modes = supportedExecutionModes(info, {
    managedViaWrapperRuntimes: new Set(["hermes"]),
  });
  assert.deepEqual(modes, ["resident"],
    `wrapper child must still claim resident; got ${JSON.stringify(modes)}`);
});

test("wrapper-backed flag with array form (not Set) also works", () => {
  const info = {
    sessionMode: "managed",
    runtime: "hermes",
    capabilities: ["managed-run", "native-managed-run"],
    runtimeConfig: {},
  };
  const modes = supportedExecutionModes(info, {
    managedViaWrapperRuntimes: ["hermes", "codex"],
  });
  assert.ok(!modes.includes("managed"), `array form: hermes still excluded; got ${JSON.stringify(modes)}`);
});

test("wrapper-backed flag unset: existing behavior preserved", () => {
  const info = {
    sessionMode: "managed",
    runtime: "hermes",
    capabilities: ["managed-run", "native-managed-run"],
    runtimeConfig: {},
  };
  const modes = supportedExecutionModes(info, {});
  assert.ok(modes.includes("managed"), `no flag passed: managed still advertised; got ${JSON.stringify(modes)}`);
});

test("launchRuntimeRun with managedViaWrapper=true returns a delegated no-op controller for codex managed", async () => {
  // Task C3: even if a stray code path reaches launchRuntimeRun for a
  // wrapper-backed managed agent, the controller must NOT contend with
  // the wrapper's child bridge (which is the real delivery actor). The
  // delegated marker resolves immediately so the bridge dispatch loop
  // can finalize the run as a pass-through.
  const { launchRuntimeRun } = await import("../runtimes.js");
  const controller = launchRuntimeRun({
    agentId: "cx-test-1",
    agentInfo: { runtime: "codex", sessionMode: "managed", runtimeConfig: {}, cwd: process.cwd() },
    run: { id: "r1", executionMode: "managed", subject: "delegated", body: "x", from: "y" },
    runtimeState: {},
    callbacks: { onEvent: () => {}, onRefs: () => {} },
    managedViaWrapper: true,
  });
  const result = await controller.promise;
  assert.equal(result.status, "delegated", `wrapper-backed managed must return delegated marker; got ${JSON.stringify(result)}`);
});

test("launchRuntimeRun with managedViaWrapper=true returns a delegated no-op controller for hermes managed", async () => {
  const { launchRuntimeRun } = await import("../runtimes.js");
  const controller = launchRuntimeRun({
    agentId: "h-test-1",
    agentInfo: { runtime: "hermes", sessionMode: "managed", runtimeConfig: {}, cwd: process.cwd() },
    run: { id: "r2", executionMode: "managed", subject: "delegated", body: "x", from: "y" },
    runtimeState: {},
    callbacks: { onEvent: () => {}, onRefs: () => {} },
    managedViaWrapper: true,
  });
  const result = await controller.promise;
  assert.equal(result.status, "delegated", `wrapper-backed hermes must return delegated marker; got ${JSON.stringify(result)}`);
});
