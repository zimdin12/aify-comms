// Is this agent's turn still in flight? The probe that decides, and the timings that bound the answer.
//
// Extracted from `hermes-managed-host.js` in v0.5.4, the last cluster before the delivery loop itself. Three
// functions, 147 lines: a probe that watches gateway session state and run status to decide whether the agent
// is still working, and the pulse that keeps that answer fresh.
//
// WHY THIS IS DELICATE, and why it is worth having in one file with its timings. Getting "in flight" wrong is
// wrong in both directions and both are bad: conclude a turn ended while the agent is still working and the
// next dispatch lands mid-thought; conclude it is still working after it stopped and the turn-busy flag never
// clears, which is how an agent goes permanently deaf. The probe therefore does not trust a single signal — it
// reads gateway idle/working state AND the run's own status, and debounces idle before believing it.
//
// THE THREE TIMINGS MOVED WITH IT because they exist to bound exactly that decision:
//   TURN_START_TIMEOUT_MS  how long to wait for a turn to actually start before giving up on it
//   REPULSE_MS             how often to re-assert that a turn is still in flight
//   REPULSE_WINDOW_MS      the outer bound on re-asserting, so a stuck probe cannot hold a turn open forever
// `REPULSE_MS` and `REPULSE_WINDOW_MS` are also read by `runDeliveryLoop`, so the host imports them back.
// That is the owner rule rather than a two-sided-reader rule: repulse timing is what makes an in-flight
// answer trustworthy, and this is the module that produces that answer.
//
// NOT here: `hermes-turn-repulse.js` owns the repulse DECISIONS (`shouldManagedHostRepulse`,
// `shouldLatchComplete`, `startInFlightRepulse`) and takes its interval as a PARAMETER — it is deliberately
// timing-agnostic, so putting env-derived windows there would contradict its shape. Decisions live there,
// the timings that feed them live here, and the probe imports the decisions.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run and the wrappers relaunch.

import { DEFAULT_IDLE_DEBOUNCE_TICKS } from "./hermes-gateway-turn-detector.js";
import {
  isGatewaySessionIdle,
  isGatewaySessionWorking,
} from "./hermes-gateway-protocol.js";
import { reportTurnBusy } from "./hermes-run-reporting.mjs";
import { shouldLatchComplete, shouldManagedHostRepulse } from "./hermes-turn-repulse.js";

export const REPULSE_MS = Math.max(5000, Number(process.env.AIFY_HERMES_TURN_REPULSE_MS || 45000));
const TURN_START_TIMEOUT_MS = Math.max(
  REPULSE_MS,
  Number(process.env.AIFY_HERMES_TURN_START_TIMEOUT_MS || 90_000),
);
export const REPULSE_WINDOW_MS = Math.max(
  REPULSE_MS,
  Number(process.env.AIFY_HERMES_TURN_REPULSE_WINDOW_MS || 15 * 60 * 1000),
);


async function fetchRunStatus(httpCall, runId) {
  const id = String(runId || "").trim();
  if (!id) return { status: "", requireReply: false };
  try {
    const resp = await httpCall("GET", `/dispatch/runs/${encodeURIComponent(id)}`);
    return {
      status: String(resp?.run?.status || "").trim(),
      requireReply: !!resp?.run?.requireReply,
    };
  } catch {
    return { status: "", requireReply: false };
  }
}


export function makeInFlightProbe({
  inFlight,
  serverUrl,
  httpCall,
  maxWindowMs = REPULSE_WINDOW_MS,
  fetchStatus = (runId) => fetchRunStatus(httpCall, runId),
  readGatewayStatus = null,
  clearTurnImpl = null,
  failRunImpl = null,
  startTimeoutMs = TURN_START_TIMEOUT_MS,
  now = Date.now,
  // DEBOUNCE (fix/hermes-working-debounce): require N CONSECUTIVE gateway-idle
  // reads before latching the turn-end. The hermes gateway session["running"]
  // flag flips False MID-TURN (between tool calls / generation gaps), so a
  // SINGLE idle read is NOT a real turn boundary — latching on it false-cleared
  // turn_busy mid-turn → the working↔online flap. Any "working" read resets the
  // streak. Tunable; defaults to the flap-safe DEFAULT_IDLE_DEBOUNCE_TICKS.
  idleDebounce = DEFAULT_IDLE_DEBOUNCE_TICKS,
} = {}) {
  const idleThreshold = Math.max(1, Number(idleDebounce) || DEFAULT_IDLE_DEBOUNCE_TICKS);
  return async function isInFlight() {
    if (!serverUrl || !inFlight) return false;
    if (
      !shouldManagedHostRepulse({
        submittedAt: inFlight.submittedAt,
        completed: inFlight.completed,
        maxWindowMs,
      })
    ) {
      return false;
    }
    // Primary turn-END: observe the gateway's own session["running"] state.
    if (typeof readGatewayStatus === "function") {
      let gwStatus = "";
      try {
        gwStatus = String((await readGatewayStatus()) || "");
      } catch {
        gwStatus = ""; // gateway hiccup → treat as not-idle; fall through.
      }
      if (isGatewaySessionWorking(gwStatus)) {
        inFlight.observedWorking = true;
        inFlight.idleStreak = 0; // a working read resets the idle streak (no flap).
      }
      if (
        !inFlight.observedWorking &&
        isGatewaySessionIdle(gwStatus) &&
        now() - Number(inFlight.submittedAt || 0) >= startTimeoutMs &&
        typeof failRunImpl === "function"
      ) {
        const runId = inFlight.runId;
        const submittedAt = inFlight.submittedAt;
        const run = await fetchStatus(runId);
        if (run.status === "delivered" && run.requireReply) {
          let freshGatewayStatus = "";
          try {
            freshGatewayStatus = String((await readGatewayStatus()) || "");
          } catch {
            return true; // unknown is never evidence that a turn failed to start.
          }
          if (isGatewaySessionWorking(freshGatewayStatus)) inFlight.observedWorking = true;
          if (
            inFlight.runId !== runId ||
            inFlight.submittedAt !== submittedAt ||
            inFlight.observedWorking ||
            !isGatewaySessionIdle(freshGatewayStatus)
          ) {
            return true;
          }
          const error = new Error(
            `Hermes accepted prompt.submit but no gateway turn started within ${startTimeoutMs}ms`,
          );
          try {
            await failRunImpl(runId, error);
          } catch {
            return true; // transient server failure: retry the fail on the next probe.
          }
          inFlight.completed = true;
          inFlight.runId = "";
          inFlight.dispatchTurnOpen = false;
          inFlight.idleStreak = 0;
          await clearTurnImpl?.();
          return false;
        }
      }
      // idle is the turn-end ONLY after we've seen working (submit-race guard)
      // AND only once a SUSTAINED run of idle reads confirms it (debounce). A
      // momentary mid-turn idle blip increments but never reaches the threshold,
      // and the next working read zeroes it — so it can never false-clear.
      if (inFlight.observedWorking && isGatewaySessionIdle(gwStatus)) {
        inFlight.idleStreak = (Number(inFlight.idleStreak) || 0) + 1;
        if (inFlight.idleStreak >= idleThreshold) {
          inFlight.completed = true; // latch: gateway sustained idle → turn ended.
          inFlight.runId = "";
          inFlight.observedWorking = false;
          inFlight.idleStreak = 0;
          // Defense-in-depth (2026-07-10): revoke the detector's turn-start credit on
          // THIS turn-end path too (the continuous detector also revokes it, usually
          // sooner). Keeps dispatchTurnOpen false after ANY observed turn-end so post-
          // turn background gateway "working" can never re-fire the flap.
          inFlight.dispatchTurnOpen = false;
          if (typeof clearTurnImpl === "function") {
            // Authoritative /turn-end: clear turn_busy NOW, not on the 120s window.
            await clearTurnImpl();
          }
          return false;
        }
        // Below threshold: still in-flight, keep re-pulsing (no premature clear).
        return true;
      }
    }
    // Backstop turn-END: the in-flight run reaching a terminal status (the agent
    // self-replied, or the run failed/cancelled/stopped) — covers a dropped idle.
    const { status, requireReply } = await fetchStatus(inFlight.runId);
    if (shouldLatchComplete({ status, requireReply })) {
      inFlight.completed = true; // latch: observed turn-end → stop the beat.
      inFlight.runId = "";
      return false;
    }
    return true;
  };
}


export function makeInFlightPulse({
  httpCall,
  agentId,
  inFlight,
  reportTurnBusyImpl = reportTurnBusy,
} = {}) {
  return async function pulse() {
    await reportTurnBusyImpl(httpCall, agentId, {
      busy: true,
      runId: (inFlight && inFlight.runId) || "",
    });
  };
}
