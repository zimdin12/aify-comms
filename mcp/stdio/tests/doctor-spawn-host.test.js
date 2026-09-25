#!/usr/bin/env node
// The `spawn-delegation` row: is the aify-env serving this host answering?
//
// aify-env is the only spawner since v0.6.1, and the service refuses rather than starting a second
// one, so a down aify-env presents as spawns failing with no cause attached. Until 0.7.0 the row read
// a delegation switch instead, and on its DEFAULT reported "hosted by the aify-comms bridge itself" --
// false since v0.6.1 -- as ok, having probed nothing (v0.7 scan B4).
//
// It reads the installed launcher rather than running it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { DEFAULT_ENV_ENDPOINT, installedHostTier, spawnHostVerdict } from "../doctor-predicates.js";

const BAKED = ['#!/usr/bin/env bash', 'export AIFY_ENV_ENDPOINT="http://127.0.0.1:9900"'].join("\n");
const PRE_07 = ['#!/usr/bin/env bash', 'export AIFY_COMMS_DELEGATE_SPAWNS=""', 'export AIFY_ENV_ENDPOINT=""'].join("\n");

test("the endpoint is the launcher's, else aify-env's fixed default", () => {
  assert.equal(installedHostTier(BAKED).endpoint, "http://127.0.0.1:9900");
  assert.equal(installedHostTier(PRE_07).endpoint, DEFAULT_ENV_ENDPOINT, "an old launcher with delegation off still names aify-env");
  assert.equal(installedHostTier("@echo off\r\n").isLauncher, false, "the .cmd shim is not a launcher body");
});

test("answering is ok and names where", () => {
  const v = spawnHostVerdict({ launcherText: BAKED, endpointAnswered: true });
  assert.equal(v.ok, true);
  assert.equal(v.code, "answering");
  assert.match(v.detail, /127\.0\.0\.1:9900, which is answering/);
});

test("a herdr-aify env daemon found elsewhere is named, with its lifetime", () => {
  const v = spawnHostVerdict({ launcherText: BAKED, endpointAnswered: true, answeredAt: "http://127.0.0.1:61000" });
  assert.equal(v.ok, true);
  assert.match(v.detail, /herdr-aify env daemon at http:\/\/127\.0\.0\.1:61000/);
});

test("THE DEFAULT IS NO LONGER A FREE PASS: a launcher with delegation off is probed like any other", () => {
  const down = spawnHostVerdict({ launcherText: PRE_07, endpointAnswered: false });
  assert.equal(down.ok, false);
  assert.equal(down.code, "unreachable");
  assert.match(down.fix, /aify-env doctor/, "the check comes before any start");
  assert.match(down.fix, /operator's call/);
});

test("no evidence is not a pass", () => {
  for (const [input, why] of [
    [{ launcherText: BAKED, endpointAnswered: null }, "not asked"],
    [{ launcherText: null }, "no launcher"],
    [{ launcherText: "@echo off\r\n", endpointAnswered: true }, "the shim cannot testify"],
  ]) {
    const v = spawnHostVerdict(input);
    assert.equal(v.ok, false, why);
    assert.equal(v.code, "unknown-all", why);
  }
});
