# Dashboard Review Notes

This dashboard is now treated as the product surface, not as a raw admin page.

## Ten Review Lenses

1. Product fit: daily work should start from Chat, Agents, Environments, and Sessions. Legacy duplicate send/spawn forms should not be on Home.
2. Navigation: Home is an overview; Agents means managed dashboard-spawned teammates; manual CLI bindings are labeled separately.
3. Wording: avoid "resident session" for stale/manual rows because it implies a live process.
4. Action safety: offline manual identities do not show Stop. Stop/disable language must say whether it affects wake/dispatch or a real runtime process.
5. Tooltips: compact buttons need `title` text explaining what will happen before click.
6. Read state: read/unread is scoped to the selected chat identity. A manager should not be forced to read every agent's inbox.
7. Spawn model: `aify-comms` roots are safety boundaries; each agent's exact workspace is selected at spawn time.
8. Session control: Sessions are concrete runtime backing records; Agents are teammate identities; Runs are tracked work attempts.
9. Debug surfaces: run/event tables and repair actions remain available but should not dominate daily use.
10. Remaining debt: a dedicated frontend framework, inspector drawer, grouped DMs, richer run/thread linking, and true logs/telemetry views are still future work.
11. Visual quality: keep the UI dense and operational, but use stronger panel hierarchy, quieter borders, readable dark neutrals, and obvious primary actions so it feels like a real control plane instead of a debug dump.

## Current Dashboard Semantics

- **Home**: operational stats, communication/failure/handoff/capacity signals, and "needs attention". It should not host duplicate send/spawn forms.
- **Chat**: normal communication surface for DMs and channels. The selected **Viewing as** identity controls which messages are marked read, who sends, and which DM can be cleared.
- **Agents**: managed agent identities spawned or controlled by the bridge, plus a separate manual/resident CLI section. Managed agents may have saved resume state without a currently running process.
- **Environments**: connected host/WSL/Windows/Linux bridges and the managed-warm spawn form.
- **Sessions**: concrete runtime/session backing records. Stop/restart/recover/continue belongs here when a real managed session exists.
- **Runs**: dispatch attempts, handoff state, events, steering, and interrupts.
- **Artifacts**: shared files.
- **Help/Settings**: support and policy surfaces.

## Design Rules Going Forward

- Prefer dashboard-spawned managed agents over manual `comms_register` for normal teamwork.
- Do not expose a destructive button without hover text and a confirm when state will be deleted or wake/dispatch disabled.
- Do not show a "kill/stop" action for a row that represents an offline identity only.
- Do not let legacy `aify-comms` terminology leak into primary navigation unless it describes the actual user model.
- Keep debug/admin tables behind details panels or secondary pages.
- Keep the visual language restrained: neutral panels, clear status colors, compact tables, and chat-first workflows.
- Do not expose "dispatch" or "track as run" in normal chat. Users and agents send messages; run state is operational telemetry unless strict live-start behavior is explicitly needed.
- Chat management must be real backend state: channel delete deletes the channel; DM clear deletes the direct messages for that identity pair.
- Continue/compact currently means "create a fresh managed-warm successor from an editable handoff packet"; do not imply native session compaction exists until the runtime adapters can generate packets automatically.

## Deep Review 2026-04-28

What was corrected in this pass:

- **Environment ownership boundary**: normal MCP client sessions no longer heartbeat as dashboard environments. Only the `aify-comms` launcher exports `AIFY_ENVIRONMENT_BRIDGE=1`, so every open Codex/Claude tab should not become a duplicate spawn target.
- **Topbar behavior**: removed page-specific action swapping. The topbar is stable now: product name/page title, live indicator, refresh. Page-specific actions belong inside page content.
- **Collapsed sidebar**: removed always-visible letter badges. Collapsed nav is now a quiet dot rail with hover labels and a persistent edge collapse button.
- **Analytics**: replaced old row stacks with a switched traffic chart (`24h`, `30d`, `12m`), live-health cards, and a run-status mix chart.
- **Live failure visibility**: Home includes live-binding issues in Needs Attention, and DM chat shows a warning banner when a target is not live-wake capable.
- **Live-only spawn model**: dashboard/API now reject new non-managed-warm spawn modes. Legacy values may still exist in old data, but they are not product choices.

Remaining high-value fixes:

1. **Real managed runtime spawning**: current managed spawn/session records are still mostly control-plane backing. The bridge needs adapter-backed child process ownership, logs, and stop/restart semantics per runtime.
2. **Replace browser prompts**: artifact creation, steering, restart/recover instructions, and some destructive flows still use `prompt()`/`confirm()`. Move these to proper modals/drawers.
3. **Logs/transcripts**: Sessions should expose live output/transcript views. At the moment the UI can show session metadata but not enough runtime evidence.
4. **Group chat policy**: channels exist, but loop budgets, auto-reply controls, and private multi-agent groups are still not fully modeled.
5. **Unread semantics**: read marking is scoped to "Viewing as", which is correct, but unread counts need a clearer per-identity/global distinction in Home and Chat.
6. **Environment cleanup UX**: bridge replacement now makes the newer bridge current and queues a stop for the old bridge; Forget hides an obsolete execution target without deleting teammate identity/session/spec records. Remaining polish: show clearer banners for detached teammates and provide a richer Assign Environment modal instead of prompts.
7. **Frontend structure**: `service/dashboard.html` is now very large. Extracting API/client/state/render modules or moving to a small SPA would reduce regression risk.
