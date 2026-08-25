// Proves EVERY extraction from hermes-managed-host.js was a pure file split, and exercises the moved surface.
//
// ONE PROOF PER TARGET FILE, not per slice — the app.js lane's shape, and this file learned it the hard way.
// It began as the gateway slice's receipt, pinning the exact env import line the gateway added. The very next
// slice added TMP_DIR to that line and this proof went red: correct detection, wrong granularity. A per-slice
// proof is only runnable until the next slice touches the same lines. So both slices are declared below and
// each new one appends.
//
// Two obligations, and they are different. The reconstruction below proves nothing MOVED that was not
// declared — put EVERY span the `EXTRACTIONS` table declares back, undo the import edits, and require
// byte-identity with a tracked pre-slice fixture. The unit tests after it prove the moved code still
// answers correctly, and they deliberately assert things `hermes-managed-host.test.js` does not: nearly
// every host export already had executing importers, so a new file full of assertions the old suite
// already makes would add a file and no evidence.
//
// NO COUNTS IN THIS HEADER, deliberately (2026-08-18). It used to say "the 23 extracted spans" and "26 of
// the 27 host exports". Every slice appends to `EXTRACTIONS`, so both numbers were stale within days and
// were well past wrong when a reviewer on another instance flagged them — while the gate itself was green,
// because nothing asserts prose. A number in a comment is a claim with no test behind it; the invariant
// ("every declared span") is true after every future slice without anyone maintaining it. Same class as
// the suite counts corrected in CLAUDE.md, and as `3f1e043`.
//
// The prover lives in service/new_dashboard/extraction-proof.mjs because that is where it was built for the
// app.js lane. It is imported across trees rather than copied — a second copy of a proof is how two proofs
// come to disagree about what they prove.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  declarationSpan,
  reconstruct,
} from "../../../service/new_dashboard/extraction-proof.mjs";

import {
  MAX_REENSURE_WITHOUT_RECOVERY,
  gatewayIndexUrlFromWs,
  gatewayUnreachableMessage,
  isGatewayConnectRefused,
  nextReEnsureBudget,
  shouldApplyGatewayTurnEnd,
  sleep,
} from "../hermes-gateway.mjs";
import { HERMES_CMD, MACHINE_ID, RUNTIME } from "../hermes-env.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => fs.readFileSync(path.join(HERE, "..", rel), "utf-8");

const PRISTINE = "fixtures/hermes-managed-host.before-gateway.js";

const GATEWAY = "hermes-gateway.mjs";
const ENV = "hermes-env.mjs";
const SESSION = "hermes-active-session.mjs";
const REPORTING = "hermes-run-reporting.mjs";
const INFLIGHT = "hermes-inflight.mjs";
const AIFYHTTP = "aify-http.mjs";
const DELIVERY_LOOP = "hermes-delivery-loop.mjs";
const DELIVERY_RUN = "hermes-delivery-run.mjs";

/** Indices are 0-based positions in the PRISTINE fixture, measured from it. */
const EXTRACTIONS = [
  {
    module: ENV,
    items: [
      { name: "MACHINE_ID", at: 104, marker: "// MACHINE_ID moved to ./hermes-env.mjs in v0.5.4." },
      { name: "RUNTIME", at: 164, marker: "// RUNTIME moved to ./hermes-env.mjs in v0.5.4." },
      { name: "HERMES_CMD", at: 198, marker: "// HERMES_CMD moved to ./hermes-env.mjs in v0.5.4." },
    ],
  },
  {
    module: GATEWAY,
    items: [
      { name: "READY_TIMEOUT_MS", at: 120, marker: "// READY_TIMEOUT_MS moved to ./hermes-gateway.mjs in v0.5.4." },
      { name: "RPC_TIMEOUT_MS", at: 121, marker: "// RPC_TIMEOUT_MS moved to ./hermes-gateway.mjs in v0.5.4." },
      { name: "GATEWAY_PROBE_TIMEOUT_MS", at: 246, marker: "// GATEWAY_PROBE_TIMEOUT_MS moved to ./hermes-gateway.mjs in v0.5.4." },
      { name: "sleep", at: 251, marker: "// sleep moved to ./hermes-gateway.mjs in v0.5.4." },
      { name: "scrapeToken", at: 375, marker: "// scrapeToken moved to ./hermes-gateway.mjs in v0.5.4." },
      { name: "waitForIndexToken", at: 389, marker: "// waitForIndexToken moved to ./hermes-gateway.mjs in v0.5.4." },
      { name: "ensureGatewayHost", at: 429, marker: "// ensureGatewayHost moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "MAX_REENSURE_WITHOUT_RECOVERY", at: 658, marker: "// MAX_REENSURE_WITHOUT_RECOVERY moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "nextReEnsureBudget", at: 663, marker: "// nextReEnsureBudget moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "maybeReEnsureGatewayHost", at: 669, marker: "// maybeReEnsureGatewayHost moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "openGatewayWsClient", at: 812, marker: "// openGatewayWsClient moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "isGatewayConnectRefused", at: 979, marker: "// isGatewayConnectRefused moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      {
        name: "gatewayUnreachableMessage", at: 1002, marker: "// gatewayUnreachableMessage moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true,
        // v0.7: the gateway URL is redacted before it reaches operator-facing text; the token in
        // its query string was being stored by the control plane and served back over the API.
        editedSince: [{
          was: ["  const url = String(gatewayUrl || \"\").trim() || \"(unknown)\";"],
          now: ["  const url = redactGatewayUrl(gatewayUrl);"],
        }],
      },
      { name: "reportGatewayDead", at: 1037, marker: "// reportGatewayDead moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "gatewayIndexUrlFromWs", at: 1067, marker: "// gatewayIndexUrlFromWs moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "makeGatewayReachabilityProbe", at: 1086, marker: "// makeGatewayReachabilityProbe moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "shouldApplyGatewayTurnEnd", at: 1717, marker: "// shouldApplyGatewayTurnEnd moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "_teardownState", at: 1864, marker: "// _teardownState moved to ./hermes-gateway.mjs in v0.5.4." },
      { name: "teardownGatewayHost", at: 1868, marker: "// teardownGatewayHost moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "makeTeardown", at: 1888, marker: "// makeTeardown moved to ./hermes-gateway.mjs in v0.5.4 — teardown is that module's subject.", pristineExported: true },
      { name: "installShutdownTeardown", at: 1930, marker: "// installShutdownTeardown moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
    ],
  },
  {
    module: SESSION,
    items: [
      { name: "ATTACH_WAIT_MS", at: 128, marker: "// ATTACH_WAIT_MS moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "ATTACH_POLL_MS", at: 129, marker: "// ATTACH_POLL_MS moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "ATTACH_FRESH_GRACE_FRACTION", at: 158, marker: "// ATTACH_FRESH_GRACE_FRACTION moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "activeListRowsLocal", at: 258, marker: "// activeListRowsLocal moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "rowFreshnessStamp", at: 274, marker: "// rowFreshnessStamp moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "rowRealIdLocal", at: 291, marker: "// rowRealIdLocal moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "stampForSessionId", at: 311, marker: "// stampForSessionId moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "sessionKeyFor", at: 325, marker: "// sessionKeyFor moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "ensureStableSession", at: 755, marker: "// ensureStableSession moved to ./hermes-active-session.mjs in v0.5.4.", pristineExported: true },
      { name: "defaultWriteActiveSessionFile", at: 300, marker: "// defaultWriteActiveSessionFile moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "waitForActiveSession", at: 1155, marker: "// waitForActiveSession moved to ./hermes-active-session.mjs in v0.5.4.", pristineExported: true },
      { name: "startResumeMarkerSync", at: 2034, marker: "// startResumeMarkerSync moved to ./hermes-active-session.mjs in v0.5.4.", pristineExported: true },
      { name: "runResolveSessionCli", at: 2791, marker: "// runResolveSessionCli moved to ./hermes-active-session.mjs in v0.5.4.", pristineExported: true },
    ],
  },
  {
    module: AIFYHTTP,
    items: [
      { name: "coerceLoopbackToIPv4", at: 95, marker: "// coerceLoopbackToIPv4 moved to ./aify-http.mjs in v0.5.4." },
      { name: "AIFY_SERVER_URL", at: 99, marker: "// AIFY_SERVER_URL moved to ./aify-http.mjs in v0.5.4." },
      { name: "AIFY_API_KEY", at: 102, marker: "// AIFY_API_KEY moved to ./aify-http.mjs in v0.5.4." },
      { name: "HTTP_TIMEOUT_MS", at: 119, marker: "// HTTP_TIMEOUT_MS moved to ./aify-http.mjs in v0.5.4." },
      { name: "makeAifyHttpCall", at: 342, marker: "// makeAifyHttpCall moved to ./aify-http.mjs in v0.5.4." },
    ],
  },
  {
    module: INFLIGHT,
    items: [
      { name: "REPULSE_MS", at: 174, marker: "// REPULSE_MS moved to ./hermes-inflight.mjs in v0.5.4." },
      { name: "TURN_START_TIMEOUT_MS", at: 175, marker: "// TURN_START_TIMEOUT_MS moved to ./hermes-inflight.mjs in v0.5.4." },
      { name: "REPULSE_WINDOW_MS", at: 194, marker: "// REPULSE_WINDOW_MS moved to ./hermes-inflight.mjs in v0.5.4." },
      { name: "fetchRunStatus", at: 919, marker: "// fetchRunStatus moved to ./hermes-inflight.mjs in v0.5.4." },
      { name: "makeInFlightProbe", at: 1595, marker: "// makeInFlightProbe moved to ./hermes-inflight.mjs in v0.5.4.", pristineExported: true },
      { name: "makeInFlightPulse", at: 1731, marker: "// makeInFlightPulse moved to ./hermes-inflight.mjs in v0.5.4.", pristineExported: true },
    ],
  },
  {
    module: REPORTING,
    items: [
      { name: "CHANNEL_BRIDGE_PREFIX", at: 110, marker: "// CHANNEL_BRIDGE_PREFIX moved to ./hermes-run-reporting.mjs in v0.5.4 with its only reader." },
      { name: "channelBridgeId", at: 111, marker: "// channelBridgeId moved to ./hermes-run-reporting.mjs in v0.5.4." },
      { name: "reportTurnBusy", at: 895, marker: "// reportTurnBusy moved to ./hermes-run-reporting.mjs in v0.5.4." },
      { name: "clearTurn", at: 904, marker: "// clearTurn moved to ./hermes-run-reporting.mjs in v0.5.4." },
      { name: "markRunDelivered", at: 933, marker: "// markRunDelivered moved to ./hermes-run-reporting.mjs in v0.5.4." },
      { name: "markRunFailed", at: 946, marker: "// markRunFailed moved to ./hermes-run-reporting.mjs in v0.5.4." },
      { name: "markRunRequeued", at: 1125, marker: "// markRunRequeued moved to ./hermes-run-reporting.mjs in v0.5.4." },
    ],
  },
  {
    module: ENV,
    items: [
      { name: "TMP_DIR", at: 163, marker: "// TMP_DIR moved to ./hermes-env.mjs in v0.5.4." },
      // resolveHermesPython went to the SESSION module first and moved here a slice later, once the reviewer
      // asked whether the members were session-identity or merely session-adjacent. The plan records where a
      // body lives NOW, not where it passed through — a plan that tracked the journey would have to be
      // rewritten every time an owner was corrected, which is the opposite of what it is for.
      //
      // THE CARRIER'S MARKER DID NOT SAY THAT, and this comment recording the second hop is precisely why
      // it went unnoticed: the fact was written down HERE while the marker a reader actually follows still
      // pointed at hermes-active-session.mjs, which no longer has the symbol. Found by
      // tests/moved-names-resolve.test.js the first time it checked destinations. The marker now carries
      // the hop, in the `then on to` form the Python gate established, and this plan pins the new text.
      { name: "resolveHermesPython", at: 726, marker: "// resolveHermesPython moved to ./hermes-active-session.mjs in v0.5.4, then on to ./hermes-env.mjs.", pristineExported: true },
    ],
  },
  {
    // v0.5.4 delivery split, into TWO modules because together they are 998 lines. Spans are
    // DECLARATION-ONLY: the prover reads each body with `declarationSpan`, which stops at the
    // declaration, so a span carrying its leading comments cannot round-trip. Those comments stay in
    // the host.
    module: DELIVERY_RUN,
    items: [
      { name: "EMPTY_ATTACH_FAIL_THRESHOLD", at: 143, marker: null },
      {
        name: "noTuiAttachedMessage", at: 1018, marker: null, pristineExported: true,
        // v0.7: the gateway URL is redacted before it reaches operator-facing text; the token in
        // its query string was being stored by the control plane and served back over the API.
        editedSince: [{
          was: ["  const url = String(gatewayUrl || \"\").trim() || \"(unknown)\";"],
          now: ["  const url = redactGatewayUrl(gatewayUrl);"],
        }],
      },
      { name: "deliverRun", at: 1309, marker: null, pristineExported: true },
      { name: "runPollCycle", at: 1757, marker: null, pristineExported: true },
      { name: "CLAIM_404_GRACE", at: 1967, marker: null },
      { name: "classifyClaimError", at: 1977, marker: null, pristineExported: true },
    ],
  },
  {
    module: DELIVERY_LOOP,
    items: [
      { name: "POLL_MS", at: 115, marker: null },
      { name: "GATEWAY_TURN_POLL_MS", at: 186, marker: null },
      { name: "GATEWAY_TURN_IDLE_DEBOUNCE", at: 190, marker: null },
      { name: "GATEWAY_PROBE_MS", at: 205, marker: null },
      { name: "GATEWAY_PROBE_THRESHOLD", at: 209, marker: null },
      { name: "NO_TUI_TEARDOWN_CYCLES", at: 226, marker: null },
      { name: "NO_TUI_GRACE_MS", at: 240, marker: null },
      {
        name: "runDeliveryLoop",
        at: 2084,
        marker: null,
        pristineExported: true,
        // v0.6 Phase 1: the three gateway turn-detector callbacks moved OUT of this already-extracted
        // module into ./hermes-turn-detector-callbacks.mjs. Declared here because the reconstruction
        // puts this span back byte-for-byte, so an undeclared edit to an extracted body reads as "a
        // slice changed something it did not declare" — which is exactly what the gate is for.
        //
        // `now` is the WHOLE replaced block, comments included. Declaring only the code line left the
        // comment lines undeclared when I did this on the app.js side, and the reconstruction still
        // differed until the comments were part of the entry.
        editedSince: [
          {
            // v0.7: the gateway credential stopped travelling with these messages, and two of them stopped
            // claiming a status change the server does not make for a managed agent. Both templates became
            // calls to builders in their owning modules, which is where the redaction and the 200-char
            // status_note budget can be tested.
            was: [
              "  // gateway dead ONCE (resident-lost) so the agent stops showing `available`",
            ],
            now: [
              "  // gateway dead ONCE (resident-lost) — which for a MANAGED agent rests it",
              "  // cold-startable rather than taking it off `available`, the server's call —",
            ],
          },
          {
            was: [
              "        `Hermes gateway unreachable at ${host.wsUrl} after ${consecutiveFailures} consecutive ` +",
              "          `liveness probes; the gateway host likely died. Self-correcting off 'available' (resident-lost).`,",
            ],
            now: [
              "        gatewayUnreachableAfterProbesMessage(host.wsUrl, consecutiveFailures),",
            ],
          },
          {
            was: [
              "              `Hermes gateway at ${host.wsUrl} has had NO attached session (no visible TUI / ` +",
              "                `non-loop WS client) across ${noTuiCycles} consecutive poll cycles; the operator's ` +",
              "                `terminal was likely closed/killed. Self-correcting off 'available' (resident-lost) ` +",
              "                `and reaping the orphaned gateway host.`,",
            ],
            now: [
              "              noAttachedSessionTeardownMessage(host.wsUrl, noTuiCycles),",
            ],
          },
          {
            was: [
              "    // SET working on a gateway-running turn (edge-triggered). Thread the OPEN run",
              "    // id: shouldFireTurnStart gates this to dispatchTurnOpen===true, in which state",
              "    // inFlight.runId IS the open run \u2014 so the detector's busy beat can no longer",
              "    // overwrite agent_turn_state.turn_run_id with '' (the server does turn_run_id =",
              "    // excluded.turn_run_id on every busy beat), which had raced the makeInFlightPulse",
              "    // beat and dropped the run linkage \u2192 the reply-reminder deadlock (2026-07-10 review).",
              "    postTurnStart: () => {",
              "      inFlight.observedWorking = true;",
              "      return reportTurnBusy(httpCall, id, { busy: true, runId: inFlight.runId || \"\" }).catch(() => {});",
              "    },",
              "    // CLEAR on sustained idle \u2014 authoritative /turn-end, only ever clears. Also",
              "    // REVOKES the dispatched-turn credit AND closes the re-pulse probe window: this",
              "    // turn is over, so (a) a subsequent gateway `working` (hermes POST-TURN background",
              "    // self-improvement/memory) must not re-fire /turn-start (the flap), and (b) the",
              "    // SEPARATE makeInFlightProbe/makeInFlightPulse beat \u2014 which keeps re-pulsing a",
              "    // `delivered`+require_reply=1 run whose reply STRANDED, at its slow 45s\u00d73=135s idle",
              "    // cadence \u2014 must stop re-asserting turn_busy on this fast (\u22489s) detector turn-end.",
              "    // Setting inFlight.completed makes shouldManagedHostRepulse skip; a new delivery",
              "    // re-arms completed=false, so the next turn tracks normally (2026-07-10 review F1).",
              "    postTurnEnd: () => {",
              "      if (!shouldApplyGatewayTurnEnd(inFlight)) return;",
              "      inFlight.dispatchTurnOpen = false;",
              "      inFlight.completed = true;",
              "      return clearTurn(httpCall, id).catch(() => {});",
              "    },",
              "    // GATE the detector's /turn-start (edge + keep-alive): fire only while a dispatched",
              "    // turn is open. The instant delivery pulse (makeInFlightPulse) remains the primary",
              "    // setter for a real turn; this detector start is the continuous backstop, now scoped",
              "    // to dispatched turns so post-turn background gateway \"running\" can't flap `working`.",
              "    shouldFireTurnStart: () => inFlight.dispatchTurnOpen === true,",
            ],
            now: [
              "    // postTurnStart / postTurnEnd / shouldFireTurnStart moved to",
              "    // ./hermes-turn-detector-callbacks.mjs in v0.6 Phase 1. Each is the fix for a named 2026-07-10",
              "    // incident \u2014 the turn_run_id race that deadlocked reply reminders, and the post-turn status",
              "    // flap \u2014 and none of them had a test, because firing one needed a live gateway.",
              "    ...buildGatewayTurnCallbacks({ inFlight, httpCall, id }),",
            ],
          },
        ],
      },
    ],
  },
];

const MODULES = () => ({
  [GATEWAY]: read(GATEWAY),
  [ENV]: read(ENV),
  [SESSION]: read(SESSION),
  [REPORTING]: read(REPORTING),
  [INFLIGHT]: read(INFLIGHT),
  [AIFYHTTP]: read(AIFYHTTP),
  [DELIVERY_LOOP]: read(DELIVERY_LOOP),
  [DELIVERY_RUN]: read(DELIVERY_RUN),
});

// Import lines the extractions added, in their CURRENT form. The env line is shared by both slices — the
// gateway created it, the session slice added TMP_DIR to it — so it is pinned once, as it stands now.
const IMPORT_EDITS = [
  {
    // The delivery split. Pinned as a BLOCK so a reworded comment fails here rather than leaving
    // prose in the reconstruction. NO leading blank element: `reconstruct` locates a block with
    // `lines.indexOf(block[0])`, and an empty first entry matches the file's FIRST blank line.
    addedBlock: [
      "// v0.5.4: the delivery loop and the per-run work moved to ./hermes-delivery-loop.mjs and",
      "// ./hermes-delivery-run.mjs — 998 lines together, which one module could not hold without a fresh",
      "// violation of the 1000-line rule. The CLI entry points below stay here and call in.",
      'import { runDeliveryLoop } from "./hermes-delivery-loop.mjs";',
    ],
  },
  {
    // The env import became a multi-line block when resolveHermesPython joined it, so it is pinned as one.
    addedBlock: [
      "import {  // v0.5.4: neutral owner",
      "  TMP_DIR,",
      '} from "./hermes-env.mjs";',
    ],
  },
];


// The 74 bindings the dead-import sweep removed, as reported by the gate's own detector in
// `no-dead-imports.test.js` -- not by a second copy of the rule. They are all in the ORIGINAL import
// region (the slice-added blocks above are removed wholesale, so what the sweep took out of THOSE never
// reaches this comparison), and every one is present in the pristine fixture.
const SWEPT_IMPORTS = new Set([
  "AIFY_API_KEY", "AIFY_SERVER_URL", "ATTACH_POLL_MS", "ATTACH_WAIT_MS", "DEFAULT_IDLE_DEBOUNCE_TICKS",
  "HERMES_CMD", "MACHINE_ID", "MAX_REENSURE_WITHOUT_RECOVERY", "REPULSE_MS", "REPULSE_WINDOW_MS",
  "RUNTIME", "activeListRowsLocal", "buildPromptSubmitFrame", "buildRenderNoticeFrame",
  "buildSessionActiveListFrame", "buildSessionListFrame", "buildSessionSteerFrame", "channelBridgeId",
  "clearLoopReady", "clearTurn", "defaultClearGatewayMarkers", "defaultClearSessionMarker",
  "defaultKillByPort", "defaultMachineId", "dispatchContent", "fs", "gatewayIndexUrlFromWs",
  "gatewayUnreachableMessage", "installShutdownTeardown", "isGatewayConnectRefused",
  "isGatewaySessionIdle", "isGatewaySessionWorking", "isSessionBusyError", "isTuiDepsBuildFailure",
  "isUsableSessionId", "makeAifyHttpCall", "makeGatewayReachabilityProbe", "makeInFlightProbe",
  "makeInFlightPulse", "makeTeardown", "markRunDelivered", "markRunFailed", "markRunRequeued",
  "maybeReEnsureGatewayHost", "nextReEnsureBudget", "nodeSpawnSync", "os", "pickMostRecentSession",
  "pickMostRecentSessionRow", "pickSessionById", "pickSessionRowById", "pickSessionStatusById",
  "pickSessionStatusForKey", "pinnedSessionId", "readGatewayUrlMarker", "readSessionIdMarker",
  "reportGatewayDead", "reportTurnBusy", "resolveHermesPython", "rowResumeKey", "sessionKeyFor",
  "shouldApplyGatewayTurnEnd", "shouldLatchComplete", "shouldManagedHostRepulse", "sleep",
  "startGatewayLivenessProbe", "startHermesGatewayTurnDetector", "startInFlightRepulse",
  "startLivenessHeartbeat", "startResumeMarkerSync", "tuiDepsBuildFailureMessage", "waitForActiveSession",
  "writeLoopReady", "writeSessionIdMarker",
]);

// The import region, in either file: from the first `import` to the `loadSettingsEnv()` call that follows
// the block. Anchoring on a line that exists in BOTH files is the point -- the earlier version of this
// helper anchored on an opener comment that every slice shares, took the wrong block, and then could not
// find the block it had just deleted.
function importRegion(lines) {
  const start = lines.findIndex((l) => l.startsWith("import "));
  const end = lines.indexOf("loadSettingsEnv();");
  assert.ok(start >= 0, "no import line found");
  assert.ok(end > start, "loadSettingsEnv() must follow the import block");
  return [start, end];
}

// Split a region into import blocks so a dropped `import {` or `} from "x";` can be judged: a structural
// line may only disappear when every NAME in its block disappeared too.
function blockOf(region, i) {
  if (/^\}\s*from/.test(region[i])) {
    let open = i;
    while (open >= 0 && !/^import\s*\{/.test(region[open])) open -= 1;
    return [open, i];
  }
  if (/^import\s*\{\s*(\/\/.*)?$/.test(region[i])) {
    let close = i;
    while (close < region.length && !/^\}\s*from/.test(region[close])) close += 1;
    return [i, close];
  }
  return null;
}

function namesIn(line) {
  const block = /^\s+(\w+(?:\s+as\s+\w+)?),?\s*$/.exec(line);
  if (block) return [block[1].split(/\s+as\s+/).pop()];
  const named = /^import\s*\{([^}]*)\}\s*from\s*"[^"]+";\s*$/.exec(line);
  if (named) {
    return named[1].split(",").map((s) => s.trim()).filter(Boolean)
      .map((s) => s.split(/\s+as\s+/).pop().trim());
  }
  const def = /^import\s+(\w+)\s+from\s+"[^"]+";\s*$/.exec(line);
  if (def) return [def[1]];
  return null;
}

// Put the swept imports back, and CHECK the claim while doing it.
//
// The restore is wholesale -- the pristine's own region -- but it is not blind. The swept region must be a
// SUBSEQUENCE of the pristine's (the sweep only ever deleted whole lines; it rewrote none), and every
// pristine line missing from it must be explained by a name the gate reported dead. So an import changed
// for any other reason, or a line reordered, still fails here rather than being papered over by the
// restore.
function restoreSweptImports(lines, pristineLines) {
  const [start, end] = importRegion(lines);
  const [pStart, pEnd] = importRegion(pristineLines);
  const swept = lines.slice(start, end);
  const pristine = pristineLines.slice(pStart, pEnd);

  const dropped = [];
  let k = 0;
  for (let i = 0; i < pristine.length; i += 1) {
    if (k < swept.length && swept[k] === pristine[i]) k += 1;
    else dropped.push(i);
  }
  assert.equal(k, swept.length,
    "the swept import region is not a subsequence of the pristine one, so a line was changed or reordered "
    + "rather than merely removed");

  const droppedSet = new Set(dropped);
  for (const i of dropped) {
    const names = namesIn(pristine[i]);
    if (names) {
      for (const n of names) {
        assert.ok(SWEPT_IMPORTS.has(n),
          `import "${n}" vanished from the host but is not one of the names the sweep reported dead`);
      }
      continue;
    }
    const block = blockOf(pristine, i);
    assert.ok(block, `unexplained line removed from the import region: ${pristine[i]}`);
    for (let j = block[0]; j <= block[1]; j += 1) {
      assert.ok(droppedSet.has(j),
        `the block line "${pristine[i]}" was removed while "${pristine[j]}" survived, so the block did not `
        + "lose all of its names");
    }
  }

  return [...lines.slice(0, start), ...pristine, ...lines.slice(end)];
}

function hostWithoutSliceImports() {
  const lines = read("hermes-managed-host.js").split(String.fromCharCode(10));
  for (const edit of IMPORT_EDITS) {
    const block = edit.addedBlock ?? [edit.added];
    const at = lines.indexOf(block[0]);
    assert.notEqual(at, -1, `import line not found verbatim: ${block[0]}`);
    for (let k = 1; k < block.length; k += 1) {
      assert.equal(lines[at + k], block[k], `import block line ${k} does not match`);
    }
    lines.splice(at, block.length);
  }
  // The gateway import is a multi-line block; find and drop it as a unit.
  // Find each block by its TERMINATOR and walk BACK to the opener. My first version took the first
  // `import {  // v0.5.4: moved out` line and assumed it belonged to the module it was looking for; the two
  // blocks are not in that order in the file, so it deleted the session block while hunting for the gateway
  // one and then could not find the session block it had just removed. Anchoring on the unique line is the
  // fix — the opener comment is identical for every slice and therefore cannot identify one.
  // Only two of the five slice-added blocks survive the dead-import sweep: every name imported from
  // ./hermes-run-reporting.mjs, ./hermes-inflight.mjs and ./aify-http.mjs was used ONLY by the delivery
  // code, so those blocks went with it. They are not listed because they are not there -- a proof that
  // demanded them would fail on the sweep having worked, which is the shape of gate this series has
  // already had to fix four times.
  for (const from of [
    '} from "./hermes-gateway.mjs";',
    '} from "./hermes-active-session.mjs";',
  ]) {
    const close = lines.findIndex((l) => l.startsWith(from));
    assert.notEqual(close, -1, `the import block ending in ${from} must be present verbatim`);
    let open = close;
    while (open >= 0 && !lines[open].startsWith("import {  // v0.5.4: moved out")) open -= 1;
    assert.ok(open >= 0, `no opener found for the block ending in ${from}`);
    lines.splice(open, close - open + 1);
  }
  const pristineLines = read(PRISTINE).split(String.fromCharCode(10));
  return restoreSweptImports(lines, pristineLines).join(String.fromCharCode(10));
}

test("hermes-managed-host.js reconstructs byte-identically from the two extracted modules", () => {
  const rebuilt = reconstruct({
    after: hostWithoutSliceImports(),
    modules: MODULES(),
    extractions: EXTRACTIONS,
  });
  assert.equal(
    rebuilt,
    read(PRISTINE),
    "reconstruction differs from the pre-slice host, so the split changed something outside its declared spans",
  );
});

test("the reconstruction fixture is TRACKED, not ignored", () => {
  // A proof that needs .git does not run from `git archive`; a fixture matched by .gitignore does not exist
  // on a clean clone. Both mistakes have shipped in this repo before.
  const full = path.join(HERE, "..", PRISTINE);
  assert.ok(fs.existsSync(full), `${PRISTINE} must exist beside the bridge`);
});

test("every declared pristineExported item really is exported in its module", () => {
  // The prover cross-checks this too, but asserting it here names the offender instead of failing a 3,000
  // line byte comparison.
  for (const step of EXTRACTIONS) {
    const source = MODULES()[step.module];
    for (const item of step.items) {
      const span = declarationSpan(source, item.name);
      assert.ok(span, `${item.name} not found in ${step.module}`);
      if (item.pristineExported) {
        assert.match(span.text, /^export\s/, `${item.name} is declared pristineExported but is not exported`);
      }
    }
  }
});

// ---------------------------------------------------------------- moved surface, executed

test("gatewayIndexUrlFromWs converts a ws URL to the http index it scrapes", () => {
  assert.equal(gatewayIndexUrlFromWs("ws://127.0.0.1:8123/ws"), "http://127.0.0.1:8123/");
  assert.equal(gatewayIndexUrlFromWs("wss://host:9/ws"), "https://host:9/");
});

test("gatewayIndexUrlFromWs returns empty for input it cannot convert", () => {
  // The caller uses the result as a URL; '' is checked, a malformed string would be fetched.
  for (const value of ["", null, undefined, "not a url"]) {
    assert.equal(gatewayIndexUrlFromWs(value), "", `unexpected for ${String(value)}`);
  }
});

test("isGatewayConnectRefused recognises the refusal shapes a dead gateway produces", () => {
  assert.equal(isGatewayConnectRefused(new Error("connect ECONNREFUSED 127.0.0.1:8123")), true);
  assert.equal(isGatewayConnectRefused({ code: "ECONNREFUSED" }), true);
});

test("isGatewayConnectRefused does NOT claim an unrelated error is a refusal", () => {
  // A false positive here reports a live gateway dead and tears it down.
  assert.equal(isGatewayConnectRefused(new Error("socket hang up")), false);
  assert.equal(isGatewayConnectRefused(null), false);
  assert.equal(isGatewayConnectRefused(undefined), false);
});

test("nextReEnsureBudget spends on a re-ensure and refills on a recovery", () => {
  assert.equal(nextReEnsureBudget(3, { reEnsured: true }), 2, "a re-ensure costs one");
  assert.equal(nextReEnsureBudget(1, { recovered: true }), MAX_REENSURE_WITHOUT_RECOVERY, "recovery refills");
  assert.equal(nextReEnsureBudget(2, {}), 2, "neither event leaves it alone");
});

test("nextReEnsureBudget never goes below zero", () => {
  // The budget gates a relaunch loop; a negative would keep comparing as truthy-negative and relaunch forever.
  assert.equal(nextReEnsureBudget(0, { reEnsured: true }), 0);
  assert.equal(nextReEnsureBudget(-5, { reEnsured: true }), 0);
});

test("gatewayUnreachableMessage names the gateway URL so the operator can check it", () => {
  const msg = gatewayUnreachableMessage("ws://127.0.0.1:8123/ws");
  assert.match(msg, /8123/, "the message must carry the port that failed");
  assert.equal(typeof msg, "string");
});

test("shouldApplyGatewayTurnEnd suppresses a turn-end only while a dispatch turn is open and unobserved", () => {
  // I guessed a (sessionA, sessionB) signature and wrote a passing-looking test for a function that takes
  // ONE object. Reading it was the fix. The real rule: a gateway turn-end applies unless a dispatch turn is
  // open and no working state has been observed yet — that window is where an early turn-end would close a
  // run the agent has not actually started.
  assert.equal(shouldApplyGatewayTurnEnd({ dispatchTurnOpen: true, observedWorking: false }), false,
    "an open dispatch turn with nothing observed must NOT be ended by the gateway");
  assert.equal(shouldApplyGatewayTurnEnd({ dispatchTurnOpen: true, observedWorking: true }), true,
    "once working has been observed the turn-end is real");
  assert.equal(shouldApplyGatewayTurnEnd({ dispatchTurnOpen: false }), true,
    "with no dispatch turn open there is nothing to protect");
  assert.equal(shouldApplyGatewayTurnEnd(), true, "the default argument must not suppress a turn-end");
  assert.equal(shouldApplyGatewayTurnEnd({}), true);
});

test("sleep resolves after roughly the requested delay", async () => {
  const started = Date.now();
  await sleep(20);
  assert.ok(Date.now() - started >= 15, "sleep must actually wait");
});

// ---------------------------------------------------------------- the neutral env module

test("hermes-env exposes the identity constants both sides read", () => {
  assert.equal(RUNTIME, "hermes", "the runtime name is what agents are registered under");
  assert.equal(typeof HERMES_CMD, "string");
  assert.ok(HERMES_CMD.length > 0, "an empty hermes command would spawn nothing");
  assert.equal(typeof MACHINE_ID, "string");
  assert.ok(MACHINE_ID.length > 0, "an empty machine id breaks same-host claim matching");
});

/** The modules a file IMPORTS — parsed, not grepped. */
function importedModules(source) {
  return [...source.matchAll(/^import\s[\s\S]*?from\s*"([^"]+)"\s*;/gm)].map((m) => m[1]);
}

test("hermes-env imports neither the gateway nor the host", () => {
  // The whole reason this module exists: a constant with readers on both sides must live in neither.
  //
  // Asserted on parsed IMPORTS, not on the file text. My first version grepped for the substring and failed
  // against correct code, because both new modules NAME hermes-managed-host.js in their header comments
  // explaining what they were extracted from. Substring-versus-structure, for the umpteenth time in this
  // series: a mention is not a dependency.
  const mods = importedModules(read(ENV));
  assert.ok(!mods.some((m) => m.includes("hermes-gateway")), `env imports the gateway: ${mods}`);
  assert.ok(!mods.some((m) => m.includes("hermes-managed-host")), `env imports the host: ${mods}`);
});

test("hermes-gateway does not import the host it was extracted from", () => {
  // The dependency inversion this series exists to prevent, asserted rather than assumed.
  const mods = importedModules(read(GATEWAY));
  assert.ok(!mods.some((m) => m.includes("hermes-managed-host")), `gateway imports the host: ${mods}`);
  assert.ok(mods.some((m) => m.includes("hermes-env")), "the gateway must take its identity constants from the neutral module");
});
