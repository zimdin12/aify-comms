# 3. The switch, the rollback, the deletions

## Before the switch (SW2, SW4)

- **One pure view for push and poll (Q4).** `commsView(records, messaging) -> {status, note, deliverable,
  holdsDelivery, runOccupied}` is called by the status push, the status poll, the sessions list and every delivery
  reader.
  - The word follows C6 rows 1-6 with the rulings: Q1 `unknown`, Q3 `offline` for a proven-absent wake handle.
  - Row 5 promotion (a running run shows `working`) is applied inside the view, so it holds on every path. That
    retires "promotion on every path" as a separate slice.
- **D8 rows 1 and 2 (SW4, comms).** These are "not implemented" today (s2c-owners.md, slice 5).
  - Row 1: an undefined agent the operator stopped reads `stopped`.
  - Row 2: an undefined managed agent comms can cold-start reads `available`.
  - Each gets a unit witness in the view.
- **B4 (SW4, env).** aify-env's word needs screen, background-shell and starting inputs (agent-state-host.mjs reads
  `given.screen` and `given.backgroundShells`). First trace who supplies them. Build only the missing producer, with
  the env-schema rule (every copy, sibling suites with AIFY_ENV_REPO=candidate).
- **The 17 P7 witnesses (test_p7_witness_*.py).** All stay. 15 keep their assertions with the busy source swapped to
  the record. The two run-correlated ones change as ruled on page 1.

## The switch (SW5): one revertible commit

- Every reader on page 1 is rewired to the view in a single commit, so a single revert undoes it.
- The legacy write paths keep APPLYING through the interval. Launchers still post turn events to comms, and
  `agent_turn_state`, `agent_status_state` and `agent_hook_order` stay current. Nothing reads them for delivery, and
  the rollback needs them.
- No reverse shadow, recorder or windows.

## The rollback (SW6)

- **What reverts:** the SW5 commit, followed by a stamp and rebuild. That takes a few minutes, and nobody relaunches,
  because the launchers never changed.
- **The data is complete:** the legacy tables were written throughout.
- **The rollback test, part of SW5's review:** a turn starts after the cutover, then the rollback revert is applied
  over the same database. Hold and release, queue and claim, and run ownership must read correctly under the legacy
  readers.
- **The interval:** the rollback stays ready until a clean watch period ends. That is the operator's call, with the
  plan default being one working day of normal fleet use. Only then does SW7 run.

## The watch list, for the first hours on the real fleet

Read from comms' own routes and the dispatch tables, and reported at fixed points: after 15 min, 1 h and 4 h.

| what | why the sandbox could not show it | healthy reading |
|---|---|---|
| Hermes gateway agents (comms-senior-dev, pc-manager) | real gateway turn hooks | busy while working, idle after; queued work drains |
| resident claude panes (B1 launchers) | real hooks, prompt states | `online` or `working` matches the pane; no `unknown` while aify-env is reporting |
| agents waiting at a prompt (dev-channels, folder trust) | real Claude dialogs | aify-env says `blocked` or `starting`; comms delivers nothing into the dialog |
| queue depth per agent | real traffic | no non-steer queue older than one turn on an idle agent |
| `unknown` count | real hosts | zero for hosts whose aify-env is reporting |
| reminders and stranded failures | real reply-owed runs | no reminder while busy, and no stranded failure of a run being worked |

## The rollback trigger

Roll back on any one of these:
- a non-steer message delivered into a turn that aify-env reports busy;
- a non-steer queued run still held 10 minutes after the agent's record went idle and fresh;
- an agent of a reporting host reading `unknown` for more than one publication interval;
- the operator says so.

The first three are read from the watch, with the run and record ids that show them kept as the evidence.

## The deletions (SW7, 0.9.9, after the interval)

In S5's order (status-switch.md step 7):
1. A database backup.
2. Launchers post only to aify-env. Steven relaunches.
3. The legacy turn routes become accept-and-ignore (C7).
4. Deleted, each after proof that no live route reaches it:
   - the legacy engine, detectors and tables;
   - every comparison-only item in the inventory: the slice-1 recorder and windows, S2c-E evidence, D-UNKNOWN,
     the B6c ceiling module, B6a/b build-per-record, fold provenance (a), the in-memory delivery-turn fold
     (`delivery_turns.py`), the stale-record disjunct, the shadow's recording half of `comms_view.py`,
     `busy_consumers.py`, `view_owners.py`, and the `agent-state-shadow` doctor row.
5. The two tables are renamed out of `shadow` (`agent_state_shadow_rows` and `agent_state_shadow_publishers`),
   with a migration.

Mixed items keep their decision logic in the view. Their recording goes.
