// What happens to a run that REQUIRED a reply and ended without one.
//
// Two policies, chosen by the `managed_reply_capture_fallback` service setting:
//
//   fallback ON  — mirror the agent's final output as the reply, so a managed turn that answered in its
//                  console still closes the run
//   strict       — do NOT fabricate a reply. Record a handoff event saying the reply is still owed and
//                  leave the run visibly unanswered, because a missing reply that looks answered is worse
//                  than one that looks missing
//
// The setting is read through a 5-second cache, and the cache deliberately serves STALE on failure: this
// runs at turn end, and a settings fetch that fails must not decide the policy by accident.
//
// Extracted from server.js in v0.5.4 as a measured group — three declarations whose whole external surface
// is `httpCall`, `autoReplySubjectForRun` and `autoReplyBodyForRun`, all already imported there.
// `docs/JS_SERVER_REMAINDER_PACKET.md` measured the remainder per function and found ~105 lines across
// five unrelated subjects; that criterion is the one that wrongly shelved `hermes-managed-host.js` and
// `app.js`, and its correction is appended to that packet.
//
// `_replyCaptureFallbackCache` moves with them because all four of its references are inside
// `readReplyCaptureFallback` — it is that function's private memo, not shared state.
//
// Bodies are byte-identical to those in server.js; the only substitution is the added `export `.

export async function ensureRequiredReplyHandoff(agentId, run = {}, terminalStatus = "completed", detailText = "") {
  if (!run?.id || !run?.from) return;
  try {
    const latest = await httpCall("GET", `/dispatch/runs/${encodeURIComponent(run.id)}`);
    const current = latest?.run || {};
    if (!current.requireReply || current.resultMessageId) return;

    // Strict reply mode (managed_reply_capture_fallback=false): do NOT mirror
    // final output as the reply. The agent is expected to answer via
    // comms_send(inReplyTo); leave the run reply-owed and visible so a missing
    // reply is surfaced, not fabricated from working/telemetry text.
    if (!(await readReplyCaptureFallback())) {
      await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
        appendEvent: `Run ended without an explicit comms_send reply; strict reply mode does not auto-mirror. Reply still owed to ${run.from}.`,
        eventType: "handoff",
      });
      return;
    }

    const body = {
      from_agent: agentId,
      to: run.from,
      type: terminalStatus === "failed" ? "error" : "response",
      subject: autoReplySubjectForRun(run, terminalStatus),
      body: autoReplyBodyForRun(run, terminalStatus, detailText),
      priority: run.priority || "normal",
      trigger: false,
      // Deterministic idempotency key (#240): this owed-reply handoff is the highest-value
      // victim of a dropped send — a transient socket error here strands the require_reply
      // run. Keying the nonce on the run id lets httpCall retry the POST safely, and also
      // dedups if the handoff fires more than once for the same run.
      clientNonce: `handoff-${run.id}-${terminalStatus}`,
    };
    const replyParent = current.messageId || current.inReplyTo || "";
    if (replyParent) body.inReplyTo = replyParent;

    const sent = await httpCall("POST", "/messages/send", body);
    await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
      resultMessageId: sent?.messageId || "",
      appendEvent: `Auto-mirrored result to ${run.from} because no explicit reply message was sent during the run.`,
      eventType: "handoff",
    });
  } catch (error) {
    try {
      const latest = await httpCall("GET", `/dispatch/runs/${encodeURIComponent(run.id)}`);
      if (latest?.run?.resultMessageId) return;
      await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
        appendEvent: `Run ended without an explicit reply. Auto-mirror to ${run.from} failed: ${error?.message || error}`,
        eventType: "handoff",
      });
    } catch {
      // best effort
    }
  }
}

import { httpCall } from "./aify-service-endpoint.mjs";
import { autoReplyBodyForRun, autoReplySubjectForRun } from "./tool-response-format.mjs";

export let _replyCaptureFallbackCache = { fetchedAt: 0, value: true };
export async function readReplyCaptureFallback() {
  if (Date.now() - _replyCaptureFallbackCache.fetchedAt < 5000) {
    return _replyCaptureFallbackCache.value;
  }
  try {
    const resp = await httpCall("GET", "/settings");
    const s = (resp && resp.settings) ? resp.settings : (resp || {});
    const v = s.managed_reply_capture_fallback;
    const value = v === undefined || v === null ? true : !!v;
    _replyCaptureFallbackCache = { fetchedAt: Date.now(), value };
    return value;
  } catch (_) {
    return _replyCaptureFallbackCache.value; // best-effort: stale cache
  }
}
