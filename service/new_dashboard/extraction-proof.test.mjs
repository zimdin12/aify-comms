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
      { name: "copyActiveConsole", at: 2381, marker: "// copyActiveConsole moved to ./clipboard.mjs in v0.5.4." },
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
