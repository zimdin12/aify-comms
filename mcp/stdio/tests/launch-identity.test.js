// Launch identity: the agent id and role this bridge process was started with.
//
// WHY THESE TESTS EXIST NOW. `cleanEnvPlaceholder` lived in `server.js`, the bin entry point, which
// nothing imports — so the function that decides whether this process HAS an identity was unreachable
// from a test, while eight call sites depended on it. Extracting it (v0.5.4 layer 0) is what makes the
// assertions below possible.
//
// THE PLACEHOLDER CASE IS THE ONE THAT MATTERS. An unexpanded `${AIFY_AGENT_ID}` is truthy, so every
// `if (AIFY_AGENT_ID)` guard downstream passes and the bridge registers an agent under a name nobody
// can address. Turning it into "" is what makes those same guards read it correctly as "no identity".

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { AIFY_AGENT_ID, AIFY_AGENT_ROLE, cleanEnvPlaceholder } from "../launch-identity.mjs";
import { bridgeSources, declaringModules } from "./bridge-sources.mjs";
import { sealedChildEnv } from "./_child-env.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LEAF = pathToFileURL(path.join(STDIO, "launch-identity.mjs")).href;

test("an unexpanded ${...} placeholder becomes empty, not a literal agent id", () => {
  // The whole reason the function exists. Each of these is what a wrapper or MCP config writes when a
  // variable was never expanded, and every one of them is truthy as a plain string.
  for (const raw of ["${AIFY_AGENT_ID}", "${AIFY_COMMS_AGENT_ID}", "${ANYTHING}", "  ${padded}  "]) {
    assert.equal(cleanEnvPlaceholder(raw), "", `an unexpanded ${raw} must not survive as an identity`);
  }
});

test("a real value is returned trimmed, and nothing else is discarded", () => {
  // The failure mode opposite to the one above: over-eager sanitizing that throws away a valid id.
  assert.equal(cleanEnvPlaceholder("  comms-senior-dev  "), "comms-senior-dev");
  assert.equal(cleanEnvPlaceholder("agent-1"), "agent-1");
  // Only a WHOLE placeholder is a placeholder. A `${` inside a longer string is a legitimate — if odd —
  // id, and blanking it would lose an identity the operator did configure.
  assert.equal(cleanEnvPlaceholder("prefix-${X}"), "prefix-${X}");
  assert.equal(cleanEnvPlaceholder("${X}-suffix"), "${X}-suffix");
  // A nested/unclosed brace is not the pattern either: `[^}]+` cannot span the inner `}`.
  assert.equal(cleanEnvPlaceholder("${A${B}}"), "${A${B}}");
});

test("empty-ish input is normalised to the empty string, never a placeholder word", () => {
  for (const raw of [undefined, null, "", "   ", false, 0]) {
    assert.equal(cleanEnvPlaceholder(raw), "", `${JSON.stringify(raw)} must not stringify into an id`);
  }
});

test("the exported identity is a string in every case, so `if (id)` is the only guard needed", () => {
  assert.equal(typeof AIFY_AGENT_ID, "string");
  assert.equal(typeof AIFY_AGENT_ROLE, "string");
});

// The three constants are resolved at module load, so in-process assertions can only observe however
// THIS process was started. These child processes pin the actual behaviour; without them a hardcoded
// value would satisfy the file.

function readIdentity(env) {
  const out = execFileSync(
    process.execPath,
    ["--input-type=module", "-e",
      "import { AIFY_AGENT_ID, AIFY_AGENT_ROLE } from " + JSON.stringify(LEAF)
      + "; process.stdout.write(JSON.stringify({ id: AIFY_AGENT_ID, role: AIFY_AGENT_ROLE }));"],
    {
      env: {
        ...sealedChildEnv(),
        AIFY_AGENT_ID: "", AIFY_COMMS_AGENT_ID: "",
        AIFY_AGENT_ROLE: "", AIFY_COMMS_AGENT_ROLE: "",
        ...env,
      },
      encoding: "utf-8",
    },
  );
  return JSON.parse(out);
}

test("the launch environment decides the identity, including the legacy variable names", () => {
  assert.equal(readIdentity({ AIFY_AGENT_ID: "agent-a" }).id, "agent-a");
  assert.equal(readIdentity({ AIFY_COMMS_AGENT_ID: "agent-b" }).id, "agent-b", "the legacy name still works");
  assert.equal(
    readIdentity({ AIFY_AGENT_ID: "agent-a", AIFY_COMMS_AGENT_ID: "agent-b" }).id, "agent-a",
    "the current name wins over the legacy one",
  );
  assert.equal(readIdentity({}).id, "", "an unregistered plain session is legitimately id-less");
});

test("a placeholder reaching the real launch path is blanked, not registered", () => {
  // The end-to-end version of the first test: this is the bug as it actually arrives.
  assert.equal(readIdentity({ AIFY_AGENT_ID: "${AIFY_AGENT_ID}" }).id, "");
  assert.equal(readIdentity({ AIFY_COMMS_AGENT_ID: "${AIFY_COMMS_AGENT_ID}" }).id, "");
});

test("role falls back to coder, and — unlike the id — is NOT placeholder-sanitised", () => {
  assert.equal(readIdentity({}).role, "coder", "the documented default");
  assert.equal(readIdentity({ AIFY_AGENT_ROLE: "tester" }).role, "tester");
  assert.equal(readIdentity({ AIFY_COMMS_AGENT_ROLE: "manager" }).role, "manager");
  // PINNING A KNOWN INCONSISTENCY RATHER THAN FIXING IT. The role line does not call
  // cleanEnvPlaceholder, one line below the id line that does, so an unexpanded placeholder becomes a
  // literal role name. That is a behavioural change and this was a structural slice. The assertion
  // records today's behaviour so that changing it has to be deliberate.
  assert.equal(
    readIdentity({ AIFY_AGENT_ROLE: "${AIFY_AGENT_ROLE}" }).role, "${AIFY_AGENT_ROLE}",
    "current behaviour: the role is NOT sanitised — change this assertion only on purpose",
  );
});

test("server.js no longer declares any of the three — exactly one owner", () => {
  // A leftover declaration would shadow the import and keep working, right up until the two
  // definitions disagreed. That is a silent divergence, not a crash.
  const src = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  for (const name of ["AIFY_AGENT_ID", "AIFY_AGENT_ROLE"]) {
    assert.doesNotMatch(src, new RegExp(`^(?:const|let|var)\\s+${name}\\b`, "m"), `${name} must be imported`);
  }
  assert.doesNotMatch(src, /^(?:export\s+)?function\s+cleanEnvPlaceholder\b/m, "cleanEnvPlaceholder must be imported");
  assert.match(src, /(?<![\w.])AIFY_AGENT_ID(?![\w])/, "server.js is still expected to READ the identity");
});

test("the leaf imports nothing — it is reachable from any module without a cycle", () => {
  // The property that let it be imported at the very top of server.js, ahead of the startup banner
  // that used to precede these declarations.
  const src = readFileSync(path.join(STDIO, "launch-identity.mjs"), "utf-8");
  assert.ok(!/^import\s/m.test(src), "a launch-identity leaf should need no imports");
});

test("managed-dispatch mode is a launch fact, read from the environment at start", () => {
  // Set by the spawner, so it is launch identity in the same sense as the agent id: fixed at start and
  // not acquirable later. Child processes for the usual reason — it resolves at module load, so an
  // in-process assertion is satisfied by a constant.
  const read = (value) => execFileSync(
    process.execPath,
    ["--input-type=module", "-e",
      "import { IS_MANAGED_DISPATCH } from " + JSON.stringify(LEAF)
      + "; process.stdout.write(String(IS_MANAGED_DISPATCH));"],
    { env: { ...sealedChildEnv(), AIFY_MANAGED_DISPATCH: value }, encoding: "utf-8" },
  ).trim();

  for (const truthy of ["1", "true", "yes", "TRUE", "Yes"]) {
    assert.equal(read(truthy), "true", `${truthy} must enable managed-dispatch mode`);
  }
  // The default and the near-misses. An interactive session must never be mistaken for a managed one:
  // "0"/"false"/"" are the documented off values, and anything unrecognised stays OFF rather than
  // guessing — the safe direction, since managed mode changes how delivery is handled.
  for (const falsy of ["", "0", "false", "no", "maybe", "TRUE ", "1 "]) {
    assert.equal(read(falsy), "false", `${JSON.stringify(falsy)} must NOT enable managed-dispatch mode`);
  }
});

test("IS_ENVIRONMENT_BRIDGE is set by EITHER the flag or the env var", () => {
  // Two ways in because there are two ways to start one: an operator runs it by hand with
  // `--environment-bridge`, and a wrapper sets `AIFY_ENVIRONMENT_BRIDGE`. Both must work, and the flag must
  // work even when the env says nothing — a bridge that silently came up as an ordinary agent bridge would
  // leave the environment with no host for dashboard-managed spawns, which fail with nothing to point at.
  const read = (env, argv = []) => execFileSync(
    process.execPath,
    ["--input-type=module", "-e",
      "const m = await import(" + JSON.stringify(LEAF)
      + "); process.stdout.write(String(m.IS_ENVIRONMENT_BRIDGE));",
      // `--` first: without it node parses `--environment-bridge` as one of ITS options and exits
      // "bad option". The separator is what makes it reach `process.argv`, which is what the flag path reads.
      "--", ...argv],
    { env: { ...sealedChildEnv(), AIFY_ENVIRONMENT_BRIDGE: env }, encoding: "utf-8" },
  ).trim();

  assert.equal(read("", ["--environment-bridge"]), "true", "the flag alone must be enough");
  for (const truthy of ["1", "true", "yes", "TRUE", "Yes"]) {
    assert.equal(read(truthy), "true", `${truthy} must enable environment-bridge mode`);
  }
  // OFF is the safe default, and the same near-misses as managed-dispatch above. Starting as an environment
  // bridge by accident is the worse direction: it supersedes the bridge already serving the environment and
  // reaps its managed workers. That has taken a fleet down once.
  for (const falsy of ["", "0", "false", "no", "maybe", "TRUE ", "1 "]) {
    assert.equal(read(falsy), "false", `${JSON.stringify(falsy)} must NOT enable environment-bridge mode`);
  }
  // A near-miss on the FLAG must not count either — argv is matched exactly, not by prefix.
  for (const wrong of ["--environment-bridge=1", "-environment-bridge", "--environment_bridge"]) {
    assert.equal(read("", [wrong]), "false", `${wrong} must not be mistaken for the flag`);
  }
});

test("exactly one module declares IS_ENVIRONMENT_BRIDGE, and server.js reads it back", () => {
  assert.deepEqual(
    declaringModules("IS_ENVIRONMENT_BRIDGE"), [{ file: "launch-identity.mjs", kind: "binding" }],
    "a second declaration would let two parts of the bridge disagree about what this process is",
  );
  // SERVER.JS NO LONGER READS IT, and that is the v0.6.2 change rather than a regression. Every
  // consumer there was a bridge loop, and they went with the bridge. What remains are three readers
  // that all test it NEGATED -- "if this is not a bridge, do the resident thing" -- so the flag is
  // now a vestige whose every branch is the one a resident takes.
  //
  // ASSERTED AS A SHRINKING SET rather than a fixed list: retiring the flag entirely is the follow-up
  // this points at, and a reader appearing again would mean the bridge is being rebuilt.
  //
  // `loop-gate.mjs` is in the list and is NOT a reader -- it names the flag in a comment describing
  // what callers pass as `eligible`. The scan is deliberately textual: stripping comments would make
  // it miss a flag read from a template or a computed name, and this list is short enough that one
  // documented exception is cheaper than a parser.
  const FLAG = /(?<![\w.])IS_ENVIRONMENT_BRIDGE(?![\w])/;
  const readers = bridgeSources()
    .filter(([file]) => file !== "launch-identity.mjs")
    .filter(([, text]) => FLAG.test(text))
    .map(([file]) => file);
  assert.deepEqual(
    readers.sort(),
    ["auto-registration.mjs", "bridge-main.mjs", "loop-gate.mjs", "resident-runtime-lost.mjs"],
    "the IS_ENVIRONMENT_BRIDGE readers changed. It is a vestige of the deleted environment bridge and "
      + "the list may only shrink; a NEW reader means something is keying on a flag that can no "
      + "longer be true in any supported configuration.",
  );
  // The no-second-declaration half, now asked of every module rather than of server.js alone --
  // which is stronger, and is what the `declaringModules` assertion above already establishes.
  for (const [file, text] of bridgeSources()) {
    if (file === "launch-identity.mjs") continue;
    assert.doesNotMatch(text, /^const IS_ENVIRONMENT_BRIDGE/m, `${file} re-declares the flag`);
  }
});
