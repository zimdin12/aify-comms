# 2. The sandbox gate (SW3)

The gate that replaces the one-day comparison. It runs on the env-against-comms runner (branch
098/env-comms-sandbox, `scripts/sandbox/env-against-comms.mjs`): comms and aify-env, each from a given commit, on an
internal network, owning only what they create, and cleaned up by readback.

## The stand-in agent

A defined managed agent whose launcher is a small script, not a real harness, so the sandbox needs no logins. It:
- registers as the launcher would;
- claims runs through comms' `/dispatch/claim`;
- reports turns to aify-env's real route `POST /agents/:id/turn-event` (`turn-start`, `turn-end`;
  env lib/protocol.mjs);
- records every delivery it receives with a timestamp.

The runner tells it when to start and end a turn, so busy and idle are driven by the test, never by a timer. Building
it is part of SW3. ASSUMED: aify-env accepts turn events for a lifetime it launched through such a launcher; the
first case checks this.

## Cases, each with its witness and what turns it red

Every case reads comms' answer from comms' own routes. Each one names the mutation it must fail on.

| case | witness | red when |
|---|---|---|
| register and define | the agent reads `online` (record `idle`), and its record is fresh | the view ignores the record (a mutant reading the legacy engine) |
| idle, message sent | delivered at once: the claim returns the run | the claim gate treats `false` as hold |
| busy, non-steer message | queued; delivered within one claim pass after `turn-end` | the gate drops `busy` (delivered mid-turn) or never releases (still queued after `turn-end`) |
| busy, steerable channel message | injected mid-turn (carve-out) | the carve-out is removed |
| a run is running on an idle record | the word reads `working` (row 5) | promotion is skipped at the view |
| comms restarts mid-turn | after restart and before aify-env's next publication: `busy` true, and a non-steer send queues | the reload is removed (busy reads unknown, then delivers after the next publication: the timing of the delivery tells the two apart) |
| aify-env silent (container paused) | after 180 s the word reads `unknown`; a send queues; nothing is delivered | unknown is read as false (the run is delivered) |
| aify-env back (unpaused) | the queued run is delivered after the first fresh publication; the word returns | the queue never drains |
| stop, through aify-env's journal | the word reads `stopped`; a send is stored and not run | the view's stop row is skipped |
| restart, through aify-env | the same agent id returns to `online`; queued work drains | a restart leaves the agent `unknown` |
| an unverified lifetime (launcher without a pid aify-env can verify) | aify-env's fallback decides `busy`; comms adds no timer of its own | a comms-side timer is reintroduced (a mutant restoring `strictExpiry`) |
| a reminder and a stranded run during a turn | the reminder is skipped, and the stranded run is not failed, while busy; both act after `turn-end` | the guards read the legacy table |

The 180 s wait in the silent case is the real freshness rule, so the case lasts about four minutes. A
clock-injection seam is not added for it.

## What the gate does not cover

- Real claude, codex and hermes processes, Hermes gateways, resident panes, the dev-channels prompt, and provider
  stalls. These are the switch watch list (page 3).
- Throughput and long runs. The sandbox runs one agent.

## Pass rule

Every case passes on the candidate commits, three runs in a row, each with cleanup complete. Every mutation named in
the table has been seen to turn its case red, with each file restored by byte copy and the driver bytes kept.
