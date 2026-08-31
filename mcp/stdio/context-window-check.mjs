// The `context-window` check: can this agent still answer, or has its conversation outgrown its model?
//
// THE FAILURE THIS EXISTS FOR, measured on the operator's fleet 2026-08-31. Five managed hermes
// agents had produced nothing for over two hours. Every signal the control plane offers read fine:
// status `online`, `lastSeen` refreshing every few seconds, dispatch runs reporting `delivered`,
// unread zero. The agents were reading their messages and starting work -- `comms-senior-dev` was
// observed running `git show` against the very commits it had been asked to review -- and then
// dying without emitting a word.
//
// The console said what nothing else did: `Context length exceeded (1,122,638 tokens). Cannot
// compress further.` Their conversations had been resuming since 5 June, 1 July and 18 August.
//
// AND THE FAILURE NOTICE COULD NOT NAME IT. The auto-mirrored dispatch failure offers four
// candidate causes -- a provider throttle, a safety refusal, a mid-turn interrupt, a stall -- and
// context exhaustion is not among them. So the one diagnosis that was readable off the screen was
// the one nobody was told to look for.
//
// WHY A DOCTOR CHECK RATHER THAN A STATUS: `derive()` answers "is this agent reachable", and the
// answer is honestly yes. A context-full agent is reachable, claims work, and cannot do it. That is
// a health question about the RUNTIME, which is what this tool is for.

// ── the numbers, off the status line ────────────────────────────────────────────────────────────
//
// The runtime's own footer carries them:
//
//   ─ ready │ gpt 5.6 sol 900k │ 820.3k/900k │ [█████████░] 91% │ 16h 26m │ ✓ 3h 13m │ cmp 5 │ …
//
// READ THE PAIR, NOT THE RENDERED PERCENT. The console is a wrapped TUI screen: box-drawing
// characters, ANSI, and lines broken mid-token. The captured footer of a dying agent had its bar and
// its percentage shredded across four visual rows while `922.4k/900k` survived intact, because the
// pair is short enough to fit between wraps. The ratio is computed here instead -- one arithmetic
// step on two numbers we actually read beats trusting a glyph the terminal may have cut in half.
//
// THE LAST MATCH WINS. The footer is the bottom line of the screen, and scrollback above it can
// carry anything -- including an agent quoting a figure like this one back at itself.
const USAGE_PAIR = /([0-9]+(?:\.[0-9]+)?)k\s*\/\s*([0-9]+(?:\.[0-9]+)?)k/g;

/**
 * The context usage in one console capture, or null when the footer was not readable.
 *
 * NULL IS NOT ZERO, and every caller has to keep that distinction: a console that could not be read
 * is unanswered, not healthy. Reporting "0% used" for a screen we failed to parse is the false green
 * this whole tool exists to refuse.
 *
 * @returns {{usedTokens:number, windowTokens:number, ratio:number}|null}
 */
export function parseContextUsage(text) {
  const source = String(text || "");
  if (!source) return null;

  let last = null;
  for (const match of source.matchAll(USAGE_PAIR)) last = match;
  if (!last) return null;

  const usedTokens = Number(last[1]) * 1000;
  const windowTokens = Number(last[2]) * 1000;
  // A zero or negative window is not a window. Dividing by it yields Infinity or NaN, and either
  // would render as a confident verdict about a number that means nothing.
  if (!Number.isFinite(usedTokens) || !Number.isFinite(windowTokens) || windowTokens <= 0) return null;

  return { usedTokens, windowTokens, ratio: usedTokens / windowTokens };
}

/** At or above this, the runtime has stopped being able to answer. Measured, not guessed: see below. */
export const CONTEXT_FULL_RATIO = 1;

/**
 * Near enough that the operator has time to act, and far enough not to cry wolf.
 *
 * CALIBRATED AGAINST A CONTROL, which is the only reason to trust the number. On 2026-08-31 the
 * three readable agents were 0.91, 1.02 and 1.19 -- and the one at 0.91 was the ONLY agent in the
 * fleet still producing output, with the most recent message of any of them. So 0.9 sits below every
 * observed failure and above the one observed success.
 */
export const CONTEXT_NEAR_RATIO = 0.9;

/**
 * What the fleet's readings mean, decided in one place over all of them.
 *
 * PURE, and it takes the whole population rather than one row: "three agents are full" and "one is"
 * are different operator situations, and a per-row verdict cannot say which it is looking at.
 *
 * @param {{agentId:string, usage:{ratio:number}|null, reason?:string}[]} rows
 */
export function contextWindowVerdict(rows = [], { fullAt = CONTEXT_FULL_RATIO, nearAt = CONTEXT_NEAR_RATIO } = {}) {
  const considered = Array.isArray(rows) ? rows : [];
  if (!considered.length) {
    return { ok: true, code: "none", detail: "no agent has a readable console to measure." };
  }

  const readable = considered.filter((row) => row && row.usage);
  if (!readable.length) {
    // NO EVIDENCE IS NOT A PASS. Every console was unreadable, so this check measured nothing --
    // and a row that reads `ok` here would be indistinguishable from a fleet that is genuinely fine.
    return {
      ok: false,
      code: "unknown-all",
      detail: `none of ${considered.length} console(s) could be read, so no agent's context was measured.`,
      fix: "Check that the consoles are attached (`comms_console_tail`); until one is readable this "
        + "check cannot tell a healthy runtime from an exhausted one.",
    };
  }

  const pct = (row) => `${row.agentId} ${Math.round(row.usage.ratio * 100)}%`;
  const full = readable.filter((row) => row.usage.ratio >= fullAt).map(pct);
  if (full.length) {
    return {
      ok: false,
      code: "exhausted",
      detail: `${full.length} agent(s) have filled their context window and cannot answer: ${full.join(", ")}`,
      fix: "Their conversations cannot be compressed further, so nothing will make them respond. Reset "
        + "one with `comms_restart freshContext=true` to start a new conversation -- which discards "
        + "that agent's history, so it is a decision rather than a repair. THAT RESET ONLY WORKS ON A "
        + "BRIDGE AT OR PAST ea18156b: before it, hermes ignored the fresh-context policy and resumed "
        + "the same conversation anyway, so the reset reported success and changed nothing. Check "
        + "`bridge-current` above.",
    };
  }

  const near = readable.filter((row) => row.usage.ratio >= nearAt).map(pct);
  if (near.length) {
    return {
      ok: false,
      code: "near-full",
      detail: `${near.length} agent(s) are close to filling their context window: ${near.join(", ")}`,
      fix: "They still answer, but each turn brings them nearer the ceiling, after which they go "
        + "silent while every other signal still reads healthy. Reset them at a moment of your "
        + "choosing rather than mid-task.",
    };
  }

  const worst = readable.reduce((a, b) => (a.usage.ratio >= b.usage.ratio ? a : b));
  const unread = considered.length - readable.length;
  return {
    ok: true,
    code: "ok",
    detail: `${readable.length} console(s) measured, worst ${pct(worst)}`
      + (unread ? `; ${unread} could not be read` : ""),
  };
}

/**
 * Ask each live managed console how full it is.
 *
 * SCOPED TO CONSOLES THAT EXIST. An agent with no console has nothing to read, and fetching one per
 * registered agent would make the doctor slower than the thing it is diagnosing.
 */
export async function checkContextWindow({ get, add, skip, maxConsoles = 24 }) {
  const listing = await get("/api/v1/agents");
  const agents = listing && typeof listing.agents === "object" ? listing.agents : null;
  if (!agents) {
    return add("context-window", false, "unknown",
      "the service did not answer, so no console could be measured.",
      "Check the `service` row above.");
  }

  const candidates = Object.entries(agents)
    .filter(([, agent]) => agent && typeof agent === "object")
    .filter(([, agent]) => agent.consoleAvailable === true && agent.sessionMode === "managed")
    .slice(0, maxConsoles);

  const rows = [];
  for (const [agentId] of candidates) {
    const console_ = await get(`/api/v1/agents/${encodeURIComponent(agentId)}/console?lines=6`);
    rows.push({ agentId, usage: parseContextUsage(console_ && console_.output) });
  }

  const verdict = contextWindowVerdict(rows);
  return add("context-window", verdict.ok, verdict.code, verdict.detail, verdict.fix);
}
