#!/usr/bin/env node
// Two agent ids the service treats as different agents map to ONE hermes marker name.
//
// `hermes-endpoint.js::sanitizeAgentId` folds runs of anything outside `[a-zA-Z0-9_-]` into a single
// dash. The service admits `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$` — dots included — so:
//
//     team.coder  ->  team-coder
//     team-coder  ->  team-coder      <- the same string, and a DIFFERENT agent
//
// That name keys FIVE markers in %TEMP%: the gateway PORT, the gateway API KEY, the gateway marker,
// the daemon PID, and the hermes SESSION ID. Two colliding agents therefore share the gateway they
// connect to, the key they authenticate with, the daemon they consider theirs, and the session they
// resume. The session-marker comment three functions away calls the scheme "agent-keyed (never
// cwd-keyed), so same-folder agents never collide" — which holds only while no two ids fold together.
//
// THE DIVERGENCE IS DOCUMENTED; THE COLLISION IS NOT. Both this module and
// `claude-session-store.js` carry a note saying the two same-named sanitisers deliberately differ
// and that unifying them "would repoint existing files on disk — a migration, not a refactor".
// Neither note observes that one of the two is not injective over the ids the API accepts. The
// reasoning that kept them apart was about naming and migration cost; this is a correctness
// property, and it was never weighed.
//
// SO THIS FILE RULES NOTHING. It pins what the two functions do TODAY, measured, so the collision
// cannot widen or quietly change while the question is open — and so that whoever rules on it is
// ruling on a fact rather than a description. `claude-session-store.js`'s sanitiser is
// collision-free over the same inputs, which is what makes this a choice rather than a constraint.

import assert from "node:assert/strict";

import { claudeSessionStorePath } from "../claude-session-store.js";
import { sessionKeyFor } from "../hermes-active-session.mjs";
import { agentPort, sanitizeAgentId as hermesSanitize } from "../hermes-endpoint.js";
import { loopReadyFile } from "../hermes-loop-ready.js";
import { pinnedSessionId } from "../hermes-session-id.js";

/** Mirrors service/api_core/validation.py::SAFE_NAME_RE — duplicated on purpose. */
const SERVICE_ACCEPTS = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/;

//: Distinct agent ids, all admitted by the service.
const IDS = ["team.coder", "team-coder", "team_coder", "a.b.c", "a-b-c", "lc-coder"];

function groupBy(ids, keyOf) {
  const groups = new Map();
  for (const id of ids) {
    const key = keyOf(id);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(id);
  }
  return [...groups.entries()].filter(([, members]) => members.length > 1);
}

// ── the fixture really is a set of distinct, valid agents ────────────────────────────────────
{
  assert.equal(new Set(IDS).size, IDS.length, "the fixture must not repeat an id");
  for (const id of IDS) {
    assert.ok(SERVICE_ACCEPTS.test(id), `${id} would be REJECTED by the service; fixture drift`);
  }
}

// ── the hermes sanitiser collides, exactly here ──────────────────────────────────────────────
{
  const collisions = groupBy(IDS, hermesSanitize).map(([key, members]) => [key, members.sort()]);
  collisions.sort(([a], [b]) => a.localeCompare(b));
  assert.deepEqual(
    collisions,
    [["a-b-c", ["a-b-c", "a.b.c"]], ["team-coder", ["team-coder", "team.coder"]]],
    "the hermes sanitiser's collision set changed. If it GREW, more pairs of distinct agents now "
      + "share a gateway port, API key, daemon pid and hermes session. If it SHRANK, the fold was "
      + "changed — which repoints marker files on disk and is the migration both modules' notes "
      + "say this would be.",
  );
}

// ── the claude one is injective over the same inputs ─────────────────────────────────────────
{
  const collisions = groupBy(IDS, (id) => claudeSessionStorePath(id, "/tmp"));
  assert.deepEqual(
    collisions, [],
    "the claude session store now collides too. It keeps dots precisely so it does not, and it is "
      + "the reason the hermes fold is a CHOICE rather than something filenames force.",
  );
}

// ── how far the fold reaches: measured, not inferred from the marker names ───────────────────
{
  // The first version of this pin listed the five TEMP markers keyed by the sanitised name and
  // said the colliding agents therefore "share the session they resume". That was an inference
  // from a filename. Sweeping every exported agent-id derivation for injectivity showed the
  // SESSION KEY ITSELF folds — `sessionKeyFor` and `pinnedSessionId` both answer `aify-team-coder`
  // for two different agents — so the sharing is direct, not merely via a marker file.
  for (const derive of [sessionKeyFor, pinnedSessionId, loopReadyFile]) {
    assert.equal(
      derive("team.coder"), derive("team-coder"),
      `${derive.name} still folds two distinct agents together — pinned as a fact; if this now `
        + `differs, the sanitiser was changed and that is the migration both modules' notes describe`,
    );
    assert.notEqual(
      derive("team_coder"), derive("team-coder"),
      `${derive.name}: underscores must stay distinct, or the collision set is wider than pinned`,
    );
  }
}

// ── the port is a SEPARATE axis, and it is designed for ──────────────────────────────────────
{
  // `agentPort` is `PORT_BASE + fnv1a(id) % PORT_SPAN` over a 1000-port range, so two agents can
  // collide by HASH regardless of any sanitiser — at ~17% for a 20-agent fleet on the birthday
  // bound. That is handled: `resolveGatewayPort` probes forward for a port that is free AND not
  // claimed by another agent's persist file, then persists the choice. Recorded as examined so it
  // is not re-derived as a finding: the hash colliding is expected, not a defect.
  assert.notEqual(agentPort("team.coder"), agentPort("team-coder"),
    "the port hash reads the RAW id, so the sanitiser fold does not reach it");
  const port = agentPort("lc-coder");
  assert.ok(Number.isInteger(port) && port >= 8642 && port <= 9641, `port ${port} outside the range`);
}

// ── what the fold actually does, so the pin is readable ──────────────────────────────────────
{
  assert.equal(hermesSanitize("team.coder"), "team-coder");
  assert.equal(hermesSanitize("team-coder"), "team-coder");
  assert.equal(hermesSanitize("team_coder"), "team_coder", "underscores survive, so they do not fold");
  assert.equal(hermesSanitize("a..b"), "a-b", "a RUN of unsafe characters folds to one dash");
  assert.equal(hermesSanitize(".lead."), "lead", "leading and trailing dashes are trimmed");
  assert.equal(hermesSanitize(""), "");
}

// ── anti-vacuity ─────────────────────────────────────────────────────────────────────────────
{
  // The collision assertion would pass against a sanitiser that mapped EVERYTHING to one string,
  // and the claude one against a sanitiser that echoed its input. Both must be false.
  assert.ok(new Set(IDS.map(hermesSanitize)).size > 1, "the fold is not a constant");
  assert.notEqual(claudeSessionStorePath("a", "/tmp"), claudeSessionStorePath("b", "/tmp"));
}

console.log("agent-id-sanitiser-collision.test.js: all assertions passed");
