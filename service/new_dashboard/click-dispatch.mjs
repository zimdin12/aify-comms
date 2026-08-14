// THE DELEGATED CLICK DISPATCHER — one listener for the whole dashboard.
//
// Every clickable control on the page is routed from here by matching `closest()` against its data
// attribute. That design is deliberate: the page re-renders wholesale on every ~15s poll, so a listener
// bound to an element would be thrown away with it. The cost is that ORDER IS BEHAVIOUR — the first
// branch whose selector matches wins, and two of the branches are here in a specific place because
// getting it wrong shipped a live bug:
//
//   - the mode-switch chip is checked BEFORE session-row selection, because the chip is nested inside a
//     selectable row and the row would otherwise swallow its click;
//   - `work-view` and `contract-view` are scoped to `button[...]` because the grid SECTION carries the
//     same attribute as a CSS state hook, so a bare selector matched every click inside Work and ate
//     Inspect/Remind/Close (live regression, 2026-07-02).
//
// This left app.js in v0.5.4 as ONE unit rather than by domain, because splitting an ordered chain into
// independently-ordered pieces is precisely how that ordering is lost. It is the LISTENER that moved;
// registering it stays in app.js, so the boot sequence is still visible where the rest of the boot is.
//
// Five injected names remain — the rest of what this dispatches to is now a sibling's export.

import { runAgentControl, startColdAgent, switchAgentModeFromRow, switchModeFromChip, toggleFavouriteRow } from './agent-click-handlers.mjs';
import { openAgentDrawer } from './agent-drawer.mjs';
import { deleteSessionById, openAgentChat, removeAgent, requestBulkSessionControl, requestSessionControl, resolveAgentSession, stopAgentWorker, submitAgentEdit, submitContinue, switchAgentSessionMode } from './agent-session-actions.mjs';
import { loadAnalytics } from './analytics-page.mjs';
import { openChatConversation, openChatReply, runChannelAction, setChatView, setPulseWindow } from './chat-click-handlers.mjs';
import { copyText } from './clipboard.mjs';
import { codexConsoleClose, codexConsoleConnect } from './codex-console.mjs';
import { resyncActiveConsole, startConsoleForSession, stopConsoleTerminal } from './console-actions.mjs';
import { runConsoleAction } from './console-click-handlers.mjs';
import { controlEnvironment, openEnvironmentRootsEditor, renderEnvironmentSpawnOptions, resetEnvironmentRoots, submitEnvironmentRoots } from './environments-panels.mjs';
import { openAgentEditForm, openCompactionHistory, openContinueForm, openMessageDetail } from './inspector-forms.mjs';
import { addChannelMember, chatChannelAction, markConversationRead, markMessageRead, openMessageThread, removeChannelMember, toggleFavorite, unsendMessage } from './message-actions.mjs';
import { navigateToPage, openEnvironmentSpawn, openHermesTabFromRow, selectAnalyticsRange } from './nav-click-handlers.mjs';
import { handleRunInspectorControl, loadMoreRunEvents, openRunInspector, requestRunControl, toggleRunEventOrder } from './run-inspector.mjs';
import { applySessionStatusPreset, openAgentSessions, selectSessionRow, selectSessionTab, toggleSessionCheckbox, toggleSessionStatusFilter } from './session-click-handlers.mjs';
import { toggleSupersededSessions } from './session-rail.mjs';
import { applyThemeChoice, selectSettingsTab } from './settings-panel.mjs';
import { deleteSharedFileFromRow } from './shared-files.mjs';
import { state } from './state.mjs';
import { closeStatusWhy, openStatusWhy } from './status-why-popover.mjs';
import { toast } from './ui.js';
import { closeWorkContract, remindWorkContract, renderContracts, renderDiagnosticsBulkToolbar, requestBulkDiagnosticAction, runMaintenance } from './work-loop-actions.mjs';
import { applyContractView, applyWorkView, jumpFromDiagnostic, toggleDiagnosticSelection } from './work-loop-panels.mjs';

let chatController = { renderConversation() {} };
let closeInspector = () => {};
let refreshSoon = () => {};
let renderSessionWorkspace = () => {};
let setPage = () => {};

/** Supply the app.js-side dependencies. Throws on a partial bag. */
export function initClickDispatch(deps) {
  const REQUIRED = ['chatController', 'closeInspector', 'refreshSoon', 'renderSessionWorkspace', 'setPage'];
  const missing = REQUIRED.filter((k) => deps == null || deps[k] == null);
  if (missing.length) throw new TypeError(`initClickDispatch requires ${missing.join(', ')}`);
  ({ chatController, closeInspector, refreshSoon, renderSessionWorkspace, setPage } = deps);
}

/**
 * Handle one click. Returns nothing; every branch that matches returns early, which is what makes the
 * chain ordered rather than a set of independent handlers.
 */
export function dispatchClick(event) {
  const settingsTab = event.target.closest('[data-settings-tab]');
  if (settingsTab) {
    selectSettingsTab(settingsTab);
    return;
  }
  const themeChoice = event.target.closest('[data-theme-choice]');
  if (themeChoice) {
    applyThemeChoice(themeChoice);
    return;
  }
  const favToggle = event.target.closest('[data-fav-toggle]');
  if (favToggle) {
    toggleFavouriteRow(favToggle, event, toggleFavorite);
    return;
  }
  const msgRead = event.target.closest('[data-msg-read]');
  if (msgRead) { markMessageRead(msgRead.dataset.msgRead, msgRead.dataset.read === '0'); return; }
  const msgUnsend = event.target.closest('[data-msg-unsend]');
  if (msgUnsend) { unsendMessage(msgUnsend.dataset.msgUnsend); return; }
  const markConvRead = event.target.closest('[data-mark-conv-read]');
  if (markConvRead) { markConversationRead(markConvRead.dataset.markConvRead); return; }
  const chatReply = event.target.closest('[data-chat-reply]');
  if (chatReply) {
    openChatReply(chatReply, chatController);
    return;
  }
  if (event.target.closest('[data-chat-reply-clear]')) {
    state.chat.replyTo = null;
    chatController.renderConversation();
    return;
  }
  const msgDetail = event.target.closest('[data-message-detail]');
  if (msgDetail) {
    openMessageDetail(msgDetail.dataset.messageDetail);
    return;
  }
  const chatOpen = event.target.closest('[data-chat-open]');
  if (chatOpen) {
    openChatConversation(chatOpen, chatController, markConversationRead);
    return;
  }
  const pulseWindow = event.target.closest('[data-pulse-window]');
  if (pulseWindow) {
    setPulseWindow(pulseWindow, chatController);
    return;
  }
  const chatView = event.target.closest('[data-chat-view]');
  if (chatView) {
    setChatView(chatView, chatController);
    return;
  }
  // MUST stay scoped to button[...]: the grid section itself carries data-work-view as a
  // CSS state attribute, so a bare [data-work-view] closest() matches EVERY click inside
  // Work and swallows Inspect/Remind/Close (live regression 2026-07-02).
  const workView = event.target.closest('button[data-work-view]');
  if (workView) {
    applyWorkView(workView);
    return;
  }
  // Work Loop List ⇄ Board layout toggle. Scoped to button[data-contract-view] for
  // the same reason as work-view above (avoid swallowing card actions).
  const contractView = event.target.closest('button[data-contract-view]');
  if (contractView) {
    applyContractView(contractView, renderContracts);
    return;
  }
  const diagJump = event.target.closest('[data-diag-jump]');
  if (diagJump) {
    jumpFromDiagnostic(diagJump);
    return;
  }
  const chatAnalytics = event.target.closest('[data-chat-analytics]');
  if (chatAnalytics) {
    chatController.openAnalytics(chatAnalytics.dataset.chatAnalytics);
    return;
  }
  const agentDrawer = event.target.closest('[data-agent-drawer]');
  if (agentDrawer) {
    openAgentDrawer(agentDrawer.dataset.agentDrawer);
    return;
  }
  const agentControl = event.target.closest('[data-agent-control]');
  if (agentControl) {
    runAgentControl(agentControl, requestSessionControl);
    return;
  }
  const agentMode = event.target.closest('[data-agent-mode]');
  if (agentMode) {
    switchAgentModeFromRow(agentMode, switchAgentSessionMode);
    return;
  }
  const agentOpenSessions = event.target.closest('[data-agent-open-sessions]');
  if (agentOpenSessions) {
    openAgentSessions(agentOpenSessions, renderSessionWorkspace, setPage, closeInspector);
    return;
  }
  const toggleSuperseded = event.target.closest('[data-toggle-superseded]');
  if (toggleSuperseded) {
    toggleSupersededSessions();
    return;
  }
  const sessionStatusPreset = event.target.closest('[data-session-status-preset]');
  if (sessionStatusPreset) {
    applySessionStatusPreset(sessionStatusPreset, renderSessionWorkspace);
    return;
  }
  const sessionStatusFilter = event.target.closest('[data-session-status-filter]');
  if (sessionStatusFilter) {
    toggleSessionStatusFilter(sessionStatusFilter, renderSessionWorkspace);
    return;
  }
  const agentCompact = event.target.closest('[data-agent-compact]');
  if (agentCompact) { openContinueForm(agentCompact.dataset.agentCompact, false); return; }
  const agentContinue = event.target.closest('[data-agent-continue]');
  if (agentContinue) { openContinueForm(agentContinue.dataset.agentContinue, true); return; }
  const continueSubmit = event.target.closest('[data-continue-submit]');
  if (continueSubmit) { submitContinue(continueSubmit.dataset.continueSubmit, continueSubmit.dataset.split === '1'); return; }
  const agentEdit = event.target.closest('[data-agent-edit]');
  if (agentEdit) { openAgentEditForm(agentEdit.dataset.agentEdit); return; }
  const agentHistory = event.target.closest('[data-agent-history]');
  if (agentHistory) { openCompactionHistory(agentHistory.dataset.agentHistory); return; }
  const agentEditSubmit = event.target.closest('[data-agent-edit-submit]');
  if (agentEditSubmit) { submitAgentEdit(agentEditSubmit.dataset.agentEditSubmit); return; }
  const sessionConfirm = event.target.closest('[data-session-confirm]');
  if (sessionConfirm) { resolveAgentSession(sessionConfirm.dataset.sessionConfirm, 'confirm'); return; }
  const sessionKeep = event.target.closest('[data-session-keep]');
  if (sessionKeep) { resolveAgentSession(sessionKeep.dataset.sessionKeep, 'keep'); return; }
  const copyCli = event.target.closest('[data-copy-cli]');
  if (copyCli) { copyText(copyCli.dataset.copyCli || '').then((ok) => toast(ok ? 'Resume command copied' : 'Copy failed', ok ? 'ok' : 'error')); return; }
  const agentDetails = event.target.closest('[data-agent-details]');
  if (agentDetails) { openAgentDrawer(agentDetails.dataset.agentDetails); return; }
  const agentStopWorker = event.target.closest('[data-agent-stop-worker]');
  if (agentStopWorker) { stopAgentWorker(agentStopWorker.dataset.agentStopWorker); return; }
  const agentRemove = event.target.closest('[data-agent-remove]');
  if (agentRemove) { removeAgent(agentRemove.dataset.agentRemove); return; }
  const agentDeleteSession = event.target.closest('[data-agent-delete-session]');
  if (agentDeleteSession) { deleteSessionById(agentDeleteSession.dataset.agentDeleteSession); return; }
  const chanAction = event.target.closest('[data-chat-channel-action]');
  if (chanAction) {
    runChannelAction(chanAction, chatChannelAction);
    return;
  }
  const chanAddMember = event.target.closest('[data-channel-add-member]');
  if (chanAddMember) { addChannelMember(chanAddMember.dataset.channelAddMember); return; }
  const chanRemoveMember = event.target.closest('[data-channel-remove-member]');
  if (chanRemoveMember) { removeChannelMember(chanRemoveMember.dataset.channelRemoveMember, chanRemoveMember.dataset.member); return; }
  const fileDelete = event.target.closest('[data-file-delete]');
  if (fileDelete) {
    deleteSharedFileFromRow(fileDelete);
    return;
  }
  const openHermesTab = event.target.closest('[data-action="open-hermes-tab"]');
  if (openHermesTab) {
    openHermesTabFromRow(openHermesTab);
    return;
  }
  const codexConnect = event.target.closest('[data-action="codex-console-connect"]');
  if (codexConnect) {
    codexConsoleConnect(
      codexConnect.dataset.agentId,
      codexConnect.dataset.appServerUrl,
      codexConnect.dataset.threadId,
    );
    return;
  }
  const codexDisconnect = event.target.closest('[data-action="codex-console-disconnect"]');
  if (codexDisconnect) {
    codexConsoleClose(codexDisconnect.dataset.agentId);
    return;
  }
  const statusWhy = event.target.closest('[data-status-why]');
  if (statusWhy) {
    openStatusWhy(statusWhy);
    return;
  }
  if (event.target.closest('[data-close-status-why]')) {
    closeStatusWhy();
    return;
  }
  const consoleAction = event.target.closest('[data-console-action]');
  if (consoleAction) {
    runConsoleAction(consoleAction, resyncActiveConsole, stopConsoleTerminal, startConsoleForSession);
    return;
  }
  // Start a managed agent that has NO session at all (the cold-agent case — there was no way to
  // do this from the dashboard before). Spawns a worker through the same path a send uses, so a
  // saved session handle is RESUMED, not discarded.
  const agentAction = event.target.closest('[data-agent-action="start"]');
  if (agentAction) {
    startColdAgent(agentAction, refreshSoon);
    return;
  }
  const analyticsRange = event.target.closest('[data-analytics-range]');
  if (analyticsRange) {
    selectAnalyticsRange(analyticsRange, loadAnalytics);
    return;
  }
  const page = event.target.closest('[data-page], [data-page-jump]')?.dataset.page || event.target.closest('[data-page-jump]')?.dataset.pageJump;
  if (page) {
    navigateToPage(page, setPage, loadAnalytics);
    return;
  }
  const diagnosticSelect = event.target.closest('[data-diagnostic-select]');
  if (diagnosticSelect) {
    toggleDiagnosticSelection(diagnosticSelect, renderDiagnosticsBulkToolbar);
    return;
  }
  const diagnosticAction = event.target.closest('[data-diagnostic-action]');
  if (diagnosticAction) {
    requestBulkDiagnosticAction(diagnosticAction.dataset.diagnosticAction);
    return;
  }
  const maintAction = event.target.closest('[data-maint-action]');
  if (maintAction) {
    runMaintenance(maintAction.dataset.maintAction);
    return;
  }
  const envSpawn = event.target.closest('[data-env-spawn]');
  if (envSpawn) {
    openEnvironmentSpawn(envSpawn, setPage, renderEnvironmentSpawnOptions);
    return;
  }
  const envRoots = event.target.closest('[data-env-roots]');
  if (envRoots) { openEnvironmentRootsEditor(envRoots.dataset.envRoots); return; }
  const envRootsSubmit = event.target.closest('[data-env-roots-submit]');
  if (envRootsSubmit) { submitEnvironmentRoots(envRootsSubmit.dataset.envRootsSubmit); return; }
  const envRootsReset = event.target.closest('[data-env-roots-reset]');
  if (envRootsReset) { resetEnvironmentRoots(envRootsReset.dataset.envRootsReset); return; }
  const copyTextBtn = event.target.closest('[data-copy-text]');
  if (copyTextBtn) { copyText(copyTextBtn.dataset.copyText).then((ok) => toast(ok ? 'Copied to clipboard' : 'Copy failed', ok ? 'ok' : 'error')); return; }
  const envControl = event.target.closest('[data-env-control]');
  if (envControl) { controlEnvironment(envControl.dataset.envId, envControl.dataset.envControl); return; }
  const openChat = event.target.closest('[data-open-chat]');
  if (openChat) { openAgentChat(openChat.dataset.openChat); return; }
  const sessionCheckbox = event.target.closest('[data-session-checkbox]');
  if (sessionCheckbox) {
    toggleSessionCheckbox(sessionCheckbox, renderSessionWorkspace);
    return;
  }
  // Mode-switch chips can live inside selectable session rows. Handle them
  // before row selection so the click reaches PATCH /agents/{id}/session-mode.
  const modeSwitchButton = event.target.closest('[data-mode-switch]');
  if (modeSwitchButton) {
    switchModeFromChip(modeSwitchButton, event, switchAgentSessionMode);
    return;
  }
  const sessionSelect = event.target.closest('[data-session-select]');
  if (sessionSelect) {
    selectSessionRow(sessionSelect, renderSessionWorkspace);
    return;
  }
  const sessionTab = event.target.closest('[data-session-tab]');
  if (sessionTab) {
    selectSessionTab(sessionTab, renderSessionWorkspace);
    return;
  }
  const bulkSessionButton = event.target.closest('[data-bulk-session-action]');
  if (bulkSessionButton) {
    requestBulkSessionControl(bulkSessionButton.dataset.bulkSessionAction);
    return;
  }
  const runChip = event.target.closest('[data-run-chip]');
  if (runChip) {
    openRunInspector({ runId: runChip.dataset.runChip, source: 'chat', sourceMessageId: runChip.dataset.messageId || '' });
    return;
  }
  const runInspectorButton = event.target.closest('[data-run-inspector]');
  if (runInspectorButton) {
    openRunInspector({ runId: runInspectorButton.dataset.runInspector, source: runInspectorButton.dataset.runSource || 'programmatic' });
    return;
  }
  const runControlButton = event.target.closest('[data-run-control]');
  if (runControlButton) {
    handleRunInspectorControl(runControlButton.dataset.runControl);
    return;
  }
  const copyRunButton = event.target.closest('[data-copy-run-id]');
  if (copyRunButton) {
    // Use the execCommand-fallback copy (navigator.clipboard is undefined on the http LAN origin).
    copyText(copyRunButton.dataset.copyRunId || '').then((ok) => toast(ok ? 'Run ID copied' : 'Copy failed', ok ? 'ok' : 'error'));
    return;
  }
  const threadButton = event.target.closest('[data-open-thread-message]');
  if (threadButton) {
    openMessageThread(threadButton.dataset.openThreadMessage);
    return;
  }
  if (event.target.closest('#run-inspector-load-more')) {
    loadMoreRunEvents();
    return;
  }
  if (event.target.closest('#run-inspector-order-toggle')) {
    toggleRunEventOrder();
    return;
  }
  const closeContractButton = event.target.closest('[data-close-contract]');
  if (closeContractButton) { closeWorkContract(closeContractButton.dataset.closeContract); return; }
  const remindContractButton = event.target.closest('[data-remind-contract]');
  if (remindContractButton) { remindWorkContract(remindContractButton.dataset.remindContract); return; }
  const steerRunButton = event.target.closest('[data-steer-run]');
  if (steerRunButton) { requestRunControl(steerRunButton.dataset.steerRun); return; }
  const sessionControlButton = event.target.closest('[data-session-control]');
  if (sessionControlButton) {
    requestSessionControl(sessionControlButton.dataset.sessionId, sessionControlButton.dataset.sessionControl);
    return;
  }
  // (Removed the catch-all [data-kind] → JSON-inspector fallback: it hijacked clicks on the
  // empty area of any row/message and popped raw JSON. Explicit inspect buttons still work.)
}
