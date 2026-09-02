# The one list, 2026-09-02

Everything outstanding: what the operator asked for, what I found, and what is done. One file so a
task cannot be lost between a compaction and a work order.

**Reviews are LAST on purpose.** A review round ADDS to this list, so running one before the list is
worked expands it faster than it shrinks. The operator's rule.

**Rules for this file.** An item leaves only when it is DONE, DROPPED with proven reasoning, or
CHANGED with the reason recorded. "Probably fine" is not a state. Every claim of done names the
evidence.

---

## A. aify-env TUI

Asked 2026-08-24, re-reported unmet 2026-08-25, still unmet 2026-09-02. This is the oldest unmet ask
in the file and the one the operator has raised most often.

- **A1. Show the managed agents that are running.** *"i wanted to have some tui for aify-env. so i can
  see all managed agents that are running etc."* (08-24 20:19), then *"i still do not see anything
  under agent, i should se sc-manager and stuff like that correct?"* (08-25 04:46).
  **Why it never worked:** the TUI lists aify-env's OWN process record. Since Phase 8 the managed
  hermes agents are spawned by the aify-comms BRIDGE, not by aify-env -- which is why they survived
  aify-env going down -- so they are not in that record and never will be. The TUI has never asked
  aify-comms anything. This is an unbuilt join, not a display bug.
- **A2. Control a managed agent's TUI directly from aify-env** (09-02). The operator names
  [herdr](https://github.com/ogulcancelik/herdr) as the model: a persistent headless server plus a
  TUI client that attaches to REAL terminals, not redraws.
  **Open design question, settle before building:** whose PTY is it? aify-env owns the PTYs it
  spawns; the bridge owns the managed hermes ones. Attaching means either moving those PTYs into
  aify-env or attaching through aify-comms. Establish which before designing on top of it.
- **A3. The TUI shows what the doctor shows** (08-24 16:15, and item 3 of the TARGET_ARCHITECTURE
  work order). `aify-env doctor` exists and works; the TUI does not render it.
- **A4. Bare `aify-env` opens the TUI** (08-24 20:19). Today a bare `aify-env` STARTS the environment
  and supersedes the incumbent. That collides with the standing safety rule and with the operator's
  own 08-24 23:23 ruling that starting means taking over. **Decide, do not assume:** either bare
  `aify-env` attaches when one is already running, or it keeps starting and the ask is retired.

### What herdr does that we should copy

Read from source (`src/detect/`), not from articles.

- **Per-agent manifests as versioned TOML data** (21 of them), carrying `version`,
  `min_engine_version`, `updated_at`, `aliases`. Patterns update without shipping code.
- **`region`** names WHERE on the screen to look: `osc_title`,
  `bottom_non_empty_lines(12)`, `last_non_empty_above_prompt_box`.
- **`priority` per rule**, so arbitration is declared rather than emergent from match order.
- **`not = [{ contains = [...] }]`** negative guards, so a "working" rule cannot fire on a
  confirmation prompt.
- **PTY activity is the working authority**; screen patterns corroborate. `skip_state_update`
  suppresses state entirely when the screen is a transcript viewer rather than live chrome.

**Why this matters to us specifically.** `_terminal_prompt_hint_from_raw` matches prompt markers
anywhere in a 64 KB tail, which is why it is restricted to `claude-code`: an agent's own prose about
"which option" reads as a prompt. herdr solved that generally with regions and negative guards; we
solved it by narrowing to one runtime. See D9.

---

## B. Dashboard

- **B1. The browser pseudo-terminal, fast and good** (08-30 17:33). *"we can use hermes dashboard as
  exmaple, they have worked on theirs for a long time."*
- **B2. A design / UI / UX pass** (08-30, and 08-25's *"easy improvement oportunities (also in
  dashboard web ui/ux)"*). Row 2 shipped three dead CSS rules and triage-tile keyboard access, which
  is not this.
- **B3. The console complaints, from May and never closed:** text gets scrambled, encoding issues,
  feels slow. Each needs a repro before a fix; do not guess.
- **B4. The doctor, visible in the dashboard** (09-02). *"i never go to that path... i have container
  that should give me that info. some random path for aify-comms doctor... no. will never use it."*
  Four checks are answerable from the service's own data with no host agent -- `env-bridge`,
  `session-handles`, `context-window`, `bridge-current`. The rest need a host reporter, which is
  aify-env's job under TARGET_ARCHITECTURE.
- **B5. Browse an agent and its processes** (09-02). *"i cannot still check the processes themself?
  (like browse agent or something)"* No agent-detail view exists: no terminals, console, runs or pids
  behind a click.
- **B6. A login prompt instead of a URL parameter.** DONE 2026-09-02, deployed and verified.

---

## C. Other operator asks

- **C1. Triage every tool: are they reasonable, do they have good descriptions** (08-30). Partly done
  -- Row 3 fixed several descriptions -- but no pass over the whole surface.
- **C2. Security work developed AND tested** (08-30). Round 7 closed and today's auth defects fixed.
  Residual: C4 and D7.
- **C3. SoC review of aify-env and aify-wrapper** (08-30), so a project unrelated to aify-comms can
  reuse them. Not started.
- **C4. The install-time key** (09-01, restated 09-02). *"when installing first time agent should ask
  for it. if installing aify-comms + aify-env + aify-wrapper (full local install) then ofc all these
  can have one ask and use same key."* The installer now CARRIES a key it finds; it does not yet ASK
  for one.
- **C5. Terminal write path, the full fix** (operator chose the full option 09-02): move the two
  status-path readers off the stored tail, then write it lazily. Scoped: both readers want the
  rendered screen, and the live screen is already rendered, so they skip a pyte render as well as a
  database read. `claim_block_reason.py` stays on the raw log -- it compares marker POSITIONS in the
  stream and a rendered screen loses that ordering.
- **C6. `comms_send` unslop** -- tighten all 2,636 B rather than cut the reply contract. `tools/list`
  costs ~7.9k tokens per agent per turn, so this is paid every turn by every agent.
- **C7. Hook source: repo rather than the installed copy** (decided 09-01, reverses `e8856126`).

---

## D. Found while working, still open

- **D1. aify-env must be restarted to pick up its credential.** The key is stored and the registry
  now names it (`credentialRef`), but the running daemon read neither at startup. Operator's action.
- **D2. Duplicate session handles.** 3 sessions claimed by 8 agents, measured 09-02. Report-only; the
  mechanism is still unknown and two traced explanations were disproved against hermes' own source.
- **D3. Orphaned hermes gateways. CLEARED 2026-09-02, and the open question is answered.** 15
  processes across 5 gateways (5 `hermes.exe` + 10 `python`, three per port). **They live at least 45
  HOURS** -- measured at 2736, 2699, 1803, 1801 and 1563 minutes old -- so nothing reaps them, which
  the doctor's own note recorded as never established.
  Killing the five trees produced a brief cascade: the orphaned delivery loops respawned gateways,
  the doctor caught 12 mid-flight, and both they and the loops were gone seconds later. Sustained
  measurement afterwards: ZERO `hermes.exe` over 18 seconds, and `hermes update` is unblocked.
  **What is still unfixed is the LEAK, not this instance.** aify-comms' survivor sweep runs at bridge
  BOOT and on GRACEFUL shutdown, so an abrupt kill is not covered -- which is how five gateways
  reached two days old. A state-based reaper is the shape this repo already prefers for exactly this
  (see `state-based-cleanup-over-event-based`).
- **D4. SPLIT-M1: no gate compares VERSION against the released tag.** VERSION read `0.6.0` while HEAD
  was 407 commits past the v0.6.0 tag and every version gate was green -- they check that the five
  files agree with EACH OTHER. The v0.6.1 bump was a hand-correction, not something a test demanded.
- **D5. aify-env's build identity may not distinguish versions.** Its `/health` omits
  `advertiseCredentials`, which the current source emits, while its content hash matches the checkout.
  Either the hash does not cover the files that changed or something else is running. **UNVERIFIED --
  do not repeat my mistake of asserting either way.**
- **D6. WRAP-M1: keyEnv secrets baked into world-readable launchers.** Latent only because no service
  sets `strictMcp: true`. Fix before one does.
- **D7. Per-agent authority.** A shared secret proves fleet membership, never "may act AS agent X".
  Blocks SSE-M1 (console input's actor is forgeable by construction), CRED-L1, Row 4 F4, and Row 1
  (dashboard terminal input). The operator's 09-02 answer described the INSTALL-TIME key (C4), which
  does not close this. Recorded as open by decision, not by oversight.
- **D8. `advertiseCredentials` missing from the running daemon's `/health`.** Unexplained; related to
  D5.
- **D9. Prompt detection is claude-code-only and unbounded in the tail.** See the herdr notes in A.
  A general detector with regions and negative guards would cover hermes, codex and pi, which today
  fall back to native events only.

---

## E. Reviews -- LAST

- **E1. A new external review round.** The operator will request one *"ONLY IF ALL OF THESE ARE DONE,
  OR DROPPED OR CHANGED WITH REASONS THAT ARE PROOVEN AND THOUGH THROUGH"*.
- **E2. Independent dev review of my own commits.** 38 of the Round 7 commits are mine and I cannot
  close them myself.

---

## Done on 2026-09-02, for context

Each verified on the artifact that executes, not on the repo.

- The dashboard was unusable with `API_KEY` set, four independent ways: the WebSocket ignored the only
  credential a browser can send; the page never sent the key at all (cross-origin, cookie does not
  ride); CORS preflights were answered 401, which they can never pass; and an https page was told to
  call a plain-http port.
- Two Caddy defects: `HTTPS_SITES` never reached the container, so that knob had never done anything;
  and a browser reaching an address by IP sends no SNI, so Caddy had a certificate and still failed
  the handshake. HTTPS now on 8801.
- The port-collision gate read compose defaults rather than `.env`, and had stopped matching the https
  proxy's line entirely -- it let a real collision through, mine, onto aify-env's own port.
- Temp-directory leak closed in all three repos: 80 test files leaked, zero shipped files did.
- `install.sh` never carried an existing key to aify-env, so every advertisement 401'd with both sides
  reporting healthy.
- `aify-env credential set` through the dispatcher stored nothing, printed nothing, and exited 0.
- v0.6.1 tagged: service, installed bridge and repo HEAD all agree.
