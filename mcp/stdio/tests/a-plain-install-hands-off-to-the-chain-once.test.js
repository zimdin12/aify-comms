// Guided install S2: a plain `install.sh --client <runtime>` hands off to aify-env's chain (scripts/chain-handoff.sh),
// carrying its key in a private file that is gone when the chain returns, never in argv. An aify-env without the
// chain is left as it is and the full path continues (exit 3); with no aify-env at all, it is installed first.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { sealedChildEnv } from "./_child-env.mjs";

const REPO = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const HANDOFF = path.join(REPO, "scripts", "chain-handoff.sh");
const QUICKSTART = path.join(REPO, "scripts", "install-quickstart.sh");
const toBash = (p) => p.replace(/\\/g, "/");
// The bash this suite already runs under, by its own path: the fake PATH below is all the child may search.
const BASH = spawnSync("bash", ["-c", 'p="$(command -v bash)"; cygpath -w "$p" 2>/dev/null || echo "$p"'], { encoding: "utf8" }).stdout.trim();

/**
 * A bin dir holding the named fake commands, each recording its argv. `npm root -g` answers a global root whose
 * aify-env holds the chain module when `chain` is set, and `npm install -g` puts it there. The fake `node` records
 * the module it runs and, for a key file, the file's text.
 */
function fakeBins(names, { chain = false } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "handoff-bin-"));
  const log = path.join(dir, "calls.log");
  const root = path.join(dir, "global");
  const module = path.join(root, "aify-env", "bin", "aify-env-install.mjs");
  const plant = `mkdir -p "${toBash(path.dirname(module))}" && : > "${toBash(module)}"`;
  if (chain) { fs.mkdirSync(path.dirname(module), { recursive: true }); fs.writeFileSync(module, ""); }
  for (const name of names) {
    const body = name === "npm"
      ? `if [ "$1" = root ]; then echo "${toBash(root)}"; exit 0; fi; echo "npm $*" >> "${toBash(log)}"; ${plant}`
      : `echo "${name} $*" >> "${toBash(log)}"; echo "${name}-env AIFY_API_KEY=\${AIFY_API_KEY:-} CLAUDE_MCP_API_KEY=\${CLAUDE_MCP_API_KEY:-}" >> "${toBash(log)}"; for a in "$@"; do case "$a" in aify-comms.credentialRef=*) f="\${a#*=}"; echo "keyfile $(cat "$f")" >> "${toBash(log)}"; echo "$f" > "${toBash(dir)}/keypath";; esac; done; exit \${FAKE_EXIT:-0}`;
    fs.writeFileSync(path.join(dir, name), `#!/usr/bin/env bash\n${body}\n`, { mode: 0o755 });
  }
  return { dir, log, module: toBash(module) };
}

function handoff(bins, env = {}) {
  const PATH = `${toBash(bins.dir)}:/usr/bin:/bin`;
  const run = spawnSync(BASH, [toBash(HANDOFF), "claude", "http://comms.test:8800", "/src/install.sh"], {
    encoding: "utf8", input: "", env: sealedChildEnv({ PATH, AIFY_API_KEY: "", ...env }),
  });
  const calls = fs.existsSync(bins.log) ? fs.readFileSync(bins.log, "utf8") : "";
  return { status: run.status, stderr: run.stderr, calls };
}

test("with the chain installed it is entered once, as its module, with the installer named and the key in a private file", () => {
  const bins = fakeBins(["npm", "node"], { chain: true });
  const out = handoff(bins, { AIFY_API_KEY: "s3cret-key", CLAUDE_MCP_API_KEY: "s3cret-key" });
  assert.equal(out.status, 0);
  const lines = out.calls.trim().split("\n");
  const entries = lines.filter((l) => l.startsWith(`node ${bins.module} `));
  assert.equal(entries.length, 1, out.calls);
  assert.match(entries[0], /--connect aify-comms=http:\/\/comms\.test:8800 --client claude --installer aify-comms=\/src\/install\.sh/);
  assert.match(entries[0], /--no-prompt/, "a run with no terminal must not ask");
  assert.ok(!entries[0].includes("s3cret-key"), "the key was in argv");
  assert.ok(lines.includes("node-env AIFY_API_KEY= CLAUDE_MCP_API_KEY="), `the chain inherited the key in its environment: ${out.calls}`);
  assert.ok(lines.includes("keyfile s3cret-key"), "the key file did not hold the key");
  // Asked of bash, which wrote the path: Node on Windows reads an MSYS `/tmp/...` as `C:\tmp\...` and would answer
  // "absent" for any path at all.
  const keyfile = fs.readFileSync(path.join(bins.dir, "keypath"), "utf8").trim();
  const exists = (p) => spawnSync(BASH, ["-c", 'test -e "$1"', "_", p]).status === 0;
  assert.ok(exists(path.join(bins.dir, "keypath").replace(/\\/g, "/")), "CONTROL: the probe finds a file that exists");
  assert.equal(exists(keyfile), false, "the key file outlived the chain");
});

test("the chain's own status is the answer, and no key means no --secret", () => {
  const bins = fakeBins(["npm", "node"], { chain: true });
  const out = handoff(bins, { FAKE_EXIT: "78" });
  assert.equal(out.status, 78);
  assert.ok(!out.calls.includes("--secret"));
  assert.ok(!out.calls.includes("--approve"), "CONTROL: nothing is approved unless the operator says so");
});

test("an operator's approvals reach the chain one by one", () => {
  const bins = fakeBins(["npm", "node"], { chain: true });
  const out = handoff(bins, { AIFY_INSTALL_APPROVE: "d1 d2" });
  assert.match(out.calls, /--approve d1 --approve d2/);
});

test("an aify-env without the chain is not replaced: the full path continues (exit 3)", () => {
  const bins = fakeBins(["aify-env", "npm", "node"]);
  const out = handoff(bins);
  assert.equal(out.status, 3);
  assert.match(out.stderr, /without the guided-install chain/);
  assert.ok(!out.calls.includes("npm "), "aify-env was reinstalled over what the host has");
  assert.ok(!out.calls.includes("aify-env "), "the aify-env command was run; an old one could read an argument as a start");
});

test("with no aify-env at all it is installed from its source first, then the chain runs", () => {
  const bins = fakeBins(["npm", "node"]);
  const out = handoff(bins, { AIFY_INSTALL_ENV_SOURCE: "/pkg/aify-env.tgz" });
  assert.equal(out.status, 0);
  const lines = out.calls.trim().split("\n");
  assert.match(lines[0], /^npm install -g .*\/pkg\/aify-env\.tgz$/);
  assert.ok(lines[1].startsWith(`node ${bins.module} --connect aify-comms=`), lines[1]);
});

test("a run that hands off stores nothing first; a full-path run resolves once and stores; the chain's modes do neither", () => {
  // The installer's own key block and handoff, executed with their helpers stubbed to record that they ran. A
  // store before the handoff replaced the host's credential under the default reference, then the chain could refuse.
  const install = fs.readFileSync(path.join(REPO, "install.sh"), "utf8");
  const block = /^case "\$MODE" in acquire-only\|assets-only\) ;; \*\)\n[\s\S]*?^;; esac$/m.exec(install)?.[0];
  assert.ok(block, "the key block must be found before it can be exercised");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "handoff-key-"));
  const log = `"${toBash(root)}/ran"`;
  fs.mkdirSync(path.join(root, "scripts"));
  fs.writeFileSync(path.join(root, "scripts", "api-key-for-install.sh"), `echo asked >> ${log}; printf k\n`);
  fs.writeFileSync(path.join(root, "scripts", "credential-carrier.sh"), `echo carried >> ${log}\n`);
  fs.writeFileSync(path.join(root, "scripts", "chain-handoff.sh"), `echo "handed-off key=$AIFY_API_KEY" >> ${log}; exit \${HANDOFF_EXIT:-0}\n`);
  const ran = (mode, env = {}) => {
    fs.rmSync(path.join(root, "ran"), { force: true });
    spawnSync(BASH, ["-c", block], { env: sealedChildEnv({ MODE: mode, SCRIPT_DIR: toBash(root), WITH_API_KEY: "false", AIFY_INSTALL_CHAIN: undefined, ...env }) });
    return fs.existsSync(path.join(root, "ran")) ? fs.readFileSync(path.join(root, "ran"), "utf8").trim().split("\n") : [];
  };
  assert.deepEqual(ran(""), ["asked", "handed-off key=k"], "a run that hands off stored the key before the chain could refuse");
  assert.deepEqual(ran("", { HANDOFF_EXIT: "3" }), ["asked", "handed-off key=k", "carried"], "CONTROL: the full path, after no handoff, stores once");
  assert.deepEqual(ran("standalone"), ["asked", "carried"], "CONTROL: --standalone is the old path");
  assert.deepEqual(ran("acquire-only"), []);
  assert.deepEqual(ran("assets-only"), []);
});

test("the chain's modes resolve no key value, so no registration can put one in argv", () => {
  // The installer's own key lookup, which every registration (claude/codex `--env`, hermes' node argument) calls.
  const install = fs.readFileSync(path.join(REPO, "install.sh"), "utf8");
  const lookup = /^aify_api_key\(\) \{[\s\S]*?^\}$|^aify_api_key\(\) \{.*\}$/m.exec(install)?.[0];
  assert.ok(lookup, "the key lookup must be found before it can be exercised");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "handoff-lookup-"));
  fs.mkdirSync(path.join(root, "scripts"));
  fs.writeFileSync(path.join(root, "scripts", "api-key.sh"), "printf from-env-or-dotenv\n");
  const key = (mode) => spawnSync(BASH, ["-c", `${lookup}\naify_api_key`], {
    encoding: "utf8", env: sealedChildEnv({ MODE: mode, SCRIPT_DIR: toBash(root), AIFY_API_KEY: "inherited" }),
  }).stdout;
  assert.equal(key(""), "from-env-or-dotenv", "CONTROL: a full install resolves its key");
  assert.equal(key("assets-only"), "");
  assert.equal(key("acquire-only"), "");
});

test("the quick-start lines are the installer's, per client", () => {
  for (const client of ["claude", "codex", "hermes"]) {
    const out = spawnSync("bash", [toBash(QUICKSTART), client], { encoding: "utf8" });
    assert.equal(out.status, 0);
    assert.match(out.stdout, /^Quick start:\n/);
    assert.match(out.stdout, new RegExp(`runtime=\\\\?"${client === "claude" ? "claude-code" : client}`));
  }
  const install = fs.readFileSync(path.join(REPO, "install.sh"), "utf8");
  assert.match(install, /scripts\/install-quickstart\.sh" "\$CLIENT"/);
});
