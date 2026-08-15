// The chat surface's pure HTML builders: a rail row, a timeline message, a delivery toast, and the
// per-agent analytics panel.
//
// Extracted from `chat.js` in v0.5.4. Closure measured with the repo's OWN parser before the move —
// `declarationSpan` from `extraction-proof.mjs`, not a hand-rolled regex, because four hand-rolled
// parsers gave four wrong answers earlier in this series. The six declarations reference `esc`,
// `relTime` and `resolveStatus`, and nothing else `chat.js` declares.
//
// WHAT MAKES THEM ONE SUBJECT: every one takes data and returns a STRING, and none touches app state
// or the DOM. That is why they were already the unit-tested half of `chat.js` — tested by CALLING
// them — while the controller around them needs a fake document. Splitting on that line stops the
// testable half from sharing a file with the half that cannot be tested the same way.
//
// THE SUBJECT-ECHO RULE IS THE ONE WORTH KNOWING. The API derives a subject from the first 80
// characters of a body when the sender supplied none, so rendering both verbatim shows the same words
// twice. `subjectIsEchoOfBody` suppresses the heading in that case and `DERIVED_SUBJECT_MAX` is the
// server's slice length — the two must agree or the duplicate comes back.
//
// Bodies byte-identical to what stood in `chat.js`.
import { esc, relTime } from './util.js';
import { resolveStatus } from './status.js';

// Chat overview shown when no conversation is open (re-click an open chat to return here).
// EXPORTED, and it was not in `chat.js` — the one declared substitution in this move. It was
// module-private there because its only caller was in the same file; now that caller imports it, so
// the keyword is what makes the relocation legal rather than a change of behaviour. The body below
// is byte-identical.
export function railItemHtml(item, selectedKey, drafts = {}, readOnly = false) {
  const active = item.key === selectedKey ? ' active' : '';
  const favClass = item.favorited ? ' fav' : '';
  const hasDraft = !!((drafts[item.key] || '').trim());
  const draftBadge = hasDraft ? '<span class="chat-draft-badge" title="Half-written message saved for this chat">draft</span>' : '';
  const dotStatus = item.kind === 'dm' ? resolveStatus(item.status) : null;
  const dot = item.kind === 'dm'
    ? `<span class="status-dot ${esc(dotStatus.dotKind)}" role="img" title="${esc(dotStatus.label)}" aria-label="${esc(dotStatus.label)}"></span>`
    : '<span class="chat-rail-hash">#</span>';
  const unread = item.unread > 0 ? `<span class="chat-unread">${item.unread}</span>` : '';
  // Loud awaiting-input marker (operator feedback 2026-07-02): a blocked agent (a real
  // prompt paused its turn) needs the operator — a red dot alone was too easy to miss.
  const awaitBadge = item.kind === 'dm' && dotStatus?.dotKind === 'blocked'
    ? '<span class="chat-await-badge" title="Agent is blocked on an interactive prompt — open its Console">⌛ input</span>'
    : '';
  // DMs get a clickable star (PATCH /agents/{id}/favorite); channels have no server favorite.
  const fav = item.kind === 'dm' && !readOnly
    ? `<span class="chat-fav-toggle${item.favorited ? ' on' : ''}" data-fav-toggle="${esc(item.id)}" role="button" tabindex="0" aria-label="${item.favorited ? 'Unfavorite' : 'Favorite'} ${esc(item.id)}" title="${item.favorited ? 'Unfavorite' : 'Favorite'}">${item.favorited ? '★' : '☆'}</span>`
    : (item.favorited ? '<span class="chat-fav" title="Favorite">★</span>' : '');
  // Sub-line carries the same compact context the old dashboard showed: "role · status · preview"
  // for DMs (parity target), preview-only for channels. Keeps each row scannable at a glance.
  const meta = item.kind === 'dm'
    ? [item.role, resolveStatus(item.status).label].filter(Boolean).join(' · ')
    : '';
  const sub = [meta, item.preview || ''].filter(Boolean).join(' · ');
  return `<button class="chat-rail-item${active}${favClass}" data-chat-open="${esc(item.key)}" title="${esc(item.id)}">
    <span class="chat-rail-head">${fav}${dot}<span class="chat-rail-name clip">${esc(item.id)}</span>${awaitBadge}${draftBadge}${unread}</span>
    <span class="chat-rail-preview clip">${esc(sub)}</span>
  </button>`;
}

// Wake-vs-stored badge: a message that triggered a dispatch run "woke" the agent; otherwise
// it was stored to the inbox. read/unread shown alongside.
// A message sent without an explicit Subject gets one DERIVED from its own body:
// `app.js` → chatSendMessage: `finalSubject = subject.trim() ? subject.trim() : body.slice(0, 80)`.
// Rendering that above the body then prints the same words twice, which operators read as the hub
// duplicating their messages (reported 2026-07-27 with a screenshot of two bubbles: one where the
// bold line was the 80-char slice cut mid-word, one under 80 chars where subject === body exactly).
//
// Nothing is duplicated in storage — this is purely the echo of the derivation. So suppress the
// heading when it carries no information the body does not already lead with, and keep it whenever
// the operator (or an agent) actually typed a distinct subject.
//
// SELF-REVIEW FIX (same day): the first cut tested `body.startsWith(subject)` — ANY prefix. That
// hid deliberately-typed short subjects: subject "Deploy" with body "Deploy the hotfix now" matched
// and vanished, destroying information the operator had explicitly entered. Strictly worse than the
// echo it was removing, because an echo is noise and a suppressed real subject is lost signal.
//
// The derivation produces exactly TWO shapes, so match exactly those and nothing else:
//   * body <= 80 chars  -> subject === body
//   * body >  80 chars  -> subject === body.slice(0, 80)
// Anything else is a subject someone chose, and it renders.
//
// Compared on trimmed text because `chatSendMessage` trims the body before slicing, and trims the
// subject separately, so a stored pair can differ by surrounding whitespace alone.
export const DERIVED_SUBJECT_MAX = 80;

export function subjectIsEchoOfBody(subject, body) {
  const s = String(subject == null ? '' : subject).trim();
  if (!s) return false; // no subject to render anyway; caller's own guard handles it
  const b = String(body == null ? '' : body).trim();
  if (!b) return false; // a subject with an empty body is the ONLY thing worth showing
  return s === b || s === b.slice(0, DERIVED_SUBJECT_MAX).trim();
}

export function messageHtml(m, identity = 'dashboard', isChannel = false) {
  const id = String(m.id || m.messageId || '');
  const runId = String(m.dispatchRunId || m.dispatch_run_id || m.runId || m.run_id || '');
  const woke = !!runId || m.dispatchRequested || m.dispatch_requested;
  const priority = String(m.priority || '').toLowerCase();
  const mine = String(m.from || '') === String(identity);
  const readOnly = String(identity) === 'all';
  const badges = [
    `<span class="msg-badge ${m.read === false ? 'unread' : 'read'}">${m.read === false ? 'unread' : 'read'}</span>`,
    woke ? '<span class="msg-badge woke">woke</span>' : '<span class="msg-badge stored">stored</span>',
    m.type ? `<span class="msg-badge type t-${esc(m.type)}">${esc(m.type)}</span>` : '',
    (priority === 'high' || priority === 'urgent') ? `<span class="msg-badge p-${esc(priority)}">${esc(priority)}</span>` : '',
    (m.expectsReply || m.expects_reply) && m.read === false ? '<span class="msg-badge await">awaiting</span>' : '',
  ].join('');
  const runChip = runId ? `<button class="run-chip" data-run-chip="${esc(runId)}" data-message-id="${esc(id)}">Run ${esc(runId.slice(0, 10))}</button>` : '';
  // Reply / read-toggle / unsend all operate on state.messages (DM store); channel messages
  // live in state.chat.channelMessages and use fan-out read ids, so those controls would be
  // dead/incorrect on channel rows — only show them for DMs.
  // These are optional actions, not reply/read-contract state. Use verb phrases so a read
  // non-reply-owing response does not render the contradictory-looking "read · Reply · Unread".
  const reply = (!isChannel && !readOnly) ? `<button class="chat-msg-reply" data-chat-reply="${esc(id)}" title="Compose an optional reply to this message">Write reply</button>` : '';
  const readToggle = (!mine && !isChannel && !readOnly) ? `<button class="chat-msg-act" data-msg-read="${esc(id)}" data-read="${m.read === false ? '0' : '1'}" title="Mark ${m.read === false ? 'read' : 'unread'}">Mark ${m.read === false ? 'read' : 'unread'}</button>` : '';
  const unsendBtn = (mine && !isChannel && !readOnly) ? `<button class="chat-msg-act danger" data-msg-unsend="${esc(id)}" title="Unsend this message">Unsend</button>` : '';
  // The ⋯ detail lookup only searches the DM store, so it's dead on channel rows — DMs only.
  const detail = !isChannel ? `<button class="chat-msg-detail" data-message-detail="${esc(id)}" aria-label="Message details" title="Message details">⋯</button>` : '';
  const actions = `${runChip}${reply}${readToggle}${unsendBtn}${detail}`;
  return `<article class="chat-msg${mine ? ' chat-msg-mine' : ''}" data-kind="message" data-id="${esc(id)}" id="chat-msg-${esc(id)}">
    <div class="chat-msg-head"><strong>${esc(m.from || 'unknown')}</strong>
      <span class="chat-msg-badges">${badges}${actions}</span>
    </div>
    ${subjectIsEchoOfBody(m.subject, m.body || m.preview || '') ? '' : (m.subject ? `<h4 class="chat-msg-subject">${esc(m.subject)}</h4>` : '')}
    <p class="chat-msg-body">${esc(m.body || m.preview || '')}</p>
    <small class="chat-msg-time">${(() => { const t = relTime(m.timestamp || m.createdAt); return t ? esc(t) + ' ago' : ''; })()}</small>
  </article>`;
}

// Map a /messages/send response to a single truthful delivery toast (the plan's "ladder":
// steered / queued-busy / console-delivered / woke / stored-offline).
export function deliveryToastFor(response, to) {
  // /messages/send returns the runs under `dispatchRuns`, not `runs` — reading the wrong key
  // silently collapsed the steered/queued/woke ladder to a generic "Sent".
  const run = (response?.dispatchRuns || response?.runs || [])[0] || {};
  const status = String(run.status || '').toLowerCase();
  const consoleDelivered = (response?.consoleDeliveries || []).length > 0;
  const notStarted = (response?.notStarted || []).length > 0;
  if (response && response.ok === false) return { tone: 'error', text: `Not delivered to ${to}: ${response.error || 'recipient cannot start live work'}` };
  if (run.steered) return { tone: 'ok', text: `Steered into ${to}'s active turn` };
  if (status === 'queued') return { tone: 'info', text: `Queued behind ${to}'s active work` };
  if (consoleDelivered) return { tone: 'ok', text: `Delivered to ${to}'s console` };
  if (status === 'claimed' || status === 'running' || status === 'delivered') return { tone: 'ok', text: `Woke ${to}` };
  if (notStarted) return { tone: 'warn', text: `Stored for ${to} (no live worker to wake)` };
  return { tone: 'ok', text: `Sent to ${to}` };
}

// Per-agent analytics panel (reuses GET /analytics/agent/{id} — the revamped metrics).
// Pure HTML builder so it can be unit-tested and rendered into the timeline area.
export function renderAnalyticsPanelHtml(agentId, data) {
  if (!data || data.ok === false) return `<div class="chat-empty">Analytics unavailable for ${esc(agentId)}.</div>`;
  const wm = Math.max(0, Math.round(Number(data.workingMinutes || 0)));
  const workLabel = `${Math.floor(wm / 60)}h ${wm % 60}m`;
  const mr = Number(data.medianReplyMinutes7d || 0);
  const mrLabel = mr >= 60 ? `${Math.floor(mr / 60)}h ${Math.round(mr % 60)}m` : `${Math.round(mr)}m`;
  const runs = data.runs7d || {};
  const peers = (Array.isArray(data.byPeer) ? data.byPeer : []).slice(0, 5);
  const peerMax = Math.max(1, ...peers.map((p) => Number(p.count || 0)));
  const peerBars = peers.length ? peers.map((p) => {
    const w = Math.max(2, Math.round((Number(p.count || 0) / peerMax) * 100));
    return `<div class="an-bar-row"><span class="an-bar-label clip">${esc(p.peer)}</span><span class="an-bar-track"><span class="an-bar-fill" style="width:${w}%"></span></span><span class="an-bar-val">${Number(p.count || 0)}</span></div>`;
  }).join('') : '<p class="subtle">No peers yet.</p>';
  const owed = Number(data.openContracts || 0);
  // Run success rate 7d (completed / (completed+failed)) — the per-agent analogue of the global leaderboard.
  const _comp = Number(runs.completed || 0), _fail = Number(runs.failed || 0);
  const succ = (_comp + _fail) > 0 ? Math.round((_comp / (_comp + _fail)) * 100) : null;
  // Daily activity — 14-day in/out series (already returned by the endpoint; was never rendered).
  const daily = Array.isArray(data.dailyActivity) ? data.dailyActivity : [];
  const dGet = (d) => ({ inc: Number(d.received ?? d.in ?? 0), out: Number(d.sent ?? d.out ?? 0) });
  const dMax = Math.max(1, ...daily.map((d) => { const v = dGet(d); return v.inc + v.out; }));
  const dailyBars = daily.map((d) => {
    const v = dGet(d); const tot = v.inc + v.out;
    const h = Math.max(2, Math.round((tot / dMax) * 100));
    return `<span class="an-hod-col" title="${esc(String(d.date || d.day || ''))}: ${v.inc} in / ${v.out} out"><span class="an-hod-fill" style="height:${h}%"></span></span>`;
  }).join('');
  const dailySection = daily.length ? `<h4 class="an-h">Daily activity — 14 days</h4><div class="an-hod">${dailyBars}</div>` : '';
  // Hour-of-day histogram (0..23, all-time) — when is this agent most active?
  const hod = Array.isArray(data.messagesPerHourOfDay) ? data.messagesPerHourOfDay : [];
  const hodMax = Math.max(1, ...hod.map((b) => Number(b.count || 0)));
  const hodBars = hod.length ? hod.map((b) => {
    const c = Number(b.count || 0);
    const h = Math.max(2, Math.round((c / hodMax) * 100));
    return `<span class="an-hod-col" title="${String(b.hour).padStart(2, '0')}:00 — ${c} msg"><span class="an-hod-fill" style="height:${h}%"></span></span>`;
  }).join('') : '';
  const hodSection = hod.length
    ? `<h4 class="an-h">By hour of day (UTC, all-time)</h4><div class="an-hod">${hodBars}</div>`
    : '';
  return `<div class="chat-analytics">
    <div class="an-cards">
      <div class="an-card"><div class="an-n">${Number(data.messagesReceived || 0)}</div><div class="an-l">Received</div></div>
      <div class="an-card"><div class="an-n">${Number(data.messagesSent || 0)}</div><div class="an-l">Sent</div></div>
      <div class="an-card"><div class="an-n" title="Total time this agent has spent as a dispatch target, all-time">${esc(workLabel)}</div><div class="an-l">Working (total)</div></div>
      <div class="an-card"><div class="an-n">${mr ? esc(mrLabel) : '—'}</div><div class="an-l">Median reply 7d</div></div>
      <div class="an-card"><div class="an-n">${succ == null ? '—' : succ + '%'}</div><div class="an-l">Run success 7d</div></div>
      <div class="an-card"><div class="an-n${owed ? ' an-bad' : ''}">${owed}</div><div class="an-l">Owes replies</div></div>
    </div>
    ${dailySection}
    <h4 class="an-h">Work runs — 7 days</h4>
    <dl class="an-runs"><dt>Completed</dt><dd>${Number(runs.completed || 0)}</dd><dt>Failed</dt><dd>${Number(runs.failed || 0)}${runs.lastFailedSubject ? ` <span class="subtle clip" title="${esc(runs.lastFailedSubject)}">· ${esc(runs.lastFailedSubject)}</span>` : ''}</dd><dt>Open</dt><dd>${Number(runs.open || 0)}</dd><dt>Avg turn</dt><dd>${data.avgRunMinutes7d ? `${data.avgRunMinutes7d} min` : '—'}</dd></dl>
    ${hodSection}
    <h4 class="an-h">Top peers</h4>${peerBars}
  </div>`;
}
