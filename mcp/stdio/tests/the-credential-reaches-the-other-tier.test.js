// The key this service is configured with has to reach aify-env, or that daemon cannot advertise.
//
// THE FAILURE THIS CLOSES, and every link in it behaves correctly: aify-env's advertisement
// credential comes only from its own process environment, and nothing on this host puts it there --
// the aify-comms bridge does not start that daemon. So the moment `API_KEY` is set here, every
// advertisement is refused, `advertising` stays false, the bridge correctly keeps describing the
// host, and the operator sees a daemon that runs and is never believed. No error anywhere.
//
// These drive the REAL script against a stubbed `aify-env` on PATH, because the thing worth proving
// is the JOIN: that the key leaves this side on stdin, that the reference comes back, and that a
// refusal from the other tier stops the install rather than being reported as success.

import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { sealedChildEnv } from "./_child-env.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SCRIPT = path.resolve(HERE, "..", "..", "..", "scripts", "credential-carrier.sh");
const CANARY = "carrier-canary-DO-NOT-LEAK-4242";

function bash() {
  for (const candidate of ["C:\\Program Files\\Git\\bin\\bash.exe", "/bin/bash", "/usr/bin/bash"]) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return "bash";
}

/**
 * A scratch PATH holding a fake `aify-env`, so nothing here touches the real daemon or the
 * operator's own credential store.
 */
function withFakeEnvTool(body) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-carrier-"));
  const script = path.join(dir, "aify-env");
  fs.writeFileSync(script, body, { mode: 0o755 });
  // Windows resolves a bare name through PATHEXT, so a `.cmd` shim is what makes the stub findable
  // the way the real command is.
  fs.writeFileSync(path.join(dir, "aify-env.cmd"),
                   `@echo off\r\n"${bash()}" "${script.split("\\").join("/")}" %*\r\n`);
  return dir;
}

function run(pathDir, { input = CANARY } = {}) {
  return new Promise((resolve) => {
    // `sealedChildEnv`, NOT a bare spread: in a live wrapper environment this process holds the
    // operator's real API key, and handing it to a child would let a test pass because a REAL
    // credential was inherited rather than because the carrier delivered one. The gate that caught
    // this exists because a Python test once read the operator's real hermes session id.
    const child = execFile(bash(), [SCRIPT], {
      env: { ...sealedChildEnv(), PATH: `${pathDir}${path.delimiter}${process.env.PATH}` },
    }, () => {});
    let out = "", err = "";
    child.stdout.on("data", (d) => { out += d; });
    child.stderr.on("data", (d) => { err += d; });
    child.stdin.end(input);
    child.on("close", (code) => resolve({ stdout: out, stderr: err, code }));
  });
}

const ACCEPTING = [
  "#!/bin/bash",
  // Records what it was given so the test can prove the key arrived on STDIN and not in argv.
  'if [ "$1" = "credential" ] && [ "$2" = "set" ]; then',
  '  cat > "$(dirname "$0")/received.txt"',
  '  printf "%s\\n" "$*" > "$(dirname "$0")/argv.txt"',
  '  echo "aify-comms-abc123.key"',
  "  exit 0",
  "fi",
  'if [ "$1" = "credential" ] && [ "$2" = "status" ]; then exit 0; fi',
  "exit 1",
].join("\n");

test("the key travels on STDIN and the REFERENCE comes back", async () => {
  const dir = withFakeEnvTool(ACCEPTING);
  try {
    const result = await run(dir);
    assert.equal(result.code, 0, result.stderr);
    assert.equal(result.stdout.trim(), "aify-comms-abc123.key");

    // THE JOIN: what this side sent is what the other tier received, byte for byte.
    assert.equal(fs.readFileSync(path.join(dir, "received.txt"), "utf8"), CANARY);

    // AND IT WAS NEVER IN ARGV, which every process on the host can read for as long as the command
    // runs. This is the whole reason the other tier refuses a `--key` flag by name.
    assert.ok(!fs.readFileSync(path.join(dir, "argv.txt"), "utf8").includes(CANARY),
              "the key was passed on the command line");
    assert.ok(!`${result.stdout}${result.stderr}`.includes(CANARY), "the key reached the output");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true, maxRetries: 3 });
  }
});

test("a REFUSAL from the other tier fails the install rather than reporting success", async () => {
  const dir = withFakeEnvTool([
    "#!/bin/bash",
    'if [ "$1" = "credential" ] && [ "$2" = "set" ]; then',
    "  cat > /dev/null",
    '  echo "CREDENTIAL_INSECURE: the credential root is readable by BUILTIN\\\\Users" >&2',
    "  exit 65",
    "fi",
    "exit 1",
  ].join("\n"));
  try {
    const result = await run(dir);
    assert.notEqual(result.code, 0, "a refused credential was reported as stored");
    assert.match(result.stderr, /refused the credential/);
    // The other tier's own diagnostic is passed through rather than reworded into something that
    // might quote what it refused.
    assert.match(result.stderr, /CREDENTIAL_INSECURE/);
    assert.ok(!result.stderr.includes(CANARY));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true, maxRetries: 3 });
  }
});

test("a STORE THAT WRITES BUT THEN REPORTS FAULTED is a failure, not a success", async () => {
  // `credential set` verifies its own write. This is the TIER BOUNDARY, and what matters here is
  // that the daemon reports the credential healthy -- not that the command believed itself.
  const dir = withFakeEnvTool([
    "#!/bin/bash",
    'if [ "$1" = "credential" ] && [ "$2" = "set" ]; then cat > /dev/null; echo "a.key"; exit 0; fi',
    'if [ "$1" = "credential" ] && [ "$2" = "status" ]; then exit 65; fi',
    "exit 1",
  ].join("\n"));
  try {
    const result = await run(dir);
    assert.notEqual(result.code, 0, "a faulted store was reported as converged");
    assert.match(result.stderr, /reports it as faulted/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true, maxRetries: 3 });
  }
});

test("a store that names NO reference is a failure -- nothing could find the file", async () => {
  const dir = withFakeEnvTool([
    "#!/bin/bash",
    'if [ "$1" = "credential" ] && [ "$2" = "set" ]; then cat > /dev/null; exit 0; fi',
    "exit 0",
  ].join("\n"));
  try {
    const result = await run(dir);
    assert.notEqual(result.code, 0);
    assert.match(result.stderr, /named no reference/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true, maxRetries: 3 });
  }
});

test("NO aify-env is not an error, and says what it costs", async () => {
  // A host that never installed the environment tier is one where the bridge still describes
  // everything itself, which is supported. Refusing the whole install over an absent optional tier
  // would be the installer deciding somebody's architecture for them.
  const empty = fs.mkdtempSync(path.join(os.tmpdir(), "aify-carrier-none-"));
  try {
    const result = await new Promise((resolve) => {
      const child = execFile(bash(), [SCRIPT], {
        // A PATH with no `aify-env` on it at all. SystemRoot stays so Windows can still resolve
        // the shell's own helpers.
        env: { PATH: empty, SystemRoot: process.env.SystemRoot || "", HOME: empty },
      }, () => {});
      let out = "", err = "";
      child.stdout.on("data", (d) => { out += d; });
      child.stderr.on("data", (d) => { err += d; });
      child.stdin.end(CANARY);
      child.on("close", (code) => resolve({ stdout: out, stderr: err, code }));
    });
    assert.equal(result.code, 0, result.stderr);
    assert.match(result.stderr, /not installed/);
    assert.equal(result.stdout.trim(), "", "it printed a reference for a store that does not exist");
  } finally {
    fs.rmSync(empty, { recursive: true, force: true, maxRetries: 3 });
  }
});

test("an empty stdin is refused before anything is called", async () => {
  const dir = withFakeEnvTool(ACCEPTING);
  try {
    const result = await run(dir, { input: "" });
    assert.notEqual(result.code, 0);
    assert.match(result.stderr, /no key arrived/);
    assert.equal(fs.existsSync(path.join(dir, "received.txt")), false,
                 "it called the other tier with nothing to store");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true, maxRetries: 3 });
  }
});
