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
