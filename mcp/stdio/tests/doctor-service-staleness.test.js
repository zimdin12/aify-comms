#!/usr/bin/env node
// A docs-only commit must not report "your service changes are NOT running" (false red).
//
// Observed live 2026-08-10: three commits touching only `docs/` and `scripts/` produced
//   "serving build 76fb7b9, repo HEAD is f94b884 (3 commit(s) behind).
//    Your service changes are NOT running. Rebuild."
// There were no service changes. `--strict` exits 1 on that, so a documentation commit would
// fail any script or CI gate using it.
//
// N13 already taught this lesson for `bridge-installed` and the fix was never carried across.
// This pins the carry-across so the two checks cannot drift apart again.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  SERVICE_IMAGE_NON_RUNTIME_PATHS,
  SERVICE_RUNTIME_EXCLUDE_PATHS,
  SERVICE_RUNTIME_PATHS,
  bridgeInstallVerdict,
  serviceBuildVerdict,
} from "../doctor-predicates.js";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

const BUILT = "76fb7b9aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const HEAD = "f94b884bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

// ── the false red this exists to prevent ─────────────────────────────────────────────
test("commits that touch NO runtime content are not stale", () => {
  const v = serviceBuildVerdict({
    builtSha: BUILT, builtShort: "76fb7b9", headSha: HEAD, headShort: "f94b884",
    runtimeCommits: 0, totalCommits: 3,
  });
  assert.equal(v.ok, true, "a docs-only commit must not fail --strict");
  assert.equal(v.code, "ok");
  assert.match(v.detail, /none touching code the service executes/);
  assert.match(v.detail, /3 commit\(s\) ahead/, "still reports the gap honestly");
  assert.equal(v.fix, "", "nothing for the operator to do");
});

test("the retired wording never comes back", () => {
  const v = serviceBuildVerdict({
    builtSha: BUILT, headSha: HEAD, headShort: "f94b884", runtimeCommits: 0, totalCommits: 3,
  });
  assert.doesNotMatch(`${v.detail} ${v.fix}`, /changes are NOT running/i);
});

// ── genuine staleness must still fail ────────────────────────────────────────────────
test("commits that DO touch runtime content are stale", () => {
  const v = serviceBuildVerdict({
    builtSha: BUILT, builtShort: "76fb7b9", headSha: HEAD, headShort: "f94b884",
    runtimeCommits: 2, totalCommits: 5,
  });
  assert.equal(v.ok, false);
  assert.equal(v.code, "stale");
  assert.match(v.detail, /2 commit\(s\) since then changed code the service EXECUTES/);
  assert.match(v.detail, /RUNNING service is older/);
  assert.match(v.fix, /stamp\.sh.*docker compose up -d --build/s);
});

test("equal shas are ok", () => {
  const v = serviceBuildVerdict({ builtSha: BUILT, builtShort: "76fb7b9", headSha: BUILT });
  assert.equal(v.ok, true);
  assert.match(v.detail, /== repo HEAD/);
});

test("a service reporting no build sha is still a failure", () => {
  const v = serviceBuildVerdict({ builtSha: "", headSha: HEAD });
  assert.equal(v.ok, false);
  assert.equal(v.code, "unknown-build");
  assert.match(v.fix, /stamp\.sh/);
});

test("no checkout to compare against is not a failure", () => {
  const v = serviceBuildVerdict({ builtSha: BUILT, builtShort: "76fb7b9", headSha: "" });
  assert.equal(v.ok, true);
  assert.match(v.detail, /no checkout to compare against/);
});

// ── the two checks must keep agreeing in shape ───────────────────────────────────────
test("service and bridge verdicts behave identically on the benign case", () => {
  const svc = serviceBuildVerdict({
    builtSha: BUILT, headSha: HEAD, headShort: "f94b884", runtimeCommits: 0, totalCommits: 3,
  });
  const br = bridgeInstallVerdict({
    installedSha: BUILT, headSha: HEAD, headShort: "f94b884", bridgeCommits: 0, totalCommits: 3,
  });
  assert.equal(svc.ok, br.ok, "both must be green when nothing relevant changed");
  assert.equal(svc.fix, br.fix, "neither should hand the operator a chore");
});

// ── every Dockerfile COPY source must be ACCOUNTED FOR, runtime or not ───────────────
// Reviewer's design: narrowing to runtime paths must not lose drift visibility. So each COPY
// source is either runtime (a rebuild trigger) or explicitly listed as non-runtime cargo WITH A
// REASON. Silent omission is the failure mode to prevent — that would let a new COPY of genuinely
// executed code slip in while doctor reported clean, which is a false GREEN, the worse direction.
test("every Dockerfile COPY source is either runtime or reasoned non-runtime cargo", () => {
  const dockerfile = readFileSync(new URL("../../../Dockerfile", import.meta.url), "utf8");
  const copied = new Set();
  for (const line of dockerfile.split("\n")) {
    const m = line.match(/^\s*COPY\s+(.+)$/);
    if (!m) continue;
    const parts = m[1].trim().split(/\s+/);
    parts.pop(); // destination
    for (const src of parts) {
      if (src.startsWith("--")) continue;
      copied.add(src.replace(/\/$/, ""));
    }
  }
  assert.ok(copied.size > 0, "Dockerfile must have COPY lines to check");
  for (const src of copied) {
    const top = src.split("/")[0];
    const isRuntime = SERVICE_RUNTIME_PATHS.some((p) => p === src || p === top || p.startsWith(`${src}/`));
    const isCargo = Object.hasOwn(SERVICE_IMAGE_NON_RUNTIME_PATHS, src)
      || Object.hasOwn(SERVICE_IMAGE_NON_RUNTIME_PATHS, top);
    assert.ok(
      isRuntime || isCargo,
      `Dockerfile COPYs "${src}" but it is neither in SERVICE_RUNTIME_PATHS nor listed in `
        + "SERVICE_IMAGE_NON_RUNTIME_PATHS with a reason. Decide which it is — if the service "
        + "executes it, doctor must demand a rebuild; if not, say why not.",
    );
  }
});

test("every non-runtime entry carries a non-trivial reason", () => {
  for (const [path, reason] of Object.entries(SERVICE_IMAGE_NON_RUNTIME_PATHS)) {
    assert.equal(typeof reason, "string");
    assert.ok(reason.length > 25, `"${path}" needs a real reason, got: ${JSON.stringify(reason)}`);
  }
});

test("host-side bridge code is NOT a service rebuild trigger", () => {
  // The concrete false red that forced this rework: a bridge-only commit demanded a container
  // rebuild for code the container never executes.
  //
  // THIS TEST USED TO PIN THE WRONG THING. It asserted the literal list — `!includes("mcp")` and
  // `includes("mcp/sse_server.py")` — which is WHERE the rule was written rather than WHAT it does.
  // Naming the exact file made `mcp/` opt-in, so a second runtime module beside the SSE transport
  // would have been cargo by default and doctor would have called the container clean while the
  // code it runs had changed. The list satisfied this test and still had the hole, because the two
  // are not the same claim. Excluding `mcp/stdio` from a directory-wide `mcp` keeps the property
  // this test was built for AND closes that.
  assert.ok(SERVICE_RUNTIME_PATHS.includes("mcp"),
    "mcp/ must be runtime by DEFAULT, so a new module beside sse_server.py is covered on the day "
    + "it is created — the only day the question can still be answered");
  assert.ok(!SERVICE_RUNTIME_PATHS.includes("mcp/stdio"), "the bridge is not runtime");
  assert.ok(SERVICE_RUNTIME_EXCLUDE_PATHS.includes("mcp/stdio"),
    "and it must be excluded explicitly, or every bridge commit demands a container rebuild");
});

test("a real bridge-only commit is not selected by the real pathspec", () => {
  // The property itself, run against git rather than against the arrays. The list assertions above
  // describe a pathspec; only git can say what that pathspec SELECTS, and the exclude syntax
  // (`:(exclude)`) is the part most likely to be written correctly-looking and wrong.
  //
  // THE SUBJECT IS FOUND, NOT HARDCODED: a pinned sha would rot, and this must keep meaning
  // something as history grows. It walks back until it finds a commit whose every changed file is
  // under mcp/stdio/ — a genuine bridge-only commit — and asserts the doctor's own pathspec skips
  // it. If none exists in range the test says so rather than passing on an empty search.
  const git = (...args) => execFileSync("git", args, { cwd: REPO, encoding: "utf8" }).trim();
  const spec = [
    ...SERVICE_RUNTIME_PATHS,
    ...SERVICE_RUNTIME_EXCLUDE_PATHS.map((p) => `:(exclude)${p}`),
  ];

  let bridgeOnly = null;
  for (const sha of git("log", "--format=%H", "-40", "--", "mcp/stdio").split("\n").filter(Boolean)) {
    const touched = git("show", "--name-only", "--format=", sha).split("\n").filter(Boolean);
    if (touched.length && touched.every((f) => f.startsWith("mcp/stdio/"))) {
      bridgeOnly = sha;
      break;
    }
  }
  assert.ok(bridgeOnly, "no bridge-only commit found in the last 40 touching mcp/stdio — this "
    + "test proved nothing and must not be read as a pass");

  const selected = git("log", "--format=%H", `${bridgeOnly}^..${bridgeOnly}`, "--", ...spec);
  assert.equal(selected, "", `${bridgeOnly} touches only mcp/stdio and must not demand a rebuild`);

  // …and the same pathspec must still SELECT the SSE transport, or the exclude swallowed the
  // include and every commit would look docs-only — the opposite false report, equally silent.
  const sseCommit = git("log", "--format=%H", "-1", "--", "mcp/sse_server.py");
  assert.ok(sseCommit, "expected mcp/sse_server.py to have history");
  assert.equal(
    git("log", "--format=%H", `${sseCommit}^..${sseCommit}`, "--", ...spec),
    sseCommit,
    "a commit touching the SSE transport MUST demand a rebuild",
  );
});

test("test files under a runtime path are excluded", () => {
  // Found by this very fix flagging its own commit: adding service/tests/... demanded a rebuild.
  // Nothing in the image runs pytest, so a test-only commit cannot change what the service does.
  assert.ok(SERVICE_RUNTIME_EXCLUDE_PATHS.includes("service/tests"));
  assert.ok(SERVICE_RUNTIME_EXCLUDE_PATHS.some((p) => p.endsWith("*.test.mjs")));
  // Excludes must sit INSIDE a runtime path, or they are pointless.
  for (const ex of SERVICE_RUNTIME_EXCLUDE_PATHS) {
    assert.ok(
      SERVICE_RUNTIME_PATHS.some((rp) => ex === rp || ex.startsWith(`${rp}/`)),
      `exclude "${ex}" is not inside any runtime path — it excludes nothing`,
    );
  }
});

console.log("doctor-service-staleness.test.js: all assertions passed");

// ── a DIFFERENT sha that is ZERO commits behind is a contradiction, not a pass ────────
//
// External review of the release ladder, 2026-08-11. `doctor.js` computes both commit counts with
// `git rev-list --count <builtSha>..HEAD`. When the built sha is not in local history — deployed
// from an unmerged branch, a force-push, another clone — that git call FAILS, `sh()` returns "",
// both counts arrive as 0, and this predicate fell through to "healthy — 0 commit(s) ahead".
//
// A false green for exactly the "serving ≠ HEAD" case the check exists to catch, inside the tool
// whose whole theme is refusing to pass on absent evidence. Same class as `bridge-current`'s
// unknown-all, one layer down and found by someone else.
test("an unrelated built sha with zero commits between is UNKNOWN, not healthy", () => {
  const v = serviceBuildVerdict({
    builtSha: "deadbeefdeadbeefdeadbeef",
    headSha: "cafebabecafebabecafebabe",
    headShort: "cafebab",
    runtimeCommits: 0,
    totalCommits: 0,
  });
  assert.equal(v.ok, false, "a contradiction must not read as a clean bill of health");
  assert.equal(v.code, "unknown-build");
  assert.match(v.detail, /cannot both be true/);
  assert.match(v.fix, /Fetch the branch|rebuild/i);
});

test("the same-sha case is still plainly healthy", () => {
  // The guard must not swallow the ordinary green: identical shas legitimately have zero commits
  // between them, and that is the most common state on a healthy host.
  const sha = "1234567890abcdef1234567890abcdef12345678";
  const v = serviceBuildVerdict({
    builtSha: sha, headSha: sha, headShort: "1234567", runtimeCommits: 0, totalCommits: 0,
  });
  assert.equal(v.ok, true, "build == HEAD is the normal healthy case and must stay green");
});

test("a real behind-by-docs-only state is still green, not unknown", () => {
  const v = serviceBuildVerdict({
    builtSha: "aaaaaaaaaaaa", headSha: "bbbbbbbbbbbb", headShort: "bbbbbbb",
    runtimeCommits: 0, totalCommits: 4,
  });
  assert.equal(v.ok, true, "behind by commits that cannot reach the container is not staleness");
  assert.equal(v.code, "ok");
});

test("an env-supplied build SHA makes certification unavailable", () => {
  // THE HOLE. `config/service.json` is refused the five stamp-owned fields outright, because a
  // hand-edited file could make THIS check agree with a sha nothing was built from. Environment
  // variables reach the same fields and are NOT refused -- SERVICE_VERSION is a documented one-off and
  // a CI image may legitimately stamp its own sha. So the service reports the override and this
  // refuses to certify rather than comparing a value someone supplied.
  const verdict = serviceBuildVerdict({
    builtSha: "cafebabe1234", builtShort: "cafebab",
    headSha: "cafebabe1234", headShort: "cafebab",   // a PERFECT match, which is exactly the danger
    identityOverriddenBy: ["build_sha"],
  });
  assert.equal(verdict.ok, false, "an env-supplied SHA matching HEAD was certified as current");
  assert.equal(verdict.code, "build-identity-overridden");
});

test("only the SHA invalidates the comparison; the other overrides are caveats", () => {
  // REFUSING ON ALL FIVE WAS OVER-BROAD and destroyed evidence. Doctor compares `build_sha`; version,
  // branch and built_at do not participate in that predicate at all, and SERVICE_VERSION is the one
  // override the design explicitly allows. Withholding a still-valid sha comparison because someone
  // set a documented variable is a worse answer than reporting both facts.
  const verdict = serviceBuildVerdict({
    builtSha: "cafebabe1234", builtShort: "cafebab",
    headSha: "cafebabe1234", headShort: "cafebab",
    identityOverriddenBy: ["version", "build_branch", "built_at"],
  });
  assert.equal(verdict.ok, true, `a valid sha comparison was withheld: ${verdict.detail}`);
  assert.match(verdict.detail, /== repo HEAD/, "the comparison it can still make was not reported");
  assert.match(verdict.detail, /came from the environment/, "the caveat was dropped entirely");
});

test("an overridden SHORT never becomes the evidence, but does not withhold it either", () => {
  // `build_short` is display text. It must not be trusted as the reported identity -- so the short is
  // DERIVED from the full sha -- and it must not block a comparison the full sha can still support.
  const verdict = serviceBuildVerdict({
    builtSha: "cafebabe1234", builtShort: "LIESHORT",
    headSha: "cafebabe1234", headShort: "cafebab",
    identityOverriddenBy: ["build_short"],
  });
  assert.equal(verdict.ok, true, "an overridden display short withheld a valid sha comparison");
  assert.doesNotMatch(verdict.detail, /LIESHORT/, "the supplied short was reported as the build identity");
  assert.match(verdict.detail, /cafebab/, "the short derived from the trusted sha is missing");
});

test("an ordinary report is still certified, so the refusal is not blanket", () => {
  // ANTI-VACUITY. A verdict refusing everything would satisfy the assertions above while making the
  // check useless, and an EMPTY list is not a claim of override -- the field is absent from the payload
  // in the normal case, so the default must behave exactly as its absence does.
  const clean = serviceBuildVerdict({
    builtSha: "cafebabe1234", builtShort: "cafebab", headSha: "cafebabe1234", headShort: "cafebab",
  });
  assert.equal(clean.ok, true, `a clean build was refused: ${clean.detail}`);
  assert.equal(clean.code, "ok");
  assert.doesNotMatch(clean.detail, /environment/, "a clean report carried an override caveat");

  const empty = serviceBuildVerdict({
    builtSha: "cafebabe1234", builtShort: "cafebab", headSha: "cafebabe1234", identityOverriddenBy: [],
  });
  assert.equal(empty.ok, true, "an empty override list was treated as an override");
});
