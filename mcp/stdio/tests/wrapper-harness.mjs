// Render a wrapper and actually RUN it against a stub runtime.
//
// WHY THIS EXISTS. Every existing guard on the generated wrappers reads TEXT — `bash -n`, or a regex
// over the rendered body. Text guards prove a line was written; they cannot tell you the wrapper
// exports the variable, that the export is reachable, or that argv survives. v0.6 Phase 2 changes what
// these wrappers DO, and this repo has a standing rule about that: a test that asserts where code lives
// proves only that somebody typed it.
//
// So: put a stub on PATH under the runtime's own name, run the real rendered wrapper, and read back the
// environment and argv the runtime was actually launched with. That is the only kind of assertion that
// can fail when a refactor breaks the wrapper.
//
// SEALING. `runWrapper` overrides HOME, TMPDIR/TEMP/TMP and PATH, and CLEARS every AIFY_*/HARNESS_*
// variable in the parent environment before applying the caller's. Two reasons, both learned here:
// a test that reads the operator's live env passes or fails on what happens to be on this machine, and
// this suite runs on a box with a working fleet. The endpoint defaults to 127.0.0.2:1 — SET but
// pointing NOWHERE — because a wrapper that reaches a real service would register agents into the
// operator's production registry. Hostile means unreachable, never "the local one".

import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { tmpDir } from "./_tmpdir.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const REPO = path.resolve(HERE, "..", "..", "..");
export const INSTALL_SH = path.join(REPO, "install.sh");

// Rendered into the wrapper as its baked-in endpoint. A literal so the text is identical on every
// machine, and deliberately NOT the operator's 8800.
export const RENDER_URL = "http://127.0.0.1:8899";

// Reachable by nothing. See the sealing note above.
export const NOWHERE_URL = "http://127.0.0.2:1";

/** Render one client's wrapper(s) into a throwaway dir. */
export function renderWrapper(client, { url = RENDER_URL } = {}) {
  const dir = tmpDir(`aify-${client}-wrapper-`);
  execFileSync("bash", [INSTALL_SH, "--client", client, url, "--emit-wrappers", dir], { stdio: "ignore" });
  return dir;
}

/**
 * A stand-in for the runtime CLI. Records argv and the full environment it was launched with, then
 * exits with `exitCode`. Named exactly as the wrapper will invoke it, and placed first on PATH.
 */
export function writeStubRuntime(binDir, name, { exitCode = 0 } = {}) {
  fs.mkdirSync(binDir, { recursive: true });
  const recordPath = path.join(binDir, `${name}.record`);
  const stub = [
    "#!/bin/bash",
    "# Stub runtime installed by wrapper-harness.mjs. Records how it was launched.",
    `{`,
    `  echo "ARGV_BEGIN"`,
    `  for a in "$@"; do echo "$a"; done`,
    `  echo "ARGV_END"`,
    `  echo "ENV_BEGIN"`,
    `  env`,
    `  echo "ENV_END"`,
    `} > ${JSON.stringify(recordPath)} 2>&1`,
    `exit ${exitCode}`,
    "",
  ].join("\n");
  const stubPath = path.join(binDir, name);
  fs.writeFileSync(stubPath, stub);
  fs.chmodSync(stubPath, 0o755);
  return recordPath;
}

function parseRecord(text) {
  const argv = [];
  const env = {};
  let mode = "";
  for (const line of text.split(/\r?\n/)) {
    if (line === "ARGV_BEGIN") { mode = "argv"; continue; }
    if (line === "ARGV_END") { mode = ""; continue; }
    if (line === "ENV_BEGIN") { mode = "env"; continue; }
    if (line === "ENV_END") { mode = ""; continue; }
    if (mode === "argv") { argv.push(line); continue; }
    if (mode === "env") {
      const eq = line.indexOf("=");
      if (eq > 0) env[line.slice(0, eq)] = line.slice(eq + 1);
    }
  }
  return { argv, env };
}

/**
 * Run a rendered wrapper with a stub runtime on PATH.
 *
 * @returns {{status:number, stdout:string, stderr:string, launched:boolean, argv:string[], env:object}}
 *          `launched` is false when the wrapper exited without ever reaching the runtime — which is
 *          the whole point of the --check and exit-78 cases, and is NOT the same as an empty argv.
 */
export function runWrapper(wrapperPath, {
  runtimeName,
  args = [],
  env = {},
  stubExitCode = 0,
  withStub = true,
  timeout = 30_000,
} = {}) {
  const dir = path.dirname(wrapperPath);
  const binDir = path.join(dir, "stub-bin");
  const home = path.join(dir, "home");
  fs.mkdirSync(home, { recursive: true });
  const recordPath = withStub
    ? writeStubRuntime(binDir, runtimeName, { exitCode: stubExitCode })
    : path.join(binDir, `${runtimeName}.record`);
  if (!withStub) fs.mkdirSync(binDir, { recursive: true });

  // Start from the parent env so bash, node and coreutils still resolve, then strip everything this
  // project could read out of the ambient shell.
  const childEnv = { ...process.env };
  for (const key of Object.keys(childEnv)) {
    if (/^(AIFY_|HARNESS_|CLAUDE_|CODEX_|HERMES_|PI_|OMP_)/.test(key)) delete childEnv[key];
  }
  Object.assign(childEnv, {
    HOME: home,
    USERPROFILE: home,
    TMPDIR: dir,
    TEMP: dir,
    TMP: dir,
    PATH: `${binDir}${path.delimiter}${process.env.PATH}`,
    AIFY_COMMS_URL: NOWHERE_URL,
    ...env,
  });
  for (const [k, v] of Object.entries(childEnv)) {
    if (v === undefined || v === null) delete childEnv[k];
  }

  const res = spawnSync("bash", [wrapperPath, ...args], {
    env: childEnv,
    encoding: "utf8",
    timeout,
    stdio: ["ignore", "pipe", "pipe"],
  });

  const launched = fs.existsSync(recordPath);
  const parsed = launched ? parseRecord(fs.readFileSync(recordPath, "utf8")) : { argv: [], env: {} };
  return {
    status: res.status,
    stdout: res.stdout || "",
    stderr: res.stderr || "",
    launched,
    argv: parsed.argv,
    env: parsed.env,
  };
}
