import { dispatchContent } from "./claude-channel-content.js";
import {
  ATTACH_POLL_MS,
  ATTACH_WAIT_MS,
  waitForActiveSession,
} from "./hermes-active-session.mjs";
import {
  MACHINE_ID,
  TMP_DIR,
} from "./hermes-env.mjs";
import {
  buildPromptSubmitFrame,
  buildRenderNoticeFrame,
  buildSessionActiveListFrame,
  buildSessionSteerFrame,
  isGatewaySessionWorking,
  isSessionBusyError,
  pickSessionStatusById,
} from "./hermes-gateway-protocol.js";
import {
  gatewayUnreachableMessage,
  isGatewayConnectRefused,
  reportGatewayDead,
  sleep,
  redactGatewayUrl,
} from "./hermes-gateway.mjs";
import {
  channelBridgeId,
  clearTurn,
  markRunDelivered,
  markRunFailed,
  markRunRequeued,
  reportTurnBusy,
} from "./hermes-run-reporting.mjs";

// Delivering ONE run, and the poll cycle that finds work — the per-iteration half of the managed
// hermes host.
//
// Split out of `hermes-managed-host.js` in v0.5.4 together with `hermes-delivery-loop.mjs`. One module
// would have been 998 lines, a fresh violation of the 1000-line rule, so the pair split on the real
// boundary: THIS module is what one iteration DOES, the loop module is what drives the iterations.
// The dependency runs loop -> run and never back.

const CLAIM_404_GRACE = Math.max(
  1,
  Number(process.env.AIFY_HERMES_CLAIM_404_GRACE || 3),
);

const EMPTY_ATTACH_FAIL_THRESHOLD = Math.max(
  1,
  Number(process.env.AIFY_HERMES_EMPTY_ATTACH_FAIL_THRESHOLD || 5),
);

export function noTuiAttachedMessage(gatewayUrl, attempts) {
  const url = redactGatewayUrl(gatewayUrl);
  const n = Number(attempts) || 0;
  return (
    `No visible hermes TUI attached to gateway ${url} ` +
    `(session.active_list empty across ${n} consecutive delivery attempts). ` +
    `The visible TUI is not a client of this gateway — relaunch this agent's ` +
    `hermes-aify session so the TUI attaches (HERMES_TUI_GATEWAY_URL) and can render messages.`
  );
}

/**
 * Why the loop is tearing a gateway down: it has had no attached session for long enough that the
 * visible TUI is gone rather than slow.
 *
 * THIS TEXT REACHES THE OPERATOR. It is POSTed as `reason` to /agents/{id}/resident-lost, and the
 * server writes it into `agents.status_note` -- truncated to 200 characters -- so it is read on the
 * dashboard, not just in a log.
 *
 * IT DELIBERATELY PROMISES NO STATUS CHANGE. The version this replaced said "Self-correcting off
 * 'available'", which is true for a RESIDENT and false for a MANAGED agent: the server rests a
 * managed worker at `status='active'`, which derives `available`, precisely so the next message can
 * cold-start a fresh session (`test_a_managed_worker_rests_COLD_STARTABLE_not_stopped`; resting it at
 * `stopped` was the 2026-07-06 defect that left a whole hermes team unwakeable). So the old sentence
 * was embedded, verbatim, in a status_note that went on to say "will cold-start a fresh session on
 * the next message" -- one field telling the operator two opposite things. Which state the agent
 * rests in is the SERVER's decision, made from session_mode; the bridge does not know it and must not
 * narrate it.
 */
export function noAttachedSessionTeardownMessage(gatewayUrl, cycles) {
  const url = redactGatewayUrl(gatewayUrl);
  const n = Number(cycles) || 0;
  return (
    `No visible TUI attached across ${n} poll cycles — relaunch this agent's hermes-aify ` +
    `session to reattach. Reaping the orphaned gateway host ${url}.`
  );
}

export function classifyClaimError(err, counter = { count: 0 }, { grace = CLAIM_404_GRACE } = {}) {
  const status = Number(err?.status);
  if (status === 410) {
    return { terminal: true, reason: "agent-removed" };
  }
  if (status === 404) {
    counter.count = (counter.count || 0) + 1;
    if (counter.count >= grace) {
      return { terminal: true, reason: "agent-removed" };
    }
    return { terminal: false };
  }
  // Any non-404 success or non-terminal error resets the 404 grace counter.
  counter.count = 0;
  return { terminal: false };
}

export async function deliverRun({
  run,
  agentId,
  httpCall,
  wsClient,
  rpcId,
  attachWaitMs = ATTACH_WAIT_MS,
  attachPollMs = ATTACH_POLL_MS,
  sleepImpl = sleep,
  // In-flight tracker (#172): the delivery loop owns a single object whose
  // { submittedAt, completed } fields gate the re-pulse beat. deliverRun stamps
  // submittedAt on a successful submit (opens the window) and zeroes it on
  // requeue/failure (closes it). `now` is injectable for tests.
  inFlight = null,
  now = Date.now,
  // The gateway WS URL this delivery is connecting through. Used only to build
  // an actionable failure message + drive the self-correct when the connect is
  // refused (dead ephemeral port). Optional; "" → message says "(unknown)".
  gatewayUrl = "",
  // Temp dir holding the agent's real-session-id marker (native-session-id
  // model). Defaults to the process temp dir; injectable so tests isolate the
  // marker read/write from the shared tmp dir.
  tempDir = TMP_DIR,
  // BOUNDED NO-ATTACH FAIL (Task 2.3). A mutable per-LOOP map { runId -> count }
  // of CONSECUTIVE empty-active_list requeues for the same run, owned by
  // runDeliveryLoop so it persists across poll cycles (the same run is re-claimed
  // each cycle after a requeue). When a run's count reaches `emptyAttachFailThreshold`
  // we markRunFailed with noTuiAttachedMessage (the visible TUI never attached to
  // this gateway) INSTEAD of requeuing forever. A successful attach/delivery deletes
  // the run's entry (resets the streak), so a slow-but-eventual cold start is never
  // failed. Optional — defaults to a throwaway map (single-shot callers/tests keep
  // the legacy infinite-requeue cold-start behaviour until the threshold trips).
  emptyAttachCounter = new Map(),
  emptyAttachFailThreshold = EMPTY_ATTACH_FAIL_THRESHOLD,
} = {}) {
  await reportTurnBusy(httpCall, agentId, { busy: true, runId: run?.id || "" }).catch(() => {});
  let id = typeof rpcId === "number" ? rpcId : Date.now() % 100000;
  // STALE-SESSION BIND-RACE GUARD: capture WHEN this delivery attempt began so
  // waitForActiveSession's most-recent fallback only binds a session that started
  // at/after this point — never a stale pre-relaunch session the reused gateway is
  // still listing while the fresh `hermes --tui` re-attaches.
  const deliveryStartedAt = now();
  try {
    // Re-discover the agent's REAL session id every delivery (native-session-id
    // model, 2026-06-03), WAITING (bounded) for the cold-start attach to finish.
    // waitForActiveSession resolves by the bound real id (marker) with a
    // most-recent-live-session fallback — never the synthetic `aify-<agentId>`.
    const sessionId = await waitForActiveSession({
      wsClient,
      agentId,
      tempDir,
      nextId: () => id++,
      deadlineMs: attachWaitMs,
      intervalMs: attachPollMs,
      sleepImpl,
      now,
      since: deliveryStartedAt,
    });
    if (!sessionId) {
      // No visible TUI session attached this attempt. Count CONSECUTIVE empties
      // for this run (the per-loop map persists across poll cycles, since the same
      // run is re-claimed each cycle after a requeue). Below the bounded threshold
      // this is a genuine cold-start → REQUEUE (claimable) so the next poll delivers
      // once the TUI finishes resuming. At/above the threshold the visible TUI is
      // NOT a client of this gateway (it never attached) → FAIL with an actionable
      // message mirrored to the sender, instead of silently requeuing forever
      // (Task 2.3 / the ci-9136 active_list=0 strand).
      const runKey = String(run?.id || "");
      const prior = runKey ? Number(emptyAttachCounter.get(runKey) || 0) : 0;
      const attempts = prior + 1;
      if (runKey) emptyAttachCounter.set(runKey, attempts);

      if (attempts >= emptyAttachFailThreshold) {
        const message = noTuiAttachedMessage(gatewayUrl, attempts);
        console.error(
          `[hermes-managed-host] run ${run?.id || "?"}: ${message} — FAILING (bounded no-attach after ${attempts} attempts).`,
        );
        await markRunFailed(httpCall, run, new Error(message)).catch(() => {});
        if (runKey) emptyAttachCounter.delete(runKey);
        if (inFlight) {
          inFlight.submittedAt = 0;
          inFlight.runId = "";
          inFlight.dispatchTurnOpen = false; // no delivered turn on this path → no detector turn-start credit
        }
        await clearTurn(httpCall, agentId).catch(() => {});
        return;
      }

      // Transient (cold start): TUI not attached yet → requeue so the next poll
      // delivers once it finishes resuming.
      console.error(
        `[hermes-managed-host] run ${run?.id || "?"}: agent '${agentId}' session did not attach within ${attachWaitMs}ms — requeuing (attempt ${attempts}/${emptyAttachFailThreshold}, will retry).`,
      );
      await markRunRequeued(
        httpCall,
        run,
        `agent '${agentId}' session not attached within ${attachWaitMs}ms (attempt ${attempts}/${emptyAttachFailThreshold})`,
      ).catch(() => {});
      // No delivery happened — clear the turn_busy pulse so the agent does not
      // falsely show "working" while the run sits requeued, and close the
      // in-flight window so the re-pulse beat stops.
      if (inFlight) {
        inFlight.submittedAt = 0;
        inFlight.runId = "";
        inFlight.dispatchTurnOpen = false; // no delivered turn on this path → no detector turn-start credit
      }
      await clearTurn(httpCall, agentId).catch(() => {});
      return;
    }
    // The visible TUI session DID attach — reset this run's no-attach streak so a
    // future transient empty starts the bounded budget fresh (never inherits a
    // stale count from a prior cold start that ultimately delivered).
    if (run?.id) emptyAttachCounter.delete(String(run.id));

    const text = dispatchContent(agentId, run || {});

    // #3 COMPLEMENT: draw the INBOUND message as a boxed notice in the operator's
    // visible TUI BEFORE submitting the turn. prompt.submit is fire-and-forget,
    // so without this the operator never sees WHAT arrived — only the agent's
    // eventual reply (rendered via the plugin's prompt.submit transport-tee). The
    // plugin registers aify.session.render_notice on the gateway; a gateway
    // WITHOUT the plugin answers `unknown method`, so this is best-effort — any
    // failure is swallowed so delivery never regresses on the notice.
    try {
      const sender = String(run?.from || "").trim();
      const subject = String(run?.subject || "").trim();
      const notice = [
        sender ? `Incoming from ${sender}` : "Incoming aify-comms message",
        subject ? `Re: ${subject}` : "",
        "",
        text,
      ]
        .filter((line, idx, arr) => line !== "" || (idx > 0 && arr[idx - 1] !== ""))
        .join("\n")
        .trim();
      await wsClient.request(
        buildRenderNoticeFrame({
          id: id++,
          sessionId,
          notice,
          status: sender ? `aify-comms · ${sender}` : "aify-comms",
        }),
      );
    } catch {
      /* plugin-less gateway or transient render failure — never block delivery */
    }

    let steered = false;
    let busyForSteer = false;
    if (run?.steerIfBusy) {
      try {
        const active = await wsClient.request(buildSessionActiveListFrame({ id: id++ }));
        const status = pickSessionStatusById(active, sessionId);
        if (isGatewaySessionWorking(status)) {
          busyForSteer = true;
          const result = await wsClient.request(buildSessionSteerFrame({ id: id++, sessionId, text }));
          steered = String(result?.status || result?.result?.status || "").toLowerCase() === "queued";
        }
      } catch {
        steered = false;
      }
    }

    if (busyForSteer && !steered) {
      await markRunRequeued(httpCall, run, "Hermes rejected the non-interrupting steer; retry after turn-end");
      return;
    }

    try {
      if (!steered) {
        await wsClient.request(buildPromptSubmitFrame({ id: id++, sessionId, text }));
      }
    } catch (err) {
      if (isSessionBusyError(err)) {
        await markRunRequeued(httpCall, run, "Hermes session became busy before prompt.submit; retry after turn-end");
        // 4009 proves another turn is active. Keep turn_busy latched so claim cannot race it again.
        if (inFlight) {
          inFlight.submittedAt = 0;
          inFlight.runId = "";
          inFlight.dispatchTurnOpen = false;
        }
        return;
      } else {
        throw err;
      }
    }

    await markRunDelivered(httpCall, run);
    if (steered) return;
    // SUCCESS: do NOT clear turn_busy. prompt.submit is FIRE-AND-FORGET (it
    // returns on accept, not turn completion), so the visible-TUI turn is only
    // just STARTING — clearing here loses the "working" signal for the entire
    // turn (operator-reported 2026-05-31: managed hermes never showed working).
    // Instead OPEN the in-flight re-pulse window (#172): stamp submittedAt so
    // the delivery loop's beat keeps turn_busy fresh past the 120s window for
    // the duration of a long managed turn (bounded by REPULSE_WINDOW_MS so a
    // missed completion can't stick `working` forever). The agent's own reply
    // (require_reply → _mark_dispatch_run_answered) clears it precisely on
    // completion. The blocking hermes-channel.js path uses a promise-anchored
    // beat instead because its chatStream runs the turn to completion inline.
    if (inFlight) {
      inFlight.submittedAt = now();
      inFlight.completed = false;
      // Track WHICH run opened this window so the re-pulse beat can poll that
      // run's status and detect the true turn-end (terminal status) — see
      // runDeliveryLoop's isInFlight probe. (#3)
      inFlight.runId = String(run?.id || "");
      // WS5 Task 5.2: re-arm the gateway idle-after-working guard for THIS turn.
      // The probe only reads `idle` as turn-END once it has observed `working`,
      // so a fresh submit must reset the flag (a stale true from a prior turn
      // could otherwise end the new turn on a momentary post-submit idle).
      inFlight.observedWorking = false;
      // Grant the detector's turn-start credit: a dispatched turn is now open, so
      // the gateway `working` this submit triggers IS a real turn (fire /turn-start).
      // Revoked by the detector's turn-END wrapper. See the inFlight declaration.
      inFlight.dispatchTurnOpen = true;
    }
  } catch (error) {
    // Gateway connect-refused (dead ephemeral port): fail the run with an
    // ACTIONABLE message instead of a raw ECONNREFUSED, and self-correct the
    // agent off `available` (resident-lost) so the next capability computation
    // stops accepting runs against the dead gateway. Narrow classifier — a
    // mid-stream / RPC error falls through to the normal failure path.
    if (isGatewayConnectRefused(error)) {
      const message = gatewayUnreachableMessage(gatewayUrl);
      console.error(`[hermes-managed-host] run ${run?.id || "?"} delivery failed: ${message}`);
      await markRunFailed(httpCall, run, new Error(message)).catch(() => {});
      await reportGatewayDead({ httpCall, agentId, gatewayUrl, reason: message }).catch(() => {});
      if (inFlight) {
        inFlight.submittedAt = 0;
        inFlight.runId = "";
        inFlight.dispatchTurnOpen = false; // no delivered turn on this path → no detector turn-start credit
      }
      await clearTurn(httpCall, agentId).catch(() => {});
      return;
    }
    console.error(
      `[hermes-managed-host] run ${run?.id || "?"} delivery failed:`,
      error?.message || String(error),
    );
    await markRunFailed(httpCall, run, error).catch(() => {});
    // Delivery failed → not working. Close the in-flight window and clear the
    // pulse we set above.
    if (inFlight) {
      inFlight.submittedAt = 0;
      inFlight.runId = "";
      inFlight.dispatchTurnOpen = false; // symmetry with the other failure paths (2026-07-10 review F4)
    }
    await clearTurn(httpCall, agentId).catch(() => {});
  }
}

export async function runPollCycle({
  agentId,
  machineId = MACHINE_ID,
  bridgeId = channelBridgeId(agentId),
  httpCall,
  wsClient,
  maxBatch = 20,
  // In-flight tracker passed through to deliverRun so a submitted turn opens
  // the re-pulse window (#172).
  inFlight = null,
  // Gateway WS URL threaded to deliverRun for actionable connect-refused
  // failures + the gateway-dead self-correct.
  gatewayUrl = "",
  // Consecutive-404 self-heal counter (owned by the loop; persists across
  // cycles). `{ count }`. Optional — defaults to a fresh per-cycle counter.
  claimErrorCounter = { count: 0 },
  // Temp dir holding the agent's real-session-id marker (native-session-id
  // model). Threaded to deliverRun so the loop and tests agree on its location.
  tempDir = TMP_DIR,
  // BOUNDED NO-ATTACH FAIL counter (Task 2.3), owned by runDeliveryLoop so it
  // persists across poll cycles. Threaded straight through to deliverRun.
  emptyAttachCounter = new Map(),
  // Attach-window timing + sleep seam, forwarded verbatim to deliverRun. The
  // production loop passes none of these, so they default to the module
  // ATTACH_* constants and the real sleep — behaviour is unchanged. Tests inject
  // a no-op sleep / tiny deadline so the poll-cycle path doesn't wait the full
  // 25s attach window.
  attachWaitMs = ATTACH_WAIT_MS,
  attachPollMs = ATTACH_POLL_MS,
  sleepImpl = sleep,
} = {}) {
  let processed = 0;
  let released = false;
  let terminal = null;
  let claimOk = false;
  try {
    for (let i = 0; i < maxBatch; i++) {
      let claim;
      try {
        claim = await httpCall("POST", "/dispatch/claim", {
          agentId,
          machineId,
          bridgeId,
          // Standalone channel sidecar (NOT a wrapper-PTY child): the service gate
          // accepts a channel-sidecar claim for managed hermes the same way it
          // accepts claude's.
          bridgeKind: "channel-sidecar",
          executionModes: ["channel", "resident"],
        });
        // A successful claim round-trip resets the 404 grace counter and marks
        // the loop a LIVE CLAIMER (drives the ready-marker — Task 1.4).
        claimErrorCounter.count = 0;
        claimOk = true;
      } catch (claimErr) {
        const cls = classifyClaimError(claimErr, claimErrorCounter);
        if (cls.terminal) {
          console.error(
            `[hermes-managed-host] /dispatch/claim ${claimErr?.status} for '${agentId}' is TERMINAL (${cls.reason}); tearing down + exiting.`,
          );
          terminal = cls.reason;
          break;
        }
        // Transient (WS/connect/RPC/5xx, or pre-grace 404): swallow + retry on
        // the next cycle, preserving the loop's existing behaviour.
        console.error("[hermes-managed-host] poll cycle claim error:", claimErr?.message || String(claimErr));
        break;
      }
      // Phase H1 (status v2): the agent was explicitly DISABLED (server returns
      // a terminal `stopped` body on a SUCCESSFUL claim). Treat it like
      // agent-removed (TERMINAL → teardown + procExit(0) in runDeliveryLoop) so
      // the orphan worker + gateway reap themselves. UNLIKE agent-removed,
      // `stopped` is REVERSIBLE: the terminal handler skips the
      // agent-removed-only marker/session-binding clears (gated on
      // reason === "agent-removed"), so a re-enable + relaunch resumes the same
      // session. This is a success-body signal, NOT a claim error, so it is
      // handled here on the success path — not in classifyClaimError.
      if (claim?.stopped) {
        console.error(
          `[hermes-managed-host] agent '${agentId}' is stopped (disabled); helper tearing down + exiting.`,
        );
        terminal = "agent-stopped";
        break;
      }
      // Mode FSM release: operator switched this agent to resident — stop driving.
      if (claim?.release) {
        console.error(
          `[hermes-managed-host] released: agent '${agentId}' switched to resident; helper stopping.`,
        );
        released = true;
        break;
      }
      const run = claim?.run;
      const mode = String(run?.executionMode || "").trim().toLowerCase();
      if (!run || !["channel", "resident"].includes(mode)) break;
      await deliverRun({ run, agentId, httpCall, wsClient, inFlight, gatewayUrl, tempDir, emptyAttachCounter, attachWaitMs, attachPollMs, sleepImpl });
      processed++;
    }
  } catch (error) {
    console.error("[hermes-managed-host] poll cycle error:", error?.message || String(error));
  }
  return terminal ? { processed, released, terminal, claimOk } : { processed, released, claimOk };
}
