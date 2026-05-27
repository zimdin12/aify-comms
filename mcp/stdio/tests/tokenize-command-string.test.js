#!/usr/bin/env node
// Verifies tokenizeCommandString (fix I7) handles quote-grouped paths,
// backslash escapes, and the historical whitespace-separated form so
// AIFY_HERMES_ACP_COMMAND / AIFY_CODEX_COMMAND can use either shape.

import assert from "node:assert/strict";
import { tokenizeCommandString } from "../runtimes.js";

// Backwards-compat: plain whitespace tokens still work.
{
  const t = tokenizeCommandString("hermes acp --accept-hooks");
  assert.equal(t.command, "hermes");
  assert.deepEqual(t.args, ["acp", "--accept-hooks"]);
}

// Double-quoted path with spaces survives as one token.
{
  const t = tokenizeCommandString('"C:\\Program Files\\hermes\\hermes.exe" acp --accept-hooks');
  assert.equal(t.command, "C:\\Program Files\\hermes\\hermes.exe");
  assert.deepEqual(t.args, ["acp", "--accept-hooks"]);
}

// Single-quoted path also survives.
{
  const t = tokenizeCommandString("'/opt/Path With Space/hermes' acp");
  assert.equal(t.command, "/opt/Path With Space/hermes");
  assert.deepEqual(t.args, ["acp"]);
}

// Backslash-escape preserves a space character (POSIX habit).
{
  const t = tokenizeCommandString("/opt/Path\\ With\\ Space/hermes acp");
  assert.equal(t.command, "/opt/Path With Space/hermes");
  assert.deepEqual(t.args, ["acp"]);
}

// Windows path separators are LITERAL (do not act as escapes) so
// unquoted Windows-style paths survive intact.
{
  const t = tokenizeCommandString("node C:\\Docker\\aify-comms\\fake.mjs --flag");
  assert.equal(t.command, "node");
  assert.deepEqual(t.args, ["C:\\Docker\\aify-comms\\fake.mjs", "--flag"]);
}

// Empty input → empty result, no crash.
{
  const t = tokenizeCommandString("");
  assert.equal(t.command, "");
  assert.deepEqual(t.args, []);
}

// Whitespace-only input → empty result, no crash.
{
  const t = tokenizeCommandString("   \t  ");
  assert.equal(t.command, "");
  assert.deepEqual(t.args, []);
}

// Mixed quotes inside args (uncommon but legal).
{
  const t = tokenizeCommandString('node "fake hermes.mjs" --flag="value with spaces"');
  assert.equal(t.command, "node");
  assert.deepEqual(t.args, ["fake hermes.mjs", "--flag=value with spaces"]);
}

// Empty quoted string is a real token (matters for some args).
{
  const t = tokenizeCommandString('cmd "" --next');
  assert.equal(t.command, "cmd");
  assert.deepEqual(t.args, ["", "--next"]);
}

console.log("tokenize-command-string.test.js: all assertions passed");
