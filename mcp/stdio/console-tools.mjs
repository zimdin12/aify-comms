// The managed console: reading what a terminal is showing, and typing into it.
//
// Two MCP tools — `comms_console_tail` and `comms_console_input` — plus their handlers and the description
// that makes the second one's danger legible. v0.5.4 layer 2 of the server.js decomposition.
//
// THREE NAMES ARE EXPORTED HERE, not one, and that is the reviewer's rule rather than an exception:
// `console-tools.test.js` consumes all three from outside the group. The handlers were already exported in
// `server.js` for exactly that reason — they take an injectable `httpCall` so their wire behaviour can be
// asserted without a service, which is the shape the rest of this decomposition has been converging on.
//
// `comms_interrupt` is NOT here despite being console-shaped. It sends terminal-native Ctrl+C and belongs to
// the dispatch group, whose other tools it is used alongside; its console behaviour is asserted in
// `console-tools.test.js` next to these two, which is where the subject lives even though the code does not.
//
// WHY THE INPUT TOOL CARRIES SUCH A LONG DESCRIPTION. Typing into another agent's console is
// recovery-only: it injects keystrokes into a live session that a human or a runtime may be mid-turn in.
// The normal way to give an agent work is a message, which queues or steers. `CONSOLE_INPUT_TOOL_DESCRIPTION`
// says so at length because the tool is indistinguishable from a useful one until it has already interrupted
// someone, and the description is the only thing standing between a model and that mistake.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { IS_REMOTE, httpCall } from "./aify-service-endpoint.mjs";
import { AIFY_AGENT_ID } from "./launch-identity.mjs";
// Handlers exported as named functions so they can be unit-tested with an
// injected httpCall. They default to the module-level httpCall in production.
export async function commsConsoleTailHandler({ agentId, lines }, { httpCall: call = httpCall } = {}) {
  if (!IS_REMOTE) {
    return { content: [{ type: "text", text: "Console tail is only available in remote server mode." }], isError: true };
  }
  try {
    const n = Math.max(1, Math.min(Number(lines || 40), 200));
    const r = await call("GET", `/agents/${encodeURIComponent(agentId)}/console?lines=${n}`);
    if (!r.live && r.historical) {
      // A DEAD worker's output is the case that matters most, and until v0.2 this
      // handler threw it away: the server said "no live console" and the agent that
      // needed the diagnosis could not reach it (2026-08-07 — the cause sat in the
      // row for 2.5h while the operator relayed it to a human by hand). Lead with
      // the one-line cause, and say plainly that this is a RECORDING, so nobody
      // reads it as the state of a running session.
      const head = r.failureLine ? `Cause: ${r.failureLine}\n\n` : "";
      // WHICH STORE ANSWERED, said out loud only when it is the surprising one. Two stores hold a
      // terminal's output: the accumulated `terminal_sessions.output` column and the
      // `terminal_events` rows. The column is normal and needs no remark. Falling back to the events
      // means the column held nothing but the terminal's own exit marker -- which is the shape that
      // made this tool answer "(nothing was recorded)" for sc-architect on 2026-08-26 while 14,773
      // characters of its last screen sat in the events. A reader deciding how much to trust a
      // reconstruction should know it is one.
      const store = r.recordedFrom === "events"
        ? "Recovered from the terminal's recorded events; the output column held only its exit marker.\n\n"
        : "";
      // HOW IT ENDED, on the line above the output, because it is the answer to the question that
      // brings anyone here. Three distinct cases and they must not be collapsed: a signal means
      // something killed it, a code means it chose to stop, and nothing recorded means the record
      // cannot say -- which is what every terminal said before 2026-08-26, and is still what an
      // older bridge produces. `exitCode === 0` is a clean exit and must print, so this tests for
      // null/undefined rather than truthiness.
      const exit = r.exitSignal
        ? `Killed by ${r.exitSignal}.\n`
        : (r.exitCode === null || r.exitCode === undefined ? "" : `Exited with code ${r.exitCode}.\n`);
      return {
        content: [{
          type: "text",
          text:
            `NOT LIVE — last recorded console of ${agentId} (terminal ${r.terminalId}, ${r.status}` +
            `${r.stoppedAt ? ` at ${r.stoppedAt}` : ""}). This worker is gone; the output below is history.\n\n` +
            `${exit}${store}${head}${r.output || "(nothing was recorded)"}`,
        }],
      };
    }
    if (!r.live) {
      return { content: [{ type: "text", text: r.message || `${agentId} has no live console.` }] };
    }
    return {
      content: [{
        type: "text",
        text: `Console of ${agentId} (terminal ${r.terminalId}, status ${r.status}), last ${r.lines} lines:\n${r.output || "(empty)"}`,
      }],
    };
  } catch (error) {
    return { content: [{ type: "text", text: error.message }], isError: true };
  }
}

export async function commsConsoleInputHandler({ agentId, text, enter, from }, { httpCall: call = httpCall } = {}) {
  if (!IS_REMOTE) {
    return { content: [{ type: "text", text: "Console input is only available in remote server mode." }], isError: true };
  }
  // NO CALLER IDENTITY, NO CALL -- and say so here rather than letting the server say it.
  //
  // The endpoint requires `from` and 400s without it, then 403s if it is not a REGISTERED
  // agent. Both are right: writing keystrokes into another agent's live console is the
  // privileged half of this pair, and `comms_console_tail` is an ungated GET precisely because
  // reading is not.
  //
  // But `from` is stamped by the bridge from AIFY_AGENT_ID and is deliberately NOT a tool
  // parameter -- exposing it would let any caller name any agent as the requester. So an
  // id-less session (an unregistered plain session is legitimately id-less) got
  // `console input requires a `from` caller` back from the server: an error naming a field it
  // has no way to provide, through a schema that does not offer it. Reported as "the tool is
  // uncallable through MCP because its schema omits what the server requires", and the schema
  // is correct -- the message was the problem.
  const caller = String(from || AIFY_AGENT_ID || "").trim();
  if (!caller) {
    return {
      content: [{
        type: "text",
        text: "Console input needs a registered agent identity to attribute it to, and this"
          + " session has none (AIFY_AGENT_ID is unset). Register with comms_register first, or"
          + " run through a wrapper that sets it. Reading is unaffected: comms_console_tail"
          + " needs no identity.",
      }],
      isError: true,
    };
  }
  try {
    const r = await call("POST", `/agents/${encodeURIComponent(agentId)}/console/input`, {
      text: text || "",
      enter: enter === undefined ? true : !!enter,
      from: caller,
    });
    if (!r.ok) {
      return { content: [{ type: "text", text: r.message || `Could not send input to ${agentId}.` }], isError: true };
    }
    // "Input sent" was the sentence an operator's sc-manager read as confirmation before burning
    // ~15 minutes retrying a lever that could not work (C8). The write is only QUEUED, and even a
    // completed control proves nothing beyond "bytes reached the PTY". Say that.
    return {
      content: [{ type: "text", text: `Input QUEUED to ${agentId}'s console (terminal ${r.terminalId}, control ${r.controlId}). This is NOT confirmation: it proves only that the bytes were written to the PTY, not that the runtime acted on them. Read the console with comms_console_tail and check whether the draft is still at the prompt before assuming it worked — and do not retry blind, a repeated Enter has been observed to change nothing.` }],
    };
  } catch (error) {
    return { content: [{ type: "text", text: error.message }], isError: true };
  }
}

export const CONSOLE_INPUT_TOOL_DESCRIPTION =
  "Recovery-only: send keystrokes/text into another managed agent's live console. " +
  "Read the console first with comms_console_tail and use this only for a proven interactive prompt or operator recovery. " +
  "Do not inject normal work messages, reminders, or duplicate comms_send delivery through the console. Audited. " +
  "NOT RELIABLE AS A SUBMIT: a successful call means the bytes were written to the PTY, never that the runtime acted on them. " +
  "Observed 2026-07-26 on a stuck managed-claude draft — two text writes and three bare-Enter retries ALL reported success while the draft never submitted. " +
  "If one attempt does not visibly change the console, escalate to the operator instead of retrying; repeated Enter has been measured to do nothing.";

// Registers the two console tools. A function rather than a module-scope side effect, so a fake server
// can capture the registrations and a test can call the handlers without an MCP transport. `z` is the
// caller's zod — see the other tool groups for why it is not imported here.
//
// The two bodies below are the original server.js text, indented one level. Nothing else changed.
export function registerConsoleTools(server, z) {
  server.tool(
    "comms_console_tail",
    "Read the last N lines of another agent's console (read-only; managed agents). " +
      "Works on a DEAD worker too: with no live console it returns the LAST RECORDED output of the " +
      "agent's most recent terminal, clearly marked NOT LIVE and led by the one-line cause. " +
      "This is the tool to reach for when a spawn or dispatch failed and you want to know WHY, " +
      "instead of asking the operator to read the terminal for you.",
    {
      agentId: z.string().describe("Agent whose console to read"),
      lines: z.number().int().min(1).max(200).optional().describe("How many trailing lines to return. Default 40."),
    },
    (args) => commsConsoleTailHandler(args)
  );

  server.tool(
    "comms_console_input",
    CONSOLE_INPUT_TOOL_DESCRIPTION,
    {
      agentId: z.string().describe("Agent whose console to send input to"),
      text: z.string().optional().describe("Text/command to type. Empty string + enter=true sends just Enter."),
      enter: z.boolean().optional().describe("Append a carriage return. Default true. ATTEMPTS a submit — does not guarantee one; see the tool description."),
    },
    (args) => commsConsoleInputHandler({ ...args, from: AIFY_AGENT_ID })
  );
}
