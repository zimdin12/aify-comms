# Turn-start runner: the dashboard's rules reach an agent's turn (plan, 2026-10-18)

Owner: comms-tech-lead (the code is aify-wrapper's). Reviewers: dashboard-manager (contract), comms-senior-dev.

## The problem

aify-dashboard v0.5 built `POST /events/turn-start` (server/src/routes/turn-start.ts): given an agent, its folder
and its session, it answers with the text that agent should see as its turn begins (the operator's rules, owed
answers, staleness). Nothing calls it, so no agent has ever received a rule. The contract is the dashboard's
DESIGN-INTEGRATION.md item 4: one runner per harness, owned by aify-wrapper, calling every service whose registry
entry declares `turnStart`, in parallel under one shared 1.5 s deadline, concatenated in registry order, always
exit 0. It fell out of comms' finish-0.9 plan when that was re-planned on 2026-10-14.

## The premise to settle first

**A Claude turn that a channel wake starts runs no `UserPromptSubmit`** (claude-aify.sh.in:835 says so, and the
launcher re-asserts `working` from PostToolUse for exactly that reason). Most turns on this fleet are channel
wakes. So a runner on `UserPromptSubmit` alone reaches the turns the operator types, and few others.

- Step 0 re-measures this on the current Claude Code: one resident, one typed prompt and one channel wake, and a
  hook that appends to a file. If a channel wake does fire it, slice 2 below is unnecessary.
- **Step 0, first reading (2026-10-18): the comment is stale.** comms-tech-lead's `UserPromptSubmit` hook
  (claude-session-hook.js) rewrote its store file at 22:25:58.43Z. That was 0.8 s after a channel message
  (1792275957621, 22:25:57.62Z) woke the session, and no prompt was typed then. The file did not change during that
  turn's tool calls. The second reading agrees: the hook fired at 22:37:43.71Z, 0.7 s after message 1792276662997 (22:37:43.00Z) woke the session. **Slice 2 is dropped:** the slice 1 hook covers channel-woken turns, and C3 no longer applies.
- If it does not: slice 2 has the comms channel bridge (`claude-channel.js`) run the same runner when it delivers a
  wake, and put the text into the channel event it emits. One runner, two callers; no second hook.

## Slices

1. **Claude, typed turns.** aify-wrapper `bin/aify-turn-start.mjs`:
   - reads the hook's stdin (`session_id`, `cwd`), and `AIFY_AGENT_ID`, the machine id and the registry;
   - POSTs `{agentId, cwd, machineId, sessionKey, isFirstTurn}` to each declaring service, with the key its
     `credentialRef` names, all at once under the shared deadline;
   - prints the joined text as `hookSpecificOutput.additionalContext` and exits 0 on every path.

   The registry parser accepts `turnStart: {path, budgetMs}` and refuses an unknown key, as it does for
   `sessionInject`. claude-aify's settings gain one `UserPromptSubmit` command. The dashboard adds `turnStart` to its
   services.json entry.
2. ~~Claude, channel turns~~: dropped by step 0. A channel wake fires `UserPromptSubmit`, so slice 1 covers it.
3. **Codex** (`UserPromptSubmit` through `-c hooks.*`, with Codex's trust hash kept stable) and **hermes**
   (`pre_llm_call` in the aify hermes plugin, which lib/hermes-herdr-state.py already registers for state).

## Contract conditions (dashboard-manager APPROVE, 2026-10-18)

- **No `isFirstTurn`.** The runner omits it and keeps no state. The dashboard counts turns per `sessionKey`
  (events/sessions.ts:73), and it will read an absent field as "the harness didn't say". That dashboard change
  lands before slice 1.
- **The path is final:** `POST /events/turn-start`, auth agent. The registry shape is `turnStart: {path, budgetMs}`,
  like `agentState.path`.
- **C1, identity rides the credential.** Send `x-api-key` (the `credentialRef` key) and `x-aify-agent:
  $AIFY_AGENT_ID`. Without the header, owed answers are looked up for no particular agent.
- **C2, machineId is the bridge's derivation exactly:** `${tag}:${host}` lower-cased. host = AIFY_MACHINE_ID,
  else COMPUTERNAME, else HOSTNAME, else os.hostname(); tag = the platform, or `wsl` (dashboard
  server/src/bridge/machine-id.ts). `win32:StevenZ-L` places nobody, silently. The runner test asserts that a placed
  agent gets a non-null `projectId`.
- **C3, slice 2 sends the hook's `sessionKey`.** For one Claude session, the channel bridge sends the same
  `sessionKey` as the hook, or the session's ledger splits in two. Slice 2 must say where the bridge reads it.
- **C4, the registry entry goes in last.** dashboard-manager adds `turnStart` to services.json only after the
  wrapper that accepts the key is installed here, and only after checking that aify-env's readServices reads the
  entry unchanged.

## Proof

- Runner tests:
  - a slow service is cut at its budget and contributes nothing;
  - a failing service contributes nothing;
  - the order is the registry's;
  - exit is 0 on every path;
  - the key never reaches stdout.
- A launcher test names the runner in the settings it writes.
- **Live:** an operator rule written on the dashboard appears in a resident's next typed turn, and the dashboard's
  firing log names that agent. Slice 2 repeats this for a channel wake.

## Not in this plan

A comms notice through the same runner (comms has no turn-start notice today), and any change to the dashboard's
route.
