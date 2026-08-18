// The three callbacks the hermes gateway turn-detector fires back at the delivery loop. Extracted
// from `hermes-delivery-loop.mjs` (v0.6 Phase 1) with the bodies byte-identical.
//
// WHY THESE THREE ARE WORTH REACHING, and it is not coverage for its own sake — each one is the fix for
// a named incident, and none of them had a test:
//
//   * `postTurnStart` threads the OPEN run id. Without it the detector's busy beat overwrote
//     `agent_turn_state.turn_run_id` with "" (the server does `turn_run_id = excluded.turn_run_id` on
//     every busy beat), which raced the delivery pulse, dropped the run linkage, and deadlocked the
//     reply reminders (2026-07-10 review).
//   * `postTurnEnd` revokes the dispatched-turn credit AND closes the re-pulse window, so hermes'
//     POST-TURN background work cannot re-fire `/turn-start` — the status flap (2026-07-10 review F1).
//   * `shouldFireTurnStart` is the gate that scopes the detector to dispatched turns at all.
//
// They were unreachable by construction: built inside `runDeliveryLoop` and handed to
// `startHermesGatewayTurnDetector`, so firing one meant a live gateway and a live service. The
// coverage census lists eleven never-called functions in that file; these are three of them.
//
// `httpCall` is a PARAMETER, as it already was for `reportTurnBusy`/`clearTurn` — the reporters take
// the transport rather than importing one, which is what makes this bundle assertable without a server.

import { shouldApplyGatewayTurnEnd } from "./hermes-gateway.mjs";
import { clearTurn, reportTurnBusy } from "./hermes-run-reporting.mjs";

/**
 * @param {object}   args
 * @param {object}   args.inFlight  the loop's mutable per-delivery state (mutated in place)
 * @param {Function} args.httpCall  the transport the reporters call through
 * @param {string}   args.id        the agent id
 */
export function buildGatewayTurnCallbacks({ inFlight, httpCall, id }) {
  return {
    // SET working on a gateway-running turn (edge-triggered). Thread the OPEN run
    // id: shouldFireTurnStart gates this to dispatchTurnOpen===true, in which state
    // inFlight.runId IS the open run — so the detector's busy beat can no longer
    // overwrite agent_turn_state.turn_run_id with '' (the server does turn_run_id =
    // excluded.turn_run_id on every busy beat), which had raced the makeInFlightPulse
    // beat and dropped the run linkage → the reply-reminder deadlock (2026-07-10 review).
    postTurnStart: () => {
      inFlight.observedWorking = true;
      return reportTurnBusy(httpCall, id, { busy: true, runId: inFlight.runId || "" }).catch(() => {});
    },
    // CLEAR on sustained idle — authoritative /turn-end, only ever clears. Also
    // REVOKES the dispatched-turn credit AND closes the re-pulse probe window: this
    // turn is over, so (a) a subsequent gateway `working` (hermes POST-TURN background
    // self-improvement/memory) must not re-fire /turn-start (the flap), and (b) the
    // SEPARATE makeInFlightProbe/makeInFlightPulse beat — which keeps re-pulsing a
    // `delivered`+require_reply=1 run whose reply STRANDED, at its slow 45s×3=135s idle
    // cadence — must stop re-asserting turn_busy on this fast (≈9s) detector turn-end.
    // Setting inFlight.completed makes shouldManagedHostRepulse skip; a new delivery
    // re-arms completed=false, so the next turn tracks normally (2026-07-10 review F1).
    postTurnEnd: () => {
      if (!shouldApplyGatewayTurnEnd(inFlight)) return;
      inFlight.dispatchTurnOpen = false;
      inFlight.completed = true;
      return clearTurn(httpCall, id).catch(() => {});
    },
    // GATE the detector's /turn-start (edge + keep-alive): fire only while a dispatched
    // turn is open. The instant delivery pulse (makeInFlightPulse) remains the primary
    // setter for a real turn; this detector start is the continuous backstop, now scoped
    // to dispatched turns so post-turn background gateway "running" can't flap `working`.
    shouldFireTurnStart: () => inFlight.dispatchTurnOpen === true,
  };
}
