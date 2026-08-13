// What an environment says it is — the description the dashboard draws its spawn targets from.
//
// An environment is one host as the control plane sees it: a machine, an OS, a set of workspace roots, and
// the runtimes it can actually launch. Getting this wrong does not raise an error; it offers a spawn for a
// runtime that is not installed, or a workspace root nothing can resolve. `environmentKind` and
// `environmentOs` are what an environment ROW IS MATCHED ON, so two bridges describing the same host
// differently would register as two environments and split its workers between them.
//
// None of it was reachable from a test before the extraction: it lived in `server.js`, the bin entry point.
//
// EVERY CASE THAT DEPENDS ON THE ENVIRONMENT RUNS IN A CHILD with the inputs supplied by the fixture. These
// functions read `AIFY_ENVIRONMENT_KIND`, `AIFY_ENVIRONMENT_LABEL`, `AIFY_CWD_ROOTS`, `WSL_DISTRO_NAME` and
// `container` — and this suite is run on a live host, where inheriting any of them would test the machine
// rather than the code.

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { declaringModules, isUsedInBridge } from "./bridge-sources.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LEAF = pathToFileURL(path.join(STDIO, "environment-identity.mjs")).href;

// Evaluate an expression against the module in a child whose environment is exactly what the fixture says.
// The five inputs are blanked by default so a case only sees what it sets.
function evalIn(expr, env = {}) {
  const script = `
    const m = await import(${JSON.stringify(LEAF)});
    process.stdout.write(JSON.stringify(${expr}));
  `;
  return JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", script], {
    env: {
      ...process.env,
      AIFY_ENVIRONMENT_KIND: "", AIFY_ENVIRONMENT_LABEL: "", AIFY_CWD_ROOTS: "",
      WSL_DISTRO_NAME: "", container: "",
      AIFY_SERVER_URL: "", CLAUDE_MCP_SERVER_URL: "",
      ...env,
    },
    encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"],
  }));
}

test("KIND IS THE MATCH KEY, and an explicit value always wins", () => {
  // An operator naming the kind is overriding detection on purpose — a host that looks like one thing and
  // must register as another. If detection could override it, the row would move under the bridge's feet.
  assert.equal(evalIn("m.environmentKind()", { AIFY_ENVIRONMENT_KIND: "my-vm" }), "my-vm");
  assert.equal(evalIn("m.environmentKind()", { AIFY_ENVIRONMENT_KIND: "  padded  " }), "padded",
    "…trimmed, because a stray space would make a second environment row");
  // Blank is not a value: it must fall through to detection rather than registering an empty kind.
  for (const blank of ["", "   "]) {
    assert.notEqual(evalIn("m.environmentKind()", { AIFY_ENVIRONMENT_KIND: blank }), blank);
    assert.ok(evalIn("m.environmentKind()", { AIFY_ENVIRONMENT_KIND: blank }));
  }
});

test("WSL and docker are detected ahead of the platform", () => {
  // Both look like linux to `process.platform`, and both are the case where that answer is wrong: a WSL
  // bridge and a native linux bridge on the same box must not collide into one environment row.
  assert.equal(evalIn("m.environmentKind()", { WSL_DISTRO_NAME: "Ubuntu" }), "wsl");
  assert.equal(evalIn("m.environmentKind()", { container: "podman" }), "docker");
  // …and an explicit kind still outranks both.
  assert.equal(evalIn("m.environmentKind()", { WSL_DISTRO_NAME: "Ubuntu", AIFY_ENVIRONMENT_KIND: "custom" }),
    "custom");
});

test("kind and os agree about the platform they both describe", () => {
  // `environmentOs` is the narrower of the two: it never reports wsl or docker, only the OS family. On this
  // host they must still agree, or a row would be matched on one and displayed with the other.
  const kind = evalIn("m.environmentKind()");
  const os = evalIn("m.environmentOs()");
  assert.ok(["windows", "macos", "linux"].includes(os), `os must be one of the three families, got ${os}`);
  if (["windows", "macos", "linux"].includes(kind)) {
    assert.equal(kind, os, "with no container or WSL marker the two must give the same answer");
  }
});

test("THE LABEL IS HUMAN-FACING AND NEVER RAGGED", () => {
  // It is what the operator reads in the dashboard's environment picker. The WSL branch interpolates a
  // distro name that may be absent, which is exactly where a double space or a trailing word appears.
  assert.equal(evalIn(`m.environmentLabel("windows", "BOX")`), "Windows on BOX");
  assert.equal(evalIn(`m.environmentLabel("macos", "BOX")`), "macOS on BOX");
  assert.equal(evalIn(`m.environmentLabel("docker", "BOX")`), "Docker on BOX");
  assert.equal(evalIn(`m.environmentLabel("anything-else", "BOX")`), "Linux on BOX",
    "an unknown kind reads as Linux rather than echoing the kind back at the operator");
  assert.equal(evalIn(`m.environmentLabel("wsl", "BOX")`, { WSL_DISTRO_NAME: "Ubuntu" }), "WSL Ubuntu on BOX");
  // The collapse that keeps it tidy when the distro is unset — without it this reads "WSL  on BOX".
  const noDistro = evalIn(`m.environmentLabel("wsl", "BOX")`);
  assert.equal(noDistro, "WSL on BOX");
  assert.doesNotMatch(noDistro, / {2}/, "no double spaces");
  assert.equal(noDistro, noDistro.trim());
  // An explicit label wins over every branch, including WSL's interpolation.
  assert.equal(evalIn(`m.environmentLabel("wsl", "BOX")`, { AIFY_ENVIRONMENT_LABEL: "My Box" }), "My Box");
});

test("THE DEFAULT ROOTS BRANCH IS NEVER EMPTY — an environment with no roots offers nowhere to spawn", () => {
  // Scoped to the DEFAULT branch on purpose: the next test shows a malformed explicit override CAN yield an
  // empty list, so a title claiming roots are never empty would be false. With no override the answer is
  // exactly the process's own directory, the one place a spawn is certain to resolve.
  const roots = evalIn("m.cwdRootsForEnvironment()");
  assert.ok(Array.isArray(roots) && roots.length >= 1, "there must always be at least one root");
  assert.ok(roots.every((r) => r && r.trim()), "and none of them may be blank");
});

test("an explicit roots list is split, trimmed, deduped and ORDER-PRESERVED", () => {
  // Order is not cosmetic: the first root is the default workspace a spawn lands in. The delimiter is the
  // platform's PATH separator, so the fixture builds the string with `path.delimiter` rather than assuming.
  // The fixture is deliberately NOT in sorted order. My first version used `/a /b /c`, which sorts to
  // itself — so a mutation adding `.sort()` survived and the order claim was never actually tested.
  const d = path.delimiter;
  const roots = evalIn("m.cwdRootsForEnvironment()", { AIFY_CWD_ROOTS: `/c${d} /a ${d}/c${d}${d}/b` });
  assert.deepEqual(roots, ["/c", "/a", "/b"],
    "trimmed, empty segments dropped, the repeated /c deduped, and FIRST-SEEN order kept — not sorted");
  // CURRENT BEHAVIOUR, AND IT IS A GAP — reported, not fixed here, because changing it is a behaviour
  // decision. A non-empty `AIFY_CWD_ROOTS` whose every segment is blank takes the explicit branch, filters
  // to nothing, and yields `[]`: an environment advertising NO place to spawn into. Only a malformed
  // override can produce it; the default branch cannot. Pinned so a fix has something to flip.
  const degenerate = evalIn("m.cwdRootsForEnvironment()", { AIFY_CWD_ROOTS: `${d}${d}   ${d}` });
  assert.deepEqual(degenerate, [],
    "CURRENT: a separators-only roots list yields no roots rather than falling back to the default");
});

test("THE HEARTBEAT PAYLOAD CARRIES WHAT AN ENVIRONMENT IS MATCHED AND JUDGED ON", () => {
  // The description the dashboard renders. `id`/`machineId` are how the row is found; the build and version
  // are how an operator tells which code is answering; the runtimes are the spawn menu.
  const p = evalIn("m.environmentHeartbeatPayload()");
  // Field names read off the real payload, not assumed: it is `id`, not `environmentId`.
  for (const field of ["id", "machineId", "kind", "os", "label", "cwdRoots", "bridgeId", "bridgeVersion",
    "runtimes", "terminalRuntimes"]) {
    assert.ok(field in p, `the payload must carry ${field}`);
  }
  assert.equal(p.kind, evalIn("m.environmentKind()"), "the payload's kind is the same function's answer");
  assert.equal(p.os, evalIn("m.environmentOs()"));
  assert.deepEqual(p.cwdRoots, evalIn("m.cwdRootsForEnvironment()"));
  assert.ok(Array.isArray(p.cwdRoots) && p.cwdRoots.length >= 1);
  // Nothing may reach the control plane as a placeholder or an undefined — these are rendered verbatim.
  const flat = JSON.stringify(p);
  assert.doesNotMatch(flat, /undefined|\[object Object\]|\$\{/,
    `the payload leaked a placeholder: ${flat.slice(0, 200)}`);
});

test("the payload reports the SAME build tag the rest of the bridge does", () => {
  // The environment row is one of the places `bridgeBuild` surfaces, and it is what `aify-comms doctor`
  // reads to decide whether a live bridge is running current code. A payload with its own idea of the build
  // would make that check answer about nothing.
  const both = evalIn(`[m.environmentHeartbeatPayload(), (await import(${
    JSON.stringify(pathToFileURL(path.join(STDIO, "bridge-build.mjs")).href)})).BRIDGE_BUILD_TAG]`);
  const [payload, tag] = both;
  const reported = payload.bridgeBuild ?? payload.build ?? null;
  if (reported !== null) {
    assert.equal(reported, tag, "the payload's build must be the owner's build");
  } else {
    // Current shape: the heartbeat does not carry a build field. Pinned so adding one is a decision, and so
    // this test does not silently pass by asserting nothing.
    assert.ok(tag, "the owner still produces a tag even though the heartbeat does not carry it");
  }
});

test("exactly one module declares each, and the bridge still uses them", () => {
  for (const name of ["environmentKind", "environmentOs", "environmentLabel",
    "cwdRootsForEnvironment", "environmentHeartbeatPayload"]) {
    assert.deepEqual(declaringModules(name), [{ file: "environment-identity.mjs", kind: "function" }],
      `${name} must be declared exactly once, by its owner`);
    assert.ok(isUsedInBridge(name), `${name} must still be called by something`);
  }
});

test("the owner holds no state and does not send anything", () => {
  // It builds the description; `server.js` owns the timer and the decision to send. That split is what
  // makes it importable by a test at all.
  const src = fs.readFileSync(path.join(STDIO, "environment-identity.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
  assert.doesNotMatch(src, /setInterval|httpCall|environmentHeartbeatTimer/,
    "this module describes the environment; it must not also report it");
  const imports = [...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]).sort();
  assert.deepEqual(imports, [
    "./bridge-build.mjs",
    "./bridge-instance.mjs",
    "./dedupe.mjs",
    "./environment-runtimes.js",
    "./registration-inputs.mjs",
    "./runtimes.js",
    "./terminal-runtime.js",
    "./version.js",
    "fs",
    "os",
    "path",
  ]);
});

// ── workspaceWithinRoots — the check that turns the advertised roots into a permission ──────────────
//
// Called only with `environment.cwdRoots`, at four spawn/dispatch sites. A false NO blocks a legitimate
// spawn with "outside this bridge's advertised roots"; a false YES lets an agent be launched in a directory
// the environment never offered. Both directions are asserted, and it runs in a child because it reads
// `HOME`/`USERPROFILE` to expand `~`.

function withinIn(workspace, roots, env = {}) {
  return evalIn(`m.workspaceWithinRoots(${JSON.stringify(workspace)}, ${JSON.stringify(roots)})`, env);
}

test("A SIBLING THAT MERELY SHARES A PREFIX IS NOT INSIDE", () => {
  // The classic containment bug: `startsWith(root)` alone would put `/srv/data-evil` inside `/srv/data`.
  // The check appends the separator, and that is the whole difference between a boundary and a prefix.
  assert.equal(withinIn("/srv/data-evil", ["/srv/data"]), false, "a prefix sibling must NOT be inside");
  assert.equal(withinIn("/srv/database", ["/srv/data"]), false);
  // …while the real cases still pass.
  assert.equal(withinIn("/srv/data", ["/srv/data"]), true, "the root itself is inside");
  assert.equal(withinIn("/srv/data/app", ["/srv/data"]), true, "a child is inside");
  assert.equal(withinIn("/srv/data/a/b/c", ["/srv/data"]), true, "…at any depth");
  // A parent is not inside its own child.
  assert.equal(withinIn("/srv", ["/srv/data"]), false);
});

test('"/" IS THE MATCH-ALL ROOT — the 2026-06-03 regression', () => {
  // Recorded in the function itself: stripping the trailing slash turned "/" into "" and it was filtered
  // out, so a "/"-rooted environment matched NOTHING and every managed spawn was rejected. The default
  // advertised roots were `['/', '~']`, so this broke normal environments, not exotic ones.
  assert.equal(withinIn("/anywhere/at/all", ["/"]), true);
  assert.equal(withinIn("C:/Docker/x", ["/"]), true);
  assert.equal(withinIn("/anything", ["/", "/srv"]), true, "match-all wins even beside a narrow root");
});

test("~ IS EXPANDED — the other half of that regression", () => {
  // A root of "~" was never expanded, so an absolute workspace under the home directory never matched it.
  const home = "/home/tester";
  const env = { HOME: home, USERPROFILE: home };
  assert.equal(withinIn(`${home}/proj`, ["~"], env), true, "a path under home matches the ~ root");
  assert.equal(withinIn(home, ["~"], env), true, "home itself matches");
  assert.equal(withinIn(`${home}/proj/deep`, ["~/proj"], env), true, "~/sub is expanded too");
  assert.equal(withinIn("/elsewhere/proj", ["~"], env), false, "…and something outside home still fails");
});

test("separators and trailing slashes are normalised on BOTH sides", () => {
  // The roots come from an operator env var and the workspace from a spawn request, so the two spellings
  // meet here for the first time. A backslash root would otherwise never match a forward-slash workspace.
  assert.equal(withinIn("C:/Docker/x", ["C:\\Docker"]), true, "a backslash root matches a forward-slash path");
  assert.equal(withinIn("C:\\Docker\\x", ["C:/Docker"]), true, "…and the reverse");
  assert.equal(withinIn("/srv/data/app", ["/srv/data/"]), true, "a trailing slash on the root is ignored");
  assert.equal(withinIn("/srv/data/", ["/srv/data"]), true, "…and on the workspace");
  assert.equal(withinIn("  /srv/data/app  ", ["  /srv/data  "]), true, "surrounding whitespace is trimmed");
});

test("IT FAILS OPEN when there is nothing to check against, and that is deliberate", () => {
  // Current behaviour, pinned because it is a permission check that answers YES on absent input. An
  // environment that advertises no usable roots does not restrict anything — the alternative would block
  // every spawn on a misconfigured roots list, which is the failure the 2026-06-03 fix was undoing.
  assert.equal(withinIn("/anything", []), true, "no roots at all imposes no restriction");
  assert.equal(withinIn("/anything", ["", "   "]), true, "…nor do blank roots");
  assert.equal(withinIn("", ["/srv"]), true, "an absent workspace is not checked");
  assert.equal(withinIn("   ", ["/srv"]), true);
});

test("the roots this environment advertises are accepted by its own check", () => {
  // The two halves must agree: whatever `cwdRootsForEnvironment` publishes has to pass the gate that reads
  // it, or the bridge would advertise a root it then refuses to spawn into.
  const roots = evalIn("m.cwdRootsForEnvironment()");
  for (const root of roots) {
    assert.equal(withinIn(root, roots), true, `advertised root ${root} must satisfy its own check`);
    assert.equal(withinIn(`${root}/child`, roots), true, `…and so must a child of it`);
  }
});
