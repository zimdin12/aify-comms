#!/usr/bin/env node
// How a runtime is identified — from the environment, and from a resume command line.
//
// `detectRuntime` classifies the session a bridge is running inside, and that answer picks the
// controller, the capability set and the delivery path. `extractRuntimeSessionHandleFromCommand`
// recovers a session handle from a live process's command line, which is how a restarted bridge
// re-binds to the conversation an agent was already in. Neither was named by a test.
//
// THE ENVIRONMENT IS SEALED HERE, and that is not a formality. This suite runs inside Claude Code,
// so `CLAUDECODE` and `CLAUDE_PROJECT_DIR` are set in the real process — an unsealed test would read
// the operator's live session and pass for a reason that has nothing to do with the code. That is
// the exact failure recorded in this project's rule about tests sealing every ambient input, where a
// test read the operator's live hermes gateway marker. Every variable the function consults is
// deleted before each case and the ORIGINAL environment is restored at the end.
//
// Only CONFIG variables are set below (runtime names, session ids, home directories). No ACTION flag
// is ever set in a test run here — setting one made a test become the environment bridge and reap
// the live fleet.

import assert from "node:assert/strict";

import { detectRuntime, extractRuntimeSessionHandleFromCommand } from "../runtimes.js";

const CONSULTED = [
  "AIFY_AGENT_RUNTIME", "AIFY_RUNTIME",
  "CODEX_HOME", "CODEX_SANDBOX",
  "HERMES_SESSION_ID", "HERMES_HOME",
  "OPENCODE_CLIENT", "OPENCODE_CONFIG_DIR",
  "PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID",
  "CLAUDE_PROJECT_DIR", "CLAUDECODE",
];

const ORIGINAL = new Map(CONSULTED.map((name) => [name, process.env[name]]));

function withEnv(vars, fn) {
  for (const name of CONSULTED) delete process.env[name];
  for (const [name, value] of Object.entries(vars)) process.env[name] = value;
  try {
    return fn();
  } finally {
    for (const name of CONSULTED) delete process.env[name];
  }
}

// The seal is asserted rather than assumed: if a later edit adds a variable to the function without
// adding it here, this catches it as a bare environment that is not actually bare.
withEnv({}, () => {
  for (const name of CONSULTED) {
    assert.equal(process.env[name], undefined, `${name} leaked into a sealed case`);
  }
  assert.equal(detectRuntime(), "generic", "a genuinely empty environment is `generic`, not a guess");
});

try {
  // ── the explicit argument outranks the whole environment ─────────────────────────────────
  withEnv({ CODEX_HOME: "/home/x/.codex", CLAUDECODE: "1" }, () => {
    assert.equal(detectRuntime("hermes"), "hermes", "an explicit runtime wins over every marker");
    assert.equal(detectRuntime("claude"), "claude-code", "and is normalised on the way through");
  });

  // A falsy explicit value is NOT a choice — it falls through to the environment rather than
  // producing an empty runtime.
  withEnv({ CODEX_HOME: "/home/x/.codex" }, () => {
    for (const empty of ["", null, undefined, 0]) {
      assert.equal(detectRuntime(empty), "codex", `${JSON.stringify(empty)} must fall through`);
    }
  });

  // ── the explicit env vars, in their own order ────────────────────────────────────────────
  withEnv({ AIFY_AGENT_RUNTIME: "pi", AIFY_RUNTIME: "codex", CLAUDECODE: "1" }, () => {
    assert.equal(detectRuntime(), "pi", "AIFY_AGENT_RUNTIME is the more specific of the two");
  });
  withEnv({ AIFY_RUNTIME: "codex", HERMES_HOME: "/h" }, () => {
    assert.equal(detectRuntime(), "codex");
  });
  withEnv({ AIFY_AGENT_RUNTIME: "  CLAUDE  " }, () => {
    assert.equal(detectRuntime(), "claude-code", "the env value is normalised like any other");
  });

  // ── the marker ladder, and the order it resolves ties in ─────────────────────────────────
  const MARKERS = [
    ["codex", ["CODEX_HOME", "CODEX_SANDBOX"]],
    ["hermes", ["HERMES_SESSION_ID", "HERMES_HOME"]],
    ["opencode", ["OPENCODE_CLIENT", "OPENCODE_CONFIG_DIR"]],
    ["pi", ["PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID"]],
    ["claude-code", ["CLAUDE_PROJECT_DIR", "CLAUDECODE"]],
  ];

  for (const [runtime, names] of MARKERS) {
    for (const name of names) {
      withEnv({ [name]: "1" }, () => {
        assert.equal(detectRuntime(), runtime, `${name} alone must identify ${runtime}`);
      });
    }
  }

  // THE ORDER IS THE CONTRACT, not an accident of writing. A managed codex agent launched FROM a
  // Claude Code session carries both sets of markers, and it must read as codex — the runtime it
  // IS, not the one that started it. Every earlier entry beats every later one.
  for (let i = 0; i < MARKERS.length; i += 1) {
    for (let j = i + 1; j < MARKERS.length; j += 1) {
      const [winner, [winnerVar]] = MARKERS[i];
      const [loser, [loserVar]] = MARKERS[j];
      withEnv({ [winnerVar]: "1", [loserVar]: "1" }, () => {
        assert.equal(detectRuntime(), winner, `${winnerVar} must outrank ${loserVar} (${winner} over ${loser})`);
      });
    }
  }

  // An unrecognised explicit runtime is passed through normalised rather than forced to `generic` —
  // a runtime this bridge does not know yet is still the operator's answer.
  withEnv({ AIFY_RUNTIME: "some-future-runtime" }, () => {
    assert.equal(detectRuntime(), "some-future-runtime");
  });

  // An EMPTY marker variable is not a marker. Exported-but-blank is common in shell wrappers, and
  // treating it as present would misclassify every session that sources such a script.
  withEnv({ CODEX_HOME: "", CLAUDECODE: "1" }, () => {
    assert.equal(detectRuntime(), "claude-code", "a blank CODEX_HOME must not claim the session");
  });
} finally {
  for (const [name, value] of ORIGINAL) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
}

// The restore is verified by reading the environment back, not by the absence of an error.
for (const [name, value] of ORIGINAL) {
  assert.equal(process.env[name], value, `${name} was not restored to its original value`);
}

// ── extractRuntimeSessionHandleFromCommand ───────────────────────────────────────────────────
{
  // Codex resumes positionally; the other three use a flag.
  assert.equal(extractRuntimeSessionHandleFromCommand("codex", "codex resume 019abc-def"), "019abc-def");
  assert.equal(
    extractRuntimeSessionHandleFromCommand("codex", "codex resume --include-non-interactive 019abc"),
    "019abc",
    "the optional flag between `resume` and the id must not swallow the id",
  );

  for (const runtime of ["pi", "hermes", "claude-code"]) {
    for (const flag of ["--resume", "--session-id", "-r"]) {
      for (const sep of [" ", "="]) {
        assert.equal(
          extractRuntimeSessionHandleFromCommand(runtime, `run ${flag}${sep}sess-42 --other`),
          "sess-42",
          `${runtime} ${flag}${sep}`,
        );
      }
    }
  }

  // CODEX ALSO ANSWERS THE FLAG FORM, which is the spelling its WRAPPER takes. install.sh's codex
  // branch parses `--resume`/`--session-id` (space- or `=`-separated, never `-r`) and rewrites them
  // into the positional subcommand above; `adapters/codex.js` hands the operator exactly that form
  // as the takeover command. Only the subcommand spelling was recognised here, so the command the
  // adapter itself produces read as having no session id at all.
  for (const flag of ["--resume", "--session-id"]) {
    for (const sep of [" ", "="]) {
      assert.equal(
        extractRuntimeSessionHandleFromCommand("codex", `codex-aify ${flag}${sep}019abc --model gpt`),
        "019abc",
        `codex ${flag}${sep}`,
      );
    }
  }
  assert.equal(
    extractRuntimeSessionHandleFromCommand("codex", "codex-aify -r 019abc"), "",
    "codex-aify does not parse -r — reading it as a handle would claim one that codex never got",
  );

  // OPENCODE WAS LISTED BELOW AS HAVING "no resume convention". IT HAS ONE.
  // `adapters/opencode.js` declares `sessionIdSource === "resume"` and
  // `resumeCommand(id) => "opencode-aify --resume <id>"`. The grouping froze an omission as if it
  // were a decision: the runtime whose own adapter names the flag was pinned as recognising none.
  // Its flag set is just `--resume`, which is the only one that adapter declares.
  assert.equal(
    extractRuntimeSessionHandleFromCommand("opencode", "opencode-aify --resume sess-42 --model z"),
    "sess-42",
    "opencode's adapter declares --resume; the extractor must read the command it produces",
  );

  // A runtime with no resume convention yields nothing rather than a wrong guess.
  for (const runtime of ["generic", "", null, "some-future-runtime"]) {
    assert.equal(
      extractRuntimeSessionHandleFromCommand(runtime, "run --resume sess-42"),
      "",
      `${JSON.stringify(runtime)} has no resume regex`,
    );
  }

  // Aliases reach the same regex — `claude` and `claude-code` are one runtime.
  assert.equal(extractRuntimeSessionHandleFromCommand("claude", "claude --resume sess-42"), "sess-42");

  // QUOTING. A handle or path with spaces only survives if it is quoted, and the quotes must be
  // stripped — a caller that received `"C:/Program Files/x"` WITH the quotes would resume nothing.
  assert.equal(
    extractRuntimeSessionHandleFromCommand("hermes", 'hermes --resume "sess 42" --tui'),
    "sess 42",
    "a double-quoted handle keeps its spaces and loses its quotes",
  );
  assert.equal(
    extractRuntimeSessionHandleFromCommand("hermes", "hermes --resume 'sess 42' --tui"),
    "sess 42",
    "single quotes too",
  );
  assert.equal(
    extractRuntimeSessionHandleFromCommand("codex", 'codex resume "C:/Program Files/repo"'),
    "C:/Program Files/repo",
  );

  // The FIRST match wins — a later `--resume` in an argument to something else cannot displace the
  // real one, because the regex is unanchored and matched without the global flag.
  assert.equal(
    extractRuntimeSessionHandleFromCommand("pi", "pi --resume first --note '--resume second'"),
    "first",
  );

  // THE FLAG IS A TOKEN, NOT A SUBSTRING. `(?:^|\s)` requires the flag to start the string or follow
  // whitespace, so the `--resume` tail inside `--no-resume` is preceded by `-` and does not match —
  // a handle is never recovered from a flag that means the opposite. I expected this to be loose and
  // it is not; the assertion records the tighter behaviour so a later "simplification" of the
  // anchor has something to fail against.
  for (const command of ["hermes --no-resume=sess-42", "hermes --auto-resume sess-42", "hermes x--resume sess-42"]) {
    assert.equal(extractRuntimeSessionHandleFromCommand("hermes", command), "", command);
  }
  assert.equal(
    extractRuntimeSessionHandleFromCommand("hermes", "hermes --no-resume=a --resume=real"),
    "real",
    "and a real flag later in the same line is still found",
  );

  // Degenerate inputs are "" rather than a throw — this reads a live process's command line, which
  // can be empty or missing entirely.
  for (const command of ["", null, undefined, "codex resume", "hermes --resume"]) {
    assert.equal(
      extractRuntimeSessionHandleFromCommand("codex", command),
      "",
      `${JSON.stringify(command)} yields no handle`,
    );
  }
}

console.log("runtime-identification.test.js: all assertions passed");
