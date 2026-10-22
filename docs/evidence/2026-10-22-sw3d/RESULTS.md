# SW3d results: GREEN (2026-10-22)

The pair comms `78270d30` + aify-env `ed9ebe5` passes the gate under PREREGISTRATION.md (`926f39de`). All three
attempts exited 0. None was rerun or replaced.

| attempt | start, UTC | exit, UTC | primary | cleanup |
|---|---|---|---|---|
| q1 | 14:23:50Z | `q1 exit=0 at 14:58:43Z` | 14/14 cases passed | complete, every owned resource read back absent |
| q2 | 14:58:43Z | `q2 exit=0 at 15:33:34Z` | 14/14 | complete |
| q3 | 15:33:34Z | `q3 exit=0 at 16:07:58Z` | 14/14 | complete |

Times are from `runs/exits.txt`, written by the run loop. The preregistration was committed at 14:23:40Z.

## Validity

- Source roster: each attempt logs 14 sha256 lines, and 14 of 14 match `b70699c4`'s blobs in all three
  (`git show b70699c4:<path>`). Negative control: the same lines against `e7750b9f` match 9 of 14.
- No `REUSING images` line, no case FAIL, ERROR or UNKNOWN line in any attempt.

## What ran on this host beside the attempts

The preregistration said no other Docker or suite run would sit beside the attempts. That was not fully held, so it
is stated here:

- I ran single test files, not suites: `pytest service/tests/test_an_explicit_start_clears_a_stop.py` with two
  neighbours (about 3 s each), and `node --test` over three dashboard test files (about 1 s), between about 14:35Z and
  14:55Z.
- The guided-install S2 implementation, a delegated agent of mine, ran unit-level chain tests (19-20 tests) and
  targeted pytest and node files during the campaign. It ran no Docker and no full suite.
- dashboard-manager, another agent, ran a Docker sandbox of the install chain on this host during the campaign. That
  was a throwaway node container and a dashboard container. The time is not recorded here.

All three attempts passed regardless, so this adds load and does not explain a failure. It is recorded rather than
left out.

## Where things are

- `runs/q1.gate.txt`, `runs/q2.gate.txt`, `runs/q3.gate.txt`: each attempt's console. `runs/exits.txt` holds the
  start and exit times.
- `q1/`, `q2/` and `q3/`: each attempt's out dir, with `attempt.json` and one `evidence.json` per scenario.

## The gate owner's disposition of the deviation (2026-10-22)

The preregistration's quiet-host condition was not kept (above). Its effect on these results is not established: all
three passing does not show that the extra load changed nothing, and the Docker overlap's time is unknown.

As the gate's owner, I accept these results for one purpose only: landing the qualified source on comms main (SW5L).
That merge deploys nothing and is undone by reverting one merge commit.

They are not accepted as the deploy qualification. Before SW6 deploys SW5, one more set of three attempts runs on a
quiet host, under its own preregistration, on the same pair and apparatus (or on the merged main's exact tree if it is
identical), and it must be green under the same rule. The operator's time is not involved: the set runs in the
sandbox while the rest of 0.9.8 is built.
