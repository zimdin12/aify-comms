import { spawn } from "child_process";
import { createRequire } from "module";
import { homedir } from "node:os";
import { normalizeRuntime, terminateProcessTree } from "./runtimes.js";
import { reapPriorManagedClaude } from "./reap-managed-claude.js";
import { classifyClaudeConsoleTail, hasActiveSubagents } from "./claude-console-spinner.js";
import { matchConsolePrompt } from "./claude-console-prompts.js";

// node-pty's pty.spawn calls native chdir(2) with the cwd verbatim. POSIX
// chdir does not expand "~" — operator-supplied workspaces like
// "~/projects/foo" therefore fail immediately with ENOENT and the terminal
// dies seconds after attaching. Expand here so any caller that hands us a
// shell-style path gets the right directory. Exported for unit testing.
export function expandUserHome(value) {
  const raw = String(value || "");
  if (!raw) return raw;
  if (raw === "~") return homedir();
  if (raw.startsWith("~/")) return `${homedir()}${raw.slice(1)}`;
  return raw;
}

const require = createRequire(import.meta.url);
let pty = null;
try {
  const loaded = require("node-pty");
  pty = loaded?.default || loaded;
} catch {
  pty = null;
}

export function bridgeTerminalSupported() {
  if (["0", "false", "no"].includes(String(process.env.AIFY_TERMINAL_BRIDGE || "1").toLowerCase())) return false;
  return !!pty;
}

// The pure text handling left for `terminal-text.js` in v0.5.4 — strip, classify, and strip a
// resume out of a command or an env. The manager below is their only caller in this file.
import {
  appendTail,
  classifyTerminalRuntimeOutput,
  hermesResumeStallHealMs,
  hermesResumeStillPending,
  terminalCommandWithoutResume,
  terminalEnvWithoutResume,
} from "./terminal-text.js";

async function waitForExitOrTimeout(exitPromise, timeoutMs = 1000) {
  let timer = null;
  let timedOut = false;
  try {
    return await Promise.race([
      Promise.resolve(exitPromise).then(() => true),
      new Promise((resolve) => {
        timer = setTimeout(() => {
          timedOut = true;
          resolve(false);
        }, Math.max(1, Number(timeoutMs) || 1000));
      }),
    ]);
  } finally {
    if (!timedOut && timer) clearTimeout(timer);
  }
}


export class TerminalProcessManager {
  constructor({
    onOutput = async () => {},
    onExit = async () => {},
    onHeal = async () => {},
    idleFlushMs = 16,
    maxLatencyMs = 33,
    maxBatchChars = 16 * 1024,
    autoAnswer = true,
    autoAnswerKeyDelayMs = 150,
    consoleKeepaliveMs = 4000,
    consoleKeepaliveIdleGraceTicks = 30,
    consoleKeepaliveIdleReprobeTicks = 4,
    envDelegation = null,
  } = {}) {
    // Injected rather than read here, so a test can drive both branches without setting an env var --
    // and so the default really is "whatever the environment says", which is off.
    this.envDelegation = envDelegation;
    this.onOutput = onOutput;
    this.onExit = onExit;
    this.onHeal = onHeal;
    // Auto-answer claude TUI prompts (managed claude only). On by default;
    // AIFY_NO_AUTO_ANSWER=1 (read by the bridge that constructs this) or autoAnswer:false
    // disables it. Never types into a resident/operator session.
    this.autoAnswer = autoAnswer !== false;
    this.autoAnswerKeyDelayMs = Math.max(0, Number(autoAnswerKeyDelayMs) || 0);
    // Managed-claude repaint keepalive (2026-06-05): claude only re-emits its spinner footer
    // while its PTY is actively rendered, so an UNWATCHED working claude goes quiet on the PTY
    // and the console-working lease goes stale -> `online`. Nudge the PTY so it keeps emitting.
    this.consoleKeepaliveMs = Math.max(0, Number(consoleKeepaliveMs) || 0);
    // After this many CONSECUTIVE idle-prompt ticks, drop to the SLOW re-probe cadence below
    // (don't stop entirely). Default 30 ≈ 2 min at the 4s cadence. A working/unknown tick resets
    // the streak. See the IDLE-GRACE GATE in _armConsoleKeepalive.
    this.consoleKeepaliveIdleGraceTicks = Math.max(1, Number(consoleKeepaliveIdleGraceTicks) || 30);
    // Once past the idle grace, keep nudging at 1-in-N ticks instead of stopping. A FULL stop
    // could never re-discover work that resumes after a long idle: an unwatched claude goes quiet
    // on its PTY, so without a nudge it never re-emits a working footer, the console-working lease
    // (CONSOLE_WORKING_LEASE_SECONDS=20s) lapses, and status falsely flips working->online (#224).
    // The re-probe interval must stay BELOW that lease so a resumed-but-quiet turn is re-detected
    // before the lease expires (default 4 → 16s at the 4s cadence < 20s). Churn stays negligible:
    // a genuinely idle console re-emits only its IDLE residue on each probe, so no working pulse
    // ever fires — only the sub-ms resize toggle, once per window.
    this.consoleKeepaliveIdleReprobeTicks = Math.max(1, Number(consoleKeepaliveIdleReprobeTicks) || 4);
    this.idleFlushMs = Math.max(1, Number(idleFlushMs) || 16);
    this.maxLatencyMs = Math.max(this.idleFlushMs, Number(maxLatencyMs) || 33);
    this.maxBatchChars = Math.max(1024, Number(maxBatchChars) || 16 * 1024);
    this.terminals = new Map();
    this.outputStates = new Map();
  }

  has(id) {
    return this.terminals.has(id);
  }

  stateFor(id) {
    const state = this.terminals.get(id);
    if (!state) return null;
    return {
      id: state.id,
      runtime: state.runtime,
      status: state.status,
      command: state.command,
      outputTail: state.outputTail || "",
      // agentId + consoleClass drive the host-side working pulse (server.js onOutput).
      // agentId was previously omitted here, so the legacy any-output pulse — which
      // reads stateFor(...).agentId — never fired (a contributor to the managed-claude
      // under-report). consoleClass is the claude TUI spinner classification.
      agentId: state.agentId || "",
      consoleClass: state.consoleClass || null,
      subagentsActive: !!state.subagentsActive,
    };
  }

  emitOutputForTest(id, text) {
    return this._handleOutput(id, { runtime: "" }, text);
  }

  flushOutputForTest(id) {
    return this._flushOutput(id);
  }


  async start({ id, command, cwd = process.cwd(), env = process.env, cols = 100, rows = 28, runtime = "", sessionHandle = "", healAttempted = false, agentId = "", sessionMode = "" }) {
    if (!id) throw new Error("Terminal id is required");
    if (!command) throw new Error("Terminal command is required");
    if (this.terminals.has(id)) {
      await this.stop(id, "restarting terminal");
    }
    const spec = { id, command, cwd, env, cols, rows, runtime: normalizeRuntime(runtime), sessionHandle, healAttempted, agentId, sessionMode };
    // v0.6 Phase 8: the seam where spawning leaves aify-comms.
    //
    // OFF unless AIFY_ENV_ENDPOINT is set, which nothing in this repo sets. With it unset this is one
    // boolean and the next two lines are the whole of what happens, exactly as before.
    //
    // It REFUSES rather than half-delegating. Feeding a delegated process into _handleOutput and
    // _handleExit would inherit the batching, auto-answer and classification for free, which is why
    // parity is reachable at all -- but `state.term` is also used to write, resize and kill, and the
    // console keepalive probes it. A delegated start without that shim would produce agents that are
    // subtly different in ways nobody could attribute. Refusing names the gap at the point of use
    // instead of leaving it in a document.
    if (this.envDelegation?.isEnabled()) {
      throw new Error(
        "AIFY_ENV_ENDPOINT is set, but delegating terminals to aify-env is not finished: a term shim "
        + "(write/resize/kill) and the console keepalive still run against a local pty. Unset it to "
        + "spawn locally. See docs/PHASE8_STATUS.md.",
      );
    }
    if (pty) {
      return this.startPty(spec);
    }
    return this.startPipeProcess(spec);
  }

  async startPty({ id, command, cwd, env, cols = 100, rows = 28, runtime = "", sessionHandle = "", healAttempted = false, agentId = "", sessionMode = "" }) {
    const windows = process.platform === "win32";
    const shell = windows
      ? (process.env.COMSPEC || "cmd.exe")
      : (process.env.SHELL || "bash");
    const trimmedCommand = String(command || "").trim();
    const lowerCommand = trimmedCommand.toLowerCase();
    const shellName = shell.split(/[\\/]/).pop().toLowerCase();
    const args = windows
      ? (lowerCommand === "cmd" || lowerCommand === "cmd.exe" || lowerCommand === shellName ? [] : ["/d", "/s", "/c", trimmedCommand])
      : ["-lc", command];
    const resolvedCwd = expandUserHome(cwd) || process.cwd();
    // Managed kill-prior (2026-05-31): before launching a managed claude PTY,
    // reap any orphaned claude.exe still bound to this agent's stable --resume
    // handle. Managed claude churns terminals and a server-marked-'failed'
    // terminal leaves no live handle for terminateProcessTree, so old native
    // claude.exe processes are never reaped and N siblings accumulate, splitting
    // channel delivery across them. Reaping by the per-agent resume handle here
    // (defense-in-depth with the claude-aify wrapper's own reap) collapses it to
    // exactly one. Only fires for a genuine new spawn — terminal reuse upstream
    // never reaches startPty.
    // AGENT-SCOPED (safety, 2026-05-31): pass agentId so the reaper only kills
    // THIS agent's prior managed claude — verified via each candidate's parent
    // --aify-agent wrapper — and can NEVER kill another agent or a resident
    // operator session that shares the same --resume id (handle collision, the
    // incident that force-closed comms-tech-lead). No agentId → reaper no-ops.
    if (normalizeRuntime(runtime) === "claude-code" && agentId) {
      const m = /--resume[=\s]+([0-9a-fA-F][0-9a-fA-F-]{7,})/.exec(trimmedCommand);
      const handle = (m && m[1]) || String(sessionHandle || "").trim();
      if (handle) {
        try { reapPriorManagedClaude(handle, { agentId }); } catch { /* best-effort */ }
      }
    }
    const term = pty.spawn(shell, args, {
      name: "xterm-256color",
      cols: Math.min(2000, Math.max(20, Number(cols || 100))),
      rows: Math.min(1000, Math.max(6, Number(rows || 28))),
      cwd: resolvedCwd,
      env,
    });
    let resolveExit = null;
    const exitPromise = new Promise((resolve) => {
      resolveExit = resolve;
    });
    const state = {
      id,
      command,
      cwd,
      env,
      cols: Math.min(2000, Math.max(20, Number(cols || 100))),
      rows: Math.min(1000, Math.max(6, Number(rows || 28))),
      runtime: normalizeRuntime(runtime),
      sessionHandle: String(sessionHandle || "").trim(),
      healAttempted: !!healAttempted,
      agentId: String(agentId || "").trim(),
      // FIX 6 (2026-06-03): store sessionMode so stopAll can skip resident consoles.
      sessionMode: String(sessionMode || "").trim(),
      term,
      status: "attached",
      kind: "pty",
      outputTail: "",
      classification: null,
      resumeHealTimer: null,
      exitPromise,
      resolveExit,
    };
    this.terminals.set(id, state);
    state.stopConsoleKeepalive = this._armConsoleKeepalive(id, state);
    term.onData((text) => {
      this._handleOutput(id, state, text).catch(() => {});
    });
    term.onExit(({ exitCode, signal }) => {
      this._handleExit(id, state, { code: exitCode, signal }).catch(() => {});
    });
    return { pid: term.pid, status: "attached", pty: true };
  }

  async startPipeProcess({ id, command, cwd, env, cols = 100, rows = 28, runtime = "", sessionHandle = "", healAttempted = false, agentId = "", sessionMode = "" }) {
    const resolvedCwd = expandUserHome(cwd) || process.cwd();
    const proc = spawn(command, {
      cwd: resolvedCwd,
      env,
      shell: true,
      // HARD no-popup requirement (operator): this non-PTY fallback (used only
      // when node-pty is unavailable) streams to the dashboard via piped stdio
      // and never needs a visible OS window. windowsHide:true so a degraded
      // host never flashes a console window for a bridge-managed agent.
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
    });
    let resolveExit = null;
    const exitPromise = new Promise((resolve) => {
      resolveExit = resolve;
    });
    const state = {
      id,
      command,
      cwd,
      env,
      cols,
      rows,
      runtime: normalizeRuntime(runtime),
      sessionHandle: String(sessionHandle || "").trim(),
      healAttempted: !!healAttempted,
      agentId: String(agentId || "").trim(),
      // FIX 6 (2026-06-03): store sessionMode so stopAll can skip resident consoles.
      sessionMode: String(sessionMode || "").trim(),
      proc,
      status: "attached",
      kind: "pipe",
      outputTail: "",
      classification: null,
      resumeHealTimer: null,
      exitPromise,
      resolveExit,
    };
    this.terminals.set(id, state);
    const emit = (chunk) => {
      const text = chunk?.toString?.("utf8") || String(chunk || "");
      this._handleOutput(id, state, text).catch(() => {});
    };
    proc.stdout?.on("data", emit);
    proc.stderr?.on("data", emit);
    proc.on("close", (code, signal) => {
      this._handleExit(id, state, { code, signal }).catch(() => {});
    });
    proc.on("error", (error) => {
      this._handleExit(id, state, { error }).catch(() => {});
    });
    return { pid: proc.pid, status: "attached", pty: false };
  }

  async _handleOutput(id, state, text) {
    if (!text) return;
    state.outputTail = appendTail(state.outputTail, text);
    // Console working-signal (claude only): classify the visible TUI footer so the host
    // can drive a spinner-gated working lease. Non-claude runtimes keep their own native
    // turn detectors and are never classified here.
    state.consoleClass =
      state.runtime === "claude-code" ? classifyClaudeConsoleTail(state.outputTail) : null;
    // Background-subagents flag (2026-06-11): the agents manager + a running row means
    // claude is orchestrating subagents — surfaced as a status mini-tag via the lease pulse.
    state.subagentsActive =
      state.runtime === "claude-code" ? hasActiveSubagents(state.outputTail) : false;
    // Console prompt auto-answer (managed claude only). Type the answer once per on-screen
    // appearance: track the answered rule; reset when the prompt clears so a later distinct
    // appearance is answered again. Never types into a resident/operator session, and NEVER
    // while claude is mid-turn (consoleClass==="working") — a generating claude writing
    // prose about a prompt must not have keystrokes injected; a real boot prompt awaits
    // input with no working spinner. Combined with the menu-cursor requirement in
    // matchConsolePrompt, this prevents self-output misfires.
    if (
      this.autoAnswer &&
      state.runtime === "claude-code" &&
      state.sessionMode === "managed" &&
      state.consoleClass !== "working"
    ) {
      const rule = matchConsolePrompt(state.outputTail, {
        // RESTORED TO ON after the actual defect was fixed (2026-08-01). It was briefly forced
        // OFF as a stop-the-bleeding measure when the operator reported the compaction dialog
        // being answered with the FIRST option instead of "keep as is" — context they had chosen
        // to preserve was silently compacted away.
        //
        // The cause was NOT this flag: claude's resume dialog CHANGED shape (summary moved to
        // option 1, a third option appeared) and computeResumeAnswer counted its arrow moves with
        // a numbering-only regex that the new menu no longer satisfies. Fixed in
        // claude-console-prompts.js and pinned by tests/resume-dialog-current-layout.test.js
        // against both numbered and unnumbered 3-option frames, including the mid-render partial
        // frame, which must refuse to press.
        //
        // claude-console-prompts.js documents exactly this hazard: the menu renders
        // PROGRESSIVELY and "Resume from summary" paints before "Resume full session", so a
        // keystroke computed mid-render can land on summary. That file added guard after guard
        // (unambiguous-marker gate, cursor-aware navigation, no Enter fallback) — and the
        // failure still reached a live agent.
        //
        // The asymmetry is why the default is only defensible once the landing is pinned, and
        // this repo wrote it down in the v0.2 plan's WS-8: a wrong press "silently loses context
        // the operator explicitly chose to preserve — unrecoverable and fleet-wide", while the
        // alternative is a STALL, which is "visible and recoverable".
        //
        // ON is safe here because every guard fails CLOSED — no cursor on an option row, no
        // target row, an implausible spread, or zero computed moves all return null, i.e. NO
        // keystrokes. An unrecognised or half-painted frame therefore stalls; it never guesses.
        // Kill it per-host with AIFY_AUTO_CONFIRM_COMPACTION=0 if the dialog drifts again, and
        // treat a drift as a reason to re-pin resume-dialog-current-layout.test.js first.
        autoConfirmCompaction: process.env.AIFY_AUTO_CONFIRM_COMPACTION !== "0",
      });
      if (rule && state.answeredPrompt !== rule.name) {
        state.answeredPrompt = rule.name;
        this._sendAnswer(id, rule.answer, state, rule.name);
      } else if (!rule) {
        state.answeredPrompt = null;
      }
    }
    const classification = classifyTerminalRuntimeOutput(state.runtime, state.outputTail);
    await this._enqueueOutput(id, text);
    if (classification?.kind === "auth" && !state.classification) {
      state.classification = classification;
      await this._enqueueOutput(id, `\n[aify-comms] ${classification.message}\n`, { flushNow: true });
      if (state.kind === "pty") {
        try { terminateProcessTree(state.term, "SIGTERM"); } catch { try { state.term?.kill(); } catch {} }
      }
      else terminateProcessTree(state.proc, "SIGTERM");
    }
    this._armHermesResumeStallHeal(id, state);
  }

  _armHermesResumeStallHeal(id, state) {
    if (!state || state.runtime !== "hermes" || !state.sessionHandle || state.healAttempted || state.stopping) return;
    if (!hermesResumeStillPending(state.outputTail)) {
      if (state.resumeHealTimer) {
        clearTimeout(state.resumeHealTimer);
        state.resumeHealTimer = null;
      }
      return;
    }
    if (state.resumeHealTimer) return;
    state.resumeHealTimer = setTimeout(() => {
      state.resumeHealTimer = null;
      if (!this.terminals.has(id) || state.stopping || state.healAttempted) return;
      if (!hermesResumeStillPending(state.outputTail)) return;
      const message = `Hermes saved session handle did not become ready: ${state.sessionHandle}`;
      state.classification = {
        kind: "missing_session",
        status: "failed",
        sessionHandle: state.sessionHandle,
        message,
      };
      if (state.kind === "pty") {
        try { terminateProcessTree(state.term, "SIGTERM"); } catch { try { state.term?.kill(); } catch {} }
      }
      else terminateProcessTree(state.proc, "SIGTERM");
    }, hermesResumeStallHealMs());
    if (typeof state.resumeHealTimer.unref === "function") state.resumeHealTimer.unref();
  }

  async _enqueueOutput(id, text, { flushNow = false } = {}) {
    const chunk = String(text || "");
    if (!id || !chunk) return;
    let state = this.outputStates.get(id);
    if (!state) {
      state = { chunks: [], chars: 0, idleTimer: null, maxTimer: null, chain: Promise.resolve() };
      this.outputStates.set(id, state);
    }
    state.chunks.push(chunk);
    state.chars += chunk.length;
    if (!state.maxTimer) {
      state.maxTimer = setTimeout(() => {
        this._flushOutput(id).catch(() => {});
      }, this.maxLatencyMs);
    }
    if (state.idleTimer) clearTimeout(state.idleTimer);
    state.idleTimer = setTimeout(() => {
      this._flushOutput(id).catch(() => {});
    }, this.idleFlushMs);
    if (flushNow || state.chars >= this.maxBatchChars) await this._flushOutput(id);
  }

  async _flushOutput(id) {
    const state = this.outputStates.get(id);
    if (!state || !state.chunks.length) return state?.chain || Promise.resolve();
    if (state.idleTimer) clearTimeout(state.idleTimer);
    if (state.maxTimer) clearTimeout(state.maxTimer);
    const output = state.chunks.join("");
    state.chunks = [];
    state.chars = 0;
    state.idleTimer = null;
    state.maxTimer = null;
    const deliver = state.chain.then(() => this.onOutput(id, output));
    state.chain = deliver.catch(() => {});
    await deliver;
    if (!state.chunks.length && !state.idleTimer && !state.maxTimer) this.outputStates.delete(id);
  }

  async _handleExit(id, state, detail = {}) {
    if (state.finalized) return;
    state.finalized = true;
    try { state.stopConsoleKeepalive?.(); } catch { /* best-effort */ }
    if (state.resumeHealTimer) {
      clearTimeout(state.resumeHealTimer);
      state.resumeHealTimer = null;
    }
    if (this.terminals.get(id) === state) this.terminals.delete(id);
    state.resolveExit?.(detail);
    const classification = state.classification || classifyTerminalRuntimeOutput(state.runtime, state.outputTail);
    try {
      await this._flushOutput(id);
    } catch {
      // Exit status is still authoritative; do not let an output backfill
      // failure prevent the terminal from reaching stopped/failed.
    }
    if (
      classification?.kind === "missing_session" &&
      state.sessionHandle &&
      !state.healAttempted &&
      !state.stopping
    ) {
      const freshCommand = terminalCommandWithoutResume(state.runtime, state.command);
      if (freshCommand && freshCommand !== state.command) {
        await this.onHeal(id, {
          runtime: state.runtime,
          agentId: state.agentId,
          previousSessionHandle: state.sessionHandle,
          reason: classification.kind,
          message: classification.message,
        });
        await this.onOutput(
          id,
          `\n[aify-comms] ${classification.message}; starting a fresh ${state.runtime || "runtime"} session without --resume.\n`,
        );
        await this.start({
          id,
          command: freshCommand,
          cwd: state.cwd,
          env: terminalEnvWithoutResume(state.runtime, state.env),
          cols: state.cols,
          rows: state.rows,
          runtime: state.runtime,
          sessionHandle: "",
          healAttempted: true,
          agentId: state.agentId,
          // Carry the session-mode tag through the heal — dropping it untagged the healed
          // console, so stopAll's FIX-6 resident skip no longer protected an operator's
          // resident console from an env-bridge restart (review, 2026-06-10).
          sessionMode: state.sessionMode,
        });
        return;
      }
    }
    const nextDetail = { ...detail };
    if (classification) {
      nextDetail.classification = classification;
      if (classification.status === "failed" && !nextDetail.error) nextDetail.error = new Error(classification.message);
    }
    // B3 (visible-TUI): when a managed console PTY exits, best-effort reap any
    // descendant worker tree (claude.exe + channel-sidecar + MCP children) that
    // Windows may have left reparented/alive. Harmless no-op if the root is
    // already gone. The authoritative backstop is the sidecar self-exit guard.
    // Reached ONLY on the final-exit path — the hermes resume-heal restart
    // branch above returns before here, so a healthy re-spawn is never reaped.
    if (state.kind === "pty" && state.term) {
      try { this._reapPtyTree(state.term); } catch { /* best effort */ }
    }
    await this.onExit(id, nextDetail);
  }

  // Indirection so the final-exit descendant reap (B3) is observable in tests
  // without spawning/killing a real process tree. Production delegates straight
  // to terminateProcessTree (taskkill /t /f on win32; process-group + captured
  // descendants on POSIX). Tests override this method to record the call.
  _reapPtyTree(term) {
    terminateProcessTree(term, "SIGKILL");
  }

  input(id, body = "") {
    const terminal = this.terminals.get(id);
    if (!terminal) throw new Error(`Terminal "${id}" is not running`);
    if (terminal.kind === "pty") {
      terminal.term.write(String(body || ""));
      return;
    }
    terminal.proc?.stdin?.write(String(body || ""));
  }

  // Send a prompt auto-answer. A string is one write; an ARRAY is a SEQUENCE of keystrokes
  // sent with `autoAnswerKeyDelayMs` between them, so a menu move (e.g. ↓) re-renders before
  // the confirm (Enter) — sending them in one write loses the move to an Ink/React state-
  // batching race (Enter reads the pre-move selection). Stops if the terminal or prompt changes.
  _sendAnswer(id, answer, state, ruleName) {
    const keys = Array.isArray(answer) ? answer.slice() : [answer];
    const sendNext = () => {
      const current = this.terminals.get(id);
      if (!keys.length || current !== state || current.answeredPrompt !== ruleName) return;
      const key = keys.shift();
      try { this.input(id, key); } catch { return; }
      if (keys.length) {
        const t = setTimeout(sendNext, this.autoAnswerKeyDelayMs);
        if (t && typeof t.unref === "function") t.unref();
      }
    };
    sendNext();
  }

  // Periodically SIGWINCH a managed claude PTY so claude re-emits its footer even when the
  // dashboard Console is closed — keeping the console-working lease fresh. A same-dims resize
  // sends NO SIGWINCH (the Linux kernel skips it when the winsize is unchanged — verified
  // empirically), so each tick momentarily shrinks one column to FORCE the signal, then restores
  // the true dims so the net terminal size is unchanged. The shrink/restore is synchronous, so
  // the at-width-1-less window is sub-millisecond. claude-managed-only; best-effort; a noop for
  // other runtimes / resident / when disabled.
  _armConsoleKeepalive(id, state) {
    if (!this.consoleKeepaliveMs || state.runtime !== "claude-code"
        || state.sessionMode !== "managed" || state.kind !== "pty") {
      return () => {};
    }
    const tick = () => {
      const st = this.terminals.get(id);
      if (!st || !st.term) return;
      // IDLE-GRACE GATE (2026-06-18): nudge full-rate while work is plausible; once the console has
      // shown the IDLE PROMPT (consoleClass==="idle") for a sustained run of ticks, drop to a SLOW
      // re-probe cadence rather than stopping entirely. A working/unknown class resets the streak
      // (working-but-quiet keeps consoleClass==="working" and is never throttled; "unknown" could
      // be working, so it also keeps full-rate). The earlier design stopped nudging completely
      // after the grace, which created a self-reinforcing dead state (#224): when a turn RESUMED
      // after a long idle, an unwatched claude stayed quiet on its PTY, so with no nudge it never
      // re-emitted a working footer, consoleClass stayed latched at "idle", the console-working
      // lease (20s) lapsed, and status falsely flipped working->online. Re-probing at 1-in-N ticks
      // (below the lease TTL) re-discovers resumed work within the lease window while keeping churn
      // negligible — a genuinely idle console only re-emits its idle residue, so no working pulse.
      if (st.consoleClass === "idle") {
        st._kaIdleTicks = (st._kaIdleTicks || 0) + 1;
      } else {
        st._kaIdleTicks = 0;
      }
      if (st._kaIdleTicks > this.consoleKeepaliveIdleGraceTicks
          && (st._kaIdleTicks % this.consoleKeepaliveIdleReprobeTicks) !== 0) {
        return;
      }
      const cols = Math.max(20, Number(st.cols || 100));
      const rows = Math.max(6, Number(st.rows || 28));
      try {
        st.term.resize(cols - 1, rows); // changed dim → SIGWINCH
        st.term.resize(cols, rows);     // restore true dims (net unchanged)
      } catch { /* best-effort */ }
    };
    const timer = setInterval(tick, this.consoleKeepaliveMs);
    if (timer && typeof timer.unref === "function") timer.unref();
    return () => clearInterval(timer);
  }

  resize(id, cols = 0, rows = 0) {
    const terminal = this.terminals.get(id);
    if (!terminal) throw new Error(`Terminal "${id}" is not running`);
    if (terminal.kind === "pty") {
      // Clamp BOTH bounds (Hermes parity). The lower floor keeps a usable grid; the UPPER cap
      // guards node-pty's TIOCSWINSZ ioctl, which throws on an absurd winsize — Hermes hit this
      // when WSL2 reported `columns=131072, rows=1` and the resize crashed the PTY. We run heavily
      // on WSL2, so cap cols/rows to sane maxima before handing them to term.resize().
      const nextCols = Math.min(2000, Math.max(20, Number(cols || 100)));
      const nextRows = Math.min(1000, Math.max(6, Number(rows || 28)));
      // Persist the new dims on state — the console keepalive restores state.cols/rows after
      // its SIGWINCH toggle, so stale dims here made it snap a dashboard-resized console back
      // to the SPAWN-time size every 4s (review must-fix, 2026-06-10).
      terminal.cols = nextCols;
      terminal.rows = nextRows;
      terminal.term.resize(nextCols, nextRows);
    }
    return { status: "attached" };
  }

  async stop(id, reason = "terminal stop requested") {
    const terminal = this.terminals.get(id);
    if (!terminal) return { stopped: false };
    terminal.stopping = true;
    this.terminals.delete(id);
    if (terminal.kind === "pty") {
      // term.kill() sends a single SIGHUP to the wrapper bash, which the wrapper
      // traps do not catch and which never reaches its sibling/child processes.
      // Kill the whole process group instead.
      try { terminateProcessTree(terminal.term, "SIGTERM"); }
      catch { try { terminal.term.kill(); } catch {} }
      const exited = await waitForExitOrTimeout(terminal.exitPromise, 1500);
      if (!exited) {
        // SIGTERM ignored within the grace (a wrapper that traps TERM, a wedged
        // child) — escalate to SIGKILL so a Stop DETERMINISTICALLY halts the backing
        // instead of leaving an orphan PTY for a reaper (2026-06-07).
        try { terminateProcessTree(terminal.term, "SIGKILL"); } catch {}
        await waitForExitOrTimeout(terminal.exitPromise, 1000);
      }
      return { stopped: true };
    }
    try {
      terminal.proc.stdin?.end();
    } catch {
      // Best effort.
    }
    terminateProcessTree(terminal.proc, "SIGTERM");
    const exited = await waitForExitOrTimeout(terminal.exitPromise, 3000);
    if (!exited) {
      await this._handleExit(id, terminal, { signal: "SIGTERM" });
    }
    return { stopped: true };
  }

  // Kill-by-pid fallback (2026-06-02): reap a PTY process tree by its root pid
  // when this manager never owned the terminal in its in-memory `terminals` Map
  // (the owning bridge restarted/died, orphaning a still-live PTY). Used ONLY as
  // a fallback after a Map-miss `stop()` — never on the owned-in-memory path.
  // Machine-local: the caller only invokes this for stop controls claimed for
  // THIS bridge's environment, so the pid always belongs to a process on this
  // machine. Routes through _reapPtyTree so the actual kill is test-injectable.
  killByPid(pid) {
    const numeric = Number(pid);
    if (!Number.isInteger(numeric) || numeric <= 0) return { killed: false };
    this._reapPtyTree({ pid: numeric });
    return { killed: true };
  }

  // Enumerate the console PTYs THIS bridge currently owns in-memory, with their
  // root pid. Used by the env-bridge dead-PTY check (WS4 Task 4.2): a row that
  // is still `attached` server-side but whose local pid is no longer alive must
  // be host-reported as dead (the server cannot probe a remote pid). Returns
  // `[{ terminalId, pid, status, agentId, runtime }]`. Only owned, pid-bearing
  // entries are included.
  listOwnedSessions() {
    const out = [];
    for (const [id, state] of this.terminals.entries()) {
      const pid = state?.term?.pid ?? state?.proc?.pid;
      const numeric = Number(pid);
      if (!Number.isInteger(numeric) || numeric <= 0) continue;
      out.push({
        terminalId: id,
        pid: numeric,
        status: String(state?.status || ""),
        agentId: String(state?.agentId || ""),
        runtime: String(state?.runtime || ""),
      });
    }
    return out;
  }

  async stopAll(reason = "terminal manager shutdown") {
    const ids = Array.from(this.terminals.keys());
    for (const id of ids) {
      // FIX 6 (2026-06-03): never reap an operator-launched RESIDENT console on
      // an env-bridge shutdown. A bridge exit (e.g. env-bridge restart) calls
      // stopAll, which previously SIGTERMed every owned PTY — killing resident
      // codex consoles the operator started. Skip resident-mode terminals.
      const st = this.terminals.get(id);
      if (st && String(st.sessionMode).toLowerCase() === "resident") continue;
      await this.stop(id, reason);
    }
  }
}
