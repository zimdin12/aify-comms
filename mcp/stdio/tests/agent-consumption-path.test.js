// `readAgentConsumption` — and the path it builds from two AGENT-SUPPLIED strings.
//
// The export ratchet listed it as named by no test. It is the bridge's per-agent token attribution:
// given a runtime, a cwd and a session handle, read that session's claude transcript and sum what it
// billed. Both the cwd and the handle come from whatever the agent registered with, and they are
// interpolated into a filesystem path — so the interesting property is not the arithmetic, it is
// CONTAINMENT.
//
//     ~/.claude/projects/<cwd-with-every-non-alnum-as-dash>/<sessionId-scrubbed>.jsonl
//
// The scrub is what stops a registration reaching outside the projects directory. `..` collapses to
// `--`, a separator collapses to `-`, and an absolute path loses its root — so no combination of the
// two inputs can climb out. That is asserted here by INSPECTING THE PATH the function asks for, via
// the injected reader, rather than by checking what it returned: a traversal that reads the wrong file
// and finds no usage data returns null, which looks exactly like success.
//
// THE INJECTED READER IS ALSO THE SEAL. `readFile` defaults to the real `readFileSync`, so a test that
// omitted it would read the operator's own `~/.claude/projects` on this machine. Every test below
// passes its own.

import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import { readAgentConsumption, readClaudeConsumption } from "../usage-collector.js";

const HOME = process.env.HOME || process.env.USERPROFILE || "";
const PROJECTS = path.join(HOME, ".claude", "projects");

// Records the path asked for and returns a transcript with one billed message.
function recordingReader(record, content = "") {
  return (requested) => {
    record.push(requested);
    return content;
  };
}

const ONE_MESSAGE = JSON.stringify({
  message: { usage: { input_tokens: 10, output_tokens: 3, cache_read_input_tokens: 7 } },
});

function askedPath(overrides = {}) {
  const asked = [];
  readAgentConsumption({
    runtime: "claude-code",
    cwd: "/home/dev/project",
    sessionHandle: "11111111-2222-3333-4444-555555555555",
    readFile: recordingReader(asked, ONE_MESSAGE),
    ...overrides,
  });
  assert.equal(asked.length, 1, `expected one read, got ${asked.length}`);
  return asked[0];
}

// ── the path it builds ──────────────────────────────────────────────────────────────────────────

test("the transcript is read from the claude projects directory", () => {
  const asked = askedPath();
  assert.equal(path.dirname(path.dirname(asked)), PROJECTS);
  assert.match(path.basename(asked), /\.jsonl$/);
});

test("the cwd becomes the project directory with every non-alnum char as a dash", () => {
  // Claude's own encoding. Reproducing it is the whole reason the path is deterministic and no
  // directory listing is needed.
  const asked = askedPath({ cwd: "/home/dev/my project" });
  assert.equal(path.basename(path.dirname(asked)), "-home-dev-my-project");
});

test("the session handle names the transcript file", () => {
  const asked = askedPath({ sessionHandle: "abc-123" });
  assert.equal(path.basename(asked), "abc-123.jsonl");
});

// ── containment ─────────────────────────────────────────────────────────────────────────────────

test("a TRAVERSING cwd cannot climb out of the projects directory", () => {
  // The registration is agent-supplied. `..` has no surviving meaning after the scrub — it becomes
  // `--`, a literal directory name — so the read stays inside `projects/`.
  for (const cwd of ["../../../../etc", "..\\..\\windows", "/../..", "....//....//"]) {
    const asked = askedPath({ cwd });
    assert.equal(path.dirname(path.dirname(asked)), PROJECTS, cwd);
    assert.ok(!path.basename(path.dirname(asked)).includes(".."), `${cwd} left a ".." segment`);
  }
});

test("a TRAVERSING session handle cannot climb out either", () => {
  // The handle keeps hyphens (claude session ids are UUIDs) and loses everything else, so a dot or a
  // separator in it cannot become a path element.
  for (const handle of ["../../secret", "..\\..\\secret", "/etc/passwd", "a/b/c"]) {
    const asked = askedPath({ sessionHandle: handle });
    assert.equal(path.dirname(path.dirname(asked)), PROJECTS, handle);
    assert.equal(path.basename(path.dirname(asked)), "-home-dev-project", handle);
    assert.ok(!path.basename(asked).includes(".."), `${handle} left a ".." segment`);
  }
});

test("neither input can introduce a PATH SEPARATOR", () => {
  // The property that makes the two scrubs enough: after them, each interpolated string is a single
  // path segment, so the shape of the path cannot be changed by its contents.
  const asked = askedPath({ cwd: "a/b\\c", sessionHandle: "d/e\\f" });
  const relative = path.relative(PROJECTS, asked);
  assert.equal(relative.split(path.sep).length, 2, `${relative} is not <dir>/<file>`);
});

test("an ABSOLUTE cwd loses its root rather than replacing the path", () => {
  // `path.join` with an absolute later segment would otherwise discard everything before it. The
  // scrub turns the leading separator into a dash first, so the segment stays relative.
  const asked = askedPath({ cwd: "/etc" });
  assert.equal(path.dirname(path.dirname(asked)), PROJECTS);
  assert.equal(path.basename(path.dirname(asked)), "-etc");
});

// ── the runtime gate ────────────────────────────────────────────────────────────────────────────

test("only claude reads a transcript — the other runtimes return null WITHOUT a read", () => {
  // Their session-to-file mapping is not implemented, and guessing one would attribute another
  // runtime's tokens to a file that is not its transcript.
  for (const runtime of ["codex", "hermes", "opencode", "pi", "", undefined]) {
    const asked = [];
    const result = readAgentConsumption({
      runtime, cwd: "/home/dev/project", sessionHandle: "abc",
      readFile: recordingReader(asked, ONE_MESSAGE),
    });
    assert.equal(result, null, String(runtime));
    assert.deepEqual(asked, [], `${runtime} touched the filesystem`);
  }
});

test("both claude spellings are accepted", () => {
  for (const runtime of ["claude-code", "claude", "CLAUDE-CODE"]) {
    const asked = [];
    const result = readAgentConsumption({
      runtime, cwd: "/home/dev/project", sessionHandle: "abc",
      readFile: recordingReader(asked, ONE_MESSAGE),
    });
    assert.ok(result, `${runtime} was not recognised`);
    assert.equal(asked.length, 1);
  }
});

test("a MISSING cwd or handle returns null without a read", () => {
  // Half an identity cannot name a transcript. Reading with an empty segment would resolve to the
  // projects directory itself, or to a bare `.jsonl`.
  for (const overrides of [{ cwd: "" }, { sessionHandle: "" }, { cwd: undefined },
    { sessionHandle: undefined }]) {
    const asked = [];
    const result = readAgentConsumption({
      runtime: "claude-code", cwd: "/home/dev/project", sessionHandle: "abc",
      readFile: recordingReader(asked, ONE_MESSAGE), ...overrides,
    });
    assert.equal(result, null, JSON.stringify(overrides));
    assert.deepEqual(asked, [], JSON.stringify(overrides));
  }
});

test("an UNREADABLE transcript is null, not an exception", () => {
  // The common case by far: the session ran on another host, or the transcript has been rotated
  // away. This is called in a loop over every agent, and one missing file must not stop the rest.
  const result = readAgentConsumption({
    runtime: "claude-code", cwd: "/home/dev/project", sessionHandle: "abc",
    readFile: () => { throw Object.assign(new Error("ENOENT"), { code: "ENOENT" }); },
  });
  assert.equal(result, null);
});

// ── what it returns when it does read ───────────────────────────────────────────────────────────

test("a readable transcript is summed into the three token totals", () => {
  const result = readAgentConsumption({
    runtime: "claude-code", cwd: "/home/dev/project", sessionHandle: "abc",
    readFile: () => ONE_MESSAGE,
  });
  assert.deepEqual(result, { input_tokens: 10, output_tokens: 3, cache_tokens: 7 });
});

test("it sums ACROSS messages, because each one is a separate billed request", () => {
  // Not the last message's usage — the session total. Reading only one would under-report every
  // multi-turn session, which is all of them.
  const transcript = [ONE_MESSAGE, ONE_MESSAGE, JSON.stringify({ message: {} })].join("\n");
  const result = readAgentConsumption({
    runtime: "claude-code", cwd: "/home/dev/project", sessionHandle: "abc",
    readFile: () => transcript,
  });
  assert.deepEqual(result, { input_tokens: 20, output_tokens: 6, cache_tokens: 14 });
});

test("cache CREATION and cache READ are both counted as cache tokens", () => {
  // They are billed differently but both are cache traffic; the dashboard shows one figure.
  const result = readClaudeConsumption(JSON.stringify({
    message: { usage: { cache_creation_input_tokens: 4, cache_read_input_tokens: 5 } },
  }));
  assert.equal(result.cache_tokens, 9);
});

test("a corrupt line is SKIPPED rather than abandoning the file", () => {
  // Transcripts are appended live, so the last line is routinely half-written. Bailing out would
  // report zero for an agent that is mid-turn — the moment its usage matters most.
  const transcript = ["{not json", ONE_MESSAGE, '{"message":{"usage":'].join("\n");
  assert.deepEqual(readClaudeConsumption(transcript),
    { input_tokens: 10, output_tokens: 3, cache_tokens: 7 });
});

test("an empty transcript is ZEROS, not null", () => {
  // The caller distinguishes "nothing to attribute" from "could not read" by the shape: zeros are a
  // real answer and are filtered out by the collector, null means the file was unreachable.
  assert.deepEqual(readClaudeConsumption(""),
    { input_tokens: 0, output_tokens: 0, cache_tokens: 0 });
  assert.deepEqual(readClaudeConsumption(null),
    { input_tokens: 0, output_tokens: 0, cache_tokens: 0 });
});

test("a non-numeric usage value counts as zero rather than poisoning the sum", () => {
  // One `undefined` reaching the arithmetic makes the whole session's total NaN, which serialises as
  // null and reads downstream as "unknown" for an agent whose usage IS known.
  //
  // EVERY FIELD IS EXERCISED WITH A MISSING ONE, which my first version did not do: it passed
  // `output_tokens: "12"`, and `Number("12")` is 12 with or without the `|| 0`, so the mutation that
  // removed that guard survived. `undefined` is the input that separates them.
  const result = readClaudeConsumption(JSON.stringify({ message: { usage: { input_tokens: 5 } } }));
  assert.equal(result.input_tokens, 5);
  assert.equal(result.output_tokens, 0, "a missing output count became NaN");
  assert.equal(result.cache_tokens, 0, "a missing cache count became NaN");
});

test("a NULL count and a numeric STRING are both handled", () => {
  const result = readClaudeConsumption(JSON.stringify({
    message: { usage: { input_tokens: null, output_tokens: "12" } },
  }));
  assert.equal(result.input_tokens, 0);
  assert.equal(result.output_tokens, 12);
});

test("no total is ever NaN, whatever the usage object holds", () => {
  // The property rather than one case: NaN is the failure that propagates, because it survives every
  // later addition and only becomes visible as a null in the dashboard.
  for (const usage of [{}, { input_tokens: "x" }, { output_tokens: {} },
    { cache_read_input_tokens: [] }, { input_tokens: undefined }]) {
    const result = readClaudeConsumption(JSON.stringify({ message: { usage } }));
    for (const [field, value] of Object.entries(result)) {
      assert.ok(Number.isFinite(value), `${field} was ${value} for ${JSON.stringify(usage)}`);
    }
  }
});
