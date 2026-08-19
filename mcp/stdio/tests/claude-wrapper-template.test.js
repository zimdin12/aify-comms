#!/usr/bin/env node
// The wrapper templates in `wrappers/` and the renderer in install.sh must agree.
//
// v0.6 Phase 2 moved the claude-aify body out of a 335-line unquoted heredoc into
// `wrappers/claude-aify.sh.in`. The heredoc's failure mode was escaping — every runtime `$` written
// `\$`, invisible while reading. The template's failure mode is different and needs its own guard:
// a placeholder that one side knows and the other does not.
//
// Add `@@FOO@@` to a template and forget the renderer, and the operator's installed wrapper contains
// the literal text `@@FOO@@`. Nothing fails: install.sh exits 0, the wrapper is written, `bash -n`
// parses it, and it breaks at launch on whatever that placeholder was standing in for. Delete a
// placeholder from a template and leave it in the renderer and you get dead substitution code that
// reads as coverage.
//
// So both sets are derived INDEPENDENTLY — one by scanning the templates, one by scanning install.sh's
// renderer — and compared. Neither side supplies the other's expected value, which is the whole point:
// an assertion that imports what it is checking cannot fail.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { REPO, renderWrapper } from "./wrapper-harness.mjs";

const WRAPPERS_DIR = path.join(REPO, "wrappers");
const INSTALL_SH = path.join(REPO, "install.sh");

const templateNames = () =>
  fs.existsSync(WRAPPERS_DIR) ? fs.readdirSync(WRAPPERS_DIR).filter((f) => f.endsWith(".sh.in")) : [];

/** Placeholders the templates actually use. */
function placeholdersInTemplates() {
  const found = new Set();
  for (const name of templateNames()) {
    const text = fs.readFileSync(path.join(WRAPPERS_DIR, name), "utf8");
    for (const line of text.split(/\r?\n/)) {
      if (line.startsWith("#|")) continue; // template-only documentation, stripped at render
      for (const m of line.matchAll(/@@([A-Z0-9_]+)@@/g)) found.add(m[1]);
    }
  }
  return found;
}

/**
 * Placeholders install.sh can actually substitute: the fixed set written into the renderer, plus the
 * `KEY=VALUE` extras its call sites pass.
 *
 * The extras exist for hermes, whose generator computes three values that cannot be derived from the
 * checkout — a Windows-converted plugin path, a node-openable bridge dir, and a prebuilt TUI bundle
 * baked only when it exists. The renderer applies them through `@@${_pair%%=*}@@`, so no literal name
 * appears in its body and scanning the body alone would report every hermes placeholder as unknown.
 *
 * Both halves are still derived from install.sh and NEITHER from the templates, which is what keeps
 * the comparison below a real one rather than an assertion importing its own expected value.
 */
function placeholdersInRenderer() {
  const text = fs.readFileSync(INSTALL_SH, "utf8");
  const start = text.indexOf("render_wrapper_template() {");
  assert.ok(start >= 0, "install.sh must define render_wrapper_template");
  const end = text.indexOf("\n}\n", start);
  assert.ok(end > start, "render_wrapper_template must be a closed function");
  const body = text.slice(start, end);
  const fixed = [...body.matchAll(/@@([A-Z0-9_]+)@@/g)].map((m) => m[1]);

  // Extras, read off every `render_wrapper_template … "NAME=$value"` argument. A call may be
  // continued across lines with a trailing backslash, so the invocation is reassembled by joining
  // continued lines rather than matched with one expression — a regex that has to model line
  // continuation is a regex that quietly matches nothing when the continuation moves.
  const extras = [];
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i += 1) {
    if (!lines[i].includes("render_wrapper_template ")) continue;
    let invocation = lines[i];
    let j = i;
    while (invocation.trimEnd().endsWith("\\") && j + 1 < lines.length) {
      j += 1;
      invocation = `${invocation.trimEnd().slice(0, -1)} ${lines[j].trim()}`;
    }
    for (const arg of invocation.matchAll(/"([A-Z0-9_]+)=\$/g)) extras.push(arg[1]);
  }
  return new Set([...fixed, ...extras]);
}

test("wrappers/ holds at least one template — the scan is not silently empty", () => {
  // Without this, every assertion below passes vacuously the day the directory is renamed.
  assert.ok(templateNames().length > 0, "expected at least one wrappers/*.sh.in");
  assert.ok(templateNames().includes("claude-aify.sh.in"));
});

test("every placeholder a template uses, the renderer substitutes", () => {
  const used = placeholdersInTemplates();
  const known = placeholdersInRenderer();
  const unknown = [...used].filter((p) => !known.has(p)).sort();
  assert.deepEqual(
    unknown,
    [],
    `templates use placeholders install.sh cannot substitute: ${unknown.join(", ")}. `
      + "The installed wrapper would contain the literal token and fail at launch, with install.sh "
      + "exiting 0 and bash -n passing.",
  );
});

test("every placeholder the renderer substitutes, some template uses", () => {
  const used = placeholdersInTemplates();
  const known = placeholdersInRenderer();
  const dead = [...known].filter((p) => !used.has(p)).sort();
  assert.deepEqual(dead, [], `renderer substitutes placeholders no template uses: ${dead.join(", ")}`);
});

test("the rendered wrapper carries no unsubstituted placeholder", () => {
  // The end-to-end version of the check above: whatever the two scans think, the artifact an operator
  // gets must not contain a token.
  const dir = renderWrapper("claude");
  const text = fs.readFileSync(path.join(dir, "claude-aify"), "utf8");
  const leftover = [...text.matchAll(/@@[A-Z0-9_]+@@/g)].map((m) => m[0]);
  assert.deepEqual(leftover, [], `unsubstituted placeholders in the installed wrapper: ${leftover}`);
});

test("the rendered wrapper carries no template-only documentation", () => {
  const dir = renderWrapper("claude");
  const lines = fs.readFileSync(path.join(dir, "claude-aify"), "utf8").split(/\r?\n/);
  const leaked = lines.filter((l) => l.startsWith("#|"));
  assert.deepEqual(leaked, [], "`#|` lines are for the template, not the installed wrapper");
});

test("the template keeps its shebang first, so the rendered wrapper is executable", () => {
  const text = fs.readFileSync(path.join(WRAPPERS_DIR, "claude-aify.sh.in"), "utf8");
  assert.ok(text.startsWith("#!/bin/bash\n"), "a template's first line must still be the shebang");
  const dir = renderWrapper("claude");
  const rendered = fs.readFileSync(path.join(dir, "claude-aify"), "utf8");
  assert.ok(rendered.startsWith("#!/bin/bash\n"), "and it must survive rendering as line 1");
});

test("the renderer refuses a missing template instead of writing an empty wrapper", () => {
  // `grep -v` on a nonexistent file returns empty, and an empty wrapper installs perfectly happily.
  const text = fs.readFileSync(INSTALL_SH, "utf8");
  const start = text.indexOf("render_wrapper_template() {");
  const body = text.slice(start, text.indexOf("\n}\n", start));
  assert.match(body, /\[ -f "\$template" \]/, "the renderer must check the template exists");
  assert.match(body, /exit 1/, "and must fail rather than continue");
});
