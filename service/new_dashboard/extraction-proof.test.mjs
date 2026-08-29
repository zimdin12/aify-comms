// Proves EVERY app.js extraction so far was a pure file split, and proves the prover can fail.
//
// The extracted modules' own tests show the moved code works. They cannot show that nothing ELSE in a
// 5,000-line file changed — a whitespace edit two functions away, a line dropped during a splice, an
// import inserted in the wrong place. So this reconstructs app.js as it was before ANY extraction, from
// the current app.js plus every module extracted since, and requires byte-identity.
//
// ONE PRISTINE FIXTURE, A GROWING PLAN. The first version compared against a per-slice snapshot and went
// stale the moment slice 2 touched app.js — a proof that can only run once is a receipt, not a gate. The
// fixture below never changes; each slice appends an entry to EXTRACTIONS. So this keeps proving the whole
// history, and a later slice cannot quietly undo an earlier one.
//
// The fixture is TRACKED, not a `git show`. A proof that needs `.git` does not run from `git archive`, and
// that exact mistake shipped a route-surface gate in v0.5 that had never been in the repo at all:
// `.gitignore`'s bare `data/` matched `service/tests/data/`, the snapshots were untracked, and the gate
// raised FileNotFoundError on a clean clone while passing locally.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

import { declarationSpan, functionSpan, moduleScopeBrowserRefs, reconstruct } from "./extraction-proof.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = (p) => fs.readFileSync(path.join(HERE, p), "utf-8");

const LF = String.fromCharCode(10);

const PRISTINE = "fixtures/app.before-settings-fields.js";

/** One entry per extraction slice, in order. `at` values are indices into the PRISTINE file. */
const EXTRACTIONS = [
  {
    module: "settings-fields.mjs",
    importLine: "import { settingsFieldHtml } from './settings-fields.mjs';",
    items: [
      {
        name: "settingsFieldHtml",
        at: 1068,
        marker: [
          "// settingsFieldHtml moved to ./settings-fields.mjs in v0.5.4 (with themePreviewTilesHtml, which",
          "// only it calls and which stays private there).",
        ],
        // Same consolidation. The helper also LOWERCASES, which this did not -- so a setting saved as
        // #AABBCC showed that spelling in the code label beside a swatch the browser had normalised to
        // #aabbcc, and the theme applied the lowercase one. One question, one answer.
        editedSince: [
        {
          was: [
          "    const hex = /^#[0-9a-fA-F]{6}$/.test(String(value || '')) ? value : fallback;",
        ],
          now: [
          "    // Through the helper, which also LOWERCASES. This kept the value exactly as stored, while",
          "    // `<input type=\"color\">` normalises its own value to lowercase -- so a setting saved as #AABBCC",
          "    // showed `#AABBCC` in the code label beside a swatch driven by `#aabbcc`, and the theme applied",
          "    // the lowercase one. One question, one answer.",
          "    const hex = normalizedHexColor(value, fallback);",
        ],
        },
      ],
      },
      { name: "themePreviewTilesHtml", at: 1041, marker: null },
    ],
  },
  {
    module: "util.js",
    // This slice EDITED an existing import rather than adding a line, so the proof restores the old text.
    importLine:
      "import { esc, fileSizeLabel, relTime, tsMs, usageFmtTokens, usageResetLabel } from './util.js';",
    importWas: "import { esc, relTime, tsMs } from './util.js';",
    items: [
      // Indices are 0-based positions in the PRISTINE fixture, MEASURED from it rather than copied from
      // the extraction script's output — my first values came from the post-slice-1 file and were wrong by
      // one and by forty-six. The proof caught it, which is the point of it being position-sensitive.
      { name: "fileSizeLabel", at: 304, marker: "// fileSizeLabel moved to ./util.js in v0.5.4." },
      { name: "usageResetLabel", at: 1248, marker: "// usageResetLabel moved to ./util.js in v0.5.4." },
      { name: "usageFmtTokens", at: 1256, marker: "// usageFmtTokens moved to ./util.js in v0.5.4." },
    ],
  },
  {
    module: "record-fields.mjs",
    importLine: "} from './record-fields.mjs';",
    importBlock: [
      "import {",
      "  asAgentArray,",
      "  asArray,",
      "  contractActionable,",
      "  contractCategory,",
      "  environmentRoots,",
      "  environmentRuntimes,",
      "  messageId,",
      "  messageIdOf,",
      "  messageRunId,",
      "  runPendingControlCount,",
      "  runTargetAgent,",
      "  sessionAgentId,",
      "  sessionEnvironmentId,",
      "  sessionId,",
      "  sessionRuntime,",
      "} from './record-fields.mjs';",
    ],
    items: [
      { name: "messageIdOf", at: 219, marker: "// messageIdOf moved to ./record-fields.mjs in v0.5.4." },
      { name: "asAgentArray", at: 733, marker: "// asAgentArray moved to ./record-fields.mjs in v0.5.4." },
      { name: "sessionEnvironmentId", at: 1617, marker: "// sessionEnvironmentId moved to ./record-fields.mjs in v0.5.4." },
      { name: "sessionRuntime", at: 1621, marker: "// sessionRuntime moved to ./record-fields.mjs in v0.5.4." },
      { name: "messageId", at: 1696, marker: "// messageId moved to ./record-fields.mjs in v0.5.4." },
      { name: "messageRunId", at: 1700, marker: "// messageRunId moved to ./record-fields.mjs in v0.5.4." },
      { name: "contractCategory", at: 2910, marker: "// contractCategory moved to ./record-fields.mjs in v0.5.4." },
      { name: "environmentRoots", at: 2990, marker: "// environmentRoots moved to ./record-fields.mjs in v0.5.4." },
      { name: "runPendingControlCount", at: 3267, marker: "// runPendingControlCount moved to ./record-fields.mjs in v0.5.4." },
      // The last three readers of this shape. Indices MEASURED from the pristine fixture, not copied from
      // the current file — `at` is a position in the pre-extraction app.js, and every earlier slice has
      // already shifted the live one.
      { name: "sessionId", at: 1609, marker: "// sessionId moved to ./record-fields.mjs in v0.5.4." },
      { name: "sessionAgentId", at: 1613, marker: "// sessionAgentId moved to ./record-fields.mjs in v0.5.4." },
      { name: "runTargetAgent", at: 1704, marker: "// runTargetAgent moved to ./record-fields.mjs in v0.5.4." },
      { name: "environmentRuntimes", at: 2983, marker: "// environmentRuntimes moved to ./record-fields.mjs in v0.5.4." },
      { name: "asArray", at: 738, marker: "// asArray moved to ./record-fields.mjs in v0.5.4." },
      { name: "contractActionable", at: 1410, marker: "// contractActionable moved to ./record-fields.mjs in v0.5.4." },
    ],
  },
  {
    // status.js ALREADY EXISTED and was already imported, so this slice WIDENS an import rather than
    // adding one. `importWas` is what the line looked like before; reconstruct() restores it instead of
    // deleting the line, which is the difference between proving a widening and proving an insertion.
    module: "status.js",
    // ONE LINE, not a block, and that is load-bearing: reconstruct() locates an import block by
    // `indexOf(block[0])`, so a second `import {` opener in app.js would make the record-fields block
    // resolve to this one instead. The pristine file had this import on a single line; keeping it that
    // way keeps the opener unique rather than teaching the harness to disambiguate mid-slice.
    importLine: "import { AGENT_STATUSES, LIVE_AGENT_STATUSES, STATUS_KINDS, renderStatusChip, resolveStatus, runStatusContext, statusWhyContext } from './status.js';",
    importWas: "import { STATUS_KINDS, AGENT_STATUSES, LIVE_AGENT_STATUSES, resolveStatus, renderStatusChip } from './status.js';",
    items: [
      {
        name: "statusWhyContext",
        at: 438,
        marker: "// statusWhyContext moved to ./status.js in v0.5.4.",
        editedSince: [
          // status_note is a DATABASE COLUMN, never a payload key, so the alternate was dead.
          {
            was: [
              "    if (item.statusNote || item.status_note) parts.push(`Note: ${item.statusNote || item.status_note}.`);",
            ],
            now: [
              "    if (item.statusNote) parts.push(`Note: ${item.statusNote}.`);",
            ],
          },
        ],
      },
      { name: "runStatusContext", at: 3243, marker: "// runStatusContext moved to ./status.js in v0.5.4." },
    ],
  },
  {
    // A NEW module, so `importWas` is absent: reconstruct() deletes the import line rather than
    // restoring a previous one. The line sits immediately after the record-fields block, which is
    // where the extraction put it.
    module: "environment-start-command.mjs",
    importLine: "import { environmentStartCommand } from './environment-start-command.mjs';",
    items: [
      { name: "environmentStartCommand", at: 3106,
        marker: "// environmentStartCommand moved to ./environment-start-command.mjs in v0.5.4." },
    ],
  },
  {
    module: "run-event.mjs",
    importLine: "import { renderRunEvent } from './run-event.mjs';",
    items: [
      { name: "renderEventBody", at: 3271, marker: "// renderEventBody moved to ./run-event.mjs in v0.5.4." },
      { name: "renderRunEvent", at: 3280, marker: "// renderRunEvent moved to ./run-event.mjs in v0.5.4." },
    ],
  },
  {
    module: "terminal-width.mjs",
    importLine: "import { applyRenderedWidth } from './terminal-width.mjs';",
    items: [
      { name: "applyRenderedWidth", at: 2293,
        marker: "// applyRenderedWidth moved to ./terminal-width.mjs in v0.5.4." },
    ],
  },
  {
    module: "cli-resume.mjs",
    importLine: "import { continueCliCommand, continueCliDetails, continueCliInfo, resumeMachineNote } from './cli-resume.mjs';",
    importWas: "import { continueCliInfo, resumeMachineNote } from './cli-resume.mjs';",
    items: [
      { name: "continueCliDetails", at: 3461, marker: "// continueCliDetails moved to ./cli-resume.mjs in v0.5.4." },
      { name: "continueCliCommand", at: 3465, marker: "// continueCliCommand moved to ./cli-resume.mjs in v0.5.4." },
    ],
  },
  {
    // The `state` object — 44 lines, and the first slice here that moves DATA rather than behaviour.
    // It is proven the same way regardless: the declaration is byte-identical to the pristine one, with
    // `export ` added, and reconstruct() strips that because `pristineExported` is absent (app.js declared
    // it privately). What this slice cannot prove by reconstruction is the property that actually matters
    // — that every reader gets ONE object — so `state-identity.test.mjs` carries that half.
    module: "state.mjs",
    importLine: "import { state } from './state.mjs';",
    items: [
      {
        name: "state",
        at: 43,
        marker: "// state moved to ./state.mjs in v0.5.4 — see that module for why the earlier measurement said it would not help.",
        // The Sessions list has always been a PAGE and nothing said so. Measured on the live database
        // 2026-08-28: 303 sessions past the default filter, the dashboard asks for 80, and the empty
        // state offered a Spawn button while 303 existed. `/sessions` now reports `truncated` the way
        // `/contracts` and `/terminals` already did, and this flag is where the render reads it.
        editedSince: [
          // 2026-08-29: the Chat rail is built from the most recent 80 messages and badges unread
          // from that page. The inbox response says `showing`, `total` and `unreadTotal`; the
          // transport dropped all three, so the surface rendered 80 of 3,189 and badged 29 against
          // 1,792. EDITS ARE UNDONE LATEST FIRST, so this goes at the HEAD of the array.
          {
            was: [
              "  runsTruncated: false,",
            ],
            now: [
              "  runsTruncated: false,",
              "  // WHAT THE INBOX SAID ABOUT ITSELF. `/messages/inbox/dashboard` reports `showing`, `total`",
              "  // and `unreadTotal`, and the transport dropped all three -- so Chat rendered 80 of 3,189",
              "  // messages, and badged unread from the 29 inside that page against 1,792 that exist.",
              "  // Zeroes until the first successful load, which renders no note rather than a wrong one.",
              "  inboxCounts: { showing: 0, total: 0, unreadTotal: 0 },",
            ],
          },
          {
            was: [
              "  sessionsTruncated: false,",
            ],
            now: [
              "  sessionsTruncated: false,",
              "  // The Runs list is a page too, and its From / To / runtime dropdowns are built FROM that page,",
              "  // so an agent whose last run fell off it is not merely absent -- it is unselectable, while the",
              "  // empty state invited the operator to adjust the filters. Measured on the live database",
              "  // 2026-08-29: a limit=80 page reached back to 26 August and offered ONE distinct sender.",
              "  runsTruncated: false,",
            ],
          },
          {
            was: [
              "  showSupersededSessions: false,",
            ],
            now: [
              "  showSupersededSessions: false,",
              "  // The Sessions list is a PAGE, and until 2026-08-29 nothing said so. Measured on the live",
              "  // database: 303 sessions survive the default filter, the page asks for 80, and the empty",
              "  // state read \"No sessions yet -- spawn a managed session\" while 303 existed. `/sessions`",
              "  // now reports `truncated`, the way `/contracts` and `/terminals` already did.",
              "  sessionsTruncated: false,",
            ],
          },
        ],
      },
    ],
  },
  {
    // ui.js ALREADY EXISTED and was already imported, so this WIDENS an import rather than adding one
    // -- `importWas` restores the old text instead of deleting the line.
    //
    // `byId` is the second of the three shared leaf names blocking every subject slice in app.js.
    // `state` went first; `apiBase` cannot follow, because it is evaluated at module load from
    // `location` and `localStorage`, and making it lazy would mean editing call sites that stay in
    // app.js -- which this proof forbids by construction.
    module: "ui.js",
    importLine: "import { byId, toast, uiConfirm, uiPrompt, installRejectionToast } from './ui.js';",
    importWas: "import { toast, uiConfirm, uiPrompt, installRejectionToast } from './ui.js';",
    items: [
      {
        name: "byId",
        at: 126,
        marker: "// byId moved to ./ui.js in v0.5.4 \u2014 it is a DOM lookup, and ui.js already owns the DOM helpers.",
      },
    ],
  },
  {
    // The `marker` carries the seeding call as well as the comment. reconstruct() verifies every marker
    // line VERBATIM before splicing it out, which is the same treatment `importLine` gets — and importLine
    // is executable code too. So this is the existing contract, not a loosening of it: the reconstruction
    // still has to come back byte-identical to the pristine fixture.
    module: "api-client.mjs",
    importLine: "import { setApiBase, api } from './api-client.mjs';",
    items: [
      {
        name: "api",
        at: 479,
        marker: [
          "// api moved to ./api-client.mjs in v0.5.4.",
          "setApiBase(apiBase, apiOrigin);",
        ],
        // R5-H1 (2026-08-18): `api()` now attaches the operator key. Declared here because the proof
        // reconstructs app.js from this declaration byte-for-byte, so an undeclared edit to an
        // extracted body reads as "a slice changed something outside its spans" — which is exactly
        // what the gate is for. The `was` text is what app.js carried before the extraction; it is a
        // permanent record of the divergence, not a way around the check.
        //
        // The header is attached AFTER the caller's headers are resolved rather than merged into them:
        // two tests pin that a caller's `headers` REPLACE the default, because `headers: {}` is how a
        // multipart upload drops the JSON content-type. My first attempt merged them and broke upload.
        editedSince: [
          {
            was: [
              "  const response = await fetch(`${apiBase}${path}`, {",
              "    headers: { 'Content-Type': 'application/json' },",
              "    ...options,",
              "  });",
            ],
            now: [
              "  // A CALLER'S HEADERS REPLACE THE DEFAULT \u2014 deliberately, and two tests pin it: `headers: {}` is how",
              "  // file upload drops the JSON content-type, and a multipart POST carrying `application/json` does not",
              "  // upload. My first version merged them and broke exactly that; the tests said so.",
              "  //",
              "  // The operator key is attached AFTER, so it survives either shape without changing which",
              "  // content-type a caller ends up with.",
              "  const { headers: callerHeaders, ...rest } = options;",
              "  const headers = callerHeaders ? { ...callerHeaders } : { 'Content-Type': 'application/json' };",
              "  if (operatorKey) headers['X-Aify-Operator-Key'] = operatorKey;",
              "  const response = await fetch(`${apiBase}${path}`, { headers, ...rest });",
            ],
          },
        ],
      },
    ],
  },
  {
    module: "xterm-lifecycle.mjs",
    importLine: "import { disposeActiveXterm } from './xterm-lifecycle.mjs';",
    items: [
      { name: "disposeActiveXterm", at: 1859, marker: "// disposeActiveXterm moved to ./xterm-lifecycle.mjs in v0.5.4." },
    ],
  },
  {
    module: "version-badge.mjs",
    importLine: "import { loadVersionBadge } from './version-badge.mjs';",
    items: [
      {
        name: "loadVersionBadge", at: 4977,
        marker: "// loadVersionBadge moved to ./version-badge.mjs in v0.5.4.",
        // Remembers the service build it already fetched, so the environments panel can name a
        // bridge running a different one -- see staleBridgeBadge.
        editedSince: [
          // Recorded on a successful fetch, for the environments panel to compare against.
          {
            was: [
              "    const short = esc(v.sha_short || v.sha || '?');",
            ],
            now: [
              "    const short = esc(v.sha_short || v.sha || '?');",
              "    // Recorded before the badge is painted, so a reader that runs in the same tick sees it.",
              "    serviceBuild = String(v.sha_short || v.sha || '').trim();",
            ],
          },
          // And FORGOTTEN on a failed one: an empty build is never compared against.
          {
            was: [
              "    badge.title = 'Build version unavailable';",
            ],
            now: [
              "    badge.title = 'Build version unavailable';",
              "    // FORGOTTEN TOO. The badge blanks itself here rather than leaving the last good value on screen,",
              "    // for the reason this file opens with: the failure that matters is showing something REASSURING",
              "    // when it knows nothing. The remembered build is read by `staleBridgeBadge` to decide whether a",
              "    // bridge is on a different build, so keeping a stale one would have it compare against a service",
              "    // sha that may no longer be what is running. Empty is never compared against.",
              "    serviceBuild = '';",
            ],
          },
        ],
      },
    ],
  },
  {
    module: "message-transport.mjs",
    importLine: "import { chatLoadChannels, chatLoadConversation, chatSendMessage, sendRunFollowup } from './message-transport.mjs';",
    items: [
      {
        name: "chatLoadChannels", at: 141, marker: "// chatLoadChannels moved to ./message-transport.mjs in v0.5.4.",
        // Reports its own failure now: it swallows the error, so the poll cycle's catch could
        // never see one and the noteSliceFailure added there in 85780f7a was dead code.
        editedSince: [{
          was: [
            "  } catch (_) { /* keep prior list */ }",
          ],
          now: [
            "  } catch (_) { noteSliceFailure('channels'); /* keep prior list */ }",
          ],
        }],
      },
      { name: "chatLoadConversation", at: 149, marker: "// chatLoadConversation moved to ./message-transport.mjs in v0.5.4." },
      { name: "chatSendMessage", at: 153, marker: "// chatSendMessage moved to ./message-transport.mjs in v0.5.4." },
      { name: "sendRunFollowup", at: 4055, marker: "// sendRunFollowup moved to ./message-transport.mjs in v0.5.4." },
      { name: "sendMessageWithTimeout", at: 4155, marker: "// sendMessageWithTimeout moved to ./message-transport.mjs in v0.5.4." },
    ],
  },
  {
    module: "shared-files.mjs",
    importLine: "import { attachChatFile, deleteSharedFile, loadFiles, renderFiles, uploadPastedImage, uploadSharedFile } from './shared-files.mjs';",
    items: [
      {
        name: "loadFiles", at: 301, marker: "// loadFiles moved to ./shared-files.mjs in v0.5.4.",
        // Reports its own failure now: it swallows the error, so the poll cycle's catch could
        // never see one and the noteSliceFailure added there in 85780f7a was dead code.
        editedSince: [{
          was: [
            "  try { const res = await api('/shared'); state.files = res.files || res || []; } catch (_) { /* keep prior */ }",
          ],
          now: [
            "  // REPORTED HERE, where the failure is actually seen. The poll cycle wraps this call in its own",
            "  // catch, but that catch can never run: this function swallows its own error, so `await loadFiles()`",
            "  // returns normally on failure and the caller has nothing to catch.",
            "  try { const res = await api('/shared'); state.files = res.files || res || []; }",
            "  catch (_) { noteSliceFailure('files'); /* keep prior */ }",
          ],
        }],
      },
      { name: "renderFiles", at: 310, marker: "// renderFiles moved to ./shared-files.mjs in v0.5.4." },
      { name: "uploadSharedFile", at: 327, marker: "// uploadSharedFile moved to ./shared-files.mjs in v0.5.4." },
      { name: "attachChatFile", at: 353, marker: "// attachChatFile moved to ./shared-files.mjs in v0.5.4." },
      { name: "deleteSharedFile", at: 376, marker: "// deleteSharedFile moved to ./shared-files.mjs in v0.5.4." },
      { name: "pastedImageName", at: 4169, marker: "// pastedImageName moved to ./shared-files.mjs in v0.5.4." },
      { name: "uploadPastedImage", at: 4174, marker: "// uploadPastedImage moved to ./shared-files.mjs in v0.5.4." },
    ],
  },
  {
    module: "api-origin.mjs",
    importLine: "import { resolveApiOrigin } from './api-origin.mjs';",
    items: [
      {
        name: "resolveApiOrigin", at: 16, marker: "// resolveApiOrigin moved to ./api-origin.mjs in v0.5.4.",
        // VALIDATES THE OVERRIDE NOW. `?apiOrigin=` was stored and returned with only trailing
        // slashes stripped, and it feeds every fetch, the WebSocket URL, `legacy.href`, and the Help
        // card's install snippet -- a shell command the operator is told to copy and run.
        editedSince: [{
          was: [
            "  const requested = params.get('apiOrigin');",
            "  if (requested) {",
            "    localStorage.setItem('aify.next.apiOrigin', requested.replace(/\\/+$/, ''));",
            "    return requested.replace(/\\/+$/, '');",
            "  }",
            "  const stored = localStorage.getItem('aify.next.apiOrigin');",
            "  if (stored) return stored.replace(/\\/+$/, '');",
          ],
          now: [
            "  const requested = asHttpOrigin(params.get('apiOrigin'));",
            "  if (requested) {",
            "    localStorage.setItem('aify.next.apiOrigin', requested);",
            "    return requested;",
            "  }",
            "  // A stored value is checked too, not just a fresh one. Validating only the query parameter would",
            "  // leave an override written before this existed \u2014 or by any other route to localStorage \u2014 in force",
            "  // for as long as the browser keeps it.",
            "  const stored = asHttpOrigin(localStorage.getItem('aify.next.apiOrigin'));",
            "  if (stored) return stored;",
            "  localStorage.removeItem('aify.next.apiOrigin');",
          ],
        }],
      },
    ],
  },
  {
    module: "session-rail.mjs",
    importLine: "import { SESSION_FILTER_KINDS, agentForSession, ensureSelectedSession, renderSessionRail, selectedSession, selectedSessionIds } from './session-rail.mjs';",
    items: [
      { name: "agentForSession", at: 1625, marker: "// agentForSession moved to ./session-rail.mjs in v0.5.4." },
      { name: "groupedSessionsByEnvironment", at: 1630, marker: "// groupedSessionsByEnvironment moved to ./session-rail.mjs in v0.5.4." },
      { name: "selectedSessionIds", at: 1662, marker: "// selectedSessionIds moved to ./session-rail.mjs in v0.5.4." },
      { name: "renderSessionBulkToolbar", at: 1722, marker: "// renderSessionBulkToolbar moved to ./session-rail.mjs in v0.5.4." },
      { name: "SESSION_FILTER_KINDS", at: 1740, marker: "// SESSION_FILTER_KINDS moved to ./session-rail.mjs in v0.5.4." },
      { name: "renderSessionStatusFilter", at: 1742, marker: "// renderSessionStatusFilter moved to ./session-rail.mjs in v0.5.4." },
      {
        name: "renderSessionRail", at: 1775, marker: "// renderSessionRail moved to ./session-rail.mjs in v0.5.4.",
        // The workspace is a PATH and no longer wears the prose class alone. `.preview` carries
        // `overflow-wrap: anywhere`, which broke paths mid-word on the live dashboard
        // (`echoes_of_the_fa | llen`) and, because `anywhere` also feeds min-content sizing, let the
        // card shrink and wrap text that fits: 3 of 12 paths dropped from two lines to one.
        //
        // AND THE LIST NOW SAYS IT IS A PAGE. Measured on the live database 2026-08-28: 303 sessions
        // past the default filter, the dashboard asks for 80, and the empty state offered a Spawn
        // button while 303 existed -- which sent an operator to start a second session for an agent
        // that already had one, off the page.
        //
        // ONE ARRAY, not a second `editedSince` key beside the first. An object literal takes the
        // LAST key of a repeated name, so a second one is silently discarded and the gate then fails
        // with the declared edit apparently ignored -- which is what happened writing this entry.
        editedSince: [{
          was: [
            "              <p class=\"preview\">${esc(session.workspace || session.cwd || '')}</p>",
          ],
          now: [
            "              <p class=\"preview session-path\">${esc(session.workspace || session.cwd || '')}</p>",
          ],
        },
        {
          was: [
            "  byId('session-rail').innerHTML = groups.length ? groups.map((group) => `",
          ],
          now: [
            "  // SAID ON SCREEN, AND ONLY WHAT IS TRUE. The list is a page of the newest rows, live ones first, and",
            "  // an operator whose session is not on it gets an empty result that reads as \"it does not exist\".",
            "  // Measured on the live database 2026-08-28: 303 rows past the default filter, 80 requested.",
            "  //",
            "  // THE FIRST VERSION OF THIS NOTE SAID \"Narrow with Find, or filter by status\" AND THAT WAS WORSE",
            "  // THAN SILENCE. Both are client-side over `state.sessions` -- the 80 rows already fetched -- so",
            "  // neither can reach the row the note exists to warn about. A reviewer caught it: advice that cannot",
            "  // work sends an operator round a loop that always ends where it started.",
            "  //",
            "  // What is off the page is HISTORY, not live work: the ordering puts live sessions first, so a",
            "  // bounded page can only lose ended ones. This endpoint's own docstring says it is not a history",
            "  // feed and names where history lives, so the note sends the reader there instead of somewhere the",
            "  // row is not.",
            "  const capped = state.sessionsTruncated",
            "    ? '<div class=\"mb mb-warn\">Showing the most recent sessions, live ones first. Find and the status filter search only these — older sessions are not loaded. Full history is under Environments.</div>'",
            "    : '';",
            "  byId('session-rail').innerHTML = capped + (groups.length ? groups.map((group) => `",
          ],
        },
        {
          was: [
            "    </details>`).join('') : '<div class=\"empty-state\"><span class=\"empty-icon\">🖥️</span><strong>No sessions yet</strong><p>Spawn a managed session from Environments to get an agent running.</p><button class=\"primary\" data-page-jump=\"environments\">Spawn a session</button></div>';",
          ],
          now: [
            "    </details>`).join('') : (state.sessionsTruncated",
            "    // NOT \"no sessions yet\" when the server said there are more. That sentence sent an operator to",
            "    // spawn a second session for an agent that already had one running.",
            "    ? '<div class=\"empty-state\"><span class=\"empty-icon\">🔎</span><strong>None on this page</strong><p>None of the loaded sessions match. Older sessions are not loaded and are not searchable here — full history is under Environments.</p></div>'",
            "    : '<div class=\"empty-state\"><span class=\"empty-icon\">🖥️</span><strong>No sessions yet</strong><p>Spawn a managed session from Environments to get an agent running.</p><button class=\"primary\" data-page-jump=\"environments\">Spawn a session</button></div>'));",
          ],
        }],
      },
      { name: "sessionGroupCollapsed", at: 1805, marker: "// sessionGroupCollapsed moved to ./session-rail.mjs in v0.5.4." },
      { name: "selectedSession", at: 1666, marker: "// selectedSession moved to ./session-rail.mjs in v0.5.4." },
      { name: "ensureSelectedSession", at: 1670, marker: "// ensureSelectedSession moved to ./session-rail.mjs in v0.5.4." },
    ],
  },
  {
    module: "settings-panel.mjs",
    importLine: "import { previewAppearance, refreshActiveTerminalTheme, renderSettings, terminalAccentColor, terminalThemeFromDashboard } from './settings-panel.mjs';",
    items: [
      { name: "EFFORT_OPTS", at: 988, marker: "// EFFORT_OPTS moved to ./settings-panel.mjs in v0.5.4." },
      { name: "PI_EFFORT_OPTS", at: 990, marker: "// PI_EFFORT_OPTS moved to ./settings-panel.mjs in v0.5.4." },
      { name: "SETTINGS_SCHEMA", at: 991, marker: "// SETTINGS_SCHEMA moved to ./settings-panel.mjs in v0.5.4." },
      { name: "SETTINGS_TAB_LABELS", at: 1051, marker: "// SETTINGS_TAB_LABELS moved to ./settings-panel.mjs in v0.5.4." },
      { name: "SETTINGS_TAB_DESC", at: 1055, marker: "// SETTINGS_TAB_DESC moved to ./settings-panel.mjs in v0.5.4." },
      { name: "HELP_TAB", at: 1063, marker: "// HELP_TAB moved to ./settings-panel.mjs in v0.5.4." },
      { name: "activeSettingsTab", at: 1110, marker: "// activeSettingsTab moved to ./settings-panel.mjs in v0.5.4." },
      { name: "renderSettings", at: 1118, marker: "// renderSettings moved to ./settings-panel.mjs in v0.5.4." },
      { name: "readAppearanceInputs", at: 1147, marker: "// readAppearanceInputs moved to ./settings-panel.mjs in v0.5.4." },
      { name: "previewAppearance", at: 1159, marker: "// previewAppearance moved to ./settings-panel.mjs in v0.5.4." },
      { name: "terminalAccentColor", at: 1875, marker: "// terminalAccentColor moved to ./settings-panel.mjs in v0.5.4.",
        // Routed through `normalizedHexColor`, theme.js's exported answer to what a usable hex colour
        // is. This hand-rolled the same regex, as did settings-fields.mjs -- three implementations of
        // one question, which agree until somebody widens one of them.
        editedSince: [
        {
          was: [
          "    const v = getComputedStyle(document.body).getPropertyValue('--accent').trim();",
          "    if (/^#[0-9a-fA-F]{6}$/.test(v)) return v;",
        ],
          now: [
          "    // `normalizedHexColor` is the one place that decides what a usable hex colour is. This hand-rolled",
          "    // the same regex, as did settings-fields.mjs -- three implementations of one question, which agree",
          "    // until somebody widens one of them.",
          "    const v = normalizedHexColor(getComputedStyle(document.body).getPropertyValue('--accent'), '');",
          "    if (v) return v;",
        ],
        },
      ],
      },
      { name: "terminalThemeFromDashboard", at: 1883, marker: "// terminalThemeFromDashboard moved to ./settings-panel.mjs in v0.5.4." },
      { name: "refreshActiveTerminalTheme", at: 1901, marker: "// refreshActiveTerminalTheme moved to ./settings-panel.mjs in v0.5.4." },
    ],
  },
  {
    module: "agent-drawer.mjs",
    importLine: "import { openAgentDrawer, sessionForAgent, syncInspectorToSelection } from './agent-drawer.mjs';",
    items: [
      { name: "sessionForAgent", at: 1708, marker: "// sessionForAgent moved to ./agent-drawer.mjs in v0.5.4." },
      { name: "openAgentDrawer", at: 3469, marker: "// openAgentDrawer moved to ./agent-drawer.mjs in v0.5.4." },
      { name: "syncInspectorToSelection", at: 3558, marker: "// syncInspectorToSelection moved to ./agent-drawer.mjs in v0.5.4." },
    ],
  },
  {
    module: "work-loop-panels.mjs",
    importLine: "import { contractCard, diagnosticKey, filtered, renderActivityFeed, renderAttention, renderContractBoard } from './work-loop-panels.mjs';",
    items: [
      { name: "filtered", at: 911, marker: "// filtered moved to ./work-loop-panels.mjs in v0.5.4." },
      { name: "contractCard", at: 1386, marker: "// contractCard moved to ./work-loop-panels.mjs in v0.5.4." },
      { name: "renderAttention", at: 1416, marker: "// renderAttention moved to ./work-loop-panels.mjs in v0.5.4.",
        // The strip's count moved into the HEADER, which is the only part `.collapsed` leaves
        // visible -- so the panel could not say whether anything needed attention in the state an
        // operator leaves it in. The count is taken BEFORE the 8-item cap, so a truncated list
        // cannot present itself as a total. `attentionSummaryLabel` is a NEW declaration in the
        // module and so belongs to no span here.
        editedSince: [
        {
          was: [
          "  const items = filtered(state.contracts, ['subject', 'preview', 'from', 'targetAgentId'])",
          "    .filter((c) => c.overdue || c.state === 'working' || c.state === 'queued')",
          "    .slice(0, 8);",
        ],
          now: [
          "  // Counted BEFORE the cap, so the header can tell a full list from a truncated one.",
          "  const matching = filtered(state.contracts, ['subject', 'preview', 'from', 'targetAgentId'])",
          "    .filter((c) => c.overdue || c.state === 'working' || c.state === 'queued');",
          "  const items = matching.slice(0, 8);",
          "  const summary = byId('attention-summary');",
          "  if (summary) {",
          "    summary.textContent = attentionSummaryLabel(matching.length, items.length);",
          "    // `chat-unread` is the accent count pill the conversation rail already uses, so a count here",
          "    // reads as the same kind of thing it does there -- and needs no new rule on a stylesheet that is",
          "    // already 1,844 lines and outside both size gates.",
          "    summary.className = matching.length ? 'chat-unread' : 'subtle';",
          "  }",
        ],
        },
      ] },
      { name: "diagnosticKey", at: 1429, marker: "// diagnosticKey moved to ./work-loop-panels.mjs in v0.5.4." },
      { name: "activityItems", at: 1520, marker: "// activityItems moved to ./work-loop-panels.mjs in v0.5.4." },
      { name: "renderActivityFeed", at: 1553, marker: "// renderActivityFeed moved to ./work-loop-panels.mjs in v0.5.4." },
      { name: "CONTRACT_BOARD_COLUMNS", at: 2918, marker: "// CONTRACT_BOARD_COLUMNS moved to ./work-loop-panels.mjs in v0.5.4." },
      { name: "renderContractBoard", at: 2927, marker: "// renderContractBoard moved to ./work-loop-panels.mjs in v0.5.4." },
    ],
  },
  {
    module: "codex-console.mjs",
    importLine: "import { codexConsoleAppendLine, codexConsoleClose, codexConsoleConnect, codexConsoleConnections, codexConsoleSendTurn } from './codex-console.mjs';",
    items: [
      { name: "codexConsoleConnections", at: 2446, marker: "// codexConsoleConnections moved to ./codex-console.mjs in v0.5.4." },
      { name: "codexConsoleClose", at: 2450, marker: "// codexConsoleClose moved to ./codex-console.mjs in v0.5.4." },
      { name: "codexConsoleAppendLine", at: 2457, marker: "// codexConsoleAppendLine moved to ./codex-console.mjs in v0.5.4." },
      { name: "codexConsoleAppendText", at: 2468, marker: "// codexConsoleAppendText moved to ./codex-console.mjs in v0.5.4." },
      { name: "codexConsoleConnect", at: 2482, marker: "// codexConsoleConnect moved to ./codex-console.mjs in v0.5.4." },
      { name: "codexConsoleSendTurn", at: 2558, marker: "// codexConsoleSendTurn moved to ./codex-console.mjs in v0.5.4." },
    ],
  },
  {
    module: "identity-directory.mjs",
    importLine: "import { openIdentityDirectory } from './identity-directory.mjs';",
    items: [
      {
        name: "openIdentityDirectory",
        at: 3402,
        marker: "// openIdentityDirectory moved to ./identity-directory.mjs in v0.5.4.",
        editedSince: [
          // Dropped dead snake_case alternates: the service emits lastSeen and unread, never last_seen or unreadCount.
          {
            was: [
              "    const lastSeen = agent.lastSeen || agent.last_seen || '';",
            ],
            now: [
              "    const lastSeen = agent.lastSeen || '';",
            ],
          },
          {
            was: [
              "      <td>${Number(agent.unread || agent.unreadCount || 0) || 0}</td>",
            ],
            now: [
              "      <td>${Number(agent.unread || 0) || 0}</td>",
            ],
          },
        ],
      },
    ],
  },
  {
    module: "status-why-popover.mjs",
    importLine: "import { closeStatusWhy, openStatusWhy } from './status-why-popover.mjs';",
    items: [
      { name: "_statusWhyReturnFocus", at: 1577, marker: "// _statusWhyReturnFocus moved to ./status-why-popover.mjs in v0.5.4." },
      { name: "openStatusWhy", at: 1578, marker: "// openStatusWhy moved to ./status-why-popover.mjs in v0.5.4." },
      { name: "closeStatusWhy", at: 1600, marker: "// closeStatusWhy moved to ./status-why-popover.mjs in v0.5.4." },
    ],
  },
  {
    module: "session-activity.mjs",
    importLine: "import { renderSessionActivity, runFrom } from './session-activity.mjs';",
    items: [
      { name: "messagesForSession", at: 1687, marker: "// messagesForSession moved to ./session-activity.mjs in v0.5.4." },
      {
        name: "renderSessionActivity", at: 1818,
        marker: "// renderSessionActivity moved to ./session-activity.mjs in v0.5.4.",
        // No longer pre-escapes a label renderStatusChip escapes again.
        editedSince: [{
          was: [
            "      <div class=\"item-title\"><strong>${esc(m.from || 'unknown')}</strong>${renderStatusChip(m.read ? 'completed' : 'queued', { label: esc(m.type || (m.read ? 'read' : 'unread')), why: `Message ${m.read ? 'read' : 'unread'}.` })}</div>",
          ],
          now: [
            "      <div class=\"item-title\"><strong>${esc(m.from || 'unknown')}</strong>${renderStatusChip(m.read ? 'completed' : 'queued', { label: m.type || (m.read ? 'read' : 'unread'), why: `Message ${m.read ? 'read' : 'unread'}.` })}</div>",
          ],
        }],
      },
      { name: "runFrom", at: 3175, marker: "// runFrom moved to ./session-activity.mjs in v0.5.4." },
    ],
  },
  {
    module: "environments-panels.mjs",
    // The import line was EDITED by the later actions slice rather than a new one being added, so this
    // entry names the line as it stands now. `importWas` stays absent: the pristine file had no such
    // import at all, so unwinding still deletes the line rather than restoring anything.
    importLine: "import { controlEnvironment, createSpawnRequest, initEnvironmentActions, openEnvironmentRootsEditor, renderEnvironmentSpawnOptions, renderEnvironmentSummary, renderRuntime, renderSpawnRequests, resetEnvironmentRoots, submitEnvironmentRoots } from './environments-panels.mjs';",
    seeding: "initEnvironmentActions({ closeInspector, inspect, refresh, refreshSoon });",
    items: [
      { name: "renderEnvironmentSpawnOptions", at: 3010, marker: "// renderEnvironmentSpawnOptions moved to ./environments-panels.mjs in v0.5.4." },
      {
        name: "renderRuntime", at: 3038, marker: "// renderRuntime moved to ./environments-panels.mjs in v0.5.4.",
        // An OFFLINE environment now says how long it has been silent. `offline` alone read the same
        // for a host that dropped a minute ago and one abandoned in June, and `lastSeen` was already
        // on the wire — the card simply dropped it.
        // Also dropped a dead snake_case alternate: the environment payload emits machineId, never
        // machine_id, so the `||` branch could never be taken.
        // Also names a bridge running a different build than the service -- the blind spot that sent
        // the operator into two aify-env restarts for something only a bridge relaunch fixes.
        editedSince: [{
          was: [
            "      <p class=\"preview\">${esc(env.kind || env.os || '')} · ${esc(env.machineId || env.machine_id || '')}</p>",
          ],
          now: [
            "      <p class=\"preview\">${esc(env.kind || env.os || '')} \u00b7 ${esc(env.machineId || '')}${offlineAge(env)}${staleBridgeBadge(env)}${unknownProcessBadge(env)}</p>",
          ],
        }, {
          // A SECOND PAIR, not an amendment to the first: this edit covers a DIFFERENT line. `was`
          // and `now` must span the same region, so inserting a line before an existing one is
          // expressed as that one line becoming two.
          //
          // An environment that cannot open a terminal now says WHY. Without it, the fix that made
          // `terminal` honest was a trade: agents went from wrongly `available` to correctly
          // `offline` with no stated cause, which sends an operator hunting a delivery bug -- the
          // same wrong hunt, one tier over.
          was: [
            "      <div class=\"env-runtime-list\">",
          ],
          now: [
            "      ${terminalReasonNote(env)}",
            "      <div class=\"env-runtime-list\">",
          ],
        }],
      },
      { name: "renderSpawnRequests", at: 3063, marker: "// renderSpawnRequests moved to ./environments-panels.mjs in v0.5.4." },
      { name: "renderEnvironmentSummary", at: 2995, marker: "// renderEnvironmentSummary moved to ./environments-panels.mjs in v0.5.4." },
      { name: "openEnvironmentRootsEditor", at: 3122, marker: "// openEnvironmentRootsEditor moved to ./environments-panels.mjs in v0.5.4." },
      // The four ACTIONS, added later in v0.5.4. They landed in this module rather than a new one
      // because an environment's actions and the panels that render them are one subject.
      { name: "controlEnvironment", at: 3092, marker: "// controlEnvironment moved to ./environments-panels.mjs in v0.5.4." },
      { name: "submitEnvironmentRoots", at: 3153, marker: "// submitEnvironmentRoots moved to ./environments-panels.mjs in v0.5.4." },
      { name: "resetEnvironmentRoots", at: 3165, marker: "// resetEnvironmentRoots moved to ./environments-panels.mjs in v0.5.4." },
      { name: "createSpawnRequest", at: 3994, marker: "// createSpawnRequest moved to ./environments-panels.mjs in v0.5.4." },
    ],
  },
  {
    module: "summary-tiles.mjs",
    importLine: "import { metric, renderDiagnosticsSummary, renderMetrics, renderUsageConsumption, selectedDiagnostics } from './summary-tiles.mjs';",
    items: [
      { name: "renderUsageConsumption", at: 1313, marker: "// renderUsageConsumption moved to ./summary-tiles.mjs in v0.5.4." },
      { name: "metric", at: 1366, marker: "// metric moved to ./summary-tiles.mjs in v0.5.4." },
      { name: "renderMetrics", at: 1371, marker: "// renderMetrics moved to ./summary-tiles.mjs in v0.5.4." },
      { name: "selectedDiagnostics", at: 1433, marker: "// selectedDiagnostics moved to ./summary-tiles.mjs in v0.5.4." },
      { name: "renderDiagnosticsSummary", at: 1456, marker: "// renderDiagnosticsSummary moved to ./summary-tiles.mjs in v0.5.4." },
    ],
  },
  {
    module: "clipboard.mjs",
    importLine: "import { copyActiveConsole, copyText } from './clipboard.mjs';",
    items: [
      { name: "copyText", at: 2366, marker: "// copyText moved to ./clipboard.mjs in v0.5.4." },
      {
        name: "copyActiveConsole",
        at: 2381,
        marker: "// copyActiveConsole moved to ./clipboard.mjs in v0.5.4.",
        // FIRST DECLARED POST-EXTRACTION EDIT. `copyText(...).then(...)` had no `.catch`, so a rejected
        // clipboard write was an unhandled rejection inside a keydown listener and the operator got no
        // message at all — the same outcome the `false` branch of that toast exists to prevent. Fixing
        // it changes a body the proof reconstructs, so the change is written down here rather than
        // silently tolerated.
        editedSince: [{
          was: "  copyText(text).then((ok) => toast(ok ? 'Console copied' : 'Copy failed', ok ? 'ok' : 'error'));",
          now: [
            "  // `.catch` as well as `.then`: `copyText` RESOLVES false on a refused clipboard, but it can also",
            "  // REJECT — the execCommand fallback throws on a detached document. Without this the rejection is",
            "  // unhandled inside a keydown listener, and the operator gets no message at all for a failed copy,",
            "  // which is the same outcome the false branch exists to prevent.",
            "  copyText(text)",
            "    .then((ok) => toast(ok ? 'Console copied' : 'Copy failed', ok ? 'ok' : 'error'))",
            "    .catch(() => toast('Copy failed', 'error'));",
          ],
        }],
      },
    ],
  },
  {
    module: "inspector-forms.mjs",
    importLine: "import { openAgentEditForm, openCompactionHistory, openContinueForm, openMessageDetail } from './inspector-forms.mjs';",
    items: [
      {
        name: "openAgentEditForm",
        at: 3609,
        marker: "// openAgentEditForm moved to ./inspector-forms.mjs in v0.5.4.",
        editedSince: [
          // Dropped a dead alternate: the environment payload emits id, never environmentId.
          {
            was: [
              "      const id = String(env.id || env.environmentId || '');",
            ],
            now: [
              "      const id = String(env.id || '');",
            ],
          },
          // Dropped a dead snake_case alternate: the service emits sessionHandle.
          {
            was: [
              "      <label class=\"settings-label\">Native session handle<input id=\"edit-agent-handle\" type=\"text\" value=\"${esc(agent.sessionHandle || agent.session_handle || '')}\" placeholder=\"Claude/Codex/Pi session id \u2014 blank clears\"></label>",
            ],
            now: [
              "      <label class=\"settings-label\">Native session handle<input id=\"edit-agent-handle\" type=\"text\" value=\"${esc(agent.sessionHandle || '')}\" placeholder=\"Claude/Codex/Pi session id \u2014 blank clears\"></label>",
            ],
          },
        ],
      },
      { name: "openMessageDetail", at: 3696, marker: "// openMessageDetail moved to ./inspector-forms.mjs in v0.5.4." },
      {
        name: "openCompactionHistory",
        at: 3575,
        marker: "// openCompactionHistory moved to ./inspector-forms.mjs in v0.5.4.",
        // THE PANEL READ ITS METADATA AT THE WRONG LEVEL. `_spawn_request_to_dict` emits no
        // top-level `metadata`; the spec's metadata arrives as `spawnSpec.metadata`, so
        // `r.metadata || {}` was always empty and every branch it drove was dead. Measured against
        // the live service over 200 spawn records: top-level `metadata` on 0, `spawnSpec.metadata`
        // on 149. The logic moved to the exported `spawnRecordLineage`, which is a NEW declaration
        // in the module and therefore not part of any span here.
        editedSince: [
        // No longer pre-escapes a label renderStatusChip escapes again: it rendered `a & b` as `a &amp;amp; b`.
        {
          was: [
            "      <div class=\"history-head\"><strong>${esc(mode)}</strong>${renderStatusChip(r.status || 'queued', { label: esc(r.status || 'queued'), why: `Spawn request ${r.status || 'queued'}.` })}</div>",
          ],
          now: [
            "      <div class=\"history-head\"><strong>${esc(mode)}</strong>${renderStatusChip(r.status || 'queued', { label: r.status || 'queued', why: `Spawn request ${r.status || 'queued'}.` })}</div>",
          ],
        },
        {
          was: [
          "      const m = r.metadata || {};",
          "      return m.continuedFromAgentId === agentId || r.agentId === agentId || r.agent_id === agentId;",
        ],
          now: [
          "      // An agent's history is the records it CAME FROM as well as the ones that produced it.",
          "      const { fromAgentId } = spawnRecordLineage(r);",
          "      return fromAgentId === agentId || r.agentId === agentId || r.agent_id === agentId;",
        ],
        },
        {
          was: [
          "    const m = r.metadata || {};",
          "    const mode = m.splitIdentity ? 'Continue-as' : m.compactMode === 'handoff' ? 'Compact' : 'Spawn';",
        ],
          now: [
          "    const { mode, fromAgentId, fromSessionId, requestedBy, selfRequested } = spawnRecordLineage(r);",
        ],
        },
        {
          was: [
          "        <dt>New agent</dt><dd>${esc(r.agentId || r.agent_id || '—')}</dd>",
        ],
          now: [
          "        <dt>New agent</dt><dd>${esc(r.agentId || r.agent_id || '—')}</dd>",
          "        <dt>Requested by</dt><dd>${esc(requestedBy || 'not recorded')}${selfRequested ? ' <span class=\"subtle\">(itself)</span>' : ''}</dd>",
        ],
        },
        {
          was: [
          "        ${m.continuedFromAgentId ? `<dt>From agent</dt><dd>${esc(m.continuedFromAgentId)}</dd>` : ''}",
          "        ${m.continuedFromSessionId ? `<dt>From session</dt><dd class=\"clip\">${esc(m.continuedFromSessionId)}</dd>` : ''}",
        ],
          now: [
          "        ${fromAgentId ? `<dt>From agent</dt><dd>${esc(fromAgentId)}</dd>` : ''}",
          "        ${fromSessionId ? `<dt>From session</dt><dd class=\"clip\">${esc(fromSessionId)}</dd>` : ''}",
        ],
        },
      ],
      },
      { name: "buildHandoffPacket", at: 3723, marker: "// buildHandoffPacket moved to ./inspector-forms.mjs in v0.5.4." },
      { name: "openContinueForm", at: 3731, marker: "// openContinueForm moved to ./inspector-forms.mjs in v0.5.4." },
    ],
  },
  {
    module: "run-inspector-controls.mjs",
    importLine: "import { renderRunInspectorControls, runInspectorCapabilities, sessionForRun } from './run-inspector-controls.mjs';",
    items: [
      { name: "sessionForRun", at: 1712, marker: "// sessionForRun moved to ./run-inspector-controls.mjs in v0.5.4." },
      { name: "runInspectorCapabilities", at: 3252, marker: "// runInspectorCapabilities moved to ./run-inspector-controls.mjs in v0.5.4." },
      { name: "renderRunInspectorControls", at: 3292, marker: "// renderRunInspectorControls moved to ./run-inspector-controls.mjs in v0.5.4." },
    ],
  },
  {
    module: "chat-prefs.mjs",
    importLine: "import { persistChatDrafts, persistChatPrefs, syncChatChips } from './chat-prefs.mjs';",
    items: [
      { name: "persistChatPrefs", at: 4803, marker: "// persistChatPrefs moved to ./chat-prefs.mjs in v0.5.4." },
      { name: "syncChatChips", at: 4815, marker: "// syncChatChips moved to ./chat-prefs.mjs in v0.5.4." },
      { name: "persistChatDrafts", at: 4907, marker: "// persistChatDrafts moved to ./chat-prefs.mjs in v0.5.4." },
    ],
  },
  // THE FIRST EXTRACT-METHOD, and the first entry whose item is not a whole declaration in the pristine
  // file. app.js's delegated click handler holds 82 branch bodies and not one of them is a declaration, so
  // until `wrapper` existed this plan could not describe a single line of it. The guard and the `return;`
  // stay in app.js: only the body moved, dedented by two, into the module that already owned everything it
  // touches.
  {
    module: "settings-panel.mjs",
    importLine: "import { applyThemeChoice, previewAppearance, refreshActiveTerminalTheme, renderSettings, terminalAccentColor, terminalThemeFromDashboard } from './settings-panel.mjs';",
    importWas: "import { previewAppearance, refreshActiveTerminalTheme, renderSettings, terminalAccentColor, terminalThemeFromDashboard } from './settings-panel.mjs';",
    items: [
      {
        name: "applyThemeChoice",
        at: 4246,
        marker: "    applyThemeChoice(themeChoice);",
        wrapper: {
          header: ["export function applyThemeChoice(themeChoice) {"],
          footer: ["}"],
          dedent: "  ",
        },
      },
    ],
  },

  // The two chat-shell toggles. Their bodies take NO parameter — neither uses the element `closest()`
  // matched, only `state.chat` — so the extracted functions are nullary and the guard left in app.js still
  // owns the element. Appended AFTER the older chat-prefs entry on purpose: this edits the import line that
  // one wrote, and unwinding is newest-first.
  {
    module: "chat-prefs.mjs",
    importLine: "import { persistChatDrafts, persistChatPrefs, syncChatChips, toggleChatCompact, toggleChatPeek } from './chat-prefs.mjs';",
    importWas: "import { persistChatDrafts, persistChatPrefs, syncChatChips } from './chat-prefs.mjs';",
    items: [
      {
        name: "toggleChatCompact",
        at: 4849,
        marker: "    toggleChatCompact();",
        wrapper: { header: ["export function toggleChatCompact() {"], footer: ["}"], dedent: "  " },
      },
      {
        name: "toggleChatPeek",
        at: 4855,
        marker: "    toggleChatPeek();",
        wrapper: { header: ["export function toggleChatPeek() {"], footer: ["}"], dedent: "  " },
      },
    ],
  },

  {
    module: "settings-panel.mjs",
    importLine: "import { applyThemeChoice, previewAppearance, refreshActiveTerminalTheme, renderSettings, selectSettingsTab, terminalAccentColor, terminalThemeFromDashboard } from './settings-panel.mjs';",
    importWas: "import { applyThemeChoice, previewAppearance, refreshActiveTerminalTheme, renderSettings, terminalAccentColor, terminalThemeFromDashboard } from './settings-panel.mjs';",
    items: [
      {
        name: "selectSettingsTab",
        at: 4239,
        marker: "    selectSettingsTab(settingsTab);",
        wrapper: { header: ["export function selectSettingsTab(settingsTab) {"], footer: ["}"], dedent: "  " },
      },
    ],
  },
  {
    module: "work-loop-panels.mjs",
    importLine: "import { applyWorkView, contractCard, diagnosticKey, filtered, jumpFromDiagnostic, renderActivityFeed, renderAttention, renderContractBoard } from './work-loop-panels.mjs';",
    importWas: "import { contractCard, diagnosticKey, filtered, renderActivityFeed, renderAttention, renderContractBoard } from './work-loop-panels.mjs';",
    items: [
      {
        name: "applyWorkView",
        at: 4332,
        marker: "    applyWorkView(workView);",
        wrapper: { header: ["export function applyWorkView(workView) {"], footer: ["}"], dedent: "  " },
      },
      {
        name: "jumpFromDiagnostic",
        at: 4351,
        marker: "    jumpFromDiagnostic(diagJump);",
        wrapper: { header: ["export function jumpFromDiagnostic(diagJump) {"], footer: ["}"], dedent: "  " },
      },
    ],
  },

  {
    module: "session-rail.mjs",
    importLine: "import { SESSION_FILTER_KINDS, agentForSession, ensureSelectedSession, renderSessionRail, selectedSession, selectedSessionIds, toggleSupersededSessions } from './session-rail.mjs';",
    importWas: "import { SESSION_FILTER_KINDS, agentForSession, ensureSelectedSession, renderSessionRail, selectedSession, selectedSessionIds } from './session-rail.mjs';",
    items: [
      {
        name: "toggleSupersededSessions",
        at: 4391,
        marker: "    toggleSupersededSessions();",
        wrapper: { header: ["export function toggleSupersededSessions() {"], footer: ["}"], dedent: "  " },
      },
    ],
  },

  // FIRST SLICE TO INJECT. `chatController` cannot move — app.js builds it from app.js-local callbacks —
  // so these bodies take it as a parameter. The body text is untouched: the name it reads is a parameter
  // now instead of a module-scope const, which is exactly what the header declaration records.
  {
    module: "chat-click-handlers.mjs",
    importLine: "import { openChatConversation, openChatReply, runChannelAction, setChatView, setPulseWindow } from './chat-click-handlers.mjs';",
    items: [
      {
        name: "openChatReply",
        at: 4275,
        marker: "    openChatReply(chatReply, chatController);",
        wrapper: {
          header: ["export function openChatReply(chatReply, chatController) {"],
          footer: ["}"], dedent: "  ",
        },
      },
      {
        name: "openChatConversation",
        at: 4295,
        marker: "    openChatConversation(chatOpen, chatController, markConversationRead);",
        wrapper: {
          header: ["export function openChatConversation(chatOpen, chatController, markConversationRead) {"],
          footer: ["}"], dedent: "  ",
        },
      },
      {
        name: "setPulseWindow",
        at: 4310,
        marker: "    setPulseWindow(pulseWindow, chatController);",
        wrapper: {
          header: ["export function setPulseWindow(pulseWindow, chatController) {"],
          footer: ["}"], dedent: "  ",
        },
      },
      {
        name: "setChatView",
        at: 4319,
        marker: "    setChatView(chatView, chatController);",
        wrapper: {
          header: ["export function setChatView(chatView, chatController) {"],
          footer: ["}"], dedent: "  ",
        },
      },

      {
        name: "runChannelAction",
        at: 4440,
        marker: "    runChannelAction(chanAction, chatChannelAction);",
        wrapper: {
          header: ["export function runChannelAction(chanAction, chatChannelAction) {"],
          footer: ["}"], dedent: "  ",
        },
      },
    ],
  },

  // The status.js import shed LIVE_AGENT_STATUSES: its only reader in app.js was SESSION_LIVE_KINDS,
  // which moved. Declared as its own entry with no items, so the edit is unwound rather than left as a
  // dead import the reconstruction would have to tolerate.
  {
    module: "session-click-handlers.mjs",
    importLine: "import { AGENT_STATUSES, STATUS_KINDS, renderStatusChip, resolveStatus, runStatusContext, statusWhyContext } from './status.js';",
    importWas: "import { AGENT_STATUSES, LIVE_AGENT_STATUSES, STATUS_KINDS, renderStatusChip, resolveStatus, runStatusContext, statusWhyContext } from './status.js';",
    items: [],
  },
  {
    module: "session-click-handlers.mjs",
    importLine: "import { applySessionStatusPreset, toggleSessionCheckbox, toggleSessionStatusFilter } from './session-click-handlers.mjs';",
    items: [
      {
        name: "SESSION_LIVE_KINDS",
        at: 1741,
        marker: "// SESSION_LIVE_KINDS moved to ./session-click-handlers.mjs in v0.5.4.",
      },
      {
        name: "persistSessionStatusFilter",
        at: 1771,
        marker: "// persistSessionStatusFilter moved to ./session-click-handlers.mjs in v0.5.4.",
      },
      {
        name: "applySessionStatusPreset",
        at: 4397,
        marker: "    applySessionStatusPreset(sessionStatusPreset, renderSessionWorkspace);",
        wrapper: {
          header: ["export function applySessionStatusPreset(sessionStatusPreset, renderSessionWorkspace) {"],
          footer: ["}"], dedent: "  ",
        },
      },
      {
        name: "toggleSessionStatusFilter",
        at: 4405,
        marker: "    toggleSessionStatusFilter(sessionStatusFilter, renderSessionWorkspace);",
        wrapper: {
          header: ["export function toggleSessionStatusFilter(sessionStatusFilter, renderSessionWorkspace) {"],
          footer: ["}"], dedent: "  ",
        },
      },
      {
        name: "toggleSessionCheckbox",
        at: 4565,
        marker: "    toggleSessionCheckbox(sessionCheckbox, renderSessionWorkspace);",
        wrapper: {
          header: ["export function toggleSessionCheckbox(sessionCheckbox, renderSessionWorkspace) {"],
          footer: ["}"], dedent: "  ",
        },
      },
    ],
  },

  {
    module: "agent-click-handlers.mjs",
    importLine: "import { runAgentControl, startColdAgent, switchAgentModeFromRow, switchModeFromChip, toggleFavouriteRow } from './agent-click-handlers.mjs';",
    items: [
      {
        name: "startColdAgent",
        at: 4498,
        marker: "    startColdAgent(agentAction, refreshSoon);",
        wrapper: {
          header: ["export function startColdAgent(agentAction, refreshSoon) {"],
          footer: ["}"], dedent: "  ",
        },
      },
      {
        name: "switchModeFromChip",
        at: 4575,
        marker: "    switchModeFromChip(modeSwitchButton, event, switchAgentSessionMode);",
        wrapper: {
          header: ["export function switchModeFromChip(modeSwitchButton, event, switchAgentSessionMode) {"],
          footer: ["}"], dedent: "  ",
        },
      },

      {
        name: "toggleFavouriteRow",
        at: 4263,
        marker: "    toggleFavouriteRow(favToggle, event, toggleFavorite);",
        wrapper: {
          header: ["export function toggleFavouriteRow(favToggle, event, toggleFavorite) {"],
          footer: ["}"], dedent: "  ",
        },
      },
      {
        name: "runAgentControl",
        at: 4371,
        marker: "    runAgentControl(agentControl, requestSessionControl);",
        wrapper: {
          header: ["export function runAgentControl(agentControl, requestSessionControl) {"],
          footer: ["}"], dedent: "  ",
        },
      },
      {
        name: "switchAgentModeFromRow",
        at: 4377,
        marker: "    switchAgentModeFromRow(agentMode, switchAgentSessionMode);",
        wrapper: {
          header: ["export function switchAgentModeFromRow(agentMode, switchAgentSessionMode) {"],
          footer: ["}"], dedent: "  ",
        },
      },
    ],
  },

  {
    module: "session-click-handlers.mjs",
    importLine: "import { applySessionStatusPreset, openAgentSessions, selectSessionRow, selectSessionTab, toggleSessionCheckbox, toggleSessionStatusFilter } from './session-click-handlers.mjs';",
    importWas: "import { applySessionStatusPreset, toggleSessionCheckbox, toggleSessionStatusFilter } from './session-click-handlers.mjs';",
    items: [
      {
        name: "openAgentSessions",
        at: 4383,
        marker: "    openAgentSessions(agentOpenSessions, renderSessionWorkspace, setPage, closeInspector);",
        wrapper: {
          header: ["export function openAgentSessions(agentOpenSessions, renderSessionWorkspace, setPage, closeInspector) {"],
          footer: ["}"], dedent: "  ",
        },
      },
      {
        name: "selectSessionRow",
        at: 4584,
        marker: "    selectSessionRow(sessionSelect, renderSessionWorkspace);",
        wrapper: {
          header: ["export function selectSessionRow(sessionSelect, renderSessionWorkspace) {"],
          footer: ["}"], dedent: "  ",
        },
      },

      {
        name: "selectSessionTab",
        at: 4592,
        marker: "    selectSessionTab(sessionTab, renderSessionWorkspace);",
        wrapper: {
          header: ["export function selectSessionTab(sessionTab, renderSessionWorkspace) {"],
          footer: ["}"], dedent: "  ",
        },
      },
    ],
  },
  {
    module: "work-loop-panels.mjs",
    importLine: "import { applyWorkView, contractCard, diagnosticKey, filtered, jumpFromDiagnostic, renderActivityFeed, renderAttention, renderContractBoard, toggleDiagnosticSelection } from './work-loop-panels.mjs';",
    importWas: "import { applyWorkView, contractCard, diagnosticKey, filtered, jumpFromDiagnostic, renderActivityFeed, renderAttention, renderContractBoard } from './work-loop-panels.mjs';",
    items: [
      {
        name: "toggleDiagnosticSelection",
        at: 4528,
        marker: "    toggleDiagnosticSelection(diagnosticSelect, renderDiagnosticsBulkToolbar);",
        wrapper: {
          header: ["export function toggleDiagnosticSelection(diagnosticSelect, renderDiagnosticsBulkToolbar) {"],
          footer: ["}"], dedent: "  ",
        },
      },
    ],
  },

  {
    module: "nav-click-handlers.mjs",
    importLine: "import { navigateToPage, openEnvironmentSpawn, openHermesTabFromRow, selectAnalyticsRange } from './nav-click-handlers.mjs';",
    items: [
      {
        name: "selectAnalyticsRange",
        at: 4515,
        marker: "    selectAnalyticsRange(analyticsRange, loadAnalytics);",
        wrapper: {
          header: ["export function selectAnalyticsRange(analyticsRange, loadAnalytics) {"],
          footer: ["}"], dedent: "  ",
        },
      },
      {
        name: "navigateToPage",
        editedSince: [
          {
            was: [
            ],
            now: [
              "    // Same rule, larger slice: /spawn-requests is polled only while the Environments page is open",
              "    // (414,690 of a 1,419,728 byte cycle), so opening it must fetch once or the table shows its",
              "    // previous contents -- or nothing at all on a first open -- for up to a full refresh interval.",
              "    if (page === 'environments') loadSpawnRequests();",
            ],
          },
          {
          was: [],
          now: [
            "    // Files is polled only while its page is open (see files-page.mjs), so opening it must fetch once.",
            "    // Without this the page would show whatever was last seen -- or nothing at all on a first open --",
            "    // for up to a full refresh interval, 15 seconds by default.",
            "    if (page === 'files') loadFiles().then(renderFiles);",
          ],
        }],
        at: 4521,
        marker: "    navigateToPage(page, setPage, loadAnalytics);",
        wrapper: {
          header: ["export function navigateToPage(page, setPage, loadAnalytics) {"],
          footer: ["}"], dedent: "  ",
        },
      },
      {
        name: "openEnvironmentSpawn",
        at: 4546,
        marker: "    openEnvironmentSpawn(envSpawn, setPage, renderEnvironmentSpawnOptions);",
        wrapper: {
          header: ["export function openEnvironmentSpawn(envSpawn, setPage, renderEnvironmentSpawnOptions) {"],
          footer: ["}"], dedent: "  ",
        },
      },

      {
        name: "openHermesTabFromRow",
        at: 4456,
        marker: "    openHermesTabFromRow(openHermesTab);",
        wrapper: {
          header: ["export function openHermesTabFromRow(openHermesTab) {"],
          footer: ["}"], dedent: "  ",
        },
      },
    ],
  },

  {
    module: "work-loop-panels.mjs",
    importLine: "import { applyContractView, applyWorkView, contractCard, diagnosticKey, filtered, jumpFromDiagnostic, renderActivityFeed, renderAttention, renderContractBoard, toggleDiagnosticSelection } from './work-loop-panels.mjs';",
    importWas: "import { applyWorkView, contractCard, diagnosticKey, filtered, jumpFromDiagnostic, renderActivityFeed, renderAttention, renderContractBoard, toggleDiagnosticSelection } from './work-loop-panels.mjs';",
    items: [
      {
        name: "applyContractView",
        at: 4343,
        marker: "    applyContractView(contractView, renderContracts);",
        wrapper: {
          header: ["export function applyContractView(contractView, renderContracts) {"],
          footer: ["}"], dedent: "  ",
        },
      },
    ],
  },
  {
    module: "console-await.mjs",
    importLine: "import { consoleAwaitingInputHint, updateAwaitPill } from './console-await.mjs';",
    items: [
      { name: "consoleAwaitingInputHint", at: 2421, marker: "// consoleAwaitingInputHint moved to ./console-await.mjs in v0.5.4." },
      { name: "updateAwaitPill", at: 2427, marker: "// updateAwaitPill moved to ./console-await.mjs in v0.5.4." },
    ],
  },
  {
    module: "session-rail.mjs",
    importLine: "import { SESSION_FILTER_KINDS, agentForSession, agentForTerminal, ensureSelectedSession, renderSessionRail, selectedSession, selectedSessionIds, toggleSupersededSessions } from './session-rail.mjs';",
    importWas: "import { SESSION_FILTER_KINDS, agentForSession, ensureSelectedSession, renderSessionRail, selectedSession, selectedSessionIds, toggleSupersededSessions } from './session-rail.mjs';",
    items: [
      { name: "agentForTerminal", at: 2272, marker: "// agentForTerminal moved to ./session-rail.mjs in v0.5.4." },
    ],
  },
  // Plain declaration relocations — no wrapper, the whole `function` span moves as it always did.
  {
    module: "run-helpers.mjs",
    importLine: "import { patchRun, runQueryPath, runSourceMessage, syncRunFilterOptions } from './run-helpers.mjs';",
    items: [
      { name: "runQueryPath", at: 705, marker: "// runQueryPath moved to ./run-helpers.mjs in v0.5.4." },
      { name: "runSourceMessage", at: 1716, marker: "// runSourceMessage moved to ./run-helpers.mjs in v0.5.4." },
      { name: "syncRunFilterOptions", at: 3180, marker: "// syncRunFilterOptions moved to ./run-helpers.mjs in v0.5.4." },
      { name: "patchRun", at: 3920, marker: "// patchRun moved to ./run-helpers.mjs in v0.5.4." },
    ],
  },
  {
    module: "work-loop-panels.mjs",
    importLine: "import { applyContractView, applyWorkView, contractCard, diagnosticKey, filtered, jumpFromDiagnostic, pruneDiagnosticSelection, renderActivityFeed, renderAttention, renderContractBoard, toggleDiagnosticSelection } from './work-loop-panels.mjs';",
    importWas: "import { applyContractView, applyWorkView, contractCard, diagnosticKey, filtered, jumpFromDiagnostic, renderActivityFeed, renderAttention, renderContractBoard, toggleDiagnosticSelection } from './work-loop-panels.mjs';",
    items: [
      { name: "pruneDiagnosticSelection", at: 1446, marker: "// pruneDiagnosticSelection moved to ./work-loop-panels.mjs in v0.5.4." },
    ],
  },
  // NOT a click-handler branch: a whole top-level `document.addEventListener('keydown', …)`. The body
  // sits at the same indentation inside the extracted function as it did inside the arrow, so there is
  // no dedent to declare — the only substitution is the header and footer.
  {
    module: "keyboard-shortcuts.mjs",
    importLine: "import { handleGlobalKeydown } from './keyboard-shortcuts.mjs';",
    items: [
      {
        name: "handleGlobalKeydown",
        at: 4651,
        marker: "  handleGlobalKeydown(event, closeInspector, toggleFavorite);",
        wrapper: {
          header: ["export function handleGlobalKeydown(event, closeInspector, toggleFavorite) {"],
          footer: ["}"],
        },
      },
    ],
  },
  // The shared-file row's delete button. app.js's shared-files import SWAPPED a name rather than gaining
  // one: `deleteSharedFile` had no other caller left in app.js once this body moved.
  {
    module: "shared-files.mjs",
    importLine: "import { attachChatFile, deleteSharedFileFromRow, loadFiles, renderFiles, uploadPastedImage, uploadSharedFile } from './shared-files.mjs';",
    importWas: "import { attachChatFile, deleteSharedFile, loadFiles, renderFiles, uploadPastedImage, uploadSharedFile } from './shared-files.mjs';",
    items: [
      {
        name: "deleteSharedFileFromRow",
        at: 4450,
        marker: "    deleteSharedFileFromRow(fileDelete);",
        wrapper: {
          header: ["export function deleteSharedFileFromRow(fileDelete) {"],
          footer: ["}"], dedent: "  ",
        },
      },
    ],
  },
  {
    module: "console-click-handlers.mjs",
    importLine: "import { runConsoleAction } from './console-click-handlers.mjs';",
    items: [
      {
        name: "runConsoleAction",
        at: 4485,
        marker: "    runConsoleAction(consoleAction, resyncActiveConsole, stopConsoleTerminal, startConsoleForSession);",
        wrapper: {
          header: ["export function runConsoleAction(consoleAction, resyncActiveConsole, stopConsoleTerminal, startConsoleForSession) {"],
          footer: ["}"], dedent: "  ",
        },
      },
    ],
  },
  {
    module: "layout-prefs.mjs",
    importLine: "import { preferredNavCollapsed, setNavCollapsed, toggleSessionGroupCollapsed } from './layout-prefs.mjs';",
    items: [
      {
        // Both EDITED since the move, in the same commit as the two work-loop guards and for the same
        // reason: `boot-wiring.test.mjs` ran the boot against a THROWING localStorage — which is what a
        // private window gives you, not a null — and found these were the only two readers in the whole
        // boot path without a guard. `preferredNavCollapsed` is called near the END of the restore, so
        // the page painted and the boot then stopped, silently.
        name: "setNavCollapsed",
        at: 3856,
        marker: "// setNavCollapsed moved to ./layout-prefs.mjs in v0.5.4.",
        editedSince: [{
          was: ["  localStorage.setItem('aify.next.navCollapsed', collapsed ? '1' : '0');"],
          now: [
            "  // The DOM update above is deliberately NOT inside the try: the sidebar must collapse even when the",
            "  // choice cannot be remembered.",
            "  try { localStorage.setItem('aify.next.navCollapsed', collapsed ? '1' : '0'); } catch { /* unavailable */ }",
          ],
        }],
      },
      {
        name: "preferredNavCollapsed",
        at: 3864,
        marker: "// preferredNavCollapsed moved to ./layout-prefs.mjs in v0.5.4.",
        editedSince: [{
          was: ["  const stored = localStorage.getItem('aify.next.navCollapsed');"],
          now: [
            "  let stored = null;",
            "  try { stored = localStorage.getItem('aify.next.navCollapsed'); } catch { /* unavailable */ }",
          ],
        }, {
          was: [],
          now: [
            "  // No readable preference falls through to the viewport, which is the same answer an operator who has",
            "  // never touched the toggle already gets.",
          ],
        }],
      },
      { name: "toggleSessionGroupCollapsed", at: 1808, marker: "// toggleSessionGroupCollapsed moved to ./layout-prefs.mjs in v0.5.4." },
    ],
  },
  {
    module: "work-loop-panels.mjs",
    importLine: "import { applyContractView, applyWorkView, contractCard, diagnosticKey, filtered, jumpFromDiagnostic, matchesGlobalFilter, pruneDiagnosticSelection, renderActivityFeed, renderAttention, renderContractBoard, toggleDiagnosticSelection } from './work-loop-panels.mjs';",
    importWas: "import { applyContractView, applyWorkView, contractCard, diagnosticKey, filtered, jumpFromDiagnostic, pruneDiagnosticSelection, renderActivityFeed, renderAttention, renderContractBoard, toggleDiagnosticSelection } from './work-loop-panels.mjs';",
    items: [
      { name: "matchesGlobalFilter", at: 918, marker: "// matchesGlobalFilter moved to ./work-loop-panels.mjs in v0.5.4." },
    ],
  },
  {
    module: "session-rail.mjs",
    importLine: "import { SESSION_FILTER_KINDS, agentForSession, agentForTerminal, ensureSelectedSession, renderSessionModeLabel, renderSessionRail, selectedSession, selectedSessionIds, toggleSupersededSessions } from './session-rail.mjs';",
    importWas: "import { SESSION_FILTER_KINDS, agentForSession, agentForTerminal, ensureSelectedSession, renderSessionRail, selectedSession, selectedSessionIds, toggleSupersededSessions } from './session-rail.mjs';",
    items: [
      { name: "renderSessionModeLabel", at: 2588, marker: "// renderSessionModeLabel moved to ./session-rail.mjs in v0.5.4." },
    ],
  },
  {
    module: "run-helpers.mjs",
    importLine: "import { RUN_INSPECTOR_EVENT_LIMIT, loadRunDetails, loadRunEvents, patchRun, runQueryPath, runSourceMessage, syncRunFilterOptions } from './run-helpers.mjs';",
    importWas: "import { patchRun, runQueryPath, runSourceMessage, syncRunFilterOptions } from './run-helpers.mjs';",
    items: [
      { name: "RUN_INSPECTOR_EVENT_LIMIT", at: 41, marker: "// RUN_INSPECTOR_EVENT_LIMIT moved to ./run-helpers.mjs in v0.5.4." },
      { name: "loadRunDetails", at: 3230, marker: "// loadRunDetails moved to ./run-helpers.mjs in v0.5.4." },
      { name: "loadRunEvents", at: 3235, marker: "// loadRunEvents moved to ./run-helpers.mjs in v0.5.4." },
    ],
  },
  {
    module: "render-memo.mjs",
    importLine: "import { renderSection } from './render-memo.mjs';",
    items: [
      { name: "_sectionSig", at: 930, marker: "// _sectionSig moved to ./render-memo.mjs in v0.5.4." },
      {
        name: "renderSection",
        at: 931,
        marker: "// renderSection moved to ./render-memo.mjs in v0.5.4.",
        // A SECTION THAT THREW USED TO LATCH ITSELF OFF. The signature is recorded BEFORE the render
        // on purpose -- a renderer re-entering its own key would recurse otherwise -- but nothing
        // undid that record when the render threw, so the memo reported a state as drawn that never
        // was. The try/catch also stops one section taking the ten after it down.
        editedSince: [
        {
          was: [],
          now: [
          "  // BEFORE the render: the re-entrancy guard. See above.",
        ],
        },
        {
          was: [
          "  renderFn();",
        ],
          now: [
          "  try {",
          "    renderFn();",
          "  } catch (error) {",
          "    // A render that did not finish is not drawn. DELETE rather than restore the previous value: the",
          "    // old signature would also compare unequal next cycle, but only until the data drifted back to",
          "    // it, and \"retry until it works\" must not depend on that.",
          "    delete _sectionSig[key];",
          "    noteSliceFailure(`render:${key}`);",
          "    // Reported AND named. The slice list is what the connection chip drains, so the operator learns",
          "    // which section failed; the console line is the only place the actual error survives.",
          "    try { console.error(`[dashboard] section \"${key}\" failed to render:`, error); } catch { /* no console */ }",
          "  }",
        ],
        },
      ],
      },
    ],
  },
  {
    module: "record-lookup.mjs",
    importLine: "import { lookup } from './record-lookup.mjs';",
    items: [
      { name: "lookup", at: 4193, marker: "// lookup moved to ./record-lookup.mjs in v0.5.4." },
    ],
  },
  {
    module: "static-links.mjs",
    importLine: "import { renderInstallSnippet, updateStaticLinks } from './static-links.mjs';",
    items: [
      { name: "renderInstallSnippet", at: 36, marker: "// renderInstallSnippet moved to ./static-links.mjs in v0.5.4." },
      { name: "updateStaticLinks", at: 4231, marker: "// updateStaticLinks moved to ./static-links.mjs in v0.5.4." },
    ],
  },
  {
    module: "session-rail.mjs",
    importLine: "import { SESSION_FILTER_KINDS, agentForSession, agentForTerminal, ensureSelectedSession, renderModeSwitchChip, renderSessionModeLabel, renderSessionRail, selectedSession, selectedSessionIds, toggleSupersededSessions } from './session-rail.mjs';",
    importWas: "import { SESSION_FILTER_KINDS, agentForSession, agentForTerminal, ensureSelectedSession, renderSessionModeLabel, renderSessionRail, selectedSession, selectedSessionIds, toggleSupersededSessions } from './session-rail.mjs';",
    items: [
      { name: "renderModeSwitchChip", at: 2578, marker: "// renderModeSwitchChip moved to ./session-rail.mjs in v0.5.4." },
    ],
  },
  {
    module: "page-titles.mjs",
    importLine: "import { pages } from './page-titles.mjs';",
    items: [
      { name: "pages", at: 116, marker: "// pages moved to ./page-titles.mjs in v0.5.4." },
    ],
  },
  {
    module: "render-memo.mjs",
    importLine: "import { _agentSig, _chatChanSig, _chatConvSig, _contractSig, _envSig, _msgSig, _runSig, _spawnReqSig } from './render-memo.mjs';",
    items: [
      { name: "_agentSig", at: 938, marker: "// _agentSig moved to ./render-memo.mjs in v0.5.4." },
      { name: "_contractSig", at: 939, marker: "// _contractSig moved to ./render-memo.mjs in v0.5.4." },
      { name: "_runSig", at: 940, marker: "// _runSig moved to ./render-memo.mjs in v0.5.4." },
      { name: "_envSig", at: 941, marker: "// _envSig moved to ./render-memo.mjs in v0.5.4." },
      { name: "_spawnReqSig", at: 942, marker: "// _spawnReqSig moved to ./render-memo.mjs in v0.5.4." },
      { name: "_msgSig", at: 943, marker: "// _msgSig moved to ./render-memo.mjs in v0.5.4." },
      { name: "_chatChanSig", at: 944, marker: "// _chatChanSig moved to ./render-memo.mjs in v0.5.4." },
      { name: "_chatConvSig", at: 945, marker: "// _chatConvSig moved to ./render-memo.mjs in v0.5.4." },
    ],
  },
  {
    module: "xterm-lifecycle.mjs",
    importLine: "import { awaitTerminalSize, disposeActiveXterm } from './xterm-lifecycle.mjs';",
    importWas: "import { disposeActiveXterm } from './xterm-lifecycle.mjs';",
    items: [
      { name: "awaitTerminalSize", at: 497, marker: "// awaitTerminalSize moved to ./xterm-lifecycle.mjs in v0.5.4." },
    ],
  },
  {
    module: "work-loop-panels.mjs",
    importLine: "import { MAINTENANCE_ACTIONS, applyContractView, applyWorkView, contractCard, diagnosticKey, filtered, jumpFromDiagnostic, matchesGlobalFilter, pruneDiagnosticSelection, renderActivityFeed, renderAttention, renderContractBoard, toggleDiagnosticSelection } from './work-loop-panels.mjs';",
    importWas: "import { applyContractView, applyWorkView, contractCard, diagnosticKey, filtered, jumpFromDiagnostic, matchesGlobalFilter, pruneDiagnosticSelection, renderActivityFeed, renderAttention, renderContractBoard, toggleDiagnosticSelection } from './work-loop-panels.mjs';",
    items: [
      { name: "MAINTENANCE_ACTIONS", at: 1481, marker: "// MAINTENANCE_ACTIONS moved to ./work-loop-panels.mjs in v0.5.4." },
    ],
  },
  // The largest single function in app.js. A whole-declaration move whose SIGNATURE gained an injected
  // parameter — so it is a plain move plus one `editedSince`, not a `wrapper`: the wrapper form restores
  // only a body, and here the pristine file held the entire declaration.
  {
    module: "xterm-mount.mjs",
    importLine: "import { mountXtermForTerminal as mountXtermForTerminalImpl } from './xterm-mount.mjs';",
    items: [
      { name: "_consoleMountGen", at: 136 },
      { name: "consoleInputBlockedToastAt", at: 1868 },
      {
        name: "mountXtermForTerminal",
        at: 1911,
        marker: [
          "// The IMPLEMENTATION lives in ./xterm-mount.mjs, together with the two counters only it reads. This is",
          "// the binding that supplies `resyncActiveConsole`, which stays here because it reaches `refresh`.",
          "// Deliberately NOT phrased as a `moved to` marker: `moved-names-resolve` treats a marker plus a local",
          "// declaration of the same name as a fork, and it is right to — this is a shim, not a move.",
          "const mountXtermForTerminal = (terminalId, agentId, container, opts) =>",
          "  mountXtermForTerminalImpl(terminalId, agentId, container, opts, { resyncActiveConsole });",
        ],
        editedSince: [{
          was: "async function mountXtermForTerminal(terminalId, agentId, container, { canInput = true } = {}) {",
          now: "async function mountXtermForTerminal(terminalId, agentId, container, { canInput = true } = {}, { resyncActiveConsole }) {",
        }],
      },
    ],
  },
  {
    module: "session-console.mjs",
    importLine: "import { renderSessionConsole as renderSessionConsoleImpl } from './session-console.mjs';",
    items: [
      {
        name: "renderSessionConsole",
        at: 2641,
        marker: [
          "// The IMPLEMENTATION lives in ./session-console.mjs. This binding supplies the three names that stay",
          "// here because each reaches `refresh`. Not phrased as a `moved to` marker — see mountXtermForTerminal.",
          "const renderSessionConsole = (session, targetEl, opts) =>",
          "  renderSessionConsoleImpl(session, targetEl, opts, { mountXtermForTerminal, refresh, resyncActiveConsole });",
        ],
        editedSince: [
          {
            was: "function renderSessionConsole(session, targetEl, opts = {}) {",
            now: "function renderSessionConsole(session, targetEl, opts = {}, { mountXtermForTerminal, refresh, resyncActiveConsole } = {}) {",
          },
          // 2026-08-26: the dead-session card now says WHY it is dead. It is built from the SESSION
          // row, which has a terminal status and no exit columns, so it could say "stopped" and
          // nothing about a worker something killed. A placeholder in the label, filled after the
          // paint from `GET /agents/{id}/console` -- which has had the code, the signal and the last
          // failure line since earlier the same day and was being asked by nothing.
          {
            was: "         <div class=\"console-embed-label\"><span>This session is ${esc(status || 'stopped')} \u2014 no live console. The agent stays <em>available</em>: a message wakes it, or start it now.</span></div>",
            now: "         <div class=\"console-embed-label\"><span>This session is ${esc(status || 'stopped')} \u2014 no live console. The agent stays <em>available</em>: a message wakes it, or start it now.<span class=\"console-dead-cause\" data-dead-cause-agent=\"${esc(agentIdForCodex || '')}\"></span></span></div>",
          },
          {
            was: ["  host.innerHTML = `${headerCard}${ptyEmbed}${startConsoleEmbed}${residentConsoleNote}${hermesIframe}${codexConsole}`;"],
            now: [
              "  host.innerHTML = `${headerCard}${ptyEmbed}${startConsoleEmbed}${residentConsoleNote}${hermesIframe}${codexConsole}`;",
              "",
              "  // WHY IT IS DEAD, asked of the endpoint that already knows. The card above is built from the SESSION",
              "  // row, which carries a terminal STATUS and no exit columns -- so it can say \"stopped\" and nothing",
              "  // about a worker something killed. `GET /agents/{id}/console` has the code, the signal and the last",
              "  // failure line; this fills them in after the paint. Best effort and only on the dead branch: the",
              "  // placeholder does not exist otherwise, and a failed fetch leaves the card exactly as rendered.",
              "  const deadCauseEl = host.querySelector('.console-dead-cause[data-dead-cause-agent]');",
              "  if (deadCauseEl) {",
              "    fillDeadConsoleCause(deadCauseEl, deadCauseEl.dataset.deadCauseAgent, { api }).catch(() => {});",
              "  }"
            ],
          },
          // 2026-08-27: `isResident` now asks the SERVICE. `records.py` emits `consoleAvailable`
          // on every agent row, with a comment saying the dashboard should hide the console button
          // for residents -- and nothing in the repo read it. This line derived the same answer
          // instead, and folded an unknown mode the OTHER way, offering a console that cannot
          // attach. The fallback now matches `_normalize_session_mode` exactly.
          {
            was: "  const isResident = normalizedSessionMode === 'resident';",
            now: [
              "  // THE SERVICE ALREADY ANSWERED THIS, and until now nobody asked. `records.py` emits",
              "  // `consoleAvailable` on every agent row with the comment \"the dashboard should hide the",
              "  // button for these\" -- and the dashboard derived it again instead, so the field was computed",
              "  // on every request and read by nothing in the repo.",
              "  //",
              "  // THE TWO FAILED IN OPPOSITE DIRECTIONS. The service normalises an unknown or empty mode to",
              "  // `resident` (`_normalize_session_mode`), so it hides the console; this line compared for",
              "  // equality, so an empty mode meant NOT resident and offered a console that cannot attach.",
              "  // Unreachable on the live fleet today -- all 47 agents carry resident or managed -- but a guard",
              "  // that opens when its input is missing is decoration, so the fallback now folds exactly the way",
              "  // `_normalize_session_mode` does: strip, lower, and anything outside SESSION_MODES is resident.",
              "  // The contract declares that set as {managed, resident}, so the rule reduces to the one below;",
              "  // a sibling test fails if a third mode is ever added to the contract.",
              "  const isResident = typeof agent?.consoleAvailable === 'boolean'",
              "    ? !agent.consoleAvailable",
              "    : normalizedSessionMode.trim() !== 'managed';",
            ],
          },
          // 2026-08-27: the SESSION half of the mode chain read `sessionMode` / `session_mode`,
          // measured present on 0 of 100 live session rows while `ownerMode` is on 100 of 100.
          // `agentForSession` returns {} rather than undefined, so the chain ran, resolved to '',
          // and -- with the fallback failing closed -- hid the console offer for every session
          // whose agent had not loaded into state.
          {
            was: "  const normalizedSessionMode = String(agent?.sessionMode || session?.sessionMode || session?.session_mode || '').toLowerCase();",
            now: [
              "  // `ownerMode` IS THE KEY A SESSION ROW CARRIES. This read `session?.sessionMode` and",
              "  // `session?.session_mode`; measured against the live service, both are present on 0 of 100",
              "  // session rows and `ownerMode` on 100 of 100. The session half of this chain has never",
              "  // resolved to anything.",
              "  //",
              "  // It matters because `agentForSession` returns `{}` -- not undefined -- when the agent is not",
              "  // in `state.agents`, so the chain runs and yields ''. With the fallback failing closed (as it",
              "  // must), that hid the console offer for EVERY session whose agent had not loaded, managed ones",
              "  // included. `refresh-cycle.mjs` uses allSettled precisely because the single-worker service",
              "  // transiently drops a request under poll load, and that is the window this fires in.",
              "  //",
              "  // `ownerMode` is derived server-side in `_agent_session_to_dict`: 'resident', 'console', or",
              "  // 'managed', already folding a `mode` of resident into 'resident'.",
              "  const normalizedSessionMode = String(agent?.sessionMode || session?.ownerMode || '').toLowerCase();",
            ],
          },
          // 2026-08-27: the chooser was passed a SECOND derivation of the same chain, including
          // the two keys no session row carries. It received undefined whenever the agent was not
          // in state, and folded that unknown the OPPOSITE way from `isResident` above.
          {
            was: "    sessionMode: agent?.sessionMode || session?.sessionMode || session?.session_mode,",
            now: [
              "    // THE ALREADY-RESOLVED MODE, not a second derivation. This re-ran the chain from the top --",
              "    // including `session?.sessionMode` and `session?.session_mode`, present on 0 of 100 live",
              "    // session rows -- so for a session whose agent is not in state it passed undefined.",
              "    //",
              "    // The two then DISAGREED about the same session. `isResident` above folds an unknown mode to",
              "    // resident (a guard that opens when its input is missing is decoration); the chooser folds it",
              "    // the other way, `normalizedSessionMode !== 'resident'`, and concluded the terminal could",
              "    // represent the current owner. One render, one answer.",
              "    sessionMode: normalizedSessionMode,",
            ],
          },
        ],
      },
    ],
  },
  {
    module: "refresh-cycle.mjs",
    importLine: "import { runRefreshCycle } from './refresh-cycle.mjs';",
    items: [
      {
        // The poll cycle. Renamed on the way out (`_refreshImpl` is an app.js-private name; the module
        // exports what it does), so BOTH the name and the six-line signature are declared edits — the
        // 106 body lines are byte-identical and the reconstruction proves it.
        name: "runRefreshCycle",
        at: 802,
        marker: [
          "// The IMPLEMENTATION of the poll cycle lives in ./refresh-cycle.mjs. The bag is built at CALL time,",
          "// not here, so every name resolves however app.js has it at the moment the poll fires.",
          "const _refreshImpl = () => runRefreshCycle({",
          "  armRefreshTimer,",
          "  chatController,",
          "  evaluateFlowGates,",
          "  loadContractsForState,",
          "  refreshOpenInspector,",
          "  renderAll,",
          "});",
        ],
                editedSince: [
        {
          was: "  if (ok(4)) state.runs = val(4).runs || [];",
          now: [
            "  if (ok(4)) {",
            "    state.runs = val(4).runs || [];",
            "    // KEPT for the same reason as the sessions flag: under a capped page, \"nothing matches\" and",
            "    // \"none exist\" are different facts that look identical.",
            "    state.runsTruncated = Boolean(val(4)?.truncated);",
            "  }",
          ],
        },
          {
            was: [
              "  const chip = refreshChipState(settled);",
            ],
            now: [
              "  // The realtime flag is PASSED, not read inside: refresh-status.mjs is a pure module and reaching",
              "  // into `state` from it would make its tests depend on a shared singleton. This is also the half",
              "  // that was missing -- the flag had four writers and no reader at all.",
              "  const chip = refreshChipState(settled, { realtimeConnected: state.realtimeConnected });",
            ],
          },
          {
            was: [
            ],
            now: [
              "  // The Environments page is the only reader of state.spawnRequests, and this slice is the LARGEST",
              "  // thing the poll moves: 414,690 bytes of a 1,419,728 byte cycle measured against the live service",
              "  // on 2026-08-26 -- 29% of it, for a table nobody is looking at. Same rule as /shared, same",
              "  // fail-closed predicate.",
              "  //",
              "  // The SLOT is kept rather than the entry dropped: ok(i) and val(i) index this array by position,",
              "  // so omitting one would silently shift every slice after it onto the wrong data.",
              "  const wantSpawnRequests = shouldLoadForPage('environments');",
            ],
          },
          {
            was: [
              "    api('/spawn-requests?limit=200'),                                     // 7",
            ],
            now: [
              "    wantSpawnRequests ? api('/spawn-requests?limit=200') : Promise.resolve(null), // 7",
            ],
          },
          {
            was: [
              "  if (ok(7)) state.spawnRequests = asArray(val(7), 'spawnRequests');",
            ],
            now: [
              "  // Guarded on the REQUEST, not just the result: a skipped slice resolves to null, and assigning",
              "  // asArray(null) would wipe the list rather than leave it alone. Nothing reads it while the page is",
              "  // closed, but wiping it would make the first render after opening flash empty.",
              "  if (wantSpawnRequests && ok(7)) state.spawnRequests = asArray(val(7), 'spawnRequests');",
            ],
          },{
          // The four out-of-band awaits stopped swallowing their failures. Each was
          // `catch (_) {}`, so a fetch outside the allSettled array could fail for ever while the
          // connection chip read `live` -- 12 to 13 requests per cycle, only 10 of them accounted.
          was: "    try { await loadContractsForState(contractStateSel, false); } catch (_) { /* keep base */ }",
          now: "    try { await loadContractsForState(contractStateSel, false); } catch (_) { noteSliceFailure('contract filter'); /* keep base */ }",
        }, {
          was: "  try { await chatLoadChannels(); } catch (_) { /* keep prior channels */ }",
          now: "  try { await chatLoadChannels(); } catch (_) { noteSliceFailure('channels'); /* keep prior channels */ }",
        }, {
          was: "    try { await chatLoadConversation(state.chat.selected.slice('channel:'.length)); } catch (_) { /* keep prior view */ }",
          now: "    try { await chatLoadConversation(state.chat.selected.slice('channel:'.length)); } catch (_) { noteSliceFailure('conversation'); /* keep prior view */ }",
        }, {
          was: "  try { await loadFiles(); } catch (_) { /* keep prior files */ }",
          now: [
            "  // Only when someone can see it. `state.files` is read by the Files page alone, and /shared is",
            "  // 113,854 bytes for 388 files (34,839 gzipped), fetched every cycle whether or not the page is",
            "  // open: 8.0 MB an hour per tab at the default 15s refresh, 23.9 at the 5s floor. navigateToPage",
            "  // loads it on open, so the page shows a fetched list rather than a cached one.",
            "  if (shouldLoadFiles()) {",
            "    try { await loadFiles(); } catch (_) { noteSliceFailure('files'); /* keep prior files */ }",
            "  }",
          ],
        }, {
          // The inbox stopped being fetched on a healthy cycle. It is the FALLBACK for
          // /messages/recent, and the primary wins whenever it returns a messages array -- so the
          // response was 300,154 bytes of a 1,419,728-byte cycle, fetched and thrown away. The SLOT
          // stays, resolving null, because ok(i)/val(i) index by POSITION.
          was: "    api('/messages/inbox/dashboard?filter=all&peek=true&limit=80'),       // 2",
          now: "    Promise.resolve(null),                                                // 2 \u2014 fetched below, only if needed",
        }, {
          was: [
            "  // messages: prefer recent, fall back to inbox, then keep prior \u2014 only touch if either succeeded.",
            "  if (ok(2) || ok(3)) {",
            "    state.messages = (ok(3) && val(3).messages) || (ok(2) && val(2).messages) || state.messages || [];",
            "  }",
          ],
          now: [
            "  // messages: prefer recent, fall back to inbox, then keep prior \u2014 only touch if either succeeded.",
            "  //",
            "  // THE INBOX WAS A FALLBACK IN THE CODE AND NOT ONE ON THE WIRE. `/messages/recent` wins whenever",
            "  // it returns a `messages` array, so on every healthy cycle the inbox response was fetched, parsed",
            "  // and thrown away: 300,154 bytes of a measured 1,419,728-byte cycle, 21% of it, confirmed against",
            "  // the running service. It is a pure read \u2014 the route's `peek=true` skips `_settle_inbox_read`",
            "  // entirely \u2014 so not sending it changes nothing but the traffic.",
            "  //",
            "  // Its SLOT stays in the array, resolving null, because ok(i)/val(i) index by POSITION: removing",
            "  // the entry would shift every slice after it onto the wrong data. The request is now made only",
            "  // when the primary did not hand us messages, and it reports its own failure the way the other",
            "  // out-of-band fetches do \u2014 a null slot resolves as `fulfilled`, so the chip would otherwise read",
            "  // a fallback that failed as a slice that succeeded.",
            "  const recentUsable = ok(3) && val(3).messages;",
            "  let inboxMessages = null;",
            "  if (!recentUsable) {",
            "    try { inboxMessages = await loadInboxMessages(); } catch (_) { noteSliceFailure('inbox'); /* keep prior messages */ }",
            "  }",
            "  if (recentUsable || inboxMessages) {",
            "    state.messages = recentUsable || inboxMessages || state.messages || [];",
            "  }",
          ],
        }, {

          // The connection chip stopped lying about a sustained partial refresh. It read 'live' in
          // green whenever /agents succeeded, whatever else had failed -- so nine of ten fetches could
          // fail and the view still claimed to be current, while the resilient poll quietly showed each
          // failed slice's last-good value. The rule moved to refresh-status.mjs, which remembers the
          // previous cycle so one blip stays green and a slice that misses twice running is named.
          was: [
            "  if (failed === 0) {",
            "    byId('api-status').textContent = 'live';",
            "    byId('api-status').className = 'status-chip ok';",
            "  } else if (ok(0)) {",
            "    byId('api-status').textContent = 'live';",
            "    byId('api-status').className = 'status-chip ok';",
            "  } else {",
            "    byId('api-status').textContent = 'reconnecting';",
            "    byId('api-status').className = 'status-chip warn';",
            "  }",
          ],
          now: [
            "  // THREE STATES, because a sustained partial refresh is not a complete one. This used to read",
            "  // 'live' in green whenever /agents succeeded, whatever else had failed -- and the poll keeps each",
            "  // slice's last-good value, so a stale panel renders exactly like one where nothing changed.",
            "  // refresh-status.mjs owns the rule, remembers the previous cycle so a single blip stays green,",
            "  // and names which slices are stale rather than counting them.",
            "  const chip = refreshChipState(settled);",
            "  const chipEl = byId('api-status');",
            "  if (chipEl) {",
            "    chipEl.textContent = chip.text;",
            "    chipEl.className = chip.className;",
            "    chipEl.title = chip.title;",
            "  }",
          ],
        }, {
          // `failed` counted what refresh-status.mjs now both counts and NAMES; two places deriving one
          // number is how they come to disagree. A deletion needs a surviving neighbour as its anchor,
          // because `now` is matched verbatim and an empty match would land at line zero.
          was: [
            "  const val = (i) => (ok(i) ? settled[i].value : undefined);",
            "  const failed = settled.filter((s) => s.status === 'rejected').length;",
          ],
          now: ["  const val = (i) => (ok(i) ? settled[i].value : undefined);"],
        }, {
          was: "async function _refreshImpl() {",
          now: [
            // `export ` is stripped before the declared edits are undone, so it is absent here.
            "async function runRefreshCycle({",
            "  armRefreshTimer,",
            "  chatController,",
            "  evaluateFlowGates,",
            "  loadContractsForState,",
            "  refreshOpenInspector,",
            "  renderAll,",
            "}) {",
          ],
        },
        // 2026-08-29: the sessions list is a PAGE and the response now says so. Measured on the
        // live database: 303 rows past the default filter, the dashboard asks for 80, and the
        // empty state offered a Spawn button while 303 existed.
        {
          was: "    state.sessions = asArray(val(5), 'sessions');",
          now: [
            "    state.sessions = asArray(val(5), 'sessions');",
            "    // KEPT, not dropped. Under a capped page, \"no sessions match\" and \"none exist\" are different",
            "    // facts that look identical -- the same reason the contracts list keeps its flag.",
            "    state.sessionsTruncated = Boolean(val(5)?.truncated);",
          ],
        }],
      },
    ],
  },
  {
    module: "realtime-socket.mjs",
    importLine: "import { connectRealtimeSocket, initRealtimeSocket, wireRealtimeResumeReconnect } from './realtime-socket.mjs';",
    // The boot call this slice added. It restores no body — the module did not exist before — so it is
    // declared here rather than smuggled in as some unrelated declaration's marker.
    seeding: "initRealtimeSocket({ dashboardNotifier, evaluateFlowGates, refreshSoon, resyncActiveConsole, scheduleRenderAll });",
    items: [
      {
        name: "dashboardSocket",
        at: 135,
        marker: "// dashboardSocket moved to ./realtime-socket.mjs in v0.5.4 — its only readers went with it.",
      },
      {
        name: "_wsReconnectAttempts",
        at: 510,
        marker: [
          "// The realtime socket cluster — connect, resume-nudge, resume wiring and the four mutable names",
          "// they own — moved to ./realtime-socket.mjs in v0.5.4. Its dependencies are supplied by the",
          "// initRealtimeSocket call in this file's init block, which MUST run before the first connect.",
        ],
      },
      { name: "WS_CONNECTING_TIMEOUT_MS", at: 511, marker: null },
      { name: "connectRealtimeSocket", at: 512, marker: null },
      {
        // THE BLANK LINE AND THE SIX COMMENT LINES ABOVE THIS DECLARATION are declared here because
        // `declarationSpan` covers a declaration, not the prose above it — so a cluster whose members
        // are separated by a comment block cannot be restored from spans alone. The anchor (`now`) is the
        // declaration line and IS verified verbatim against the module; the restored lines are not, which
        // is the one gap this mechanism opens. `realtime-socket.test.mjs` closes it by asserting the same
        // six lines are present in the module, so the comment cannot be silently dropped or reworded.
        name: "_wsResumeNudgeAt",
        at: 565,
        marker: null,
        editedSince: [{
          now: ["let _wsResumeNudgeAt = 0;"],
          was: [
            "",
            "// Reconnect on page-resume (Hermes parity). When a backgrounded/slept tab wakes, its socket is",
            "// often CLOSED with a long backoff timer still pending (up to 30s away) — the operator stares at a",
            "// stale console. On any resume signal, if we're not OPEN, reconnect NOW (short-circuiting the",
            "// backoff). A stuck-CONNECTING socket is force-closed first so the CONNECTING guard can't block the",
            "// fresh connect. Throttled so a burst of resume events (focus+visibilitychange+online together)",
            "// fires one reconnect.",
            "let _wsResumeNudgeAt = 0;",
          ],
        }],
      },
      { name: "nudgeRealtimeSocketOnResume", at: 573, marker: null },
      { name: "wireRealtimeResumeReconnect", at: 584, marker: null },
      {
        name: "applyRealtimeEvent",
        editedSince: [{
          // The eleven-name refresh allowlist became a declared disposition per event. Anything
          // not in that array fell off the end of this function and was dropped -- 35 of the 49
          // names the service broadcasts. realtime-dispositions.mjs now answers for every one, and
          // realtime-dispositions.test.mjs reads the python producer so the two cannot drift.
          was: [
            "  if ([",
            "    'message_sent',",
            "    'dispatch_queued',",
            "    'dispatch_claimed',",
            "    'dispatch_updated',",
            "    'dispatch_control_requested',",
            "    'dispatch_control_updated',",
            "    'contract_reminders_sent',",
            "    'settings_updated',",
            "    'session_control_requested',",
            "    'session_deleted',",
            "    'agent_registered',",
            "  ].includes(event)) {",
          ],
          now: [
            "  // EVERY event has a declared disposition, in realtime-dispositions.mjs. This used to be an inline",
            "  // array of eleven names, and anything not in it fell off the end of this function and was dropped",
            "  // -- 35 of the 49 names the service broadcasts, among them channel_message, terminal_stopped,",
            "  // message_deleted, conversation_cleared, file_shared and all three spawn_request_*. The default is",
            "  // now to refresh rather than to discard, and an event that IS discarded has to say why.",
            "  //",
            "  // Safe because refreshSoon debounces 250ms AND app.js coalesces while a bundle is in flight, so a",
            "  // burst of events collapses into one refetch rather than stacking bundles.",
            "  if (dispositionOf(event) === 'refresh') {",
          ],
        }],
        at: 629,
        marker: "// applyRealtimeEvent moved to ./realtime-socket.mjs in v0.5.4, with the socket it is wired to.",
      },
    ],
  },
  {
    module: "run-inspector.mjs",
    importLine: "import { handleRunInspectorControl, initRunInspector, loadMoreRunEvents, loadRunsForStatus, openRunInspector, renderRunInspector, renderRuns, requestRunControl, toggleRunEventOrder } from './run-inspector.mjs';",
    seeding: "initRunInspector({ closeInspector, evaluateFlowGates, openInspector, openRunConsole, refresh, renderDiagnosticsBulkToolbar });",
    items: [
      {
        name: "loadRunsForStatus",
        at: 723,
        marker: "// loadRunsForStatus moved to ./run-inspector.mjs in v0.5.4.",
        editedSince: [
        {
          was: [
            "  state.runs = runs.runs || [];",
          ],
          now: [
            "  state.runs = runs.runs || [];",
            "  // AND THE FLAG WITH THEM. This is the ONE action on the page that re-queries the server, and it",
            "  // stored the rows while leaving `runsTruncated` carrying the PREVIOUS query's answer -- so picking a",
            "  // status whose whole result fits on a page still showed \"Older runs are not loaded\" and the",
            "  // truncated empty state. The note claims to appear only when rows were left behind; a stale flag",
            "  // makes that claim false at the one moment the operator is acting on it.",
            "  //",
            "  // Same producer/call-site class as the ownership defect the same day: a value the response carries,",
            "  // dropped by one of two consumers, so the fix looks complete from wherever you happen to look.",
            "  state.runsTruncated = Boolean(runs?.truncated);",
          ],
        },
        ],
      },
      {
        // These two came along because nothing was left reading them. A one-line helper that stays
        // behind after its only callers move is dead code the suite cannot see.
        name: "runTo",
        at: 3176,
        marker: "// runTo and runRuntime moved to ./run-inspector.mjs in v0.5.4 — their only readers went with it.",
      },
      { name: "runRuntime", at: 3177, marker: null },
      {
        name: "renderRuns",
        at: 3190,
        marker: "// renderRuns moved to ./run-inspector.mjs in v0.5.4.",
        // 2026-08-29: the runs list is a PAGE and now says which of its filters can reach past it.
        // Status is in the query string; From, To, runtime and search are applied client-side over
        // the rows already fetched, and the three dropdowns are POPULATED from those rows -- so an
        // agent whose last run fell off the page is unselectable, while the empty state invited the
        // operator to adjust the filters.
        editedSince: [
        {
          was: [
            "    note.textContent = `Showing ${runs.length} most recent matching ${status}run${runs.length === 1 ? '' : 's'}.`;",
          ],
          now: [
            "    // WHICH FILTERS REACH THE SERVER, said only when it matters. Status is in the query string;",
            "    // From, To, runtime and search are applied here, over the rows already fetched -- and the three",
            "    // dropdowns are POPULATED from those same rows, so an agent whose last run fell off the page",
            "    // cannot even be selected. Measured on the live database 2026-08-29: a limit=80 page reached back",
            "    // to 26 August and offered one distinct sender.",
            "    const scope = state.runsTruncated",
            "      ? ' Older runs are not loaded: From, To, runtime and search cover only these, and only Status re-queries.'",
            "      : '';",
            "    note.textContent = `Showing ${runs.length} most recent matching ${status}run${runs.length === 1 ? '' : 's'}.${scope}`;",
          ],
        },
        {
          was: [
            "    </article>`).join('') || '<div class=\"empty-state\"><span class=\"empty-icon\">\uD83D\uDCE8</span><strong>No dispatch runs</strong><p>Runs appear here when an agent sends or receives work. Adjust the filters above if you expected to see some.</p></div>';",
          ],
          now: [
            "    </article>`).join('') || (state.runsTruncated",
            "    // NOT \"adjust the filters\" when four of the five cannot reach further than the loaded page. That",
            "    // sentence sends an operator round a loop that always ends where it started -- the same defect a",
            "    // reviewer caught in the sessions note the same day.",
            "    ? '<div class=\"empty-state\"><span class=\"empty-icon\">\uD83D\uDD0E</span><strong>None on this page</strong><p>None of the loaded runs match. Older runs are not loaded — only the Status filter re-queries the server.</p></div>'",
            "    : '<div class=\"empty-state\"><span class=\"empty-icon\">\uD83D\uDCE8</span><strong>No dispatch runs</strong><p>Runs appear here when an agent sends or receives work. Adjust the filters above if you expected to see some.</p></div>');",
          ],
        },
        ],
      },
      {
        name: "renderRunInspector",
        at: 3307,
        marker: "// renderRunInspector moved to ./run-inspector.mjs in v0.5.4.",
      },
      {
        name: "openRunInspector",
        at: 3361,
        marker: "// openRunInspector moved to ./run-inspector.mjs in v0.5.4.",
      },
      {
        name: "requestRunControl",
        at: 3870,
        marker: "// requestRunControl moved to ./run-inspector.mjs in v0.5.4.",
      },
      {
        name: "handleRunInspectorControl",
        at: 4073,
        marker: "// handleRunInspectorControl moved to ./run-inspector.mjs in v0.5.4.",
      },
      {
        name: "loadMoreRunEvents",
        at: 4127,
        marker: "// loadMoreRunEvents moved to ./run-inspector.mjs in v0.5.4.",
      },
      {
        name: "toggleRunEventOrder",
        at: 4145,
        marker: "// toggleRunEventOrder moved to ./run-inspector.mjs in v0.5.4.",
      },
    ],
  },
  {
    module: "agent-session-actions.mjs",
    importLine: "import { deleteSessionById, initAgentSessionActions, openAgentChat, removeAgent, requestBulkSessionControl, requestSessionControl, resolveAgentSession, stopAgentWorker, submitAgentEdit, submitContinue, switchAgentSessionMode } from './agent-session-actions.mjs';",
    seeding: "initAgentSessionActions({ chatController, closeInspector, inspect, markConversationRead, refresh, refreshSoon, renderSessionWorkspace, setPage });",
    items: [
      {
        name: "switchAgentSessionMode",
        at: 2594,
        marker: "// switchAgentSessionMode moved to ./agent-session-actions.mjs in v0.5.4.",
      },
      {
        name: "submitAgentEdit",
        at: 3641,
        marker: "// submitAgentEdit moved to ./agent-session-actions.mjs in v0.5.4.",
        editedSince: [
          // Dropped a dead snake_case alternate: the service emits sessionHandle.
          {
            was: [
              "    if (handle !== String(agent.sessionHandle || agent.session_handle || '')) {",
            ],
            now: [
              "    if (handle !== String(agent.sessionHandle || '')) {",
            ],
          },
        ],
      },
      {
        name: "resolveAgentSession",
        at: 3675,
        // `at` points at the first COMMENT line, not the declaration: 3 lines of prose
        // moved with it, and `leading` restores them FROM THE MODULE so they are byte-checked too.
        leading: 3,
        marker: "// resolveAgentSession moved to ./agent-session-actions.mjs in v0.5.4, with its sticky-identity note.",
      },
      {
        name: "submitContinue",
        at: 3755,
        marker: "// submitContinue moved to ./agent-session-actions.mjs in v0.5.4.",
      },
      {
        name: "removeAgent",
        at: 3781,
        marker: "// removeAgent moved to ./agent-session-actions.mjs in v0.5.4.",
      },
      {
        name: "stopAgentWorker",
        at: 3792,
        // `at` points at the first COMMENT line, not the declaration: 6 lines of prose
        // moved with it, and `leading` restores them FROM THE MODULE so they are byte-checked too.
        leading: 6,
        marker: "// stopAgentWorker moved to ./agent-session-actions.mjs in v0.5.4, with the six lines explaining why it is confirmed.",
      },
      {
        name: "deleteSessionById",
        at: 3820,
        marker: "// deleteSessionById moved to ./agent-session-actions.mjs in v0.5.4.",
      },
      {
        name: "requestSessionControl",
          // The failure toast names the session now, and the bulk caller's own toast is gone: the
          // callee swallows, so that catch could never run and a bulk failure was unattributable.
          editedSince: [
            {
              was: [
                "  } catch (err) { toast(`Session ${action} failed: ${err?.message || err}`, 'error'); }",
              ],
              now: [
                "  // NAMES THE SESSION. This handler is the only one that ever runs -- it swallows, so the bulk",
                "  // caller's own catch below can never fire, and the message that named the failing id lived",
                "  // there. Over one session the operator knows which; over twenty they had no way to tell.",
                "  } catch (err) { toast(`Session ${sessionId} ${action} failed: ${err?.message || err}`, 'error'); }",
              ],
            },
            // 2026-08-27: a control no longer sends a body. The route stored it as the
            // spawn request's `initial_message`, which the settle handoff turns into a
            // type=request MESSAGE to the agent that just restarted -- and the agent then
            // restarted itself again. Measured: all 21 self-issued spawn requests on the
            // operator's fleet follow one of these by 45-75 seconds.
            {
              was: [
                "        body: `Session ${action} requested from Dashboard Next.`,",
              ],
              now: [
                "        // NO `body`. It is not a note -- the route stores it as the spawn request's",
                "        // `initial_message`, and `_hand_settled_spawn_to_dispatch` turns a non-empty one into a",
                "        // real `type=request` MESSAGE plus a dispatch run addressed to the agent that just came",
                "        // up. So a receipt reading \"Session restart requested from Dashboard Next.\" arrived at",
                "        // the freshly-restarted agent as an instruction owing a reply, and the agent did the",
                "        // obvious thing: it called comms_restart on itself.",
                "        //",
                "        // MEASURED on the operator's fleet: all 21 self-issued spawn requests are preceded, 45",
                "        // to 75 seconds earlier, by exactly one of these -- a dashboard `Restart <agent>`",
                "        // message of type=request. That is the whole of \"agents exited even though I never",
                "        // stopped them\": the operator restarted one agent and the loop kept going.",
                "        //",
                "        // The service is not wrong. `initial_message` is for a BRIEF -- work the new worker is",
                "        // being started to do -- and the message it becomes exists so the agent's inbox is not",
                "        // empty and it has an id to thread a reply to. A control has no brief; sending one was",
                "        // the mistake. `comms_restart` on the bridge side already sends no body.",
              ],
            },
          ],
        at: 3882,
        marker: "// requestSessionControl moved to ./agent-session-actions.mjs in v0.5.4.",
      },
      {
        name: "requestBulkSessionControl",
          // The failure toast names the session now, and the bulk caller's own toast is gone: the
          // callee swallows, so that catch could never run and a bulk failure was unattributable.
          editedSince: [
            {
              was: [
                "      // Isolate per-item failures so one bad session doesn't abort the rest of the batch",
              ],
              now: [
                "      // Isolation comes from requestSessionControl swallowing its own error, not from this catch:",
                "// it cannot throw, so the loop continues either way and the catch below never runs. Kept as",
                "// defence in depth for the day that changes; the failing id is named by the callee now.",
              ],
            },
            {
              was: [
                "      try { await requestSessionControl(id, action, false, false); } catch (err) { toast(`${action} ${id} failed: ${err?.message || err}`, 'error'); }",
              ],
              now: [
                "      try { await requestSessionControl(id, action, false, false); } catch (_) { /* the callee reported it, and names the session */ }",
              ],
            },
          ],
        at: 3903,
        marker: "// requestBulkSessionControl moved to ./agent-session-actions.mjs in v0.5.4.",
      },
      {
        name: "openAgentChat",
        at: 4033,
        // `at` points at the first COMMENT line, not the declaration: 1 lines of prose
        // moved with it, and `leading` restores them FROM THE MODULE so they are byte-checked too.
        leading: 1,
        marker: "// openAgentChat moved to ./agent-session-actions.mjs in v0.5.4.",
      },
    ],
  },
  {
    module: "analytics-page.mjs",
    importLine: "import { loadAnalytics, renderAnalyticsPage, renderUsagePools } from './analytics-page.mjs';",
    items: [
      {
        name: "loadAnalytics",
        at: 1213,
        leading: 4,
        marker: "// loadAnalytics moved to ./analytics-page.mjs in v0.5.4, with its caching note.",
      },
      {
        name: "renderUsagePools",
        // `leading: 1`, not 3. Two of the three comment lines above it in app.js are EARLIER slices'
        // markers (`usageResetLabel`/`usageFmtTokens` moved to util.js) and belong to app.js, not here.
        // Taking them along silently stole another slice's marker and broke that entry, not this one.
        at: 1263,
        leading: 1,
        marker: "// renderUsagePools moved to ./analytics-page.mjs in v0.5.4, with the note on what a pool is.",
      },
      {
        name: "renderAnalyticsPage",
        at: 1331,
        marker: "// renderAnalyticsPage moved to ./analytics-page.mjs in v0.5.4.",
      },
    ],
  },
  {
    module: "work-loop-actions.mjs",
    importLine: "import { closeWorkContract, initWorkLoopActions, loadContractsForState, remindWorkContract, renderContracts, renderDiagnosticsBulkToolbar, requestBulkDiagnosticAction, runMaintenance } from './work-loop-actions.mjs';",
    seeding: "initWorkLoopActions({ refresh });",
    items: [
      {
        name: "loadContractsForState",
          // Reports its own failure now: it swallows the error, so the poll cycle's catch could
          // never see one and the noteSliceFailure added there in 85780f7a was dead code.
          editedSince: [{
            was: [
              "  try { const res = await api(qs); state.contracts = res.contracts || []; } catch (err) { toast(`Load contracts failed: ${err?.message || err}`, 'error'); }",
            ],
            now: [
              "  // `truncated` is kept, not dropped. The endpoint scans a bounded superset because a contract's",
              "  // state is derived in Python and the SQL filter standing in for it is deliberately wider; when",
              "  // that scan hits its ceiling the list is a page, not the whole answer. A truncated list that",
              "  // does not admit it is truncated reads as \"that is everything\" -- which is exactly the defect",
              "  // this endpoint just had, in its summary.",
              "  try {",
              "    const res = await api(qs);",
              "    state.contracts = res.contracts || [];",
              "    state.contractsTruncated = Boolean(res.truncated);",
              "  }",
              "  catch (err) { noteSliceFailure('contract filter'); toast(`Load contracts failed: ${err?.message || err}`, 'error'); }",
            ],
          }],
        at: 711,
        leading: 3,
        marker: "// loadContractsForState moved to ./work-loop-actions.mjs in v0.5.4, with the note on why it reloads.",
      },
      {
        name: "runMaintenance",
        at: 1486,
        marker: "// runMaintenance moved to ./work-loop-actions.mjs in v0.5.4.",
      },
      {
        name: "renderDiagnosticsBulkToolbar",
        at: 1500,
        marker: "// renderDiagnosticsBulkToolbar moved to ./work-loop-actions.mjs in v0.5.4.",
      },
      {
        // EDITED SINCE THE MOVE, deliberately and in its own commit. The finding — that this was the
        // one renderer here without a missing-host guard, in a function the orchestrator runs on every
        // poll — surfaced during the relocation, where fixing it would have been a behaviour change
        // smuggled into a byte-identical move. Declaring the edit is what keeps the REST of the body
        // proved, instead of the whole function quietly becoming unverified.
        name: "renderContracts",
        at: 2957,
        marker: "// renderContracts moved to ./work-loop-actions.mjs in v0.5.4.",
        editedSince: [
          // Says so when the scan was capped: under a cap, 'no match' and 'none exist' look identical.
          {
            was: [
              "  if (!contracts.length) {",
              "    host.innerHTML = '<div class=\"empty-state\"><span class=\"empty-icon\">\u2713</span><strong>No contracts match</strong><p>No reply obligations in this filter.</p></div>';",
              "  } else if (state.contractView === 'board') {",
              "    host.innerHTML = renderContractBoard(contracts);",
              "  } else {",
              "    host.innerHTML = contracts.map(contractCard).join('');",
            ],
            now: [
              "  // Said on screen rather than left to the reader to infer: under a capped scan, \"no contracts",
              "  // match\" and \"none exist\" are different facts that look identical.",
              "  const capped = state.contractsTruncated",
              "    ? '<div class=\"mb mb-warn\">Showing a partial scan \u2014 more may match than are listed.</div>'",
              "    : '';",
              "  if (!contracts.length) {",
              "    host.innerHTML = capped + '<div class=\"empty-state\"><span class=\"empty-icon\">\u2713</span><strong>No contracts match</strong><p>No reply obligations in this filter.</p></div>';",
              "  } else if (state.contractView === 'board') {",
              "    host.innerHTML = capped + renderContractBoard(contracts);",
              "  } else {",
              "    host.innerHTML = capped + contracts.map(contractCard).join('');",
            ],
          },{
          was: [],
          now: [
            "  // The missing-host guard every neighbouring renderer has — `renderUsagePools`,",
            "  // `renderDiagnosticsBulkToolbar`, `renderSessionConsole`. This one dereferenced `host` directly, and",
            "  // it is called from the render orchestrator on EVERY poll, so the day `#contract-list` is renamed or",
            "  // dropped from a page the whole dashboard stops re-rendering rather than just this panel.",
            "  //",
            "  // The bulk toolbar is still rendered on the way out: it lives in its own container and its selection",
            "  // is pruned there, so skipping it would leave a stale count beside a panel that never drew.",
            "  if (!host) { renderDiagnosticsBulkToolbar(); return; }",
          ],
        }],
      },
      {
        name: "closeWorkContract",
        at: 3927,
        marker: "// closeWorkContract moved to ./work-loop-actions.mjs in v0.5.4.",
      },
      {
        // Edited since the move, same reason and same commit as `renderContracts` above: the falsy-id
        // guard every neighbouring action already had.
        name: "remindWorkContract",
        at: 3939,
        marker: "// remindWorkContract moved to ./work-loop-actions.mjs in v0.5.4.",
        editedSince: [{
          was: [],
          now: [
            "  // The falsy-id guard every neighbour has — `closeWorkContract`, `stopAgentWorker`, `removeAgent`,",
            "  // `deleteSessionById`, `requestSessionControl`. This one did not, and posted `?runId=` for the",
            "  // server to reject. Both callers happen to supply an id today (the bulk path filters to contracts",
            "  // that have one; the click handler reads an attribute that is always written), so it was latent —",
            "  // but \"reachable only through the paths we happen to have\" is the state a guard exists to remove.",
            "  if (!runId) return;",
          ],
        }],
      },
      {
        name: "requestBulkDiagnosticAction",
        at: 3944,
        marker: "// requestBulkDiagnosticAction moved to ./work-loop-actions.mjs in v0.5.4.",
      },
    ],
  },
  {
    module: "message-actions.mjs",
    importLine: "import { addChannelMember, chatChannelAction, initMessageActions, markConversationRead, markMessageRead, mountChatConsole, openMessageThread, removeChannelMember, toggleFavorite, unsendMessage } from './message-actions.mjs';",
    seeding: "initMessageActions({ chatController, refreshSoon, renderSessionConsole });",
    items: [
      {
        name: "markMessageRead",
        at: 180,
        leading: 2,
        marker: "// markMessageRead moved to ./message-actions.mjs in v0.5.4, with its optimistic-update note.",
      },
      {
        name: "unsendMessage",
        at: 191,
        marker: "// unsendMessage moved to ./message-actions.mjs in v0.5.4.",
        // H4 (2026-08-18): `DELETE /messages/{id}` now REQUIRES an acting agent and refuses an
        // actor-less delete, because it used to remove any message by id with no ownership check at
        // all. The dashboard is an operator surface, so it names itself. This changes a body the
        // proof reconstructs, so it is written down here rather than silently tolerated — the same
        // reason the clipboard fix above carries one.
        editedSince: [{
          was: "    await api(`/messages/${encodeURIComponent(messageId)}`, { method: 'DELETE' });",
          now: [
            "    // `requestedBy` is mandatory since H4 (2026-08-18) — the endpoint refuses an actor-less",
            "    // delete. The dashboard is an operator surface, so it may unsend a message it did not write.",
            "    await api(`/messages/${encodeURIComponent(messageId)}?requestedBy=dashboard`, { method: 'DELETE' });",
          ],
        }],
      },
      {
        name: "markConversationRead",
        at: 202,
        marker: "// markConversationRead moved to ./message-actions.mjs in v0.5.4.",
      },
      {
        name: "toggleFavorite",
        at: 221,
        leading: 1,
        marker: "// toggleFavorite moved to ./message-actions.mjs in v0.5.4.",
      },
      {
        name: "mountChatConsole",
        at: 252,
        leading: 5,
        marker: "// mountChatConsole moved to ./message-actions.mjs in v0.5.4, with the note on what it mounts.",
      },
      {
        name: "chatChannelAction",
        at: 395,
        marker: "// chatChannelAction moved to ./message-actions.mjs in v0.5.4.",
      },
      {
        name: "addChannelMember",
        at: 416,
        leading: 1,
        marker: "// addChannelMember moved to ./message-actions.mjs in v0.5.4.",
      },
      {
        name: "removeChannelMember",
        at: 428,
        marker: "// removeChannelMember moved to ./message-actions.mjs in v0.5.4.",
      },
      {
        name: "openMessageThread",
        at: 4025,
        leading: 1,
        marker: "// openMessageThread moved to ./message-actions.mjs in v0.5.4.",
      },
    ],
  },
  {
    module: "console-actions.mjs",
    importLine: "import { initConsoleActions, openRunConsole, resyncActiveConsole, startConsoleForSession, stopConsoleTerminal } from './console-actions.mjs';",
    seeding: "initConsoleActions({ closeInspector, refresh, refreshSoon, setPage });",
    items: [
      {
        name: "resyncActiveConsole",
        at: 2317,
        leading: 2,
        marker: "// resyncActiveConsole moved to ./console-actions.mjs in v0.5.4, with the note on what it mirrors.",
      },
      {
        name: "stopConsoleTerminal",
        at: 2395,
        marker: "// stopConsoleTerminal moved to ./console-actions.mjs in v0.5.4.",
      },
      {
        name: "startConsoleForSession",
        at: 2406,
        marker: "// startConsoleForSession moved to ./console-actions.mjs in v0.5.4.",
      },
      {
        name: "openRunConsole",
        at: 4044,
        marker: "// openRunConsole moved to ./console-actions.mjs in v0.5.4.",
      },
    ],
  },
  {
    module: "click-dispatch.mjs",
    importLine: "import { dispatchClick, initClickDispatch } from './click-dispatch.mjs';",
    seeding: [
      "initClickDispatch({ chatController, closeInspector, refreshSoon, renderSessionWorkspace, setPage });",
      "document.addEventListener('click', dispatchClick);",
    ],
    items: [
      {
        // NOT A DECLARATION IN THE PRISTINE FILE — it was an anonymous listener callback. The 308 body
        // lines are byte-identical; the wrapper around them is the whole of the edit, so it is declared
        // as one, with the pristine `document.addEventListener('click', (event) => {` restored in place
        // of the module's function head and `});` in place of its closing brace.
        //
        // The footer anchor is three lines, not one: `}` alone matches the FIRST closing brace inside
        // the body, which would have spliced the tail of the dispatcher into the middle of itself.
        name: "dispatchClick",
        at: 4236,
        marker: [
          "// The delegated click dispatcher moved to ./click-dispatch.mjs in v0.5.4. Registering it stays here,",
          "// so the boot sequence remains visible in one place.",
        ],
        editedSince: [
          {
            // The JSDoc above the declaration is NOT part of the span — `declarationSpan` starts at
            // the `function` line — and it is new prose this slice wrote rather than anything moved,
            // so it is neither restored nor declared.
            now: ["function dispatchClick(event) {"],
            was: ["document.addEventListener('click', (event) => {"],
          },
          {
            now: [
              "  // (Removed the catch-all [data-kind] → JSON-inspector fallback: it hijacked clicks on the",
              "  // empty area of any row/message and popped raw JSON. Explicit inspect buttons still work.)",
              "}",
            ],
            was: [
              "  // (Removed the catch-all [data-kind] → JSON-inspector fallback: it hijacked clicks on the",
              "  // empty area of any row/message and popped raw JSON. Explicit inspect buttons still work.)",
              "});",
            ],
          },
        ],
      },
    ],
  },
  {
    module: "notifications.mjs",
    importLine: "import { dashboardNotifier, notificationsEnabled, toggleNotifications } from './notifications.mjs';",
    items: [
      {
        // `notificationsEnabled` is exported as a `let` and app.js reads it through a LIVE binding, so
        // the button still sees the value `toggleNotifications` assigned. Copying it instead would give
        // app.js a flag nobody writes — a toggle that does not stick.
        name: "notificationsEnabled",
        at: 594,
        leading: 3,
        marker: "// notificationsEnabled moved to ./notifications.mjs in v0.5.4 — the flag and the function that assigns it are one unit.",
      },
      { name: "dashboardNotifier", at: 598, marker: "// dashboardNotifier moved to ./notifications.mjs in v0.5.4." },
      { name: "toggleNotifications", at: 613, marker: "// toggleNotifications moved to ./notifications.mjs in v0.5.4." },
    ],
  },
  {
    module: "boot-wiring.mjs",
    importLine: "import { restorePersistedPreferences, wireGlobalControls, wireInspectorGestures, wireSettingsControls } from './boot-wiring.mjs';",
    seeding: [
      "wireGlobalControls({ chatController, closeInspector, refresh, renderAll, renderSessionWorkspace, saveSettings, chatCreateChannel, inspect });",
      "wireInspectorGestures();",
      "restorePersistedPreferences({ setPage });",
      "wireSettingsControls({ saveSettings });",
    ],
    items: [
      {
        // 264 lines of TOP-LEVEL STATEMENTS, not a declaration — so the whole of the edit is the
        // wrapper, declared with `wrapper` rather than `editedSince`. `indent: "  "` says the module
        // added two spaces to every line, which reconstruct strips; `unwrapBody` throws if any line
        // does not carry it, so a line EDITED rather than re-indented cannot hide behind the mask.
        //
        // Re-indenting is only safe because the run contains no multi-line template literal. It does
        // not: every line in it has an even number of backticks, so no string spans a line break and
        // the two spaces cannot end up inside one.
        name: "wireGlobalControls",
        at: 4650,
        marker: [
          "// The boot-time listener wiring moved to ./boot-wiring.mjs in v0.5.4. The CALL stays here so the",
          "// boot sequence is still readable in one place, in order.",
        ],
        wrapper: {
          header: [
            "export function wireGlobalControls({",
            "  chatController,",
            "  closeInspector,",
            "  refresh,",
            "  renderAll,",
            "  renderSessionWorkspace,",
            "  saveSettings,",
            "  chatCreateChannel,",
            "  inspect,",
            "}) {",
          ],
          footer: ["}"],
          indent: "  ",
        },
      },
      {
        // The swipe-to-close gesture, INCLUDING the `let` it is the only reader of. Both listeners and
        // the variable are one unit: touchstart writes it, touchend reads it.
        name: "wireInspectorGestures",
        at: 4964,
        marker: [
          "// The inspector's swipe-to-close gesture moved to ./boot-wiring.mjs in v0.5.4, with the",
          "// touch-start position it is the only reader of.",
        ],
        wrapper: {
          header: ["export function wireInspectorGestures() {"],
          footer: ["}"],
          indent: "  ",
        },
      },
      {
        name: "restorePersistedPreferences",
        // The Needs-Attention strip's collapsed state stopped living only in a CSS rotation:
        // this set the class by hand, so its toggle carried no aria-expanded, no aria-controls
        // and a title that never changed. DE-INDENTED by the wrapper's two spaces, because
        // undoEdits runs AFTER unwrapBody.
        editedSince: [
          {
            was: [
              "try {",
              "  if (localStorage.getItem('aify.next.attentionCollapsed') !== '0') {",
              "    byId('attention-strip')?.classList.add('collapsed');",
              "  }",
              "} catch {",
              "  byId('attention-strip')?.classList.add('collapsed');",
              "}",
            ],
            now: [
              "// One call, so the class, aria-expanded, aria-controls and the title cannot disagree. Both",
              "// branches used to add the class and nothing else, which is how this toggle's state came to be",
              "// legible as a CSS rotation and in no other way.",
              "setAttentionCollapsed(preferredAttentionCollapsed());",
            ],
          },
        ],
        at: 4998,
        marker: "// Preference restore + landing paint moved to ./boot-wiring.mjs in v0.5.4.",
        wrapper: {
          header: ["export function restorePersistedPreferences({ setPage }) {"],
          footer: ["}"],
          indent: "  ",
        },
      },
      {
        name: "wireSettingsControls",
        // The Needs-Attention strip's collapsed state stopped living only in a CSS rotation:
        // this set the class by hand, so its toggle carried no aria-expanded, no aria-controls
        // and a title that never changed. DE-INDENTED by the wrapper's two spaces, because
        // undoEdits runs AFTER unwrapBody.
        editedSince: [
          {
            was: [
              "  const collapsed = strip.classList.toggle('collapsed');",
              "  try { localStorage.setItem('aify.next.attentionCollapsed', collapsed ? '1' : '0'); } catch { /* ignore */ }",
            ],
            now: [
              "  setAttentionCollapsed(!strip.classList.contains('collapsed'));",
            ],
          },
        ],
        at: 5054,
        marker: "// The Settings page's controls moved to ./boot-wiring.mjs in v0.5.4.",
        wrapper: {
          header: ["export function wireSettingsControls({ saveSettings }) {"],
          footer: ["}"],
          indent: "  ",
        },
      },
    ],
  },
];

const MODULES = () => ({
  "settings-fields.mjs": read("settings-fields.mjs"),
  "util.js": read("util.js"),
  "record-fields.mjs": read("record-fields.mjs"),
  "status.js": read("status.js"),
  "environment-start-command.mjs": read("environment-start-command.mjs"),
  "run-event.mjs": read("run-event.mjs"),
  "terminal-width.mjs": read("terminal-width.mjs"),
  "cli-resume.mjs": read("cli-resume.mjs"),
  "state.mjs": read("state.mjs"),
  "ui.js": read("ui.js"),
  "api-client.mjs": read("api-client.mjs"),
  "shared-files.mjs": read("shared-files.mjs"),
  "message-transport.mjs": read("message-transport.mjs"),
  "version-badge.mjs": read("version-badge.mjs"),
  "xterm-lifecycle.mjs": read("xterm-lifecycle.mjs"),
  "api-origin.mjs": read("api-origin.mjs"),
  "session-rail.mjs": read("session-rail.mjs"),
  "settings-panel.mjs": read("settings-panel.mjs"),
  "chat-click-handlers.mjs": read("chat-click-handlers.mjs"),
  "session-click-handlers.mjs": read("session-click-handlers.mjs"),
  "agent-click-handlers.mjs": read("agent-click-handlers.mjs"),
  "nav-click-handlers.mjs": read("nav-click-handlers.mjs"),
  "console-click-handlers.mjs": read("console-click-handlers.mjs"),
  "keyboard-shortcuts.mjs": read("keyboard-shortcuts.mjs"),
  "run-helpers.mjs": read("run-helpers.mjs"),
  "console-await.mjs": read("console-await.mjs"),
  "layout-prefs.mjs": read("layout-prefs.mjs"),
  "render-memo.mjs": read("render-memo.mjs"),
  "record-lookup.mjs": read("record-lookup.mjs"),
  "static-links.mjs": read("static-links.mjs"),
  "page-titles.mjs": read("page-titles.mjs"),
  "xterm-mount.mjs": read("xterm-mount.mjs"),
  "session-console.mjs": read("session-console.mjs"),
  "refresh-cycle.mjs": read("refresh-cycle.mjs"),
  "realtime-socket.mjs": read("realtime-socket.mjs"),
  "run-inspector.mjs": read("run-inspector.mjs"),
  "agent-session-actions.mjs": read("agent-session-actions.mjs"),
  "analytics-page.mjs": read("analytics-page.mjs"),
  "work-loop-actions.mjs": read("work-loop-actions.mjs"),
  "message-actions.mjs": read("message-actions.mjs"),
  "console-actions.mjs": read("console-actions.mjs"),
  "click-dispatch.mjs": read("click-dispatch.mjs"),
  "notifications.mjs": read("notifications.mjs"),
  "boot-wiring.mjs": read("boot-wiring.mjs"),
  "agent-drawer.mjs": read("agent-drawer.mjs"),
  "work-loop-panels.mjs": read("work-loop-panels.mjs"),
  "codex-console.mjs": read("codex-console.mjs"),
  "identity-directory.mjs": read("identity-directory.mjs"),
  "status-why-popover.mjs": read("status-why-popover.mjs"),
  "session-activity.mjs": read("session-activity.mjs"),
  "environments-panels.mjs": read("environments-panels.mjs"),
  "summary-tiles.mjs": read("summary-tiles.mjs"),
  "clipboard.mjs": read("clipboard.mjs"),
  "inspector-forms.mjs": read("inspector-forms.mjs"),
  "run-inspector-controls.mjs": read("run-inspector-controls.mjs"),
  "chat-prefs.mjs": read("chat-prefs.mjs"),
});

function rebuild(overrides = {}) {
  return reconstruct({
    after: overrides.after ?? read("app.js"),
    modules: overrides.modules ?? MODULES(),
    extractions: overrides.extractions ?? EXTRACTIONS,
  });
}

test("app.js reconstructs byte-identically from every extraction to date", () => {
  assert.equal(
    rebuild(),
    read(PRISTINE),
    "reconstruction differs from the pre-extraction app.js, so some slice changed something outside the "
      + "spans it declared",
  );
});

test("every declared marker still exists verbatim SOMEWHERE the reconstruction will find it", () => {
  // A guard on the plan itself: reconstruct() asserts marker text verbatim, so a mistyped marker would
  // throw rather than silently skip that body.
  //
  // IT USED TO LOOK ONLY IN app.js, and a later slice broke that assumption without breaking anything
  // real. Some markers are not comments at all but the CALL SITE a slice left behind — `applyThemeChoice`
  // left `    applyThemeChoice(themeChoice);` inside the delegated click handler — and when the click
  // handler itself moved to click-dispatch.mjs, the line went with it. The reconstruction still passes,
  // because by the time that marker is looked for, the click body has been restored into the file; only
  // this narrower guard failed.
  //
  // So the marker must exist in app.js OR in a module the plan names, which is exactly the set of places
  // reconstruct() can end up reading it from.
  const modules = MODULES();
  const haystacks = [read("app.js"), ...Object.values(modules)];
  for (const step of EXTRACTIONS) {
    for (const item of step.items) {
      if (item.marker == null) continue;
      const first = [].concat(item.marker)[0];
      assert.ok(
        haystacks.some((source) => source.includes(first)),
        `${item.name}'s marker is in neither app.js nor any extracted module, so the plan and the files disagree`,
      );
    }
  }
});

test("the reconstruction fixture is TRACKED, not ignored", () => {
  const rel = `service/new_dashboard/${PRISTINE}`;
  assert.equal(
    isGitIgnored(rel),
    false,
    `${rel} is git-ignored, so this proof would not exist on a clean clone`,
  );
});

function isGitIgnored(rel) {
  // `git check-ignore` exits 1 when the path is NOT ignored, which is the success case here. Written with
  // a static import because this file is ESM and `require` is not defined in it — my first version used
  // `require` and the test failed on the harness rather than on the property.
  try {
    execFileSync("git", ["check-ignore", rel], { cwd: path.join(HERE, "..", ".."), stdio: "pipe" });
    return true;
  } catch {
    return false;
  }
}

test("declarationSpan ends a declaration that carries a TRAILING COMMENT", () => {
  // The terminator test was `line.endsWith(";")`, so `const x = new Map(); // note` never ended and the
  // span ran past the declaration — off the end of the module, for a const declared last, returning null.
  // Found when codex-console.mjs failed to reconstruct with "codexConsoleConnections not found".
  assert.equal(declarationSpan("const a = new Map(); // note", "a").text, "const a = new Map(); // note");

  // The obvious fix — split on the first `//` — is wrong, and this is the case that proves it: the code
  // part of a URL string would end at `'http:` and the span would run on again, one silent failure traded
  // for another. Quote state is tracked instead.
  const url = `const u = "http://example.test/x"; // note`;
  assert.equal(declarationSpan(url, "u").text, url);

  // The balance rule still governs: an IIFE's inner `;` must not terminate the declaration early.
  const iife = ["const w = (() => {", "  const raw = 1;", "  return raw;", "})();"].join(LF);
  assert.equal(declarationSpan(iife, "w").text, iife);
});

test("the browser-globals check separates LOAD-TIME access from a deferred function body", () => {
  // Added when `byId` moved to ui.js in v0.5.4. The check flagged
  // `const byId = (id) => document.getElementById(id);` — a braceless arrow, so the brace-depth counter
  // never saw a body and read `document` as module-scope code. It is not: the module imports fine in Node
  // and only touches the DOM when called.
  //
  // That mattered beyond a false alarm. The only way to satisfy the old check was to reword the
  // declaration into a braced function — which would have broken the byte-identity the reconstruction
  // proof requires of every moved body. A wrong check would have forced a wrong edit.
  assert.deepEqual(moduleScopeBrowserRefs("const byId = (id) => document.getElementById(id);"), [],
    "a braceless arrow body is deferred code, not module-scope access");
  assert.deepEqual(moduleScopeBrowserRefs("export const go = (u) => window.open(u);"), [],
    "…including when exported");

  assert.equal(moduleScopeBrowserRefs("const w = document.title;").length, 1,
    "a real load-time read must still be caught");
  assert.equal(moduleScopeBrowserRefs("const p = document.body.x || ((y) => y);").length, 1,
    "…and must not be excused by an arrow appearing LATER on the same line");
  assert.equal(moduleScopeBrowserRefs(["const f = () => {", "document.title = 1;", "};"].join(LF)).length, 0,
    "a braced body was already excluded by the depth counter; that behaviour is unchanged");
});

test("the browser-globals check honours a typeof guard, but only for the global it guards", () => {
  // Added when notifications.mjs moved. It opens with
  //   export let notificationsEnabled = readEnabled(typeof localStorage !== 'undefined' ? localStorage : null);
  // which is NOT module-scope browser code: `typeof` is the one reference that never throws on an
  // undeclared name, and the bare use sits in a branch that only evaluates when the global exists. The
  // module imports cleanly in Node — verified before this check runs — so flagging it called an importable
  // module unimportable.
  assert.deepEqual(moduleScopeBrowserRefs("const a = g(typeof localStorage !== 'undefined' ? localStorage : null);"), []);
  assert.deepEqual(moduleScopeBrowserRefs("const d = typeof window === 'undefined' ? null : window.x;"), [],
    "the inverted form of the same guard counts too");

  assert.equal(moduleScopeBrowserRefs("const b = localStorage.getItem(1);").length, 1,
    "an unguarded load-time read must still be caught");

  // THE CASE THAT KEEPS THIS HONEST: guarding one global must not excuse dereferencing another. Without
  // this the exemption would degrade into "any line containing the word typeof passes".
  const mixed = moduleScopeBrowserRefs("const c = typeof localStorage !== 'undefined' ? document.title : null;");
  assert.equal(mixed.length, 1);
  assert.equal(mixed[0].global, "document");
});

test("every extracted module has NO module-scope browser globals", () => {
  for (const [name, source] of Object.entries(MODULES())) {
    assert.deepEqual(
      moduleScopeBrowserRefs(source),
      [],
      `${name} has module-scope browser code, which makes it as unimportable as app.js and defeats the `
        + "point of extracting into it",
    );
  }
});

test("the purity check can actually SEE a module-scope browser global", () => {
  // Without this, the assertion above passes by matching nothing.
  //
  // The specimen used to be `const byId = (id) => document.getElementById(id);`, which was the wrong
  // one: that is a braceless ARROW BODY, deferred until the function is called, and the test directly
  // below already says such a body is fine. The two contradicted each other and the check sided with
  // this one -- so a module could be called unimportable for code that never runs on import, and the
  // only way to satisfy it was to reword a moved declaration and break the reconstruction proof's
  // byte-identity. Corrected when `byId` moved to ui.js in v0.5.4; this is now a real load-time read.
  const hits = moduleScopeBrowserRefs("const title = document.title;\n");
  assert.equal(hits.length, 1);
  assert.equal(hits[0].global, "document");
});

test("the purity check ignores browser globals INSIDE a function body", () => {
  // A function that touches the DOM when CALLED is fine; only module scope runs on import.
  assert.deepEqual(moduleScopeBrowserRefs("function f() {\n  return document.title;\n}\n"), []);
});

test("reconstruction FAILS when a body is restored at the wrong line", () => {
  const shifted = EXTRACTIONS.map((step) => ({
    ...step,
    items: step.items.map((item, i) => (i === 0 ? { ...item, at: item.at + 1 } : item)),
  }));
  // BREAKING can mean either answer: a different file, or a refusal. Once the plan grew entries whose
  // `at` points at a marker rather than a declaration, shifting the index stopped producing a wrong
  // rebuild and started producing a THROW — the marker is no longer where the plan says, which is the
  // proof refusing rather than guessing. Asserting only `notEqual` let that exception escape as a test
  // error, reporting a stricter proof as a broken one.
  let rebuilt = null;
  let refused = false;
  try {
    rebuilt = rebuild({ extractions: shifted });
  } catch {
    refused = true;
  }
  assert.ok(
    refused || rebuilt !== read(PRISTINE),
    "an off-by-one in a restore index must break reconstruction, or the proof is not position-sensitive",
  );
});

test("reconstruction FAILS when whitespace outside the extracted spans moves", () => {
  const original = read("app.js");
  // VERIFY THE TAMPER LANDED before reading the result. An early version replaced a string that does not
  // occur in app.js, so it tampered with nothing and passed while proving nothing — the same class as a
  // mutation applied to a docstring instead of to code.
  //
  // THE TARGET IS THE FIRST LINE, not a named declaration. It used to be `const SETTINGS_SCHEMA = [`,
  // and the settings-panel slice moved that out of app.js — so this test failed because the extraction
  // it was meant to guard had SUCCEEDED. Five gates in this series have now been anchored to something
  // the decomposition was busy removing. Every slice relocates declarations; none of them relocates the
  // top of the file, so a line-zero anchor cannot go stale the same way.
  const lines = original.split(LF);
  assert.ok(lines[0].length > 0, "app.js must start with a non-empty line to tamper with");
  const tampered = [`${lines[0]} `, ...lines.slice(1)].join(LF);
  assert.notEqual(tampered, original, "the tamper must actually change the source");
  assert.notEqual(rebuild({ after: tampered }), read(PRISTINE));
});

test("reconstruction REFUSES when a marker comment does not match verbatim", () => {
  const marker = [].concat(EXTRACTIONS[1].items[0].marker)[0];
  const tampered = read("app.js").replace(marker, "// fileSizeLabel moved.");
  assert.throws(
    () => rebuild({ after: tampered }),
    /marker not found verbatim for fileSizeLabel/,
    "a loosened marker mask could hide an edit, so a changed marker must throw rather than adapt",
  );
});

test("reconstruction REFUSES when an added import line is absent", () => {
  const tampered = read("app.js").replace(EXTRACTIONS[0].importLine, "import { x } from './y.mjs';");
  assert.throws(() => rebuild({ after: tampered }), /import line not found verbatim/);
});

test("reconstruction REFUSES when an extracted function is missing from its module", () => {
  const modules = MODULES();
  modules["util.js"] = modules["util.js"].replace("export function fileSizeLabel", "function fileSizeLabelX");
  assert.throws(() => rebuild({ modules }), /fileSizeLabel not found in util\.js/);
});

test("a PRE-EXISTING export round-trips unchanged", () => {
  // Required before touching mcp/stdio/hermes-managed-host.js, where 11 functions in the first cluster are
  // already `export function`. Their spans are byte-identical with no substitution at all, so the prover
  // must NOT strip a keyword the pristine file contained. Proven both directions on a synthetic pair.
  const pristine = ["const before = 1;", "export function pub(a) {", "  return a;", "}", "const after = 2;", ""].join(LF);
  const host = ["const before = 1;", "// pub moved to ./mod.mjs.", "const after = 2;", ""].join(LF);
  const mod = ["export function pub(a) {", "  return a;", "}", ""].join(LF);

  const kept = reconstruct({
    after: host,
    modules: { "mod.mjs": mod },
    extractions: [{
      module: "mod.mjs",
      items: [{ name: "pub", at: 1, marker: "// pub moved to ./mod.mjs.", pristineExported: true }],
    }],
  });
  assert.equal(kept, pristine, "a pre-existing export must be preserved verbatim");

  // And the default must still STRIP, or every app.js slice would regress.
  const stripped = reconstruct({
    after: host,
    modules: { "mod.mjs": mod },
    extractions: [{
      module: "mod.mjs",
      items: [{ name: "pub", at: 1, marker: "// pub moved to ./mod.mjs." }],
    }],
  });
  assert.match(stripped, /^function pub\(a\) \{$/m, "the default treats `export ` as the added substitution");
  assert.notEqual(stripped, pristine);
});

test("reconstruction REFUSES when pristineExported disagrees with the module", () => {
  // The declaration and the file must not be allowed to drift apart — that is the failure this prover is
  // for. Claiming a pre-existing export for a private function is caught, not silently honoured.
  const mod = ["function priv(a) {", "  return a;", "}", ""].join(LF);
  assert.throws(
    () => reconstruct({
      after: ["// priv moved.", ""].join(LF),
      modules: { "mod.mjs": mod },
      extractions: [{
        module: "mod.mjs",
        items: [{ name: "priv", at: 0, marker: "// priv moved.", pristineExported: true }],
      }],
    }),
    /priv is declared pristineExported but its span in mod\.mjs has no export/,
  );
});

test("functionSpan finds a whole brace-matched body, not the first closing brace", () => {
  const src = "function outer(a) {\n  if (a) {\n    return 1;\n  }\n  return 2;\n}\nfunction after() {}\n";
  const span = functionSpan(src, "outer");
  assert.match(span.text, /return 2;/, "the span must run to the function's own closing brace");
  assert.doesNotMatch(span.text, /function after/);
});

// --- extract-method support -------------------------------------------------------------------
//
// The prover could only demonstrate a RELOCATION until now, and that limit was being read as a fact about
// app.js: its 631-line delegated click handler is one top-level statement holding ~82 branch bodies, none
// of them a declaration, so "no relocation reaches it" was true and "it needs a redesign" did not follow.
// These prove the wrapper path restores a body exactly, and — the half that matters — that it REFUSES
// every way a slice could change a line while claiming to have only moved it.

/** A pristine file holding a bare body inside a guard, plus the module an extract-method would produce. */
const WRAPPED = {
  pristine: [
    "before();",
    "if (hit) {",
    "  doThing(hit);",
    "  render();",
    "}",
    "after();",
  ].join(LF),
  module: [
    "export function handleHit(hit) {",
    "    doThing(hit);",
    "    render();",
    "}",
  ].join(LF),
  // app.js after the slice: the body replaced by a call, and an import added.
  after: [
    'import { handleHit } from "./hit.mjs";',
    "before();",
    "if (hit) {",
    "  handleHit(hit);",
    "}",
    "after();",
  ].join(LF),
};

const wrappedPlan = (overrides = {}) => [{
  module: "hit.mjs",
  importLine: 'import { handleHit } from "./hit.mjs";',
  items: [{
    name: "handleHit",
    at: 2,
    marker: "  handleHit(hit);",
    wrapper: { header: ["export function handleHit(hit) {"], footer: ["}"], indent: "  " },
    ...overrides,
  }],
}];

test("EXTRACT-METHOD reconstructs byte-identically — a wrapped body is restored as it was", () => {
  const rebuilt = reconstruct({
    after: WRAPPED.after,
    modules: { "hit.mjs": WRAPPED.module },
    extractions: wrappedPlan(),
  });
  assert.equal(rebuilt, WRAPPED.pristine);
});

test("the wrapper path REFUSES a header that does not match verbatim", () => {
  // The header is executable text. A mask broad enough to accept "some function signature" would hide a
  // changed parameter list, which is a behaviour change wearing a relocation's clothes.
  const plan = wrappedPlan({
    wrapper: { header: ["export function handleHit(other) {"], footer: ["}"], indent: "  " },
  });
  assert.throws(
    () => reconstruct({ after: WRAPPED.after, modules: { "hit.mjs": WRAPPED.module }, extractions: plan }),
    /wrapper header line 0 does not match/,
  );
});

test("the wrapper path REFUSES a footer that does not match verbatim", () => {
  const plan = wrappedPlan({
    wrapper: { header: ["export function handleHit(hit) {"], footer: ["} // done"], indent: "  " },
  });
  assert.throws(
    () => reconstruct({ after: WRAPPED.after, modules: { "hit.mjs": WRAPPED.module }, extractions: plan }),
    /wrapper footer line 0 does not match/,
  );
});

test("A BODY LINE THAT WAS EDITED RATHER THAN RE-INDENTED THROWS, naming the line", () => {
  // The failure this is really guarding. Re-indentation is the one substitution an extract-method needs,
  // so it is the one an edit can hide inside: change a line AND re-indent it and the diff looks like the
  // move. Requiring every non-blank line to literally carry the declared prefix means a line that lost it
  // is reported here instead of reconstructing to something whose diff blames its neighbour.
  const edited = WRAPPED.module.replace("    render();", "render();");
  assert.throws(
    () => reconstruct({ after: WRAPPED.after, modules: { "hit.mjs": edited }, extractions: wrappedPlan() }),
    /does not carry the declared indent, so it was EDITED/,
  );
});

test("a CHANGED body line still fails the byte-identity comparison", () => {
  // Belt and braces: the indent check above catches a line that lost its prefix; this catches one that
  // kept it and changed anyway. Two assertions on the same property, because two of this series' gate
  // bugs were found only by two checks disagreeing.
  const changed = WRAPPED.module.replace("    doThing(hit);", "    doThing(hit, true);");
  const rebuilt = reconstruct({
    after: WRAPPED.after,
    modules: { "hit.mjs": changed },
    extractions: wrappedPlan(),
  });
  assert.notEqual(rebuilt, WRAPPED.pristine);
});

test("blank lines inside a wrapped body survive, indented or not", () => {
  // A blank line is whitespace-only and carries no indent to strip. Treating it as a violation would make
  // the wrapper unusable on any body with a paragraph break in it.
  const pristine = ["if (hit) {", "  a();", "", "  b();", "}"].join(LF);
  const mod = ["export function h() {", "    a();", "", "    b();", "}"].join(LF);
  const after = ["import { h } from \"./h.mjs\";", "if (hit) {", "  h();", "}"].join(LF);
  const rebuilt = reconstruct({
    after,
    modules: { "h.mjs": mod },
    extractions: [{
      module: "h.mjs",
      importLine: 'import { h } from "./h.mjs";',
      items: [{
        name: "h",
        at: 1,
        marker: "  h();",
        wrapper: { header: ["export function h() {"], footer: ["}"], indent: "  " },
      }],
    }],
  });
  assert.equal(rebuilt, pristine);
});

test("an item with NO wrapper is unaffected — the relocation path is unchanged", () => {
  // Every extraction to date uses the unwrapped path. The byte-identity test at the top of this file
  // already covers them all, but asserting it here pins the reason: `wrapper` is opt-in per item.
  const plan = wrappedPlan();
  delete plan[0].items[0].wrapper;
  plan[0].items[0].at = 1;
  // Without a wrapper the whole declaration is restored, which is NOT the pristine body — the point being
  // that the two paths are different and the choice is the slice's to declare.
  const rebuilt = reconstruct({
    after: WRAPPED.after,
    modules: { "hit.mjs": WRAPPED.module },
    extractions: plan,
  });
  assert.match(rebuilt, /function handleHit/);
  assert.notEqual(rebuilt, WRAPPED.pristine);
});

test("DEDENT is the direction a real extract-method needs — module body at 2, pristine at 4", () => {
  // The shape every branch of app.js's click handler has: the body sits at 4 spaces inside `if (x) {`,
  // and becomes a top-level function whose body sits at 2. My first version of the wrapper only handled
  // the opposite direction, which no slice would ever produce.
  const pristine = [
    "before();",
    "if (hit) {",
    "    doThing(hit);",
    "    render();",
    "    return;",
    "}",
  ].join(LF);
  const mod = [
    "export function handleHit(hit) {",
    "  doThing(hit);",
    "  render();",
    "}",
  ].join(LF);
  const after = [
    'import { handleHit } from "./hit.mjs";',
    "before();",
    "if (hit) {",
    "    handleHit(hit);",
    "    return;",
    "}",
  ].join(LF);
  const rebuilt = reconstruct({
    after,
    modules: { "hit.mjs": mod },
    extractions: [{
      module: "hit.mjs",
      importLine: 'import { handleHit } from "./hit.mjs";',
      items: [{
        name: "handleHit",
        at: 2,
        marker: "    handleHit(hit);",
        wrapper: { header: ["export function handleHit(hit) {"], footer: ["}"], dedent: "  " },
      }],
    }],
  });
  assert.equal(rebuilt, pristine, "the `return;` stays behind and the body comes back at 4 spaces");
});

test("declaring BOTH indent and dedent is refused rather than silently preferring one", () => {
  const plan = wrappedPlan({
    wrapper: { header: ["export function handleHit(hit) {"], footer: ["}"], indent: "  ", dedent: "  " },
  });
  assert.throws(
    () => reconstruct({ after: WRAPPED.after, modules: { "hit.mjs": WRAPPED.module }, extractions: plan }),
    /cannot both be true/,
  );
});

test("a changed line under DEDENT still fails byte-identity, since no verbatim check is possible there", () => {
  // Stated because the dedent path is genuinely weaker: there is no prefix in the module line to verify
  // against, so the named error is unavailable and the diff is the only guard. Asserting it means the
  // weakness is bounded and known rather than discovered during a review.
  const pristine = ["if (hit) {", "    a();", "}"].join(LF);
  const mod = ["export function h() {", "  a(1);", "}"].join(LF);
  const after = ['import { h } from "./h.mjs";', "if (hit) {", "    h();", "}"].join(LF);
  const rebuilt = reconstruct({
    after,
    modules: { "h.mjs": mod },
    extractions: [{
      module: "h.mjs",
      importLine: 'import { h } from "./h.mjs";',
      items: [{
        name: "h",
        at: 1,
        marker: "    h();",
        wrapper: { header: ["export function h() {"], footer: ["}"], dedent: "  " },
      }],
    }],
  });
  assert.notEqual(rebuilt, pristine);
});

test("A CHAINED IMPORT EDIT UNWINDS NEWEST-FIRST — two slices touching one import line", () => {
  // The case that broke the plan the moment a second slice added a name to an import line an earlier slice
  // had written. Undoing them in plan order makes the OLDER entry look for text the NEWER one replaced, and
  // it throws "import line not found verbatim". The full-history test above already covers this, but only
  // as one of many reasons it could fail; this names the property so a regression says what broke.
  const pristine = ["a();", "b();"].join(LF);
  const after = [
    'import { one, two } from "./m.mjs";',
    "a();",
    "b();",
  ].join(LF);
  const mod = ["export function one() {", "  x();", "}", "export function two() {", "  y();", "}"].join(LF);
  const rebuilt = reconstruct({
    after,
    modules: { "m.mjs": mod },
    extractions: [
      // older slice: created the import with just `one`
      {
        module: "m.mjs",
        importLine: 'import { one } from "./m.mjs";',
        items: [],
      },
      // newer slice: added `two` to that same line
      {
        module: "m.mjs",
        importLine: 'import { one, two } from "./m.mjs";',
        importWas: 'import { one } from "./m.mjs";',
        items: [],
      },
    ],
  });
  assert.equal(rebuilt, pristine, "both edits must unwind, leaving no import behind");
});

// --- declared post-extraction edits --------------------------------------------------------------
//
// Without `editedSince` every extracted module is FROZEN: the proof rebuilds app.js from the current
// modules, so any later bug fix in extracted code turns the gate red. Six modules and counting would be
// unmaintainable, and the obvious escape is to delete the gate — which is the outcome worth preventing.

const EDITED = {
  pristine: ["before();", "function f() {", "  work();", "}"].join(LF),
  module: ["export function f() {", "  guard();", "  work();", "}"].join(LF),
  after: ['import { f } from "./f.mjs";', "before();", "// f moved to ./f.mjs."].join(LF),
};

const editedPlan = (editedSince) => [{
  module: "f.mjs",
  importLine: 'import { f } from "./f.mjs";',
  items: [{ name: "f", at: 1, marker: "// f moved to ./f.mjs.", editedSince }],
}];

test("A DECLARED EDIT reconstructs to the body that originally left app.js", () => {
  const rebuilt = reconstruct({
    after: EDITED.after,
    modules: { "f.mjs": EDITED.module },
    extractions: editedPlan([{ was: [], now: "  guard();" }]),
  });
  assert.equal(rebuilt, EDITED.pristine, "the added line is undone, the rest is untouched");
});

test("an UNDECLARED edit still fails — the exemption is per-change, not blanket", () => {
  // The whole point. `editedSince` must not become a switch that stops the gate checking this module.
  const rebuilt = reconstruct({
    after: EDITED.after,
    modules: { "f.mjs": EDITED.module },
    extractions: editedPlan(undefined),
  });
  assert.notEqual(rebuilt, EDITED.pristine);
});

test("a declared edit whose `now` is NOT in the module throws, naming the mismatch", () => {
  // The plan and the module must agree. A stale declaration left behind after the code moved on would
  // otherwise silently mask whatever is there instead.
  assert.throws(
    () => reconstruct({
      after: EDITED.after,
      modules: { "f.mjs": EDITED.module },
      extractions: editedPlan([{ was: [], now: "  somethingElse();" }]),
    }),
    /declared edit not found verbatim/,
  );
});

test("a MULTI-LINE edit is matched as a block, not line by line", () => {
  const pristine = ["function f() {", "  a();", "}"].join(LF);
  const mod = ["export function f() {", "  // note", "  a()", "    .b();", "}"].join(LF);
  const rebuilt = reconstruct({
    after: ['import { f } from "./f.mjs";', "// f moved."].join(LF),
    modules: { "f.mjs": mod },
    extractions: [{
      module: "f.mjs",
      importLine: 'import { f } from "./f.mjs";',
      items: [{
        name: "f",
        at: 0,
        marker: "// f moved.",
        editedSince: [{ was: "  a();", now: ["  // note", "  a()", "    .b();"] }],
      }],
    }],
  });
  assert.equal(rebuilt, pristine);
});

test("an item with NO editedSince is unaffected", () => {
  // Every entry in the real plan but one omits it; this pins that the new field is opt-in.
  const plan = wrappedPlan();
  assert.equal(plan[0].items[0].editedSince, undefined);
  const rebuilt = reconstruct({
    after: WRAPPED.after,
    modules: { "hit.mjs": WRAPPED.module },
    extractions: plan,
  });
  assert.equal(rebuilt, WRAPPED.pristine);
});

// --- seeding lines ------------------------------------------------------------------------------
//
// A slice whose module takes its dependencies through `initX({...})` adds one call to app.js's boot
// block. That line restores no body — it did not exist before the slice — so it is not an item and
// cannot be an item's marker. Before `seeding` existed, the only way to declare it was to hang it on
// some unrelated declaration's marker, which put a socket's boot call under a variable declaration
// that had nothing to do with it. These pin the field's guarantees, both directions.

const SEEDED = {
  pristine: ["function f() {", "  a();", "}", "boot();"].join(LF),
  after: ['import { f, initF } from "./f.mjs";', "// f moved.", "initF({ dep });", "boot();"].join(LF),
  module: ["export function f() {", "  a();", "}"].join(LF),
};

const seededPlan = (over = {}) => [{
  module: "f.mjs",
  importLine: 'import { f, initF } from "./f.mjs";',
  seeding: "initF({ dep });",
  items: [{ name: "f", at: 0, marker: "// f moved." }],
  ...over,
}];

test("a SEEDING line is removed, and the file reconstructs around it", () => {
  assert.equal(reconstruct({ after: SEEDED.after, modules: { "f.mjs": SEEDED.module }, extractions: seededPlan() }),
    SEEDED.pristine);
});

test("a seeding line that is NOT in the file verbatim throws", () => {
  // The same discipline as markers and imports: a mask that silently matches nothing would let the
  // reconstruction pass while the real boot call had been reworded, deleted, or never added.
  assert.throws(
    () => reconstruct({
      after: SEEDED.after,
      modules: { "f.mjs": SEEDED.module },
      extractions: seededPlan({ seeding: "initF({ somethingElse });" }),
    }),
    /seeding line not found verbatim/,
  );
});

test("an AMBIGUOUS seeding line throws rather than deleting an arbitrary one", () => {
  // Two identical boot calls cannot be told apart by content, and removing the wrong one still
  // reconstructs — silently, because both lines are the same text. Refusing is the only honest answer.
  const after = ['import { f, initF } from "./f.mjs";', "// f moved.", "initF({ dep });", "initF({ dep });", "boot();"].join(LF);
  assert.throws(
    () => reconstruct({ after, modules: { "f.mjs": SEEDED.module }, extractions: seededPlan() }),
    /seeding line is ambiguous/,
  );
});

test("seeding accepts several lines and removes them all", () => {
  const after = ['import { f, initF } from "./f.mjs";', "// f moved.", "initF({ dep });", "initG();", "boot();"].join(LF);
  assert.equal(
    reconstruct({
      after,
      modules: { "f.mjs": SEEDED.module },
      extractions: seededPlan({ seeding: ["initF({ dep });", "initG();"] }),
    }),
    SEEDED.pristine,
  );
});

test("a slice with NO seeding is unaffected — the field is opt-in", () => {
  const plan = wrappedPlan();
  assert.equal(plan[0].seeding, undefined);
  assert.equal(reconstruct({ after: WRAPPED.after, modules: { "hit.mjs": WRAPPED.module }, extractions: plan }),
    WRAPPED.pristine);
});

test("EVERY seeding line declared in the real plan is actually present in app.js", () => {
  // Anti-vacuity for the field itself. `reconstruct` throws on a missing seeding line, so this cannot
  // fail while the reconstruction passes — which is exactly why it is worth stating separately: it
  // documents that the plan's seeding entries name real boot calls rather than aspirational ones.
  const app = read("app.js");
  const declared = EXTRACTIONS.flatMap((step) => [].concat(step.seeding ?? []));
  assert.ok(declared.length > 0, "at least one slice seeds a module; if that stops being true, delete this");
  for (const line of declared) {
    assert.ok(app.includes(line), `declared seeding line is missing from app.js: ${line}`);
  }
});

// --- leading prose ------------------------------------------------------------------------------
//
// A comment explaining a function is worthless without the function, so a slice takes both. But
// `declarationSpan` covers the declaration only, leaving the reconstruction short by those lines. The
// first slice that hit this declared them as an `editedSince` restoring text the PLAN carried, which
// reconstructs correctly and verifies nothing — reword the comment in the module and the proof still
// passes, because it never reads it. `leading: n` takes the lines from the module instead.

const LEAD = {
  pristine: ["// why f exists", "// second line", "function f() {", "  a();", "}"].join(LF),
  after: ['import { f } from "./f.mjs";', "// f moved."].join(LF),
  module: ["// why f exists", "// second line", "export function f() {", "  a();", "}"].join(LF),
};

const leadPlan = (over = {}) => [{
  module: "f.mjs",
  importLine: 'import { f } from "./f.mjs";',
  items: [{ name: "f", at: 0, marker: "// f moved.", leading: 2, ...over }],
}];

test("LEADING PROSE IS RESTORED FROM THE MODULE, not from the plan", () => {
  assert.equal(reconstruct({ after: LEAD.after, modules: { "f.mjs": LEAD.module }, extractions: leadPlan() }),
    LEAD.pristine);
});

test("a REWORDED leading comment fails the reconstruction — the property editedSince could not give", () => {
  // This is the whole reason the field exists. The plan says "two lines above the declaration", not
  // WHICH two, so the module's actual text is what lands in the rebuild and byte-identity judges it.
  const reworded = LEAD.module.replace("// why f exists", "// why f exists (edited)");
  assert.notEqual(
    reconstruct({ after: LEAD.after, modules: { "f.mjs": reworded }, extractions: leadPlan() }),
    LEAD.pristine,
  );
});

test("leading REFUSES to swallow a declaration", () => {
  // Counting too far up would silently drag a neighbouring function into this item's body, and the
  // rebuild might still land byte-identical if that function was ALSO extracted — hiding a double
  // restore. Only comments and blank lines may be absorbed.
  const withNeighbour = ["const helper = 1;", "// why f exists", "export function f() {", "  a();", "}"].join(LF);
  assert.throws(
    () => reconstruct({ after: LEAD.after, modules: { "f.mjs": withNeighbour }, extractions: leadPlan() }),
    /leading must be comment or blank lines/,
  );
});

test("leading that reaches above the top of the module throws", () => {
  assert.throws(
    () => reconstruct({ after: LEAD.after, modules: { "f.mjs": LEAD.module }, extractions: leadPlan({ leading: 99 }) }),
    /reaches above the top of the module/,
  );
});

test("leading must be a non-negative integer", () => {
  for (const bad of [-1, 1.5, "2"]) {
    assert.throws(
      () => reconstruct({ after: LEAD.after, modules: { "f.mjs": LEAD.module }, extractions: leadPlan({ leading: bad }) }),
      /leading must be a non-negative integer/,
      JSON.stringify(bad),
    );
  }
});

test("an item with NO leading is unaffected — the field is opt-in", () => {
  const plan = wrappedPlan();
  assert.equal(plan[0].items[0].leading, undefined);
  assert.equal(reconstruct({ after: WRAPPED.after, modules: { "hit.mjs": WRAPPED.module }, extractions: plan }),
    WRAPPED.pristine);
});

// ── the declaration FORMS this parser can see ────────────────────────────────────────────────
//
// A form it does not match returns null, and null reads as "no such declaration" rather than "this
// parser cannot see that shape". `class` was such a form until 2026-08-16 — in the parser the repo
// mandates for every JS measurement — so the five session classes in the bridge measured as absent,
// and a size scan over those files reported almost nothing. These fixtures are what make the set of
// covered spellings a checked fact instead of whatever the regex happened to allow.

test("declarationSpan spans a CLASS, in every spelling", () => {
  const body = '{\n  method() {\n    return { nested: 1 };\n  }\n}\n';
  for (const head of ["class Foo ", "export class Foo ", "export default class Foo "]) {
    const span = declarationSpan(head + body, "Foo");
    assert.ok(span, `${head.trim()} was not found`);
    assert.equal(span.start, 0);
    assert.equal(span.end, 4, "the span ends on the class's closing brace, not the first inner one");
  }
});

test("declarationSpan spans generator and default-export functions", () => {
  for (const head of [
    "function* gen() ", "export function* gen() ", "export async function* gen() ",
    "async function *gen() ", "export default function gen() ",
  ]) {
    const span = declarationSpan(`${head}{\n  return 1;\n}\n`, "gen");
    assert.ok(span, `${head.trim()} was not found`);
    assert.equal(span.end, 2);
  }
});

test("widening the head patterns did not make them match a bare identifier", () => {
  // `function(?:\s+|\s*\*\s*)NAME` must not collapse to `functionNAME`, and `class` must not match a
  // word merely containing it. A false POSITIVE here is worse than the null it replaced: it would
  // hand a caller a span belonging to something else entirely.
  assert.equal(declarationSpan("export const x = functionok();\n", "ok"), null);
  assert.equal(declarationSpan("const classFoo = 1;\n", "Foo"), null);
  assert.equal(declarationSpan("// class Foo is coming next slice\n", "Foo"), null);
  assert.equal(declarationSpan("const gen = 1;\n", "gen").end, 0, "a real const still wins");
});

test("the five bridge classes are measurable, and the sizes are cross-checked", () => {
  // Not a synthetic case: these are the declarations the old parser was blind to. `PiSession` at 960
  // lines matches the figure recorded independently while measuring that file by other means, which
  // is what makes this a correctness check rather than merely a non-null one.
  const read = (rel) =>
    fs.readFileSync(path.join(HERE, "..", "..", rel), "utf8").replace(/\r\n/g, "\n");
  const cases = [
    ["mcp/stdio/pi-session.js", "PiSession", 960],
    ["mcp/stdio/codex-session.js", "CodexSession", 684],
    ["mcp/stdio/hermes-session.js", "HermesSession", 529],
    // 626 -> 648 -> 654 -> 740 -> 785 -> 799 across v0.6 Phase 8: the delegation seam, a correction to
    // its comment, then startDelegated -- the method that actually routes a terminal into aify-env --
    // and now choosing the launcher FILE over whatever Windows would execute. RE-MEASURED independently
    // each time by brace-matching from the class header, never taken from the assertion's "actual": a
    // figure copied out of a failure message records whatever the change produced rather than what is
    // true.
    //
    // +14 on 2026-08-25, and the arithmetic is the third check: resolving `claude-aify` on Windows
    // returns the generated .cmd shim, which carries no shebang and no HARNESS_WRAPPER_VERSION, so
    // aify-env refused every delegated spawn while the command resolved and the file existed. The 14
    // lines are the selection and its refusal message. Cause, count and measurement agree.
    //
  // 799 -> 803 on 2026-08-25: the delegated spawn now sends a `label` -- the agent id -- so aify-env's
  // view can name the row instead of showing `p2  pid 129340  aify-comms`, which cannot tell an
  // operator which of their agents that is. Four lines: the value and the note explaining that the
  // string is displayed and never interpreted. Re-measured by brace-matching from the class header,
  // and the arithmetic agrees with the diff.
  // 817 as of 2026-08-25, and the +14 is accounted for rather than copied out of the failure:
  // two 7-line comments explaining why an unreachable catch is deliberately empty. Re-measured
  // with declarationSpan before changing this, per the cross-check rule in CLAUDE.md.
    // 817 -> 826 on 2026-08-25: the delegated spawn now strips the never-inherited markers at the
    // boundary instead of relying on `terminalChildEnv` having run upstream. Nine lines: the changed
    // env argument plus the note explaining why the strip is repeated here and why an absent env is
    // passed through rather than normalised to {}. Re-measured with declarationSpan (826), and the
    // arithmetic agrees with the diff -- one line replaced by ten.
    // 826 -> 829 on 2026-08-26: the DELEGATED exit callback now takes `(code, signal)` and forwards
    // both, instead of passing a hardcoded `signal: null`. Three lines: the changed callback plus the
    // note saying why the old form was honest when written and stopped being so once aify-env had a
    // signal to send. Re-measured TWO ways before this number was touched -- `declarationSpan` says
    // 829, and a brace-match from the class header gives lines 71..899, which is 829 -- rather than
    // copying the 829 out of the failure message, which is what CLAUDE.md's cross-check rule forbids.
    // 829 -> 843 on 2026-08-26: a comment correction, no code change. The B3 descendant reap said
    // "Harmless no-op if the root is already gone", which is false on Windows -- the root is gone by
    // definition there and `taskkill /T` on a recycled number takes a stranger's tree. The same wrong
    // belief had just cost a day one tier down, so the correction is fourteen lines and worth them.
    // Re-measured TWO ways rather than copied from the failure: `declarationSpan` says 843, and a
    // brace-match from the class header gives lines 71..913, which is 843.
    // 843 -> 849 on 2026-08-26: the delegated spawn's `label` stopped falling back to the TERMINAL id.
    // Six lines: the changed expression plus the note saying why a `term_...` string under a column
    // headed AGENT is worse than an empty one. Re-measured two ways -- `declarationSpan` says 849 and
    // a brace-match from the class header gives lines 71..919, which is 849.
    // 849 -> 896 on 2026-08-28: `_settleDelegatedExit`, which stops an unobserved stream end being
    // reported as an exit. Traced from a terminal's own event log after the operator killed
    // aify-env: every delegated stream ended at once, every terminal was finalised, and the
    // processes survived -- so the control plane said `stopped` about a live, owned process.
    // Re-measured TWO ways rather than copied from the failure message: `declarationSpan` says 896,
    // and a brace-match from the class header gives lines 72..967, which is 896.
    // 896 -> 881 on 2026-08-29: `settleDelegatedExit` and `reattachLostStreams` moved out to
    // delegated-stream.mjs, leaving two one-line methods behind. They went because the file crossed
    // the 1000-line gate and the gate was right about the reason -- that module is
    // delegated-environment POLICY, this file is terminal-process mechanics.
    // Re-measured TWO ways rather than copied from the failure: `declarationSpan` says 881, and a
    // brace-match from the class header gives lines 72..952, which is 881.
    // 881 -> 898 on 2026-08-29: `stop()` now AWAITS a delegated stop and reads the answer. It used
    // the shim's fire-and-forget kill, so a refusal reached console.error while stop() deleted the
    // terminal and returned `{ stopped: true }` -- a Stop pressed while aify-env was down left the
    // process running and this bridge with no memory of it.
    // Re-measured TWO ways rather than copied from the failure: `declarationSpan` says 898, and a
    // brace-match from the class header gives lines 72..969, which is 898.
    ["mcp/stdio/terminal-runtime.js", "TerminalProcessManager", 898],
  ];
  for (const [rel, name, expected] of cases) {
    const span = declarationSpan(read(rel), name);
    assert.ok(span, `${name} in ${rel} is still invisible`);
    assert.equal(span.end - span.start + 1, expected, `${name} span moved; re-measure before editing`);
  }
});
