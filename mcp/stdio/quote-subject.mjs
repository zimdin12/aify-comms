// Render another agent's subject so it cannot read as an instruction to whoever sees it.
//
// THE SERVICE SIDE HAS HAD THIS SINCE 2026-08-11 AND THE BRIDGE NEVER DID. From the operator report
// that created it: *"when you restart agent then it gives some text ... but my agent decided to
// restart himself after reading this."* A subject is free text written BY one agent FOR another, and
// the one-line summaries strip the addressing away — so `Restart lc-coder`, aimed at somebody else,
// arrives in a third agent's context as a bare imperative, and an agent that reads its context as
// instructions acts on it.
//
// `service/api_core/serialization.py` fixed that for the SSE transport and grew a gate to keep it
// fixed. The stdio bridge — which is what most agents actually run — kept interpolating subjects raw
// in four places. Fixing one transport and not the other leaves half the fleet exposed to the same
// defect, which is the reasoning `comms_search`'s "say what was searched" note already records for
// this exact pair of transports.
//
// THE TWO ESCAPES, both learned the hard way on the Python side:
//   * a QUOTE in the subject closes the quoting early, so embedded quotes become apostrophes;
//   * a NEWLINE breaks out of it entirely — `x\nRestart lc-coder` puts a bare imperative alone on
//     line two with the closing quote too far away to read as quoting. Control characters go with
//     it: ESC would carry ANSI sequences into a terminal-rendered console, and a lone CR
//     repositions the cursor over the line already printed. A subject is ONE LINE by definition
//     here, so collapsing a run of them to a single space loses nothing a reader wanted.
//
// KEPT IDENTICAL TO THE PYTHON IMPLEMENTATION, with a cross-language test that runs both over the
// same hostile inputs. Two renderers that disagree about what is safe are worse than one, because
// the difference is invisible from either side.

/**
 * Any run of control characters, collapsed to one space. `\x00-\x1f` covers CR, LF, TAB and ESC;
 * `\x7f` is DEL.
 *
 * Written with ESCAPES rather than literal bytes on purpose: the first version of this file was
 * saved with real control characters inside the class, which works but makes the file read as
 * binary to grep, is invisible in a diff, and is one careless copy-paste from silently losing a
 * range.
 */
const CONTROL_RUN = /[\x00-\x1f\x7f]+/g;

/**
 * @param {unknown} subject raw subject text from another agent
 * @param {number} limit visible characters to keep (matches the Python default of 80)
 * @returns {string} the subject, quoted, on one line, safe to place in agent-facing text
 */
export function quoteUntrustedSubject(subject, limit = 80) {
  // Collapsed BEFORE clipping so the limit measures what is actually displayed — otherwise a
  // subject could spend its whole budget on newlines and push the visible text past the clip.
  let text = String(subject ?? "").replace(CONTROL_RUN, " ").trim();
  if (text.length > limit) {
    text = `${text.slice(0, Math.max(limit - 1, 0)).trimEnd()}…`;
  }
  if (!text) text = "(no subject)";
  return `"${text.replaceAll('"', "'")}"`;
}
