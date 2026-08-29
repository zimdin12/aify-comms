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

// RENDERED ONCE PER PROCESS, per client and url.
//
// Each render runs the whole of install.sh. Measured 2026-08-29: ~9s warm on this host, and
// `claude-wrapper-behaviour.test.js` called this once PER TEST -- 18 renders, 192.4 seconds for a
// file whose tests are each a sub-second wrapper run. The render was 84% of the file's wall time.
//
// SAFE ONLY BECAUSE THE RUN WORKSPACE MOVED OUT. The first attempt at this cache reddened
// `claude-wrapper-contract.test.js` immediately: `runWrapper` derived its stub-bin and its sealed
// HOME from the wrapper's own directory, so sharing the render meant sharing the stub a previous
// test had installed, and the case asserting a wrapper REFUSES to launch found one and launched.
// That is the exact failure a shared fixture is supposed to be checked for -- a case passing
// because an earlier case prepared state -- and it took one run to appear. Each run now gets its
// own workspace, so the rendered directory is read-only input to every caller.
//
// The render itself is proven deterministic by `claude-wrapper-determinism.test.js`, which renders
// twice and compares bytes. It has its own renderer and is untouched by this cache.
//
// Per PROCESS, not per suite: run-all.mjs spawns one node per file, so the cache dies with the file
// and no test can see a directory another file prepared.
const rendered = new Map();

/** Render one client's wrapper(s) into a throwaway dir, reusing this process's earlier render. */
export function renderWrapper(client, { url = RENDER_URL } = {}) {
  const key = `${client} ${url}`;
  const cached = rendered.get(key);
  if (cached) return cached;
  const dir = tmpDir(`aify-${client}-wrapper-`);
  execFileSync("bash", [INSTALL_SH, "--client", client, url, "--emit-wrappers", dir], { stdio: "ignore" });
  rendered.set(key, dir);
  return dir;
}

/**
 * A stand-in for the runtime CLI. Records argv and the full environment it was launched with, then
 * exits with `exitCode`. Named exactly as the wrapper will invoke it, and placed first on PATH.
 */
export function writeStubRuntime(binDir, name, { exitCode = 0, prelude = "" } = {}) {
  fs.mkdirSync(binDir, { recursive: true });
  const recordPath = path.join(binDir, `${name}.record`);
  // The stub also captures the CONTENTS of any config file it is handed. The wrapper writes those to
  // temp files and removes them in an EXIT trap, so by the time a test could read them they are gone —
  // and a test that cannot see the MCP config cannot tell which bridge the runtime was pointed at.
  // Reading them here, at the moment of launch, is the only point they exist.
  const stub = [
    "#!/bin/bash",
    "# Stub runtime installed by wrapper-harness.mjs. Records how it was launched.",
    // A prelude runs before recording and may exec away entirely. codex needs this: its wrapper
    // starts `codex app-server` in the background and refuses to continue until that port accepts a
    // connection, so the stub has to BE a listener for that one invocation — and must not record it,
    // or the app-server launch would overwrite the foreground launch the test is asking about.
    prelude,
    `{`,
    `  echo "ARGV_BEGIN"`,
    `  for a in "$@"; do echo "$a"; done`,
    `  echo "ARGV_END"`,
    `  echo "ENV_BEGIN"`,
    `  env`,
    `  echo "ENV_END"`,
    `  prev=""`,
    `  for a in "$@"; do`,
    `    if [ "$prev" = "--mcp-config" ] || [ "$prev" = "--settings" ]; then`,
    `      echo "FILE_BEGIN $prev"`,
    `      cat "$a" 2>/dev/null`,
    `      echo ""`,
    `      echo "FILE_END"`,
    `    fi`,
    `    prev="$a"`,
    `  done`,
    `} > ${JSON.stringify(recordPath)} 2>&1`,
    `exit ${exitCode}`,
    "",
  ].join("\n");
  const stubPath = path.join(binDir, name);
  fs.writeFileSync(stubPath, stub);
  fs.chmodSync(stubPath, 0o755);
  return recordPath;
}

/**
 * A PATH with the shell utilities a wrapper needs and NO runtime CLI on it — for asserting what a
 * wrapper does when the runtime is missing.
 *
 * This one is genuinely dangerous to get wrong. The ordinary PATH contains the operator's real
 * `claude`, so a test that merely omits the stub would LAUNCH IT — an interactive session, from a
 * suite, on a machine running a live fleet. `runtimeReachable` below exists so such a test skips
 * loudly rather than doing that.
 */
export function reducedPath(binDir) {
  // POSIX form and `:` separators throughout: this string is read by BASH, not by Node. Node is told
  // which executable to run explicitly (see `BASH` below), precisely so the two never have to agree
  // on a path syntax — mixing a Windows `stub-bin` path into a `:`-joined list produced a PATH that
  // neither could parse, and the spawn failed with a null status rather than any wrapper behaviour.
  const dirs = [binDir.split("\\").join("/"), "/usr/bin", "/mingw64/bin"];
  for (const tool of ["node", "curl"]) {
    const found = spawnSync("bash", ["-lc", `command -v ${tool} || true`], { encoding: "utf8" });
    const p = (found.stdout || "").trim();
    if (p) dirs.push(path.posix.dirname(p));
  }
  return [...new Set(dirs)].join(":");
}

// Windows-native path to bash, resolved once. Node resolves a bare "bash" against the PATH it is
// handed, which the reduced-PATH tests deliberately shrink — so the interpreter is named outright.
const BASH = (() => {
  try {
    const p = execFileSync("bash", ["-lc", "cygpath -w \"$(command -v bash)\" 2>/dev/null || command -v bash"], {
      encoding: "utf8",
    }).trim();
    return p || "bash";
  } catch {
    return "bash";
  }
})();

/** Whether a runtime CLI resolves under the given PATH. Used to guard the missing-runtime tests. */
export function runtimeReachable(name, pathValue) {
  const res = spawnSync(BASH, ["-c", `command -v ${name} >/dev/null 2>&1`], {
    env: { ...process.env, PATH: pathValue },
  });
  return res.status === 0;
}

function parseRecord(text) {
  const argv = [];
  const env = {};
  const files = {};
  let mode = "";
  let fileKey = "";
  let buffer = [];
  for (const line of text.split(/\r?\n/)) {
    if (line === "ARGV_BEGIN") { mode = "argv"; continue; }
    if (line === "ARGV_END") { mode = ""; continue; }
    if (line === "ENV_BEGIN") { mode = "env"; continue; }
    if (line === "ENV_END") { mode = ""; continue; }
    if (line.startsWith("FILE_BEGIN ")) {
      mode = "file";
      fileKey = line.slice("FILE_BEGIN ".length).trim();
      buffer = [];
      continue;
    }
    if (line === "FILE_END") {
      files[fileKey] = buffer.join("\n");
      mode = "";
      continue;
    }
    if (mode === "argv") { argv.push(line); continue; }
    if (mode === "file") { buffer.push(line); continue; }
    if (mode === "env") {
      const eq = line.indexOf("=");
      if (eq > 0) env[line.slice(0, eq)] = line.slice(eq + 1);
    }
  }
  return { argv, env, files };
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
  stubPrelude = "",
  withStub = true,
  prepareHome = null,
  minimalPath = false,
  timeout = 30_000,
} = {}) {
  // A WORKSPACE PER RUN, never the wrapper's own directory. The stub runtime and the sealed HOME
  // are this call's state; putting them beside the wrapper made the rendered artifact mutable and
  // tied every run in a file to every other one.
  const workspace = tmpDir("aify-wrapper-run-");
  const binDir = path.join(workspace, "stub-bin");
  const home = path.join(workspace, "home");
  fs.mkdirSync(home, { recursive: true });
  // The sealed HOME starts empty, which is what makes "no transcript exists" the default. A caller
  // that needs the OTHER branch — a session id that validates — seeds it here rather than reaching
  // for the real ~/.claude, which would make the test depend on the operator's machine.
  if (typeof prepareHome === "function") prepareHome(home);
  const recordPath = withStub
    ? writeStubRuntime(binDir, runtimeName, { exitCode: stubExitCode, prelude: stubPrelude })
    : path.join(binDir, `${runtimeName}.record`);
  if (!withStub) fs.mkdirSync(binDir, { recursive: true });

  // Start from the parent env so bash, node and coreutils still resolve, then strip everything this
  // project could read out of the ambient shell.
  const childEnv = { ...process.env };
  for (const key of Object.keys(childEnv)) {
    if (/^(AIFY_|HARNESS_|CLAUDE_|CODEX_|HERMES_|PI_|OMP_)/.test(key)) delete childEnv[key];
  }
  // FORWARD SLASHES, and this is not cosmetic. The wrapper resolves transcripts with
  // `find "$HOME/.claude/projects" ...`, and Git Bash hands a backslash path straight to a POSIX tool
  // that reads `\` as an escape — so `find` silently matches nothing. A sealed HOME is empty anyway,
  // which is why every "no transcript exists" assertion passed regardless; only seeding a transcript
  // and expecting it to be FOUND exposed it. A test that cannot distinguish "absent" from
  // "unreadable" is passing for a reason it did not state.
  const posix = (p) => p.replace(/\\/g, "/");
  Object.assign(childEnv, {
    HOME: posix(home),
    USERPROFILE: posix(home),
    TMPDIR: posix(workspace),
    TEMP: posix(workspace),
    TMP: posix(workspace),
    PATH: minimalPath ? reducedPath(binDir) : `${binDir}${path.delimiter}${process.env.PATH}`,
    AIFY_COMMS_URL: NOWHERE_URL,
    ...env,
  });
  for (const [k, v] of Object.entries(childEnv)) {
    if (v === undefined || v === null) delete childEnv[k];
  }

  const res = spawnSync(BASH, [wrapperPath, ...args], {
    env: childEnv,
    encoding: "utf8",
    timeout,
    stdio: ["ignore", "pipe", "pipe"],
  });

  const launched = fs.existsSync(recordPath);
  const parsed = launched
    ? parseRecord(fs.readFileSync(recordPath, "utf8"))
    : { argv: [], env: {}, files: {} };
  return {
    status: res.status,
    stdout: res.stdout || "",
    stderr: res.stderr || "",
    launched,
    argv: parsed.argv,
    env: parsed.env,
    // Contents of any --mcp-config / --settings file, captured at launch: the wrapper removes them
    // in an EXIT trap, so this is the only moment they can be read.
    files: parsed.files,
  };
}

/**
 * Where the launcher templates live: the aify-wrapper PACKAGE, not this repo.
 *
 * They were a byte-identical copy under `wrappers/` until aify-comms started consuming the package,
 * so a test that still joins REPO with "wrappers" is reading a directory that no longer exists.
 */
export const WRAPPER_TEMPLATES = path.join(REPO, "mcp", "stdio", "node_modules", "aify-wrapper", "wrappers");
