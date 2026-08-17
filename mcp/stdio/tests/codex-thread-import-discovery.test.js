// Where a managed codex resume LOOKS for the thread it is resuming.
//
// Eighteenth cluster off the V8-coverage census: `runtimes-codex.js`'s `codexSourceHomes` had a zero call
// count. It is the discovery branch of `importCodexThreadRollout` — the one taken when the caller does NOT name
// a source home — and `codex-thread-import.test.js` always names one, which is why the branch that runs in
// production had never run in a test.
//
// WHAT IT DECIDES. A dashboard-managed codex agent gets its OWN CODEX_HOME, separate from the operator's. So a
// thread the operator started natively lives in `~/.codex` (or `$CODEX_HOME`) while the managed home has
// nothing, and managed resume must copy that rollout across. If discovery looks in the wrong homes it does not
// error: the resume reports no rollout, codex starts a FRESH context, and the operator's conversation is
// silently gone. That is the same shape as the quota readers — a wrong path reads exactly like no data.
//
// SEALED. `CODEX_HOME` and `HOME` (which `userHomeDir()` prefers over `os.homedir()`) both point inside a temp
// root, so no test here reads or copies from the operator's real codex home. The HOME seal is asserted by
// PLANTING a rollout at `<HOME>/.codex` and requiring discovery to find it — a seal that had not taken would
// find nothing there.
//
// SIX MUTATIONS SURVIVE THIS FILE, and none is a gap. Four are one defence guarded twice; two are unreachable:
//
//   * The target exclusion exists TWICE — in `codexSourceHomes`' filter and again as the import loop's
//     `sameResolvedPath(source, target)`. Remove either and the other still holds, so each half alone survives;
//     the harness therefore mutates BOTH at once, and that IS caught. (Same shape as the terminal_ansi guards:
//     one mutation at a time reads as a vacuous test when the truth is redundant defence. Comparing raw strings
//     instead of resolved paths is also mutated in both halves together, and caught.)
//   * `if (!id || !target)` is likewise doubled for the id: a blank thread id also produces no matches inside
//     `findFilesContaining`, whose own blank-needle guard returns []. Removing the TARGET half is caught, and
//     so is removing both.
//   * Dropping the candidate de-duplication. The loop RETURNS at the first home that holds the thread, so a
//     duplicate candidate can only cost a second directory walk, never change the answer.
//   * Keeping blank candidates in the list. The loop's own `if (!source) continue` skips them.
//   * Removing the `..`/absolute guard in `copyPreservingCodexRelativePath`. Its input is always a file the
//     walker found UNDER the source home, so `path.relative` cannot escape — the guard defends an input this
//     API cannot produce.

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";

import { tmpDir } from "./_tmpdir.js";
import { importCodexThreadRollout } from "../runtimes.js";

const THREAD = "019d9e26-071a-7521-8f7c-108789102c1b";
const SEALED_KEYS = ["CODEX_HOME", "HOME", "USERPROFILE"];

// One home layout per case, with the env pointed at it for the duration.
function withHomes({ codexHome, home }, run) {
  const saved = new Map(SEALED_KEYS.map((key) => [key, process.env[key]]));
  if (codexHome === null) delete process.env.CODEX_HOME;
  else process.env.CODEX_HOME = codexHome;
  process.env.HOME = home;
  process.env.USERPROFILE = home;
  assert.equal(process.env.HOME, home, "the HOME seal did not take");
  assert.equal(process.env.CODEX_HOME, codexHome === null ? undefined : codexHome,
    "the CODEX_HOME seal did not take");
  try {
    return run();
  } finally {
    for (const [key, value] of saved) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

// A codex home holding one thread's rollout and shell snapshot, laid out the way codex writes them.
function plantThread(home, { threadId = THREAD, marker = "{}" } = {}) {
  const rollout = path.join(home, "sessions", "2026", "04", "18",
    `rollout-2026-04-18T01-13-05-${threadId}.jsonl`);
  const snapshot = path.join(home, "shell_snapshots", `${threadId}.123456.sh`);
  fs.mkdirSync(path.dirname(rollout), { recursive: true });
  fs.mkdirSync(path.dirname(snapshot), { recursive: true });
  fs.writeFileSync(rollout, `${marker}\n`, "utf8");
  fs.writeFileSync(snapshot, "# snapshot\n", "utf8");
  return { rollout, snapshot };
}

const readCopied = (targetHome, relative) =>
  fs.readFileSync(path.join(targetHome, relative), "utf8");

test("CODEX_HOME is discovered when no source home is named", async () => {
  const base = tmpDir("aify-codex-discovery-");
  const codexHome = path.join(base, "env-home");
  const targetHome = path.join(base, "managed-home");
  plantThread(codexHome, { marker: '{"from":"env-home"}' });

  const result = withHomes({ codexHome, home: path.join(base, "user") }, () =>
    importCodexThreadRollout({ threadId: THREAD, targetHome }));

  assert.equal(result.imported, true, "the thread in CODEX_HOME was not found");
  assert.equal(result.sourceHome, codexHome);
  assert.equal(result.rollouts.length, 1);
  assert.match(readCopied(targetHome, result.rollouts[0]), /from":"env-home/,
    "the copied rollout is not the one that was planted");
  assert.equal(result.shellSnapshots.length, 1, "the shell snapshot did not come along");
});

test("the user's ~/.codex is discovered when CODEX_HOME is unset", async () => {
  // This is also the HOME seal's proof: the rollout exists ONLY under the temp home, so finding it means
  // `userHomeDir()` read the sealed value rather than the operator's real profile.
  const base = tmpDir("aify-codex-discovery-");
  const home = path.join(base, "user");
  const targetHome = path.join(base, "managed-home");
  plantThread(path.join(home, ".codex"), { marker: '{"from":"default-home"}' });

  const result = withHomes({ codexHome: null, home }, () =>
    importCodexThreadRollout({ threadId: THREAD, targetHome }));

  assert.equal(result.imported, true, "the default ~/.codex home was not searched");
  assert.equal(result.sourceHome, path.join(home, ".codex"));
  assert.match(readCopied(targetHome, result.rollouts[0]), /from":"default-home/);
});

test("CODEX_HOME is searched BEFORE the default home", async () => {
  // Order matters when both hold the thread: the env one is the operator's active configuration, and importing
  // the stale copy would resume a conversation that is behind the one they were just having.
  const base = tmpDir("aify-codex-discovery-");
  const codexHome = path.join(base, "env-home");
  const home = path.join(base, "user");
  const targetHome = path.join(base, "managed-home");
  plantThread(codexHome, { marker: '{"from":"env-home"}' });
  plantThread(path.join(home, ".codex"), { marker: '{"from":"default-home"}' });

  const result = withHomes({ codexHome, home }, () =>
    importCodexThreadRollout({ threadId: THREAD, targetHome }));

  assert.equal(result.sourceHome, codexHome, "the default home won over the configured one");
  assert.match(readCopied(targetHome, result.rollouts[0]), /from":"env-home/);
});

test("the default home is the FALLBACK when CODEX_HOME holds nothing", async () => {
  // Discovery does not stop at the first candidate that exists — it stops at the first that has the thread.
  const base = tmpDir("aify-codex-discovery-");
  const codexHome = path.join(base, "env-home");
  const home = path.join(base, "user");
  const targetHome = path.join(base, "managed-home");
  plantThread(codexHome, { threadId: "some-other-thread" });
  plantThread(path.join(home, ".codex"), { marker: '{"from":"default-home"}' });

  const result = withHomes({ codexHome, home }, () =>
    importCodexThreadRollout({ threadId: THREAD, targetHome }));

  assert.equal(result.imported, true, "discovery stopped at a home that did not hold the thread");
  assert.equal(result.sourceHome, path.join(home, ".codex"));
});

test("the TARGET home is never a source, even when it holds the thread", async () => {
  // Importing from itself would copy a file onto itself and report `imported: true` — a fabricated success that
  // hides the real answer, which is that the managed home has no rollout to resume from.
  const base = tmpDir("aify-codex-discovery-");
  const targetHome = path.join(base, "managed-home");
  plantThread(targetHome, { marker: '{"from":"target"}' });

  const result = withHomes({ codexHome: targetHome, home: path.join(base, "user") }, () =>
    importCodexThreadRollout({ threadId: THREAD, targetHome }));

  assert.equal(result.imported, false, "the managed home imported from itself");
  assert.deepEqual(result.rollouts, []);
});

test("the target is excluded even when it is reached by a different path spelling", async () => {
  // `sameResolvedPath` compares RESOLVED paths, so the same directory named with a trailing separator is still
  // the target. Comparing the raw strings would let it in as a source.
  //
  // The spelling has to survive `path.join`, which was the flaw in my first attempt: `path.join(base,
  // "managed-home", ".")` collapses to `managed-home`, so the "different" string was identical and the
  // raw-comparison mutation went uncaught. A trailing separator is not normalised away by concatenation.
  const base = tmpDir("aify-codex-discovery-");
  const targetHome = path.join(base, "managed-home");
  plantThread(targetHome);
  const spelledDifferently = `${targetHome}${path.sep}`;
  assert.notEqual(spelledDifferently, targetHome, "the fixture's two spellings are the same string");

  const result = withHomes({ codexHome: spelledDifferently, home: path.join(base, "user") }, () =>
    importCodexThreadRollout({ threadId: THREAD, targetHome }));

  assert.equal(result.imported, false, "a differently-spelled path to the target was accepted as a source");
});

test("one home reached twice is searched once", async () => {
  // CODEX_HOME set to exactly `~/.codex` is an ordinary configuration. Without the dedupe the same rollout is
  // copied twice and reported twice, which reads as two rollouts for one thread.
  const base = tmpDir("aify-codex-discovery-");
  const home = path.join(base, "user");
  const codexHome = path.join(home, ".codex");
  const targetHome = path.join(base, "managed-home");
  plantThread(codexHome);

  const result = withHomes({ codexHome, home }, () =>
    importCodexThreadRollout({ threadId: THREAD, targetHome }));

  assert.equal(result.imported, true);
  assert.equal(result.rollouts.length, 1, `the same home was searched twice: ${result.rollouts.join(", ")}`);
  assert.equal(new Set(result.rollouts).size, result.rollouts.length);
});

test("an explicit source home does NOT fall back to discovery", async () => {
  // The caller that names a source is asserting where the thread is. Falling back would make a wrong answer
  // look right by finding the thread somewhere the caller did not ask about.
  const base = tmpDir("aify-codex-discovery-");
  const codexHome = path.join(base, "env-home");
  const emptySource = path.join(base, "explicit-empty");
  const targetHome = path.join(base, "managed-home");
  plantThread(codexHome);
  fs.mkdirSync(emptySource, { recursive: true });

  const result = withHomes({ codexHome, home: path.join(base, "user") }, () =>
    importCodexThreadRollout({ threadId: THREAD, targetHome, sourceHome: emptySource }));

  assert.equal(result.imported, false, "discovery ran despite an explicit source home");
});

test("no thread anywhere reports not-imported rather than throwing", async () => {
  const base = tmpDir("aify-codex-discovery-");
  const result = withHomes({ codexHome: path.join(base, "env-home"), home: path.join(base, "user") }, () =>
    importCodexThreadRollout({ threadId: THREAD, targetHome: path.join(base, "managed-home") }));

  assert.equal(result.imported, false);
  assert.equal(result.sourceHome, "");
  assert.deepEqual(result.rollouts, []);
  assert.deepEqual(result.shellSnapshots, []);
});

test("a blank thread id or target is refused before any home is read", async () => {
  // The thread MUST be findable for this to test the guard rather than the absence of files. My first version
  // planted nothing, so every case returned `imported: false` for the wrong reason and both halves of
  // `if (!id || !target)` could be deleted with the test still green.
  const base = tmpDir("aify-codex-discovery-");
  const codexHome = path.join(base, "env-home");
  plantThread(codexHome);

  withHomes({ codexHome, home: path.join(base, "user") }, () => {
    for (const args of [
      { threadId: "", targetHome: path.join(base, "t") },
      { threadId: "   ", targetHome: path.join(base, "t") },
      { threadId: THREAD, targetHome: "" },
      { threadId: THREAD, targetHome: "   " },
      {},
    ]) {
      // A blank target with a findable thread reaches `fs.mkdirSync("")` if the guard is gone, so "returns an
      // object" is itself part of the assertion.
      const result = importCodexThreadRollout(args);
      assert.equal(result.imported, false, `${JSON.stringify(args)} was not refused`);
      assert.equal(result.sourceHome, "");
      assert.deepEqual(result.rollouts, []);
    }
    // And the same thread IS importable with both arguments present — otherwise the loop above proves nothing.
    const ok = importCodexThreadRollout({ threadId: THREAD, targetHome: path.join(base, "managed-home") });
    assert.equal(ok.imported, true, "the fixture thread was not importable at all");
  });
});

test("the copied rollout keeps its path RELATIVE to the source home", async () => {
  // Codex finds a thread by walking `sessions/<y>/<m>/<d>/`. Flattening the copy into the target root would put
  // the file where codex does not look, so the resume would still start fresh — with the import reporting
  // success.
  const base = tmpDir("aify-codex-discovery-");
  const codexHome = path.join(base, "env-home");
  const targetHome = path.join(base, "managed-home");
  const planted = plantThread(codexHome);

  const result = withHomes({ codexHome, home: path.join(base, "user") }, () =>
    importCodexThreadRollout({ threadId: THREAD, targetHome }));

  const expectedRelative = path.relative(codexHome, planted.rollout);
  assert.deepEqual(result.rollouts, [expectedRelative]);
  assert.ok(fs.existsSync(path.join(targetHome, expectedRelative)),
    "the rollout did not land at its relative path under the managed home");
  assert.ok(fs.existsSync(path.join(targetHome, path.relative(codexHome, planted.snapshot))),
    "the shell snapshot did not land at its relative path");
});
