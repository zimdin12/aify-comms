// Eight exports the JS ratchet had recorded as named by no test — seven of them CONSTANTS.
//
// `every-export-is-named-by-a-test.test.js` measured 220 modules and 990 exports and found 42 that
// no test mentioned. Twenty were left; these eight are the dashboard half, and they are worth taking
// as one slice because they are all the same KIND of thing: a vocabulary or a bound that some other
// function reads. An exported constant with no test is the shape this whole series keeps finding —
// two definitions of one set, drifting apart in silence, each looking authoritative.
//
// So none of these is asserted by reading the constant back. Each is asserted through the CONSUMER
// that gives it meaning, and where the value itself is a contract with something outside this file
// (a filesystem path, a status vocabulary that must match the server) the literal is pinned too. A
// test that only compares a constant to itself passes any change to it.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { ANALYTICS_RANGES, analyticsSeries, rangeDef } from "./analytics.js";
import { DERIVED_SUBJECT_MAX, subjectIsEchoOfBody } from "./chat-render.mjs";
import { MANAGED_CODEX_HOME, continueCliInfo } from "./cli-resume.mjs";
import { BROWSER_GLOBALS, moduleScopeBrowserRefs } from "./extraction-proof.mjs";
import { NOTIFIABLE_EVENTS, isForOperator } from "./notify.mjs";
import { LIVE_SESSION_ROW_STATUSES, sessionRowIsLive } from "./sessions-list.mjs";
import { AGENT_STATUSES, LIVE_AGENT_STATUSES, NON_LIVE_AGENT_STATUSES } from "./status.js";
import { applyCachedTheme } from "./theme.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..");

// ── ANALYTICS_RANGES ────────────────────────────────────────────────────────────────────────────

test("every analytics range is reachable by its own key", () => {
  // `rangeDef` is a find-by-key with a fallback to the first entry. A duplicate or misspelled key
  // does not throw — it silently resolves to 24h, and the operator's 12m selection renders hourly
  // data under a monthly label.
  for (const range of ANALYTICS_RANGES) {
    assert.equal(rangeDef(range.key).key, range.key);
  }
  const keys = ANALYTICS_RANGES.map((r) => r.key);
  assert.equal(new Set(keys).size, keys.length, "two ranges share a key");
});

test("an unknown range falls back to the FIRST entry, not to nothing", () => {
  assert.equal(rangeDef("decade").key, ANALYTICS_RANGES[0].key);
  assert.equal(rangeDef(undefined).key, ANALYTICS_RANGES[0].key);
});

test("every range carries the four fields its consumers read", () => {
  // `analyticsSeries` reads seriesKey/maxItems/windowLabel and the selector renders `label`. A range
  // missing one of them renders an empty chart with no error.
  for (const range of ANALYTICS_RANGES) {
    for (const field of ["key", "label", "seriesKey", "maxItems", "windowLabel"]) {
      assert.ok(range[field] !== undefined, `${range.key} has no ${field}`);
    }
    assert.equal(typeof range.maxItems, "number");
  }
});

test("a maxItems of ZERO means 'as many as there are', not 'none'", () => {
  // The All-time range is the only one with no cap, and it expresses that as 0. Read literally it
  // would bound the chart to nothing — `analyticsSeries` turns it into the series length instead.
  const all = ANALYTICS_RANGES.find((r) => r.maxItems === 0);
  assert.ok(all, "no range expresses 'uncapped'");
  const series = analyticsSeries({ [all.seriesKey]: [1, 2, 3] }, all.key);
  assert.equal(series.maxItems, 3);
});

test("a capped range keeps its cap even when more data arrives", () => {
  const hour = ANALYTICS_RANGES.find((r) => r.key === "hour");
  const series = analyticsSeries({ [hour.seriesKey]: new Array(100).fill(1) }, "hour");
  assert.equal(series.maxItems, hour.maxItems);
});

// ── DERIVED_SUBJECT_MAX ─────────────────────────────────────────────────────────────────────────

test("a subject that is the first DERIVED_SUBJECT_MAX characters of the body is an echo", () => {
  // The server derives a subject from the body when a sender gives none, so the two arrive equal and
  // the chat bubble would print the same sentence twice. The constant is the length it derives at.
  const body = "x".repeat(DERIVED_SUBJECT_MAX + 40);
  assert.equal(subjectIsEchoOfBody(body.slice(0, DERIVED_SUBJECT_MAX), body), true);
});

test("DERIVED_SUBJECT_MAX is 80 — the length the SERVER derives at", () => {
  // Pinned as a literal as well as through the behaviour above: the behaviour test is written in
  // terms of the constant, so it passes for any value. This is the half that fails if the two sides
  // stop agreeing.
  assert.equal(DERIVED_SUBJECT_MAX, 80);
});

test("a subject one character longer than the derivation is NOT an echo", () => {
  // The boundary in the direction that matters: a subject the sender actually wrote may begin with
  // the body and must still be shown.
  const body = "y".repeat(DERIVED_SUBJECT_MAX + 40);
  assert.equal(subjectIsEchoOfBody(body.slice(0, DERIVED_SUBJECT_MAX + 1), body), false);
});

// ── MANAGED_CODEX_HOME ──────────────────────────────────────────────────────────────────────────

const CODEX_AGENT = {
  id: "coder-1", runtime: "codex", sessionHandle: "thread-1", machineId: "linux:box",
};

test("the managed codex home is set for a MANAGED session and only then", () => {
  // A resume command that exported CODEX_HOME for a resident session would point the operator's own
  // terminal at the managed state directory — a different session store, so the resume finds nothing.
  const managed = continueCliInfo({ ...CODEX_AGENT, sessionMode: "managed" }, null);
  assert.ok(managed.command.includes(`CODEX_HOME="${MANAGED_CODEX_HOME}"`));

  const resident = continueCliInfo({ ...CODEX_AGENT, sessionMode: "resident" }, null);
  assert.ok(!resident.command.includes("CODEX_HOME"), resident.command);
});

test("MANAGED_CODEX_HOME is the path the installer actually creates", () => {
  // A contract with something outside this file, so the literal is pinned. It is the directory the
  // spawn path sets for managed codex workers; a drift here produces a command that runs and resumes
  // the wrong store, which looks like a lost session rather than a wrong path.
  assert.equal(MANAGED_CODEX_HOME, "$HOME/.local/state/aify-comms/managed-codex-home");
});

// ── NOTIFIABLE_EVENTS ───────────────────────────────────────────────────────────────────────────

test("only the events in NOTIFIABLE_EVENTS can notify the operator", () => {
  // The gate in front of every desktop notification. Widening it is how a dashboard starts pinging on
  // status churn — the volume failure this feature is shaped around.
  assert.equal(isForOperator("message_sent", { to: "dashboard" }), true);
  for (const event of ["agent_status", "terminal_output", "spawn_request_claimed", "", null]) {
    assert.equal(isForOperator(event, { to: "dashboard" }), false, String(event));
  }
});

test("NOTIFIABLE_EVENTS holds exactly the two message events", () => {
  assert.deepEqual([...NOTIFIABLE_EVENTS].sort(), ["channel_message", "message_sent"]);
});

test("a notifiable event still has to be FOR the operator", () => {
  // Membership is necessary, not sufficient: a message between two agents is notifiable in kind and
  // none of the operator's business.
  assert.equal(isForOperator("message_sent", { to: "some-other-agent" }), false);
});

// ── LIVE_SESSION_ROW_STATUSES ───────────────────────────────────────────────────────────────────

test("every live session-row status is treated as live", () => {
  for (const status of LIVE_SESSION_ROW_STATUSES) {
    assert.equal(sessionRowIsLive({ status }), true, status);
  }
});

test("it is a SUPERSET of the server's live-session set", () => {
  // The module says it mirrors `_LIVE_SESSION_STATUSES` plus the worker-detail statuses this list
  // also shows as live. Read out of the Python rather than retyped: a status the server considers
  // live and the dashboard does not is a session that disappears from the list while it is running.
  const source = readFileSync(
    path.join(REPO, "service", "api_core", "liveness.py"), "utf-8");
  const match = /_LIVE_SESSION_STATUSES\s*=\s*\{([^}]*)\}/.exec(source);
  assert.ok(match, "could not find _LIVE_SESSION_STATUSES in liveness.py");
  const serverStatuses = [...match[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  assert.ok(serverStatuses.length >= 5, `parsed only ${serverStatuses.length} server statuses`);
  for (const status of serverStatuses) {
    assert.equal(LIVE_SESSION_ROW_STATUSES.has(status), true,
      `the server calls "${status}" live and the dashboard does not`);
  }
});

test("an ENDED status is not live, whatever its case or spacing", () => {
  for (const status of ["stopped", " STOPPED ", "failed", "exited", "", null, undefined]) {
    assert.equal(sessionRowIsLive({ status }), false, String(status));
  }
});

// ── NON_LIVE_AGENT_STATUSES ─────────────────────────────────────────────────────────────────────

test("live and non-live agent statuses PARTITION the vocabulary", () => {
  // The two are derived from one list precisely so they cannot drift. If a status were in neither,
  // the dashboard would filter it into nothing; in both, it would appear as reachable and not.
  assert.deepEqual(
    [...LIVE_AGENT_STATUSES, ...NON_LIVE_AGENT_STATUSES].sort(),
    [...AGENT_STATUSES].sort(),
  );
  const overlap = LIVE_AGENT_STATUSES.filter((s) => NON_LIVE_AGENT_STATUSES.includes(s));
  assert.deepEqual(overlap, []);
});

test("every NON_LIVE status is a real status", () => {
  // A typo here does not throw: the filter simply removes nothing, so the misspelled state stays
  // LIVE and the dashboard offers to send work to an agent that cannot take it.
  for (const status of NON_LIVE_AGENT_STATUSES) {
    assert.equal(AGENT_STATUSES.includes(status), true, `${status} is not in AGENT_STATUSES`);
  }
});

test("the three unreachable states are the ones named", () => {
  // Pinned literally because it is a judgement, not a derivation: `misconfigured` is non-live (the
  // identity exists but needs a human), while `starting` is LIVE (a send during boot queues and is
  // delivered when the worker appears).
  assert.deepEqual([...NON_LIVE_AGENT_STATUSES].sort(),
    ["misconfigured", "offline", "stopped"]);
  assert.equal(LIVE_AGENT_STATUSES.includes("starting"), true);
});

// ── BROWSER_GLOBALS ─────────────────────────────────────────────────────────────────────────────

test("each browser global is detected at module scope", () => {
  // This list is what makes "import-safe" checkable, and it is only as good as its membership: a
  // global missing from it lets an impure module pass the extraction gate and then throw the first
  // time a test imports it.
  for (const name of BROWSER_GLOBALS) {
    const hits = moduleScopeBrowserRefs(`const x = ${name}.something;\n`);
    assert.equal(hits.length, 1, `${name} was not detected at module scope`);
  }
});

test("a reference INSIDE a function body is not a module-scope reference", () => {
  // The whole point of the distinction: a module that touches the DOM when CALLED is importable, and
  // flagging it would make every real dashboard module fail the gate.
  const hits = moduleScopeBrowserRefs("export function f() {\n  return document.title;\n}\n");
  assert.deepEqual(hits, []);
});

test("the list covers the globals this dashboard actually uses", () => {
  for (const name of ["document", "window", "localStorage", "fetch", "WebSocket"]) {
    assert.equal(BROWSER_GLOBALS.includes(name), true, `${name} is missing from BROWSER_GLOBALS`);
  }
});

// ── applyCachedTheme ────────────────────────────────────────────────────────────────────────────

function withBrowser(stored, run) {
  for (const name of ["document", "localStorage"]) {
    assert.equal(name in globalThis, false, `${name} leaked into the test environment`);
  }
  const store = new Map(Object.entries(stored));
  globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, v),
  };
  globalThis.document = {
    body: { dataset: {}, style: { setProperty(name, value) { this[name] = value; } } },
    title: "",
    querySelector: () => null,
  };
  try {
    return run(store);
  } finally {
    delete globalThis.localStorage;
    delete globalThis.document;
  }
}

// `applyCachedTheme` runs before settings are fetched so a themed install does not flash the default
// palette; the cache is the only source it has at that moment. My first test here called it and
// asserted that a helper returned null — true of any implementation, including an empty one. The
// three below assert what it PAINTS.

test("it applies the stored theme key and title to the document", () => {
  withBrowser({ aifyDashboardTheme: "forest", aifyDashboardTitle: "Ops" }, () => {
    applyCachedTheme();
    assert.equal(globalThis.document.body.dataset.theme, "forest");
    assert.equal(globalThis.document.title, "Ops");
  });
});

test("with nothing cached it paints the DEFAULT rather than an empty theme", () => {
  withBrowser({}, () => {
    applyCachedTheme();
    assert.equal(globalThis.document.body.dataset.theme, "default");
    assert.equal(globalThis.document.title, "AIFY Comms");
  });
});

test("painting the cache does NOT write back to localStorage", () => {
  // `persist: false`. Writing here would make the cache self-confirming: a corrupt or stale value
  // would be re-saved on every load and could never be corrected by the settings fetch that follows.
  withBrowser({ aifyDashboardTheme: "forest" }, (store) => {
    const before = new Map(store);
    applyCachedTheme();
    assert.deepEqual([...store.entries()].sort(), [...before.entries()].sort());
  });
});

test("an UNKNOWN cached theme falls back to the default", () => {
  // The cache is operator-writable through the browser's own storage, and a theme key that no longer
  // ships must not leave the page with no palette at all.
  withBrowser({ aifyDashboardTheme: "no-such-theme" }, () => {
    applyCachedTheme();
    assert.equal(globalThis.document.body.dataset.theme, "default");
  });
});
