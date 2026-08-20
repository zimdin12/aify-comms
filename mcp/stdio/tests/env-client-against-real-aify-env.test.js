#!/usr/bin/env node
// aify-comms' client, against a REAL aify-env.
//
// THE ONE LINK NOTHING EXERCISED. env-client.test.js drives the client with an injected fetch, and
// aify-env's protocol tests drive the server with an injected runner. Both sides are thoroughly tested
// and neither has ever met the other — so a route renamed on one side, a status code changed on the
// other, or a body shape drifting between them would leave every test in both repos green while
// delegation could not work at all.
//
// That is the same shape as every other defect this work has turned up: the tests supplied what
// production would have had to supply, so the gap sat exactly where nobody was looking.
//
// It SKIPS ITSELF ONLY IF aify-env IS NOT CHECKED OUT, and says so loudly rather than passing quietly —
// a skip that reads as green is the thing this project keeps paying for. On the machine that has both,
// it is a real conversation over a real socket.

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { EnvClient } from "../env-client.mjs";

/** Where aify-env lives. Overridable, because a sibling checkout is a convention and not a fact. */
const AIFY_ENV = process.env.AIFY_ENV_REPO || path.join(os.homedir(), "projects", "aify-env");
const DAEMON = path.join(AIFY_ENV, "bin", "aify-env.mjs");
const available = fs.existsSync(DAEMON);

function startDaemon() {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [DAEMON, "--port", "0"], { stdio: ["ignore", "pipe", "pipe"] });
    let output = "";
    const timer = setTimeout(() => reject(new Error(`aify-env did not start:\n${output}`)), 20_000);
    child.stdout.on("data", (chunk) => {
      output += chunk;
      const match = /listening on (http:\/\/127\.0\.0\.1:\d+)/.exec(output);
      if (match) {
        clearTimeout(timer);
        resolve({ child, base: match[1] });
      }
    });
    child.stderr.on("data", (chunk) => { output += chunk; });
    child.on("error", reject);
  });
}

const stopDaemon = (child) => new Promise((resolve) => {
  child.on("exit", resolve);
  child.kill();
});

/** A launcher aify-env will accept: it carries the contract marker and does something observable. */
function writeLauncher(dir, body) {
  const file = path.join(dir, "trace-aify");
  fs.writeFileSync(file, ["#!/bin/bash", 'HARNESS_WRAPPER_VERSION="0.6.0"', ...body, ""].join(String.fromCharCode(10)));
  fs.chmodSync(file, 0o755);
  return file.split(String.fromCharCode(92)).join("/");
}

test("aify-env is checked out, so this contract can actually be exercised", () => {
  // Failing rather than skipping. "The cross-repo contract is unverified" must not read as green, and
  // on the machine that owns both repos there is no legitimate reason for it to be missing.
  assert.equal(
    available,
    true,
    `aify-env not found at ${AIFY_ENV}. Set AIFY_ENV_REPO, or check it out: the client and the server `
    + "are otherwise only ever tested against each other's fakes.",
  );
});

test("START: the client's request is one this server accepts, and the handle comes back", async (t) => {
  if (!available) return t.skip("aify-env is not checked out");
  const { child, base } = await startDaemon();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-trace-"));
  try {
    const launcher = writeLauncher(dir, ["sleep 2"]);
    const client = new EnvClient({ endpoint: base });

    const started = await client.start({ service: "aify-comms", launcher, args: [] });
    assert.equal(started.ok, true, `start failed: ${started.error}`);
    assert.ok(started.handle.id, "no id came back");
    assert.ok(started.handle.pid > 0, "no pid came back");
    assert.equal(started.handle.service, "aify-comms");
    assert.equal(typeof started.handle.terminal, "boolean", "the answer must say which path it got");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
    await stopDaemon(child);
  }
});

test("LIST and HEALTH: the shapes the client expects are the shapes the server sends", async (t) => {
  if (!available) return t.skip("aify-env is not checked out");
  const { child, base } = await startDaemon();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-trace-"));
  try {
    const client = new EnvClient({ endpoint: base });
    const launcher = writeLauncher(dir, ["sleep 2"]);
    const started = await client.start({ service: "aify-comms", launcher, args: [] });
    assert.equal(started.ok, true);

    const listed = await client.list();
    assert.equal(listed.ok, true, listed.error);
    assert.ok(Array.isArray(listed.handle.processes), "list did not return {processes: []}");
    assert.equal(listed.handle.processes[0].service, "aify-comms");

    const health = await client.health();
    assert.equal(health.ok, true, health.error);
    assert.equal(health.handle.status, "healthy");
    assert.ok(Array.isArray(health.handle.processes));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
    await stopDaemon(child);
  }
});

test("OUTPUT: the client can watch a process this server started", async (t) => {
  if (!available) return t.skip("aify-env is not checked out");
  // The half a console depends on, across the seam it will actually cross.
  const { child, base } = await startDaemon();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-trace-"));
  try {
    const client = new EnvClient({ endpoint: base });
    const launcher = writeLauncher(dir, ['echo "TRACED-OUTPUT"', "sleep 2"]);
    const started = await client.start({ service: "aify-comms", launcher, args: [] });
    assert.equal(started.ok, true, started.error);

    const seen = [];
    const unsubscribe = await client.subscribeOutput(started.handle.id, (chunk) => seen.push(chunk));
    assert.notEqual(unsubscribe, null, "the client could not subscribe to a process the server started");

    const deadline = Date.now() + 10_000;
    while (!seen.join("").includes("TRACED-OUTPUT") && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    unsubscribe();
    assert.match(seen.join(""), /TRACED-OUTPUT/, "output did not cross the seam");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
    await stopDaemon(child);
  }
});

test("STOP: the client can stop what it started, and the server agrees it is gone", async (t) => {
  if (!available) return t.skip("aify-env is not checked out");
  const { child, base } = await startDaemon();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-trace-"));
  try {
    const client = new EnvClient({ endpoint: base });
    const launcher = writeLauncher(dir, ["sleep 30"]);
    const started = await client.start({ service: "aify-comms", launcher, args: [] });
    assert.equal(started.ok, true, started.error);

    const stopped = await client.stop(started.handle.id);
    assert.equal(stopped.ok, true, stopped.error);

    const listed = await client.list();
    assert.deepEqual(listed.handle.processes, [], "the server still owns a stopped process");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
    await stopDaemon(child);
  }
});

test("REFUSAL crosses the seam intact: the client reads the server's reason", async (t) => {
  if (!available) return t.skip("aify-env is not checked out");
  // A refusal that arrived as a generic failure would send somebody debugging the wrong half.
  const { child, base } = await startDaemon();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-trace-"));
  try {
    const nomarker = path.join(dir, "nomarker-aify");
    fs.writeFileSync(nomarker, "#!/bin/bash\necho nope\n");
    const client = new EnvClient({ endpoint: base });

    const started = await client.start({
      service: "aify-comms",
      launcher: nomarker.split(String.fromCharCode(92)).join("/"),
      args: [],
    });
    assert.equal(started.ok, false);
    assert.equal(started.status, 403);
    assert.match(started.error, /marker/i, "the server's reason did not survive the trip");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
    await stopDaemon(child);
  }
});
