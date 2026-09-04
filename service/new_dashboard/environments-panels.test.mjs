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

/**
 * The card's text with markup removed.
 *
 * The offline age is emitted by `relTimeHtml` as a `<span data-rel-ts>` so `rel-time-ticker.mjs`
 * can refresh the number in place instead of repainting the environment list. Stripping tags keeps
 * these assertions about WHAT THE CARD SAYS rather than about how it is marked up.
 */
const stripTags = (html) => String(html).replace(/<[^>]*>/g, "");

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

test("A STATUS THE SERVICE REFUSES IS NOT ALIASED INTO ONE IT ACCEPTS", () => {
  // This used to require `done` be chipped as `completed`, on the reasoning that "every completed
  // spawn on this page goes grey at once" without the alias. Neither value is a spawn status:
  // `PATCH /spawn-requests/{id}` validates against {claimed, starting, running, failed, cancelled}
  // and answers 400 for anything else, and `queued` is the creation default. The alias could not
  // fire, and its target was not a state either -- so a reader of that test learned a vocabulary the
  // system does not have.
  const html = spawnRows([{ agentId: "a", status: "done" }]);
  assert.ok(!html.includes("completed"),
    "an unknown status must render as itself, not be translated into a state that does not exist");
  assert.ok(html.includes("done"), "…and the raw value stays visible, so the anomaly is readable");
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

test("spawn requests are newest-first", () => {
  // `createdAt` only. It read `createdAt || created_at`, and `_spawn_request_to_dict` emits
  // camelCase for every key -- so the second spelling was a dead branch, and the row that used it
  // here sorted as if it had no timestamp at all.
  const html = spawnRows([
    { agentId: "older", createdAt: "2026-08-14T10:00:00Z" },
    { agentId: "newest", createdAt: "2026-08-14T12:00:00Z" },
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

test("an OFFLINE environment says how long it has been silent; an online one does not", () => {
  // A host that dropped a minute ago and one abandoned in June both render as `offline`, and those
  // call for opposite actions — wait, versus Forget. The age is the only thing separating them, and it
  // was already on the wire: /environments carries `lastSeen` for every row and this card dropped it.
  // On the operator's fleet 2026-08-27 the WSL environment had been silent since 2026-06-05 and the
  // card said only `offline`.
  const longAgo = new Date(Date.now() - 83 * 24 * 3600 * 1000).toISOString();

  state.environments = [{ id: "dead", label: "WSL box", status: "offline", lastSeen: longAgo }];
  const offline = withDom({ "environment-list": el() }, null, (els) => {
    renderRuntime();
    return els["environment-list"].innerHTML;
  });
  assert.match(stripTags(offline), /last seen 83d ago/,
               "an offline environment does not say how long it has been silent");

  // ANTI-VACUITY: a card that always printed an age would satisfy the assertion above while making a
  // claim about a host that is answering right now.
  state.environments = [{ id: "live", label: "Windows box", status: "online", lastSeen: longAgo }];
  const online = withDom({ "environment-list": el() }, null, (els) => {
    renderRuntime();
    return els["environment-list"].innerHTML;
  });
  assert.doesNotMatch(online, /last seen/, "an ONLINE environment claimed a last-seen age");
});

test("an offline environment with no lastSeen makes no claim about its age", () => {
  // FAILS CLOSED. `relTime` returns '' for a missing or unparseable value, so the alternative to this
  // is `last seen  ago`, or an age measured from the epoch — a number that looks like evidence.
  state.environments = [{ id: "dead", label: "WSL box", status: "offline" }];
  const html = withDom({ "environment-list": el() }, null, (els) => {
    renderRuntime();
    return els["environment-list"].innerHTML;
  });
  assert.doesNotMatch(html, /last seen/, "a row with no timestamp still claimed an age");
  assert.match(html, /WSL box/, "the card did not render at all");
});

// ---------------------------------------------------------------------------------------------------
// ADVERTISED is not CLAIMABLE. Added 2026-09-03, after the same conflation shipped in three places.
//
// `status` and `lastSeen` are refreshed by aify-env ADVERTISING the host. A spawn needs something
// offering to CLAIM the work, which the service derives into `spawnClaim` on the row. Measured
// 2026-09-02: a row read `online, lastSeen 17:26:41Z` while nothing had claimed for a day; this page
// preselected that host and offered "Spawn here…", and every spawn from the form would have been
// refused with nothing on screen saying why.

const claimable = (extra = {}) => ({
  id: "env-live", label: "Live host", status: "online",
  spawnClaim: { state: "fresh", canClaim: true, bridgeLastSeen: "2026-09-03T00:34:16Z" },
  ...extra,
});
const advertisedOnly = (extra = {}) => ({
  id: "env-advertised", label: "Advertised host", status: "online",
  spawnClaim: { state: "stale", canClaim: false, bridgeLastSeen: "2026-09-02T00:00:00Z" },
  ...extra,
});

test("the spawn form PRESELECTS a host that can claim, not merely one that is online", () => {
  // Both are `online`, and the advertised-only one comes first. Picking by status alone chose it.
  const els = spawnOptions({ environments: [advertisedOnly(), claimable()] });
  assert.match(els["env-spawn-environment"].innerHTML, /value="env-live" selected/,
    "the form preselected a host where a spawn would be refused");
});

test("an environment that cannot claim is LABELLED in the dropdown, and stays selectable", () => {
  // Selectable on purpose: an operator who has just restarted a claimer must be able to try the host
  // they expect, and disabling the option would hide the reason along with the choice.
  const html = spawnOptions({ environments: [advertisedOnly()] })["env-spawn-environment"].innerHTML;
  assert.match(html, /Advertised host \(online\) — cannot spawn/);
  assert.doesNotMatch(html, /disabled/);
});

test("the card's Spawn button SAYS a spawn there would be refused", () => {
  state.environments = [advertisedOnly()];
  const html = withDom({ "environment-list": el() }, null, (els) => {
    renderRuntime();
    return els["environment-list"].innerHTML;
  });
  assert.match(html, /Spawn here…\s*\(no claimer\)/);
  assert.match(html, /would be refused: no claimer has spoken here recently/);
});

test("an UNREADABLE claimer timestamp says corrupt row, not missing bridge", () => {
  // Different remedies: starting a claimer fixes a stale stamp and does nothing for this one.
  state.environments = [advertisedOnly({ spawnClaim: { state: "invalid", canClaim: false } })];
  const html = withDom({ "environment-list": el() }, null, (els) => {
    renderRuntime();
    return els["environment-list"].innerHTML;
  });
  assert.match(html, /unreadable claimer timestamp/);
});

test("a row with NO spawnClaim keeps its Spawn button — the page cannot know", () => {
  // FAILS OPEN, deliberately. The service settles an unstamped row against `bridge_instances`, which
  // no listing queries, and every row registered before that field existed is this shape. An older
  // service sending no field at all is the same case. Greying these out would take the feature away
  // from environments that work.
  for (const env of [{ id: "e", label: "Old", status: "online" },
                     { id: "e", label: "Old", status: "online", spawnClaim: { state: "absent", canClaim: false } }]) {
    state.environments = [env];
    const html = withDom({ "environment-list": el() }, null, (els) => {
      renderRuntime();
      return els["environment-list"].innerHTML;
    });
    assert.match(html, /Spawn here…/);
    assert.doesNotMatch(html, /no claimer/, `${JSON.stringify(env.spawnClaim)} was treated as dead`);
  }
});

test("the summary counts CAN SPAWN separately from ONLINE, and they can disagree", () => {
  // CONTROL for the whole change: if the new tile merely restated the old one, every assertion above
  // could hold while the page still answered from the wrong field.
  state.environments = [claimable(), advertisedOnly(), { id: "c", status: "offline" }];
  const html = withDom({ "environment-summary": el() }, null, (els) => {
    renderEnvironmentSummary();
    return els["environment-summary"].innerHTML;
  });
  assert.ok(html.includes("<b>2</b><span>Online bridges</span>"), "two rows are advertised");
  assert.ok(html.includes("<b>1</b><span>Can spawn</span>"), "and only one can take work");
});

// ── an environment the page cannot judge says so ─────────────────────────────────────────────────
//
// EXTERNAL REVIEW, Round 8 M8. `spawnClaim()` returns `unproven: true` for a row with no claim
// stamp, and NOTHING read it -- measured, one hit in the whole dashboard, the line that sets it. So
// a row the page cannot judge showed the same confident button as one it had checked. A field
// nothing reads changes nothing, which is this repo's own rule arriving from the other end.
//
// The fail-open stays: the service resolves an unstamped row against `bridge_instances`, which no
// listing queries, so the page genuinely cannot know and greying the button would take the feature
// from environments that work. The silence is what was wrong.

/** The environment list's HTML for one row, rendered the way the page renders it. */
function runtimeHtmlFor(env) {
  state.environments = [env];
  return withDom({ "environment-list": el() }, null, (els) => {
    renderRuntime();
    return els["environment-list"].innerHTML;
  });
}

test("a row with NO claim stamp keeps its button and says the answer is unknown", () => {
  const html = runtimeHtmlFor({ id: "windows:unstamped:default", label: "Unstamped", status: "online" });
  assert.match(html, /Spawn here/, "the button was taken away from a row that may well work");
  assert.match(html, /cannot be told/,
    "the page offered a confident Spawn button for an environment it cannot judge. `unproven` is "
    + "returned for exactly this row and was read by nobody.");
});

test("a FRESH claim still gets the plain title, so the notice means something", () => {
  // THE CONTROL. A notice on every row is one an operator stops reading, and it would hide the
  // difference this test exists to preserve.
  const html = runtimeHtmlFor({
    id: "windows:stamped:default", label: "Stamped", status: "online",
    spawnClaim: { state: "fresh" },
  });
  assert.match(html, /Open the spawn form prefilled/, "a proven row lost its ordinary title");
  assert.doesNotMatch(html, /cannot be told/, "the unknown-claim notice fired on a PROVEN row");
});

test("a STALE claim still refuses, and says why", () => {
  const html = runtimeHtmlFor({
    id: "windows:stale:default", label: "Stale", status: "online",
    spawnClaim: { state: "stale" },
  });
  assert.match(html, /no claimer/, "a row that would be refused no longer says so");
  assert.doesNotMatch(html, /cannot be told/, "a KNOWN refusal was reported as unknown");
});
