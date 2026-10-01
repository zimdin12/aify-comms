# A resident claude with background work reads `shell`

The operator, 2026-10-01: "i do not see cyan marker on you currently (but you have shell running)".

## Why it was missing

`shell` (cyan on the dashboard and in the aify-env TUI) came from one place: aify-env reading the
screen of a worker it started (`withBackgroundShell` in aify-env). The service's `derive()` read that
host observation only for managed agents. A resident has no screen anyone reads, so it showed `online`
while its shells ran.

## What changed

- **The bridge** (`mcp/stdio/claude-background-work.mjs`) follows the session transcript from where it
  first looks. It counts background shells and background agents, starting each at its
  `toolUseResult` and ending it at its task notification (a `queue-operation` enqueue with a final
  status). While the count is above zero it posts it every 5 s; it posts zero once when the work ends.
  It is armed with the turn-end detector, from the same transcript and the same identity.
- **The service** stores the report as a 20 s lease (`api_core/background_work.py`, the route
  `POST /agents/{id}/background-work`). Both status-input producers read it, and `derive()` returns
  `shell` for a live resident at its prompt. A cached `shell` recomputes no later than the lease ends.
- **The schema**: `agent_console_signal.background_at`, added by the migration only, as
  `subagents_at` is.

## Why the transcript, not the process table

A PowerShell CIM query for claude's children measured 656 to 1188 ms of CPU per call. Run on every
beat for every resident, that is 2 to 4% of a core per agent, all the time. Claude Code has no hook for
a background shell ending, and its statusline input has no shell count (its documentation, read
2026-10-01). The transcript records both ends; this was observed in a real session file, not taken
from documentation.

## Evidence

| file | what it is |
|---|---|
| `mutations.json` | 23 mutations of the service rules, the bridge's parsing, following and reporting, and the arming |
| `mutate.py` | the P2 driver: a process-tree kill on timeout, and the killing tests named for pytest as well as TAP |
| `mutations-result.txt` | 23/23 killed, each with the tests that killed it |

The first battery left two survivors, and both changed the code or the tests:

- **"An existing database never gains the column" survived.** `test_a_migrated_database_matches_a_fresh_one`
  builds its older database by subtracting the migration dict's own columns from the fresh shape. A
  column declared in `schema.py` but missing from the migrations therefore lands in the "older" shape
  too, and the gate cannot see it. I had declared `background_at` in `schema.py` as well, so the
  migration's absence was invisible. The column now lives only in the migration, as `subagents_at`
  does, and every test that writes the lease fails without it.

  The gate's blind spot remains for any column declared in both places. That is a separate finding,
  not fixed here.
- **"The reporter is never stopped" survived.** Nothing checked that disarming stops the reporter. A
  new witness arms in a child process, records every interval created and cleared, and requires none
  left after stop. Its controls: the reporter's interval was seen, and the arm took.

## What this does not show

- **Nothing here ran on a real resident.** The bridge was not reinstalled and no agent was
  relaunched, which are the operator's actions. The service was not rebuilt. The record shapes the
  parser reads came from a real transcript; that the live chain shows cyan is PENDING until those
  steps.
- **A background shell started before the bridge started is not counted.** That is by design, so a
  resumed session's history never reads as live work. It means a bridge restarted while shells run
  shows `online` until the next one starts.
