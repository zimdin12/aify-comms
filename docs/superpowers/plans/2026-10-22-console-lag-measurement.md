# Browser console lag: what is known, and the measurement before any fix (2026-10-22)

The operator reported "our browser terminal kind of lags sometimes". This page records the state as read on
2026-10-22 and the measurement that decides the fix. Nothing on it is a fresh measurement.

## Done before

- v0.6.3 (5d6fbabe, then 815ea767..93d9054b): the stream counted posts where the browser counts frames, so the
  console saw false gaps and resynced. Fixed. Then 286 minutes of observation in six windows: 0 gaps in 106,676
  sequence comparisons. Frames reached a browser socket at a median of 383 ms apart in one hour and 237 ms in the
  next. The lag itself was never attributed to a hop.
- 2026-10-13 audit: the service spent 20-35% of a core on about 34 output posts per second. aify-env 179e63e capped
  posting at one per 100 ms per terminal.
- No transport commit since.

## The output path on main

1. aify-env `lib/plugins/aify-comms/output-sender.mjs:33`: a 100 ms window per terminal, one post in flight, so a slow post delays what is
   behind it.
2. comms `service/terminal_write_queue.py:76`: flushes after 4 ms idle, 24 ms at most.
3. comms `service/api_core/terminal_output.py:101`: each flush runs a synchronous screen update for every terminal, watched or not,
   and a database write, on the single event loop.
4. comms `service/ws.py:39`: every terminal's output goes to every open tab. The browser discards the others
   (`service/new_dashboard/realtime-socket.mjs:248`) and writes to xterm.

Input: keystrokes reach aify-env at once (`service/change_feed.py:76`). Whether a slow command ahead of a keystroke delays
it is unchecked.

## Suspects, in order (not measured)

1. Under load, the per-terminal screen update and the database write block the event loop, and the waits stack
   behind aify-env's 100 ms window. That fits lag that comes and goes.
2. The fixed 100 ms window.
3. Every tab receiving every terminal's output.

## The measurement

- **Where:** the sandbox pair from `scripts/sandbox/gate` (on 098/sw3d), with a headless browser on its network.
  Never the operator's team.
- **When:** on a quiet host, after the builders' suites and around the SW6 quiet-host set, never beside it.
- **Load levels:** a quiet agent; a 30-per-second spinner; nine busy terminals with one watched. 200 keystrokes
  each. Three preregistered attempts.
- **Instrument:** a marker timestamped at each hop, behind a flag that is off by default.
  - Negative control: with the flag off, no timing lines appear.
  - Positive control: an injected 50 ms delay shows up in the right hop and in no other.
- **Decision:** fix the hop holding the largest share of the slow cases. If no hop holds a majority, report that
  instead of choosing one.

## First cut, 2026-10-25: the comms leg is not where the lag is

This was measured in a disposable service pair: the same image, an empty DB, a synthetic key. A script posted the way
aify-env posts (one in flight per terminal, at least 100 ms apart), and two websocket clients acted as tabs, with
timestamps carried inside the output.

- **Comms leg** (POST start to frame on a socket, p95). L1 quiet: 9-10 ms. L2 spinner: 9 ms. L3 nine busy terminals
  (about 100 posts/s, 3x the live rate measured on 10-13): 33-42 ms. That meets the pre-registered "comms is not the lag"
  bound (p95 ≤ 50 ms at L3 in all three attempts).
  - Positive control: with the CPUs saturated, L2 rose from 9 to 47 ms.
- **Browser.** The real Dashboard Next ran in a headless Chromium on that sandbox, socket connected, with every
  terminal's frames passing through realtime-socket. Main-thread drift stayed at timer granularity (max 16 ms) at L3.
  - Positive control: a 120 ms block showed as 113 ms of drift.
  - The long-task observer is blind in that browser, so its zeros are not evidence.
  - No console was mounted, so the xterm write was not measured.
- **aify-env.** The 100 ms window is leading-edge: an echo into an idle terminal posts at once, and one into a
  terminal that is already streaming waits up to 100 ms.

No hop holds the slow cases here, so no fix is chosen. A reproduction now needs what the sandbox lacks: the live host
under real load, the live DB's size, a mounted console, and the operator's browser. Next would be a timestamped run on
the live console while the operator reports a lag, if they want one. Probe, raw results and controls:
~/reviews/aify/console-lag-2026-10-25/ (README.md pre-registered the decision before any result).
