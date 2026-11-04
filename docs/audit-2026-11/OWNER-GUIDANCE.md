# Owner guidance used by the 2026-11 audit

What the owner told the auditors during the audit, in the order it was given. It decides how every audit issue was judged. Where it conflicts with an older repo document, this wins (see [ROLES.md](ROLES.md) for the confirmed decisions).

## Addendum from the owner (applies to everyone, overrides where it conflicts)
1. UNDERSTAND THE POINT FIRST. Before judging any skill, tool, hook or script, work out what job it exists to do
   for an agent or for the owner, and what problem made someone build it (read its own docs, the commit/casebook
   reason, DECISIONS, the doctrine). Put that intent in the `map`: one line "exists so that ..." per item. Judge
   each item against its OWN purpose first ("does it achieve what it is for, for an agent, at a sensible cost?")
   and only then against general taste. Do not call something redundant until you can state the distinct purpose
   each side claims and show that those purposes really are the same (or that one fails at its own).
2. The goal is a BETTER STACK: more useful to agents, better code. Prefer findings that would change how well an
   agent works (wrong tool picked, missed trigger, wasted context, contradictory guidance, a workflow with no
   tool) over cosmetic ones. Gaps (a job agents need that nothing does well) count as findings too.
3. The issues will be analysed by a strong reasoning model that plans the way forward. Write each body so that
   model can reason, not just obey. Use these sections:
   ## Purpose (what the item(s) exist to do, in the owner's/repo's own words, with source)
   ## Current behaviour (what actually happens, measured)
   ## Evidence (`repo/path:line`, numbers, commands you ran and their output)
   ## Problem and impact on agents
   ## Options (2-3 real alternatives incl. "leave it", each with trade-offs)
   ## Recommendation (which option and why)
   ## Acceptance criteria (testable checkboxes)
   ## Open questions / confidence (what you could not verify; high/medium/low confidence)
4. Prefer fewer, deeper findings. A finding that is only an opinion with no measurable consequence: drop it.

## Addendum 2 from the owner
5. ON DEMAND, NOT TIME-BASED. The owner dislikes time-based mechanisms: polling loops, periodic sweeps,
   heartbeats-as-truth, "quiet for N seconds means done" timers, TTL guesses, cron-like refreshes. Work should
   happen when something asks for it or when an event says it changed, done optimally (cheap, incremental,
   cached until invalidated by the event). When you find a time-based mechanism, say what event or request could
   drive it instead and whether that event already exists in the stack (SSE/websocket push, process exit, file
   write, hook, MCP call). Where time really is the only signal (e.g. a crashed process that never reports),
   say so and keep it minimal.
6. DON'T OVERTHINK OR OVERBUILD. Recommendations must be the simplest change that fixes the problem. Prefer
   deleting/merging over adding layers; no new frameworks, services, schedulers or abstraction tiers unless the
   finding proves nothing smaller works. Performance is a lens, not a project: flag what is clearly wasteful
   and measure where cheap — no elaborate benchmarking harnesses.

## Addendum 3 from the owner
7. aify-env and aify-wrapper are a PLATFORM LAYER. Other future services/projects (like aify-comms and
   aify-dashboard are today) will start using aify-wrapper and/or aify-env. Judge them as reusable platforms:
   - Service-agnostic core: does any aify-comms- or aify-dashboard-specific knowledge leak into env/wrapper
     (hardcoded service names, routes, status words, plugin dirs named after a consumer, assumptions about who
     is calling)? Flag leaks; recommend the smallest way to move them into the consumer.
   - A clear, small, versioned integration surface a NEW service can adopt from docs + schema alone: registry
     entry (~/.aify/services.json), plugin/extension point in aify-env, launcher hooks/env in aify-wrapper, the
     skill/MCP-bridge registration. How many steps and files would service #3 need today? What would it have
     to copy from aify-comms or aify-dashboard (= duplication waiting to happen)?
   - Consumer-specific logic duplicated in each consumer that belongs once in the platform (e.g. registry
     writers, credential resolution, doctor checks, install steps).
   - This weighs AGAINST folding env/wrapper into a consumer's monorepo; keep them independently consumable.

## Addendum 4 from the owner — what each product is FOR
8. aify-dashboard is the owner's "second brain" for projects: it should help agents KNOW the project — a
   "super version of LSP": graph context, a queryable knowledge base about the project, its state, roadmap and
   work. Plus a small set of manual controls: events, and questions posted/answered between agents and the owner.
   It is THE place where agents and the owner meet to share understanding of the project, the work and its state.
   Consequences for judging:
   - Its value is measured by how well an agent can get correct, current, relevant project knowledge from it
     cheaply (few calls, small payloads, fresh), and how well owner<->agent questions/events flow.
   - Anything in it outside that purpose is suspect: e.g. fleet control (spawning/killing agents, lifecycle,
     roster as a source of truth) overlaps aify-comms' job (messaging/dispatch/runs/agents) — the earlier
     "two dashboards" and "three agent-status sources" issues should be re-judged in this light (dashboard
     should CONSUME agent status for context, not own it or control the fleet), not "make dashboard the only UI".
   - aify-project-graph produces code structure (tree-sitter graph, briefs, code_intel LSP verbs). Its seam with
     aify-dashboard's graphs/knowledge base must be clean: one producer, one place agents ask; no two graph stores,
     no two "orient me in this repo" entry points that disagree.

## Addendum 5 from the owner — what aify-comms is FOR
9. aify-comms is a MESSAGE store-and-deliver system. Much of what it used to do now lives in aify-wrapper
   (launchers) and aify-env (processes, spawns, agent state). What it provides today: the owner's communication
   interface (its own dashboard on :8801), message history storage, delivery to agents, some statistics, and a
   work loop (message -> run -> claimed -> closed by reply).
   Possible FUTURE (not decided): whole-transcript saving in aify-dashboard, which could make message-history
   storage in aify-comms redundant. Don't build for that future and don't recommend acting on it now; but prefer
   recommendations that keep aify-comms thin and don't deepen lock-in to comms as a history/knowledge store
   (e.g. don't add new knowledge features to comms; keep message/history behind a small, clear boundary).
   Consequences: logic in comms that duplicates what env/wrapper now own (process/terminal knowledge, agent-state
   derivation, launcher/harness knowledge, spawning) is a leftover to delete or delegate. "Who owns agent status"
   should be judged with env as the host truth (it derives and publishes state) and comms/dashboard as consumers,
   unless the code shows a reason otherwise.

## Addendum 6 — owner confirmations
10. CONFIRMED: agent status lives in aify-env, because aify-env holds the registry of agents and serves status to
    every service that needs it. aify-comms and aify-dashboard are consumers.
11. Leftover logic: recommend removal ONLY where the counterpart exists AND is actually used. Each removal finding
    must name the counterpart (`repo/path:line`) and show it is wired and used (caller, route, test, doc of current
    flow). If the counterpart is missing or unused, the finding is "finish the move" or "leave it", not "delete".

## Addendum 7 — owner decision
12. NO MONOREPO (including no env+wrapper merge). Reason: agents build the stack; repo boundaries keep separation of
    concerns enforceable for agents. Recommendations must work within separate repos.
