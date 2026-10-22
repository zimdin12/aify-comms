// Guided install S2: a plain `install.sh --client <runtime>` hands off to aify-env's chain (scripts/chain-handoff.sh),
// carrying its key on the chain's stdin: never a file, argv or the chain's environment. Options the chain does not
// carry are refused before anything is written. A missing or older packaged aify-env is installed first; a linked
// checkout, or an aify-env npm did not install, is refused rather than replaced or silently bypassed.
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
 * aify-env is `env`: "chain" (holds the chain module), "older" (a packaged one without it), "linked" (a git
 * checkout without it), or absent. `npm install -g` plants the module. The fake `node` also records the key
 * variables it inherited and what arrived on its stdin.
 */
function fakeBins(names, { env = "" } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "handoff-bin-"));
  const log = path.join(dir, "calls.log");
  const root = path.join(dir, "global");
  const module = path.join(root, "aify-env", "bin", "aify-env-install.mjs");
  const plant = `mkdir -p "${toBash(path.dirname(module))}" && : > "${toBash(module)}"`;
  if (env) fs.mkdirSync(path.dirname(module), { recursive: true });
  if (env === "chain") fs.writeFileSync(module, "");
  if (env === "linked") fs.mkdirSync(path.join(root, "aify-env", ".git"));
  for (const name of names) {
    const body = name === "npm"
      ? `if [ "$1" = root ]; then echo "${toBash(root)}"; exit 0; fi; echo "npm $*" >> "${toBash(log)}"; ${plant}`
      : `echo "${name} $*" >> "${toBash(log)}"; echo "${name}-env AIFY_API_KEY=\${AIFY_API_KEY:-} CLAUDE_MCP_API_KEY=\${CLAUDE_MCP_API_KEY:-}" >> "${toBash(log)}"; [ -t 0 ] || echo "${name}-stdin $(cat)" >> "${toBash(log)}"; exit \${FAKE_EXIT:-0}`;
    fs.writeFileSync(path.join(dir, name), `#!/usr/bin/env bash\n${body}\n`, { mode: 0o755 });
  }
  return { dir, log, module: toBash(module) };
}

function handoff(bins, env = {}) {
  const PATH = `${toBash(bins.dir)}:/usr/bin:/bin`;
  const run = spawnSync(BASH, [toBash(HANDOFF), "claude", "http://comms.test:8800", "/src/install.sh"], {
    encoding: "utf8", input: "",
    env: sealedChildEnv({ PATH, AIFY_API_KEY: "", WITH_HOOK: undefined, MCP_TRANSPORT: undefined, ENV_ENDPOINT_GIVEN: undefined, ...env }),
  });
  const calls = fs.existsSync(bins.log) ? fs.readFileSync(bins.log, "utf8") : "";
  return { status: run.status, stderr: run.stderr, calls, lines: calls.trim().split("\n").filter(Boolean) };
}

test("with the chain installed it is entered once, as its module, with the key on stdin and nowhere else", () => {
  const bins = fakeBins(["npm", "node"], { env: "chain" });
  const out = handoff(bins, { AIFY_API_KEY: "s3cret-key", CLAUDE_MCP_API_KEY: "s3cret-key" });
  assert.equal(out.status, 0);
  const entries = out.lines.filter((l) => l.startsWith(`node ${bins.module} `));
  assert.equal(entries.length, 1, out.calls);
  assert.match(entries[0], /--connect aify-comms=http:\/\/comms\.test:8800 --client claude --installer aify-comms=\/src\/install\.sh/);
  assert.match(entries[0], /--no-prompt/, "a run with no terminal must not ask");
  assert.match(entries[0], /--secret-stdin aify-comms\.credentialRef$/);
  assert.ok(!entries[0].includes("s3cret-key"), "the key was in argv");
  assert.ok(out.lines.includes("node-env AIFY_API_KEY= CLAUDE_MCP_API_KEY="), `the chain inherited the key in its environment: ${out.calls}`);
  assert.ok(out.lines.includes("node-stdin s3cret-key"), `the key did not arrive on the chain's stdin: ${out.calls}`);
});

test("the chain's own status is the answer, and no key means no --secret", () => {
  const bins = fakeBins(["npm", "node"], { env: "chain" });
  const out = handoff(bins, { FAKE_EXIT: "78" });
  assert.equal(out.status, 78);
  assert.ok(!out.calls.includes("--secret"));
  assert.ok(!out.calls.includes("--approve"), "CONTROL: nothing is approved unless the operator says so");
});

test("an operator's approvals reach the chain one by one", () => {
  const bins = fakeBins(["npm", "node"], { env: "chain" });
  const out = handoff(bins, { AIFY_INSTALL_APPROVE: "d1 d2" });
  assert.match(out.calls, /--approve d1 --approve d2/);
});

test("options the chain does not carry are refused before anything is installed or run", () => {
  const cases = [["WITH_HOOK", "true", "--with-hook"], ["MCP_TRANSPORT", "sse", "--mcp-transport"], ["ENV_ENDPOINT_GIVEN", "true", "--env-endpoint"]];
  for (const [variable, value, flag] of cases) {
    const out = handoff(fakeBins(["npm", "node"]), { [variable]: value });
    assert.equal(out.status, 64, flag);
    assert.match(out.stderr, new RegExp(`${flag}: not carried by the guided-install chain yet, so nothing was written`));
    assert.match(out.stderr, /--standalone/);
    assert.deepEqual(out.lines, [], `${flag}: something ran before the refusal: ${out.calls}`);
  }
  const control = handoff(fakeBins(["npm", "node"], { env: "chain" }), { WITH_HOOK: "false", ENV_ENDPOINT_GIVEN: "false" });
  assert.equal(control.status, 0, "CONTROL: an option left at its default is not a refusal");
});

test("a linked aify-env checkout without the chain is never modified: refused, naming git pull and --standalone", () => {
  const bins = fakeBins(["aify-env", "npm", "node"], { env: "linked" });
  const out = handoff(bins);
  assert.equal(out.status, 78);
  assert.match(out.stderr, /linked checkout .* never modified/);
  assert.match(out.stderr, /git pull.*--standalone/s);
  assert.deepEqual(out.lines, [], `something ran: ${out.calls}`);
});

test("an aify-env on PATH that npm did not install is not replaced", () => {
  const bins = fakeBins(["aify-env", "npm", "node"]);
  const out = handoff(bins);
  assert.equal(out.status, 78);
  assert.match(out.stderr, /npm did not install it, so it is not replaced/);
  assert.deepEqual(out.lines, [], `something ran: ${out.calls}`);
});

test("an older packaged aify-env is updated from its source, then the chain runs", () => {
  const bins = fakeBins(["aify-env", "npm", "node"], { env: "older" });
  const out = handoff(bins, { AIFY_INSTALL_ENV_SOURCE: "/pkg/aify-env.tgz" });
  assert.equal(out.status, 0, out.stderr);
  assert.match(out.lines[0], /^npm install -g .*\/pkg\/aify-env\.tgz$/);
  assert.ok(out.lines[1].startsWith(`node ${bins.module} --connect aify-comms=`), out.lines[1]);
  assert.ok(!out.calls.includes("aify-env "), "the aify-env command was run; an old one could read an argument as a start");
});

test("with no aify-env at all it is installed from its source first, then the chain runs", () => {
  const bins = fakeBins(["npm", "node"]);
  const out = handoff(bins, { AIFY_INSTALL_ENV_SOURCE: "/pkg/aify-env.tgz" });
  assert.equal(out.status, 0);
  assert.match(out.lines[0], /^npm install -g .*\/pkg\/aify-env\.tgz$/);
  assert.ok(out.lines[1].startsWith(`node ${bins.module} --connect aify-comms=`), out.lines[1]);
});

test("a run that hands off stores nothing, whatever the chain answers; a full-path run resolves once and stores; the chain's modes do neither", () => {
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
  assert.deepEqual(ran("", { HANDOFF_EXIT: "78" }), ["asked", "handed-off key=k"], "a refused handoff fell back to storing");
  assert.deepEqual(ran("", { AIFY_INSTALL_CHAIN: "1" }), ["asked", "carried"], "CONTROL: under the chain's mark there is no handoff");
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
