#!/usr/bin/env node
// deprecated-runtime: pi
// Render guard for the pi-aify / omp-aify bash wrappers (v0.6 Phase 2).
//
// Pi is in v0.6's wrapper scope (operator, 2026-08-19: claude + codex + pi, hermes last), so its
// wrapper needs the same net the others get before it is parameterised onto the HARNESS_* contract.
//
// INSTALLING pi is disabled and stays disabled — OMP is single-client, so `omp-aify` cannot provide
// resident wake into an open TUI. RENDERING it is a different act, and `--emit-pi-wrappers` is carved
// out of the disable check for exactly that reason. If that carve-out is ever removed the emit hook
// becomes unreachable, so the first test here asserts the hook actually runs rather than assuming it.

import assert from "node:assert/strict";
import { test } from "node:test";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { tmpDir } from "./_tmpdir.js";
import { runWrapper } from "./wrapper-harness.mjs";
import { sealedChildEnv } from "./_child-env.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");
const INSTALL_SH = path.join(REPO, "install.sh");

function renderPiWrappers() {
  const dir = tmpDir("aify-pi-wrapper-test-");
  try {
    execFileSync(
      "bash",
      [INSTALL_SH, "--client", "pi", "http://127.0.0.1:8899", "--emit-pi-wrappers", dir],
      { stdio: "ignore", env: sealedChildEnv({ AIFY_HOME: path.resolve(HERE, "..", "..", "..") }) },
    );
    return { dir, files: fs.readdirSync(dir) };
  } catch (err) {
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* best-effort */ }
    throw err;
  }
}

// ONE RENDER FOR THE WHOLE FILE. The listing is taken when the render finishes, `bash -n` only reads,
// and `runWrapper` keeps its stub and sealed HOME in a workspace of its own, so the rendered
// directory is read-only input to every case. tmpDir removes it at exit.
let sharedPi = null;
const sharedPiRender = () => {
  if (!sharedPi) sharedPi = renderPiWrappers();
  return sharedPi;
};

test("pi wrappers render despite pi INSTALLS being disabled", () => {
  const { files } = sharedPiRender();
  assert.ok(files.includes("pi-aify"), `expected pi-aify, got ${JSON.stringify(files)}`);
  assert.ok(files.includes("omp-aify"), "the omp-aify alias must render alongside it");
});

test("pi wrappers: rendered bodies are syntactically valid (bash -n)", () => {
  const { dir } = sharedPiRender();
  for (const name of ["pi-aify", "omp-aify"]) {
    const res = spawnSync("bash", ["-n", path.join(dir, name)], { encoding: "utf8" });
    assert.equal(res.status, 0, `bash -n ${name} failed:\n${res.stderr || res.stdout}`);
  }
});

// ── Behaviour and the harness contract ──────────────────────────────────────
//
// Pi's resident wrapper is not installed, so nothing else would ever execute this file. That makes
// running it here the ONLY evidence it works at all — a template nobody renders and nobody runs is
// indistinguishable from a broken one.

// The run cases use the file's one render too. Each render is a full install.sh run, and this file
// called one per test: measured 2026-08-29 it was 89.9s, the slowest file in a 551s bridge suite, for
// six sub-second wrapper runs.
const piWrapper = () => path.join(sharedPiRender().dir, "pi-aify");
const run = (opts = {}) => runWrapper(piWrapper(), { runtimeName: "omp", ...opts });

test("pi-aify launches its runtime and forwards argv", () => {
  const r = run({ args: ["--print", "hello world"] });
  assert.equal(r.launched, true, `wrapper never reached omp:\n${r.stderr}`);
  assert.ok(r.argv.includes("--print"));
  assert.ok(r.argv.includes("hello world"), "an argument with a space must survive as ONE entry");
  assert.ok(r.argv.includes("--auto-approve"), "the unattended bypass must be on by default");
});

test("pi-aify exports the identity the bridge registers under", () => {
  const r = run({ args: ["--aify-agent", "probe-agent", "--aify-role", "tester"] });
  assert.equal(r.launched, true, r.stderr);
  assert.equal(r.env.AIFY_AGENT_ID, "probe-agent");
  assert.equal(r.env.AIFY_AGENT_ROLE, "tester");
  assert.equal(r.env.AIFY_RUNTIME, "pi");
});

test("HARNESS_IDENTITY and HARNESS_ENDPOINT drive the wrapper, flag still winning", () => {
  const r = run({ env: { HARNESS_IDENTITY: "harness-agent", HARNESS_ENDPOINT: "http://127.0.0.2:2/x" } });
  assert.equal(r.env.AIFY_AGENT_ID, "harness-agent");
  assert.equal(r.env.AIFY_COMMS_URL, "http://127.0.0.2:2/x");

  const flag = run({ args: ["--aify-agent", "flag-agent"], env: { HARNESS_IDENTITY: "harness-agent" } });
  assert.equal(flag.env.AIFY_AGENT_ID, "flag-agent");
});

test("--check reports the resolved configuration and starts nothing", () => {
  const r = run({ args: ["--check"], env: { HARNESS_IDENTITY: "probe-agent" } });
  assert.equal(r.launched, false, "--check must not launch the runtime");
  assert.equal(r.status, 0);
  const out = `${r.stdout}${r.stderr}`;
  assert.match(out, /pi-aify \d+\.\d+\.\d+/, "it must report its own version");
  assert.match(out, /probe-agent/, "and the identity it resolved");
  assert.match(out, /127\.0\.0\.2:1/, "and the endpoint");
});

test("an explicitly empty HARNESS_ENDPOINT exits 78 without starting anything", () => {
  const r = run({ env: { HARNESS_ENDPOINT: "" } });
  assert.equal(r.launched, false);
  assert.equal(r.status, 78, `expected 78, got ${r.status}: ${r.stderr}`);
});

test("HARNESS_EXTRA_ENV exports host-supplied pairs verbatim", () => {
  const r = run({ env: { HARNESS_EXTRA_ENV: "FOO=bar\nnot-a-pair\n" } });
  assert.equal(r.launched, true, `malformed entries must not be fatal: ${r.stderr}`);
  assert.equal(r.env.FOO, "bar");
});

test("pi INSTALL is still refused without the emit flag", () => {
  // The carve-out must be exactly that. If `--client pi` alone ever started installing, this repo
  // would be shipping a resident wrapper for a single-client runtime that cannot wake.
  const res = spawnSync("bash", [INSTALL_SH, "--client", "pi", "http://127.0.0.1:8899"], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  assert.equal(res.status, 1, "pi install must still exit 1");
  assert.match(`${res.stdout}${res.stderr}`, /disabled/i, "and must say why");
});

// THE ALIASES ARE READ, NOT TYPED (0.9.16). install.sh wrote omp-aify by name; the pinned package's wrappers/aliases now
// names each alias once, for this installer and for the package's own re-render. The shipped file names only omp-aify,
// so the render above cannot tell a reader from the old typed name: a COPY of the package with one more alias can.
function packageCopy(aliases) {
  const pkg = tmpDir("aify-pi-alias-package-");
  const real = path.join(HERE, "..", "node_modules", "aify-wrapper");
  fs.cpSync(path.join(real, "wrappers"), path.join(pkg, "wrappers"), { recursive: true });
  fs.copyFileSync(path.join(real, "VERSION"), path.join(pkg, "VERSION"));
  const file = path.join(pkg, "wrappers", "aliases");
  if (aliases === null) fs.rmSync(file); else fs.writeFileSync(file, aliases);
  return path.join(pkg, "wrappers").replaceAll("\\", "/");
}
function renderWith(templateDir) {
  const dir = tmpDir("aify-pi-alias-out-");
  const res = spawnSync("bash", [INSTALL_SH, "--client", "pi", "http://127.0.0.1:8899", "--emit-pi-wrappers", dir],
    { encoding: "utf8", env: sealedChildEnv({ AIFY_HOME: REPO, AIFY_WRAPPER_TEMPLATE_DIR: templateDir }) });
  return { res, launchers: fs.readdirSync(dir).filter((f) => f.endsWith("-aify")).sort() };
}

test("pi's other names come from the package's wrappers/aliases: one more alias there is one more launcher", () => {
  // CRLF and a comment, as a Windows checkout of the file can carry; another launcher's alias is not pi's.
  const { res, launchers } = renderWith(packageCopy("# names\r\npi-aify omp-aify zz-aify\r\nclaude-aify cl-aify\r\n"));
  assert.equal(res.status, 0, res.stderr);
  assert.deepEqual(launchers, ["omp-aify", "pi-aify", "zz-aify"]);
});

test("a package without wrappers/aliases stops the pi render, rather than quietly dropping omp-aify", () => {
  const { res, launchers } = renderWith(packageCopy(null));
  assert.notEqual(res.status, 0, "a missing aliases file rendered anyway");
  assert.match(res.stderr, /missing \S*aliases/);
  assert.deepEqual(launchers, [], "something was written before the refusal");
});
