// Real tests for the Environments page panels.
//
// Three behaviours here fail silently and none had a test: the `done` status alias, the online-first
// default in the spawn form, and the guard that stops the 15s poll rebuilding a dropdown the operator is
// using. All three were reachable only by source regex while this lived in app.js.
//
// SEALING. `state` is a shared singleton, so every field read here is rebuilt per test; `document` does not
// exist in Node and is installed per render, then removed.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import {
  renderEnvironmentSpawnOptions,
  renderRuntime,
  renderSpawnRequests,
} from "./environments-panels.mjs";

function el(extra = {}) {
  // classList is here because the roots editor opens the inspector drawer; without it the failure reads
  // as "Cannot read properties of undefined (reading 'add')", which says nothing about the cause.
  const classes = new Set();
  return {
    innerHTML: "", value: "", contains: () => false,
    classList: {
      add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c),
    },
    ...extra,
  };
}

function withDom(els, activeElement = null, run) {
  const had = "document" in globalThis;
  globalThis.document = { getElementById: (id) => els[id] || null, activeElement };
  try {
    return run(els);
  } finally {
    if (!had) delete globalThis.document;
  }
}

// `openEnvironmentRootsEditor` defers a focus call by 30ms. Tearing the document down synchronously left
// that timer to fire against nothing — Node reported "asynchronous activity after the test ended" and
// turned it into an uncaughtException, which fails the whole FILE rather than the test. This variant
// lets the deferred work run first.
async function withDomAsync(els, run) {
  const had = "document" in globalThis;
  globalThis.document = { getElementById: (id) => els[id] || null, activeElement: null };
  try {
    const out = await run(els);
    await new Promise((r) => setTimeout(r, 60));
    return out;
  } finally {
    if (!had) delete globalThis.document;
  }
}

function spawnRows(requests) {
  state.spawnRequests = requests;
  const els = { "spawn-requests-list": el() };
  return withDom(els, null, () => {
    renderSpawnRequests();
    return els["spawn-requests-list"].innerHTML;
  });
}

test("a `done` spawn is chipped as completed, not left unknown", () => {
  // `done` is the one spawn status the canonical resolver does not know. The alias is what keeps a
  // finished spawn out of the unknown bucket; if it ever stops working, every completed spawn on this
  // page goes grey at once and the panel stops meaning anything.
  const html = spawnRows([{ agentId: "a", status: "done" }]);
  assert.ok(html.includes("completed"), "the chip must resolve to a known status");
  assert.ok(html.includes(">done<") || html.includes("done"), "…while the raw status stays visible as the label");
});

test("other spawn statuses pass through unaliased, and case does not matter", () => {
  for (const status of ["queued", "claimed", "failed"]) {
    const html = spawnRows([{ agentId: "a", status: status.toUpperCase() }]);
    assert.ok(html.includes(status), `"${status}" must survive lowercasing intact`);
  }
});

test("a spawn with no status reads as queued rather than blank", () => {
  const html = spawnRows([{ agentId: "a" }]);
  assert.ok(html.includes("queued"));
});

test("spawn requests are newest-first, under either timestamp spelling", () => {
  const html = spawnRows([
    { agentId: "older", createdAt: "2026-08-14T10:00:00Z" },
    { agentId: "newest", created_at: "2026-08-14T12:00:00Z" },
    { agentId: "middle", createdAt: "2026-08-14T11:00:00Z" },
  ]);
  const order = ["newest", "middle", "older"].map((n) => html.indexOf(n));
  assert.deepEqual([...order].sort((a, b) => a - b), order,
    "the queue is read top-down when a spawn is stuck — order is the point of it");
});

test("an empty spawn queue explains itself", () => {
  const html = spawnRows([]);
  assert.ok(html.includes("No spawn requests"));
  assert.ok(!html.includes("<table"), "an empty table reads as a broken panel");
});

test("a failed spawn shows its error, and a claimed one its bridge", () => {
  assert.ok(spawnRows([{ agentId: "a", status: "failed", error: "no runtime" }]).includes("no runtime"),
    "the reason a spawn failed is why this panel exists");
  assert.ok(spawnRows([{ agentId: "a", status: "claimed", claimedByBridgeId: "bridge-7" }]).includes("bridge-7"));
});

function spawnOptions({ environments = [], selected = "", formHasFocus = false } = {}) {
  state.environments = environments;
  const els = {
    "env-spawn-environment": el({ value: selected }),
    "env-spawn-runtime": el(),
    "environment-spawn-form": el({ contains: () => formHasFocus }),
  };
  return withDom(els, {}, () => {
    renderEnvironmentSpawnOptions(selected);
    return els;
  });
}

test("the spawn form is NOT rebuilt while the operator is inside it", () => {
  // Same class of bug as the settings panel: the 15s poll re-renders, and rebuilding a <select> the
  // operator has open resets their choice mid-interaction.
  const els = spawnOptions({
    environments: [{ id: "e1", label: "One", status: "online" }],
    formHasFocus: true,
  });
  assert.equal(els["env-spawn-environment"].innerHTML, "", "an in-progress selection must survive the poll");
});

test("the default environment prefers an ONLINE one over merely the first", () => {
  const els = spawnOptions({
    environments: [
      { id: "offline-one", label: "Offline", status: "offline" },
      { id: "live-one", label: "Live", status: "online" },
    ],
  });
  const html = els["env-spawn-environment"].innerHTML;
  assert.match(html, /value="live-one" selected/,
    "defaulting to a dead environment sends the operator's spawn nowhere");
});

test("with nothing online it falls back to the first environment", () => {
  const els = spawnOptions({
    environments: [{ id: "first", label: "First", status: "offline" },
                   { id: "second", label: "Second", status: "offline" }],
  });
  assert.match(els["env-spawn-environment"].innerHTML, /value="first" selected/);
});

test("an explicitly selected environment is respected over the online-first default", () => {
  const els = spawnOptions({
    environments: [{ id: "chosen", label: "Chosen", status: "offline" },
                   { id: "live", label: "Live", status: "online" }],
    selected: "chosen",
  });
  assert.match(els["env-spawn-environment"].innerHTML, /value="chosen" selected/,
    "an operator's explicit choice must not be overridden on the next poll");
});

test("renderRuntime lists environments and says so when there are none", () => {
  state.environments = [];
  const empty = withDom({ "environment-list": el() }, null, (els) => {
    renderRuntime();
    return els["environment-list"].innerHTML;
  });
  assert.ok(empty.includes("empty-state"), "no environments must render an explanation, not a blank list");

  state.environments = [{ id: "e1", label: "Windows box", status: "online" }];
  const listed = withDom({ "environment-list": el() }, null, (els) => {
    renderRuntime();
    return els["environment-list"].innerHTML;
  });
  assert.ok(listed.includes("Windows box"));
  assert.ok(listed.includes('data-kind="environment"'));
});

// ---------------------------------------------------------------------------------------------------
// The environment summary tile and the roots editor, appended to this module in a later v0.5.4 slice.

import { openEnvironmentRootsEditor, renderEnvironmentSummary } from "./environments-panels.mjs";

test("the summary counts online and offline bridges by RESOLVED status", () => {
  state.environments = [
    { id: "a", status: "online" },
    { id: "b", status: "online" },
    { id: "c", status: "offline" },
  ];
  const html = withDom({ "environment-summary": el() }, null, (els) => {
    renderEnvironmentSummary();
    return els["environment-summary"].innerHTML;
  });
  assert.ok(html.includes("<b>3</b><span>Environments</span>"));
  assert.ok(html.includes("<b>2</b><span>Online bridges</span>"));
  assert.ok(html.includes("<b>1</b><span>Offline</span>"));
});

test("offline is toned BAD only when there is something offline", () => {
  // A permanently red "Offline: 0" trains the operator to ignore the tile that matters.
  state.environments = [{ id: "a", status: "online" }];
  const clean = withDom({ "environment-summary": el() }, null, (els) => {
    renderEnvironmentSummary();
    return els["environment-summary"].innerHTML;
  });
  assert.ok(!clean.includes('data-tone="bad"'), "nothing offline must not render an alarm");

  state.environments = [{ id: "a", status: "offline" }];
  const bad = withDom({ "environment-summary": el() }, null, (els) => {
    renderEnvironmentSummary();
    return els["environment-summary"].innerHTML;
  });
  assert.ok(bad.includes('data-tone="bad"'));
});

test("runtime types are counted DISTINCTLY across environments", () => {
  // Two hosts both offering claude and codex is two runtime types, not four — the tile answers "what can
  // this fleet run", not "how many runtime rows exist".
  state.environments = [
    { id: "a", status: "online", runtimes: [{ runtime: "claude" }, { runtime: "codex" }] },
    { id: "b", status: "online", runtimes: [{ runtime: "claude" }] },
  ];
  const html = withDom({ "environment-summary": el() }, null, (els) => {
    renderEnvironmentSummary();
    return els["environment-summary"].innerHTML;
  });
  assert.ok(html.includes("<b>2</b><span>Runtime types</span>"));
});

test("the roots editor flags a dashboard override, under either metadata spelling", async () => {
  // The distinction matters operationally: overridden roots do NOT track what the bridge advertises, so a
  // workspace that later becomes valid on the host stays rejected until someone clears the override.
  for (const metadata of [{ manualRoots: true }, { manual_roots: true }]) {
    state.environments = [{ id: "e1", metadata, roots: ["/srv"] }];
    const html = await withDomAsync({ "inspector-content": el(), inspector: el() }, (els) => {
      openEnvironmentRootsEditor("e1");
      return els["inspector-content"].innerHTML;
    });
    assert.ok(html.includes("dashboard override active"), `metadata=${JSON.stringify(metadata)}`);
  }

  state.environments = [{ id: "e1", roots: ["/srv"] }];
  const plain = await withDomAsync({ "inspector-content": el(), inspector: el() }, (els) => {
    openEnvironmentRootsEditor("e1");
    return els["inspector-content"].innerHTML;
  });
  assert.ok(plain.includes("using bridge-advertised roots"));
});

test("the roots editor opens for an environment that is not in state", async () => {
  // It is reachable from a row the poll has since dropped; throwing here would leave the drawer half-built.
  state.environments = [];
  const html = await withDomAsync({ "inspector-content": el(), inspector: el() }, (els) => {
    openEnvironmentRootsEditor("ghost-env");
    return els["inspector-content"].innerHTML;
  });
  assert.ok(html.includes("ghost-env"));
});
