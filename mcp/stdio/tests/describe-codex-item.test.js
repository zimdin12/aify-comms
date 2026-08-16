#!/usr/bin/env node
// The label every codex tool call gets in the console — `describeCodexItem`, which no test named.
//
// Five call sites across four modules (`codex-session.js`, `controllers/codex-legacy-helpers.js`,
// `runtimes-helpers.js`, `runtimes.js`) turn a codex `item` into the one-line label an operator reads
// while a turn runs, and it is also what `activeItems` keys its in-flight entries on.
//
// THE FAILURE IS TOTAL AND SILENT. Every field it reads has three or four accepted spellings because
// the codex API has used several — `name`/`toolName`/`call.name`/`function.name` for one value alone.
// If a shape change lands on none of them, the function does not throw and does not warn: every label
// degrades to the bare type, or to the literal "item", and the console silently stops saying what any
// tool call is. That is a whole class of observability going away with a green suite.
//
// So the spellings are the test. Each alternative is exercised separately, and the precedence between
// them is pinned, because a fallback chain that silently reorders is indistinguishable from one that
// works until the day the first field disappears.

import assert from "node:assert/strict";

import { describeCodexItem } from "../runtimes-codex.js";

// ── the type, and its fallback ───────────────────────────────────────────────────────────────
{
  assert.equal(describeCodexItem({ type: "mcpToolCall" }), "mcpToolCall");
  assert.equal(describeCodexItem({ kind: "reasoning" }), "reasoning", "`kind` is accepted as `type`");
  assert.equal(describeCodexItem({ type: "a", kind: "b" }), "a", "`type` wins when both are present");

  // "item" is the floor: a label is always produced, because the caller renders it unconditionally.
  assert.equal(describeCodexItem({}), "item");
  assert.equal(describeCodexItem(), "item", "no argument at all still labels something");
  assert.equal(describeCodexItem(null), "item");
  assert.equal(describeCodexItem({ type: "" }), "item");
  assert.equal(describeCodexItem({ type: "   " }), "item", "whitespace is not a type");
  assert.equal(describeCodexItem({ type: "  padded  " }), "padded", "and a real one is trimmed");
}

// ── the name, across every spelling codex has used ───────────────────────────────────────────
{
  for (const field of ["name", "toolName"]) {
    assert.equal(describeCodexItem({ type: "call", [field]: "read_file" }), "call read_file", field);
  }
  assert.equal(describeCodexItem({ type: "call", call: { name: "read_file" } }), "call read_file");
  assert.equal(describeCodexItem({ type: "call", function: { name: "read_file" } }), "call read_file");

  // Precedence, asserted with all four present so a reordering is visible rather than incidental.
  assert.equal(
    describeCodexItem({
      type: "call",
      name: "first",
      toolName: "second",
      call: { name: "third" },
      function: { name: "fourth" },
    }),
    "call first",
  );
  assert.equal(
    describeCodexItem({ type: "call", toolName: "second", call: { name: "third" } }),
    "call second",
    "`toolName` outranks the nested spellings",
  );
  assert.equal(
    describeCodexItem({ type: "call", call: { name: "third" }, function: { name: "fourth" } }),
    "call third",
    "`call.name` outranks `function.name`",
  );
}

// ── the server, same treatment ───────────────────────────────────────────────────────────────
{
  for (const field of ["server", "serverName", "mcpServer"]) {
    assert.equal(
      describeCodexItem({ type: "mcpToolCall", [field]: "aify-comms", name: "comms_send" }),
      "mcpToolCall aify-comms/comms_send",
      field,
    );
  }
  assert.equal(
    describeCodexItem({ type: "mcpToolCall", call: { server: "aify-comms" }, name: "comms_send" }),
    "mcpToolCall aify-comms/comms_send",
  );
  assert.equal(
    describeCodexItem({ type: "t", server: "first", serverName: "second", mcpServer: "third" }),
    "t first",
    "server precedence is `server` > `serverName` > `mcpServer`",
  );
}

// ── title is the NAME's fallback, not an extra field ─────────────────────────────────────────
{
  assert.equal(describeCodexItem({ type: "web", title: "Some Page" }), "web Some Page");
  assert.equal(
    describeCodexItem({ type: "web", name: "fetch", title: "Some Page" }),
    "web fetch",
    "a real name wins — the title is only used when there is none",
  );
  assert.equal(
    describeCodexItem({ type: "web", server: "s", title: "Some Page" }),
    "web s/Some Page",
    "and a title still pairs with the server when it is standing in for the name",
  );
}

// ── the shape of the result ──────────────────────────────────────────────────────────────────
{
  // No detail at all means the type alone, with no dangling separator or trailing space — this
  // string goes straight into console output.
  assert.equal(describeCodexItem({ type: "reasoning" }), "reasoning");
  assert.equal(describeCodexItem({ type: "reasoning", name: "" , server: "" }), "reasoning");

  // A server with no name is still worth showing; a name with no server likewise. Neither should
  // produce a bare "/".
  assert.equal(describeCodexItem({ type: "t", server: "aify-comms" }), "t aify-comms");
  assert.equal(describeCodexItem({ type: "t", name: "comms_send" }), "t comms_send");
  assert.ok(!describeCodexItem({ type: "t", server: "aify-comms" }).includes("/"));
  assert.ok(!describeCodexItem({ type: "t", name: "comms_send" }).includes("/"));

  // Values are trimmed, so a padded field does not leave the label ragged.
  assert.equal(describeCodexItem({ type: " t ", server: " s ", name: " n " }), "t s/n");
}

// ── degenerate inputs never throw ────────────────────────────────────────────────────────────
{
  // This runs on every streamed item of every codex turn; an exception here would take down the
  // turn's event handling over a label.
  for (const item of [{}, null, undefined, "string", 42, [], { call: null }, { function: "not an object" }]) {
    assert.doesNotThrow(() => describeCodexItem(item), JSON.stringify(item));
    assert.equal(typeof describeCodexItem(item), "string");
  }
  assert.equal(describeCodexItem({ type: 42 }), "42", "a non-string type is coerced, not dropped");
}

console.log("describe-codex-item.test.js: all assertions passed");
