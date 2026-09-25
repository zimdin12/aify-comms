# aify-comms troubleshooting: Dashboard console-mode & Console UX

Symptoms here that persist after a hard browser reload mostly mean the running container predates
the fix: compare `aify-comms doctor` `service` against the checkout before anything else.

## `database is locked` (503) in the dashboard

The live-status cache is an in-memory dict (`reconcilers/status_cache.py`), so the poll path writes
nothing, and `GET` list endpoints are pure reads (their repairs run on the 60s reconcile loop). A
lock now means real write contention: `service/main.py` logs `SLOW-REQ` / `DB-LOCK` lines that name
the request. The cache is process-global, so the service must stay single-worker: more uvicorn
workers bring this class of failure back.

## Console text scrambled, flickering, or garbage on attach

- **Live stream:** the service alone numbers output (`outputSeq`). Numbered output goes through
  `TERMINAL_OUTPUT_WRITES` as one ordered broadcast; `append_outside_the_queue` writes unnumbered
  output and clears the sequence, so the browser replays rather than trusting a stale number. New
  output paths must do one or the other.
- **Attach/refresh:** the dashboard paints a server-rendered screen, not the raw log:
  `GET /terminals/{id}?cols=&rows=` returns a `snapshot` rendered by `service/terminal_snapshot.py`
  at the viewer's size. A console that still scrambles after a hard reload is on a stale service.

## Environment does not advertise terminal support

**Symptom.** `/api/v1/environments` shows `terminal=false` / `pty=false`, or Start Console says
*"Environment <id> does not advertise terminal support for claude-code"*, and channel dispatches to
a managed claude sit `queued`.

aify-env owns every PTY and advertises the host. It lists a runtime in `terminalRuntimes` only when
that runtime's binary resolves on the PATH aify-env was started with, and sets `terminal` from its
own PTY check. Ask `aify-env doctor` on that host: its terminal row and the runtime's
`not found on PATH` reason say which. Report what it says; repairing the host and restarting
aify-env are the operator's (a restart reaps its managed workers).

Channel delivery still needs a PTY: `claude-channel.js` runs inside the `claude-aify` wrapper, so
with no wrapper PTY nothing claims. Workaround meanwhile: launch a resident
`claude-aify --aify-agent <id>` on any machine where claude resolves; its channel sidecar polls the
service over HTTP and claims directly.

## Console shows the wrong terminal, or none

- **Old managed xterm after switching to resident:** the Console uses a managed terminal only while
  the identity is not `resident` and the terminal is live. Switch back to managed before expecting
  dashboard-typed turns to reach the managed PTY.
- **Start Console opens a second wrapper:** `start_session_console` (`routers/session_console.py`)
  reuses a live terminal and answers `reused: true`; a sibling PTY means a stale service.
- **`session does not exist` on open:** the browser held a session id from before a rebuild or
  re-register. Reload the page and start the Console again.
- **Statuses look wrong everywhere:** see status-model.md; every badge comes from `derive()`.

## Can't copy text out of a Console

Over plain `http://`, `navigator.clipboard` is undefined, and a TUI that captures the mouse eats
plain drags. Three ways that work:
- the **Copy** button on the Console toolbar (the selection, or the whole buffer when none),
- **Ctrl+Shift+C** for the current selection,
- **Shift+drag** to select while the TUI captures the mouse.

## Before rebuilding the service

The image copies the working tree, not git HEAD, so a syntax error mid-edit bakes a crash-looping
container. Byte-compile what you changed (`python -m py_compile <files>`) and run the suites first;
recover by rebuilding from a known-green commit.
