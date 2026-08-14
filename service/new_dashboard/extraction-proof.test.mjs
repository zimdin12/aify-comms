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
      { name: "statusWhyContext", at: 438, marker: "// statusWhyContext moved to ./status.js in v0.5.4." },
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
      { name: "loadVersionBadge", at: 4977, marker: "// loadVersionBadge moved to ./version-badge.mjs in v0.5.4." },
    ],
  },
  {
    module: "message-transport.mjs",
    importLine: "import { chatLoadChannels, chatLoadConversation, chatSendMessage, sendRunFollowup } from './message-transport.mjs';",
    items: [
      { name: "chatLoadChannels", at: 141, marker: "// chatLoadChannels moved to ./message-transport.mjs in v0.5.4." },
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
      { name: "loadFiles", at: 301, marker: "// loadFiles moved to ./shared-files.mjs in v0.5.4." },
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
      { name: "resolveApiOrigin", at: 16, marker: "// resolveApiOrigin moved to ./api-origin.mjs in v0.5.4." },
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
      { name: "renderSessionRail", at: 1775, marker: "// renderSessionRail moved to ./session-rail.mjs in v0.5.4." },
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
      { name: "terminalAccentColor", at: 1875, marker: "// terminalAccentColor moved to ./settings-panel.mjs in v0.5.4." },
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
      { name: "renderAttention", at: 1416, marker: "// renderAttention moved to ./work-loop-panels.mjs in v0.5.4." },
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
      { name: "openIdentityDirectory", at: 3402, marker: "// openIdentityDirectory moved to ./identity-directory.mjs in v0.5.4." },
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
      { name: "renderSessionActivity", at: 1818, marker: "// renderSessionActivity moved to ./session-activity.mjs in v0.5.4." },
      { name: "runFrom", at: 3175, marker: "// runFrom moved to ./session-activity.mjs in v0.5.4." },
    ],
  },
  {
    module: "environments-panels.mjs",
    importLine: "import { openEnvironmentRootsEditor, renderEnvironmentSpawnOptions, renderEnvironmentSummary, renderRuntime, renderSpawnRequests } from './environments-panels.mjs';",
    items: [
      { name: "renderEnvironmentSpawnOptions", at: 3010, marker: "// renderEnvironmentSpawnOptions moved to ./environments-panels.mjs in v0.5.4." },
      { name: "renderRuntime", at: 3038, marker: "// renderRuntime moved to ./environments-panels.mjs in v0.5.4." },
      { name: "renderSpawnRequests", at: 3063, marker: "// renderSpawnRequests moved to ./environments-panels.mjs in v0.5.4." },
      { name: "renderEnvironmentSummary", at: 2995, marker: "// renderEnvironmentSummary moved to ./environments-panels.mjs in v0.5.4." },
      { name: "openEnvironmentRootsEditor", at: 3122, marker: "// openEnvironmentRootsEditor moved to ./environments-panels.mjs in v0.5.4." },
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
      { name: "openAgentEditForm", at: 3609, marker: "// openAgentEditForm moved to ./inspector-forms.mjs in v0.5.4." },
      { name: "openMessageDetail", at: 3696, marker: "// openMessageDetail moved to ./inspector-forms.mjs in v0.5.4." },
      { name: "openCompactionHistory", at: 3575, marker: "// openCompactionHistory moved to ./inspector-forms.mjs in v0.5.4." },
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
      { name: "setNavCollapsed", at: 3856, marker: "// setNavCollapsed moved to ./layout-prefs.mjs in v0.5.4." },
      { name: "preferredNavCollapsed", at: 3864, marker: "// preferredNavCollapsed moved to ./layout-prefs.mjs in v0.5.4." },
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
      { name: "renderSection", at: 931, marker: "// renderSection moved to ./render-memo.mjs in v0.5.4." },
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
        editedSince: [{
          was: "function renderSessionConsole(session, targetEl, opts = {}) {",
          now: "function renderSessionConsole(session, targetEl, opts = {}, { mountXtermForTerminal, refresh, resyncActiveConsole } = {}) {",
        }],
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
        editedSince: [{
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
        at: 3882,
        marker: "// requestSessionControl moved to ./agent-session-actions.mjs in v0.5.4.",
      },
      {
        name: "requestBulkSessionControl",
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
        name: "renderContracts",
        at: 2957,
        marker: "// renderContracts moved to ./work-loop-actions.mjs in v0.5.4.",
      },
      {
        name: "closeWorkContract",
        at: 3927,
        marker: "// closeWorkContract moved to ./work-loop-actions.mjs in v0.5.4.",
      },
      {
        name: "remindWorkContract",
        at: 3939,
        marker: "// remindWorkContract moved to ./work-loop-actions.mjs in v0.5.4.",
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

test("the second slice's marker comment is missing from app.js for one of its items", () => {
  // A guard on the plan itself: the marker text is asserted verbatim by reconstruct(), so if a slice's
  // marker were mistyped here the proof would throw rather than silently skip that body.
  const source = read("app.js");
  for (const step of EXTRACTIONS) {
    for (const item of step.items) {
      if (item.marker == null) continue;
      assert.ok(
        source.includes([].concat(item.marker)[0]),
        `${item.name}'s marker is not in app.js verbatim, so the plan and the file disagree`,
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
  assert.notEqual(
    rebuild({ extractions: shifted }),
    read(PRISTINE),
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
