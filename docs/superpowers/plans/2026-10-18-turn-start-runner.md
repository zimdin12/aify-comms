# Turn-start runner: the dashboard's rules reach an agent's turn (plan, 2026-10-18)

Owner: comms-tech-lead (the code is aify-wrapper's). Approved: dashboard-manager (contract), comms-senior-dev
(plan, at 0feaccef, with the amendments below folded in).

## The problem

aify-dashboard v0.5 built `POST /events/turn-start` (server/src/routes/turn-start.ts): given an agent, its folder
and its session, it answers with the text that agent should see as its turn begins (the operator's rules, owed
answers, staleness). Nothing calls it, so no agent has ever received a rule. The contract is the dashboard's
DESIGN-INTEGRATION.md item 4: one runner per harness, owned by aify-wrapper, calling every service whose registry
entry declares `turnStart`, in parallel under one shared 1.5 s deadline, concatenated in registry order, always
exit 0. It fell out of comms' finish-0.9 plan when that was re-planned on 2026-10-14.

## Step 0: a channel wake fires UserPromptSubmit (measured)

Historical premise: claude-aify.sh.in:835 said a turn a channel wake starts runs no `UserPromptSubmit`. **It does,
on the current Claude Code.** The `UserPromptSubmit` hook (claude-session-hook.js) rewrote its store file 0.8 s and
0.7 s after two channel messages woke comms-tech-lead (1792275957621 and 1792276662997), with nothing typed. The
store file did not change during that turn's tool calls. Claude's hook documentation agrees.

The same command is also registered on SessionStart, so the file's mtime is not an event-specific receipt. The live
proof below is what binds delivery. The stale comment is corrected in this slice, keeping PostToolUse's status
recovery.

## Slices

1. **Claude, typed and channel-woken turns.** aify-wrapper `bin/aify-turn-start.mjs`, a synchronous
   `UserPromptSubmit` command beside the existing session capture and Herdr state hooks, which stay as they are:
   - reads the hook's stdin (`session_id` unchanged as `sessionKey`, and `cwd`), and `AIFY_AGENT_ID`, the machine id
     and the registry;
   - POSTs `{agentId, cwd, machineId, sessionKey}` to each declaring service, at once, with `x-aify-agent` and the
     key its own `credentialRef` names;
   - prints the joined text as `hookSpecificOutput` (`hookEventName: "UserPromptSubmit"`, `additionalContext`) and
     exits 0 on every path.

   The wrapper's registry parser validates and carries `turnStart: {path, budgetMs}` and refuses an unknown key
   inside it, as it does for `sessionInject`.
2. ~~Claude, channel turns through the comms channel bridge~~: dropped by step 0.
3. **Codex** (`UserPromptSubmit` through `-c hooks.*`, with Codex's trust hash kept stable) and **hermes**
   (`pre_llm_call` in the aify hermes plugin, which lib/hermes-herdr-state.py already registers for state).

## Contract conditions (dashboard-manager APPROVE, 2026-10-18)

- **No `isFirstTurn`.** The runner keeps no state. The dashboard counts turns per `sessionKey` and reads an absent
  field as "the harness didn't say" (e247257, live).
- **The path is final:** `POST /events/turn-start`, auth agent. The registry shape is `turnStart: {path, budgetMs}`,
  like `agentState.path`.
- **C1, identity rides the credential.** Send `x-api-key` (the `credentialRef` key) and `x-aify-agent:
  $AIFY_AGENT_ID`.
- **C2, machineId is the bridge's derivation exactly:** `${tag}:${host}` lower-cased. host = AIFY_MACHINE_ID,
  else COMPUTERNAME, else HOSTNAME, else os.hostname(), with an empty value treated as unset and the result
  trimmed; tag = the platform, or `wsl`. A fourth copy, so an agreement test drives it against aify-env's
  `machineIdFor`. `win32:StevenZ-L` places nobody, silently (shown live).
- ~~C3~~: inapplicable while slice 2 is dropped.
- **C4, the registry entry goes in last,** after the wrapper that accepts it is installed. aify-env's readServices
  returns a projection that drops `turnStart`. The check is that adding the field leaves that projection
  unchanged. It does not require env to carry the field.

## Implementation checks (comms-senior-dev, at plan approval)

- The context command is synchronous, unlike the adjacent async Herdr hooks. The generated settings parse, and the
  session capture and state reporting still run.
- The shared deadline covers the whole invocation: request, response body, and incomplete-input and error paths.
  A service's `budgetMs` only shortens its own allowance. A fast service's answer survives a stalled or failing
  peer.
- `turnStart` survives the authoritative parser and reaches the runtime reader, in the parser's canonical service
  order.
- No key in any error, log or stdout. Redirects are refused, never followed with the key.
- Placement is proved with the request the runner itself builds, not a fixture that supplies an already correct id.

## Rollout (tied to B1)

aify-wrapper main carries B1's launcher (73971dbe), so the comms pin that brings this runner also brings B1.
Steps 2-6 follow in this order, never sooner:
1. Steven restarts aify-env on B1.
2. The new env holds its residents role.
3. The runner-bearing pin is installed and the launchers re-rendered.
4. The residents relaunch into the new hooks. A relaunch into old settings does not pick the runner up.
5. dashboard-manager adds `turnStart` to services.json (C4).

There is no bootstrap waiver, and no generation is deleted or refilled. The comms advisory's consumed surface gains
`render.sh` beside `bin/`, with a witness through the real advisory query that a render-only change is reported.

## Proof

- Runner tests:
  - a slow service is cut at its budget and contributes nothing;
  - a failing service contributes nothing;
  - the order is the registry's;
  - exit is 0 on every path;
  - the key never reaches stdout.
- A launcher test parses the settings it writes and finds the runner on `UserPromptSubmit`.
- **Live, both triggers:** after a fresh marker rule is written on the dashboard, it appears in the context of one
  resident's typed turn and of one channel-woken turn. Each is bound to the Claude version, the hook event, the
  agent and session, and the triggering message. The dashboard's firing log shows only issuance; the agent's own
  turn shows consumption.

## Not in this plan

A comms notice through the same runner (comms has no turn-start notice today), and any change to the dashboard's
route.
