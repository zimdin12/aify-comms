// Helpers extracted from createCodexControllerLegacy in runtimes.js
// (Plan 3 Task 11). Keeping the legacy controller body under the 500-line
// rule by hoisting the bigger blocks here:
//
//   - createCodexLegacyTimers — overall timeout + quiet-stall + aify-mcp-tool
//     stall timers, all wired to the same settle/reject path.
//   - resolveActiveCodexThread — thread/resume + heal-via-import + heal-via-
//     fresh-start logic.

import {
  importCodexThreadRollout,
  terminateProcessTree,
  describeCodexItem,
} from "../runtimes-helpers.js";
import { detectCodexResumeFailure } from "../codex-errors.js";

/**
 * Build the JSON-RPC notification handler for the legacy codex controller.
 * Tracks turn/started, turn/completed, item/started, item/completed, and
 * item/agentMessage/delta events, updating shared state via `ctx` and
 * pushing visible frames through `pushTerminalFrame`.
 *
 * `ctx` exposes (read/write): activeTurnId, finalText, finalStatus, finalError,
 * settled. It also exposes (read-only): activeItems (Map), callbacks, onRefs.
 */
export function buildCodexNotificationHandler({ ctx, pushTerminalFrame, markActivity }) {
  return function handleNotification(message) {
    markActivity(message.method || "runtime notification");
    const params = message.params || {};
    const { callbacks, activeItems } = ctx;
    if (message.method === "turn/started" && params.turn?.id) {
      ctx.activeTurnId = params.turn.id;
      callbacks.onRefs?.({ turnId: ctx.activeTurnId });
      callbacks.onEvent?.("turn", `Started turn ${ctx.activeTurnId}`);
      pushTerminalFrame(`\r\n\x1b[96m\x1b[1m▶ turn started\x1b[0m\r\n`);
    } else if (message.method === "turn/completed") {
      ctx.finalStatus = params.turn?.status || "completed";
      if (params.turn?.error?.message) {
        ctx.finalError = params.turn.error.message;
      }
      const usage = params.turn?.usage || params.usage;
      const usageStr = usage && (usage.input_tokens || usage.output_tokens)
        ? ` \x1b[2m(in=${usage.input_tokens || 0} out=${usage.output_tokens || 0})\x1b[0m`
        : "";
      pushTerminalFrame(`\r\n\x1b[36m\x1b[1m■ turn ended\x1b[0m${usageStr}\r\n`);
      if (ctx.finalStatus === "completed" || ctx.finalStatus === "interrupted" || ctx.finalStatus === "failed") {
        ctx.settled = true;
      }
    } else if (message.method === "item/agentMessage/delta") {
      const delta = params.delta || "";
      if (delta) {
        ctx.finalText += delta;
        pushTerminalFrame(String(delta));
      }
    } else if (message.method === "item/completed" && params.item?.type === "agentMessage") {
      ctx.finalText = params.item.text || ctx.finalText;
      if (params.item?.id) activeItems.delete(params.item.id);
    } else if (message.method === "item/started" && params.item?.id) {
      const itemType = describeCodexItem(params.item);
      activeItems.set(params.item.id, { label: itemType, startedAt: Date.now() });
      callbacks.onEvent?.("codex", `Started ${itemType}`);
      pushTerminalFrame(`\r\n\x1b[33m→ ${itemType}\x1b[0m\r\n`);
    } else if (message.method === "item/completed" && params.item?.id) {
      const itemType = activeItems.get(params.item.id)?.label || describeCodexItem(params.item);
      activeItems.delete(params.item.id);
      callbacks.onEvent?.("codex", `Completed ${itemType}`);
      pushTerminalFrame(`\x1b[32m✓ ${itemType}\x1b[0m\r\n`);
    } else if (message.method === "error" && params.error?.message) {
      ctx.finalError = params.error.message;
      pushTerminalFrame(`\r\n\x1b[31m\x1b[1m✗ error\x1b[0m \x1b[31m${params.error.message}\x1b[0m\r\n`);
    }
  };
}

/**
 * Build the three timers used by the legacy codex controller:
 *   - timer        : absolute timeout (timeoutMs)
 *   - quietTimer   : quiet-stall guard (quietTimeoutMs)
 *   - mcpToolTimer : aify-comms MCP tool stall guard (aifyMcpToolTimeoutMs)
 *
 * Each timer, on fire, terminates the runtime process tree, closes the RPC
 * client, and rejects the promise via the supplied `fail()` callback. They
 * are cleared together via the returned `clearAll()`.
 */
export function createCodexLegacyTimers({
  ctx,
  timeoutMs,
  quietTimeoutMs,
  aifyMcpToolTimeoutMs,
  fail,
}) {
  let quietTimer = null;
  let mcpToolTimer = null;

  const timer = setTimeout(() => {
    if (!ctx.settled) {
      clearInterval(quietTimer);
      clearInterval(mcpToolTimer);
      try { terminateProcessTree(ctx.proc); } catch {}
      try { ctx.rpc?.close?.(); } catch {}
      fail(new Error(`Codex run timed out after ${timeoutMs}ms`));
    }
  }, timeoutMs);

  if (quietTimeoutMs > 0) {
    quietTimer = setInterval(() => {
      // Outer try-catch: a setInterval callback that throws becomes an
      // uncaughtException in Node — would crash the bridge. Code-review
      // B-C2 (2026-05-22).
      try {
        if (ctx.settled) return;
        const idleFor = Date.now() - ctx.lastActivityAt;
        if (idleFor < quietTimeoutMs) return;
        const activeLabel = ctx.activeItems.size
          ? ` Active Codex item(s): ${[...new Set([...ctx.activeItems.values()].map((item) => item.label))].join(", ")}.`
          : "";
        const message =
          `Codex run produced no runtime activity for ${quietTimeoutMs}ms after ${ctx.activityLabel}.` +
          activeLabel +
          ` The turn was treated as stalled and terminated. Retry the message, or restart/recover the session if this repeats.`;
        ctx.finalStatus = "failed";
        ctx.finalError = message;
        ctx.settled = true;
        clearTimeout(timer);
        clearInterval(quietTimer);
        clearInterval(mcpToolTimer);
        try { ctx.callbacks.onEvent?.("stalled", message); } catch {}
        try { terminateProcessTree(ctx.proc); } catch {}
        try { ctx.rpc?.close?.(); } catch {}
        fail(new Error(message));
      } catch (_) {
        // best-effort: never let a setInterval throw crash the bridge
      }
    }, Math.min(60 * 1000, Math.max(10 * 1000, Math.floor(quietTimeoutMs / 6))));
  }

  if (aifyMcpToolTimeoutMs > 0) {
    mcpToolTimer = setInterval(() => {
      try {
        if (ctx.settled) return;
        const now = Date.now();
        const stuck = [...ctx.activeItems.values()].find((item) => (
          ctx.isAifyCommsMcpToolItem(item.label) && now - item.startedAt >= aifyMcpToolTimeoutMs
        ));
        if (!stuck) return;
        const message =
          `Codex aify-comms MCP tool call produced no completion for ${aifyMcpToolTimeoutMs}ms. ` +
          `The turn was terminated before the general quiet-stall timeout. Retry the message after the bridge is updated/restarted; if it repeats, inspect the aify-comms MCP server logs.`;
        ctx.finalStatus = "failed";
        ctx.finalError = message;
        ctx.settled = true;
        clearTimeout(timer);
        clearInterval(quietTimer);
        clearInterval(mcpToolTimer);
        try { ctx.callbacks.onEvent?.("mcp_tool_stalled", message); } catch {}
        try { terminateProcessTree(ctx.proc); } catch {}
        try { ctx.rpc?.close?.(); } catch {}
        fail(new Error(message));
      } catch (_) {
        // best-effort: never let a setInterval throw crash the bridge
      }
    }, Math.min(10 * 1000, Math.max(2 * 1000, Math.floor(aifyMcpToolTimeoutMs / 6))));
  }

  return {
    timer,
    quietTimer,
    mcpToolTimer,
    clearAll() {
      clearTimeout(timer);
      if (quietTimer) clearInterval(quietTimer);
      if (mcpToolTimer) clearInterval(mcpToolTimer);
    },
  };
}

/**
 * Resolve the codex thread for the current dispatch. Either:
 *   - thread/start a fresh one (no saved thread id), or
 *   - thread/resume the saved id with full heal logic:
 *       * heal via rollout import (managed, found in another codex home)
 *       * heal via fresh thread (when allowFreshContext)
 *       * otherwise throw a clear, instructive error
 *
 * Returns the resolved active threadId. Updates callbacks.onSessionHandleChange
 * when a heal-via-fresh-thread replaces the saved handle.
 */
export async function resolveActiveCodexThread({
  rpc,
  startThread,
  initialThreadId,
  executionMode,
  agentId,
  allowFreshContext,
  managedCodexHome,
  callbacks,
  markActivity,
}) {
  let activeThreadId = initialThreadId || null;

  if (!activeThreadId) {
    if (executionMode === "resident") {
      throw new Error(
        `Resident Codex session "${agentId}" has no bound thread ID. Re-register from the live Codex session or provide sessionHandle explicitly.`,
      );
    }
    callbacks.onEvent?.("thread", `No thread bound yet; calling thread/start`);
    try {
      activeThreadId = await startThread();
    } catch (error) {
      throw new Error(
        `Codex thread/start failed for fresh thread: ${error?.message || error}`,
        { cause: error },
      );
    }
    return activeThreadId;
  }

  callbacks.onEvent?.("thread", `Attempting thread/resume for ${activeThreadId}`);
  try {
    const resumed = await rpc.request("thread/resume", {
      threadId: activeThreadId,
      personality: "friendly",
    }, 60000);
    activeThreadId = resumed.thread?.id || activeThreadId;
    return activeThreadId;
  } catch (error) {
    // Classification lives in detectCodexResumeFailure so it can be unit-
    // tested without a live Codex.
    const failure = detectCodexResumeFailure(error);
    const resumeMessage = String(error?.message || "").trim();
    if (!failure.shouldHeal) {
      // Unknown error — surface it with the step name so the dashboard run
      // log tells us exactly which RPC call failed.
      throw new Error(
        `Codex thread/resume failed for thread ${activeThreadId} with unhandled error: ${resumeMessage}`,
        { cause: error },
      );
    }

    let resumedAfterImport = false;
    if (executionMode === "managed" && failure.noRollout && managedCodexHome) {
      const imported = importCodexThreadRollout({
        threadId: activeThreadId,
        targetHome: managedCodexHome,
      });
      if (imported.imported) {
        callbacks.onEvent?.(
          "thread",
          `Imported Codex rollout for ${activeThreadId} from ${imported.sourceHome}; retrying thread/resume`,
        );
        try {
          const resumed = await rpc.request("thread/resume", {
            threadId: activeThreadId,
            personality: "friendly",
          }, 60000);
          activeThreadId = resumed.thread?.id || activeThreadId;
          callbacks.onEvent?.(
            "thread",
            `Resumed imported Codex thread ${activeThreadId} (${imported.rollouts.length} rollout file(s), ${imported.shellSnapshots.length} shell snapshot(s))`,
          );
          markActivity("thread/resume imported rollout");
          resumedAfterImport = true;
        } catch (retryError) {
          throw new Error(
            `Codex thread/resume failed for saved thread ${activeThreadId} after importing its rollout from ${imported.sourceHome}: ` +
            `${retryError?.message || retryError}`,
            { cause: retryError },
          );
        }
      }
    }

    if (resumedAfterImport) {
      // The native rollout was found in another Codex home and the retry
      // succeeded. Keep the saved handle unchanged and continue.
      return activeThreadId;
    }

    if (!allowFreshContext) {
      throw new Error(
        `Codex thread/resume failed for saved thread ${activeThreadId} (${failure.healReason}: ${resumeMessage}). ` +
        `The bridge did not create a fresh thread because that would discard native chat memory. ` +
        `Use Dashboard -> Sessions -> Recreate only when you intentionally want a new context.`,
        { cause: error },
      );
    }

    // Only explicit fresh-context requests may create a replacement thread.
    // Ordinary restart/recovery must fail loudly instead of silently
    // discarding native chat memory.
    const previousThreadId = activeThreadId;
    const reasonLabel = failure.corruptRollout
      ? `Rollout for thread ${previousThreadId} is corrupt (${resumeMessage})`
      : `Thread ${previousThreadId} has no rollout`;
    const modeLabel = executionMode === "resident"
      ? "; healing resident session with a fresh thread (visibility in the live TUI is lost until the user relaunches codex-aify from a clean environment)"
      : "; starting a fresh thread";
    callbacks.onEvent?.("thread", reasonLabel + modeLabel);
    try {
      activeThreadId = await startThread();
    } catch (healError) {
      throw new Error(
        `Codex thread/resume for ${previousThreadId} failed with ${failure.healReason} (${resumeMessage}), ` +
        `and the auto-heal fallback thread/start also failed: ${healError?.message || healError}. ` +
        `This usually means Codex's app-server itself is in a bad state — kill the codex app-server process ` +
        `and relaunch codex-aify from the target project directory. See the aify-comms-debug skill.`,
        { cause: healError },
      );
    }
    // Push the new thread id back to the caller so the backend's stored
    // sessionHandle gets updated. Without this, the very next dispatch would
    // try to resume the same poisoned thread and hit the exact same error.
    if (activeThreadId && activeThreadId !== previousThreadId) {
      try {
        await callbacks.onSessionHandleChange?.(activeThreadId, {
          previous: previousThreadId,
          reason: failure.healReason,
        });
        callbacks.onEvent?.("thread", `Healed: ${previousThreadId} → ${activeThreadId} (${failure.healReason})`);
      } catch (cbError) {
        console.error(
          `[aify] onSessionHandleChange callback failed after healing thread: ${cbError?.message || cbError}`,
        );
      }
    }
    return activeThreadId;
  }
}
