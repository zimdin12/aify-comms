# Plan — CLI status view + dashboard upgrades (NOT STARTED)

**Status:** planned, deliberately not implemented (operator: *"save that as cli bridge upgrade plan, but let's not do it yet"*).
**Raised:** 2026-07-14, after a day in which every failure was invisible from the dashboard and had to be dug out of `/proc`, process start times, and transcripts.

Four items. (1) is the operator's main ask; (2)–(4) are the concrete UI defects found alongside it. Each records what was **verified**, so nobody has to re-derive it.

---

## 1. `aify status` — a real CLI status/debug view

**Is it a good idea? Yes — and today is the argument for it.** Every bug we hit was invisible in the dashboard *and* in the database, and only provable from the host: a bridge process running code from before the last install; an agent registered with no `AIFY_AGENT_ID` in its process env; a console rendering from a truncated log. The DB says what was *reported*; the host says what is *true*. A CLI view is the only place both can be shown side by side.

`aify-doctor` (shipped 2026-07-14) is the seed: it already proves service build vs repo HEAD, installed-vs-running bridge code, anonymous agent sessions, env bridges, wrappers, runtimes, OpenAI usage. **`aify status` is the fleet view of the same discipline** — doctor answers "is my install real?", status answers "what is my fleet doing, right now, and where?".

### Shape

```
$ aify status                      # one-screen fleet summary
$ aify status --watch              # live refresh (the "TUI" mode)
$ aify status <agent>              # one agent: process, bridge, session, turn state, last events
$ aify status --json               # machine-readable (agents parse this)
$ aify status --stale              # only what needs attention (default in --watch footer)
```

Table columns (each cell must be provable, not inferred):

| column | source of truth |
|---|---|
| agent / role | service |
| runtime · mode | service |
| status | derived status **plus** the process truth behind it (in_turn, last turn event age) |
| where | machine id · **tty** · pid · cwd — from the host, so "where does it run" is answerable at a glance |
| bridge | pid + started-at, and **STALE** if started before the last `install.sh` (the thing that silently swallowed fixes all day) |
| identity | ✗ when the process carries no `AIFY_AGENT_ID` but the agent is registered (status structurally dead) |
| session | handle + whether its transcript/gateway is live |
| unread / last msg | service |

Group by machine, then by environment. Colour only for state, never decoration. `--json` mirrors it exactly.

### Notes
- Reuse `doctor.js`'s process-truth helpers (`/proc` env, start times, binding files) — do not re-invent them.
- Non-Linux hosts must degrade to service-only data with an explicit `(process inspection unavailable)` note, never a silent blank.
- This belongs in the bridge (`mcp/stdio/`) and installs as a launcher, exactly like `aify-doctor`.

---

## 2. The dashboard console cannot be scrolled — and why

**Verified cause, two parts:**

1. **There is no history to scroll to.** The console is seeded with a *screen snapshot* — the CURRENT 28-row screen, reconstructed by pyte. It is not a log. xterm's scrollback (`scrollback: 5000`, `app.js:1701` — so it *is* configured) only fills with output that arrives **after** you attach. Worse, claude's TUI repaints in place rather than emitting new lines, so even live output mostly rewrites the same rows instead of pushing anything into scrollback.
2. **`.console-wide-mirror { overflow-y: hidden }`** (`styles.css:910`) actively kills vertical scrolling whenever the mirror is wider than the pane — which is the common case for a resident/wide console.

**Fix (now cheap, because the live screen already exists):** the live screen introduced on 2026-07-14 (`terminal_snapshot.py`, one persistent screen per terminal fed every chunk) can use **`pyte.HistoryScreen`** instead of `pyte.Screen`. That keeps real scrollback server-side; the snapshot then ships N lines of history above the current screen and xterm scrolls naturally. Plus: drop `overflow-y: hidden` from `.console-wide-mirror` (keep `overflow-x: auto`).

Ordering matters: do the HistoryScreen work first — un-hiding the scrollbar with nothing to scroll to just moves the confusion.

---

## 3. Analytics: the range selector is too coarse, and half the page ignores it

**Verified:**
- Only **four** ranges exist: `24h`, `30d`, `12m`, `All` (`analytics.js:70-75`). No 1h, 6h, 7d, 90d; no custom window.
- The range **is** honoured by `/analytics` — dispatch runs, spawn requests, message counts and the traffic series all slice by it (`api_v2.py:21551-21571`).
- The range is **NOT** honoured by `/usage` or `/usage/consumption` — neither endpoint takes a range param (`app.js:1056-1057`), so the quota and consumption blocks always show "now", whatever the selector says. That is the operator's "it only affects the first block".

**Fix:**
- Ranges: `1h · 6h · 24h · 7d · 30d · 90d · 12m · All` + a custom from/to. Requires new server buckets (`messagesPer5Min`/`PerHour` windows) — the current series are precomputed per bucket, so a finer range needs a finer series, not just a filter.
- Thread the range through `/usage/consumption` (consumption is per-agent token totals — it *should* be windowed) and make the usage-pool block state plainly that quota is a **point-in-time** reading, not a range (it genuinely is — do not fake a series for it).
- Any block that cannot honour the range must SAY so, rather than silently showing all-time data under a "24h" label.

---

## 4. A cold managed agent offers no way to start it (the hermes complaint)

**Verified:** hermes cold-start is **not** broken — spawn requests for `acma-coder` / `lca-coder` / `lc-coder` were created and ran as recently as 19:27 on 2026-07-14. The gap is purely UI: the Console tab bails at `if (!session)` (`app.js:257`) and renders "no active session — send a message and it will lazy-start". The start buttons (`canStartConsole` / `canStartDeadSession`, `app.js:2315-2324`) live **below** that early return, so an agent with no session row can never reach them.

**Fix:** in that empty state, offer **Start agent** for a managed agent with a runtime (fresh start when there is no saved handle; resume when there is) — the same actions the session path already exposes. It is a small change, but it is the difference between "why can't I start hermes models?" and a button.

---

## 5. Learn from hermes' in-browser TUI (operator ask, mis-implemented once already)

The operator asked for hermes' in-browser TUI to be **studied** — it renders a terminal in a browser and, in their words, *"actually works perfectly"*. That request was previously implemented as *"embed the hermes page in our Console tab"*, which is not what was asked and actively hijacked the tab (removed 2026-07-14). The in-tree comments even credited that embed to an operator request; the operator states they never made it. Attribution corrected.

The actual work: look at how hermes renders a live terminal in a browser (transport, redraw model, scrollback, resize) and take what makes ours better. Our console has needed three separate fixes in one day — a truncated-log replay that could not reconstruct the screen, no scrollback, and a reset that wiped what scrollback there was. Theirs reportedly has none of these problems. Read it before designing more of ours.

## Sequencing

1. **(4)** — smallest, unblocks the operator immediately.
2. **(2)** — HistoryScreen + the CSS; makes consoles genuinely usable.
3. **(1)** — `aify status`, reusing doctor's process-truth helpers.
4. **(3)** — analytics ranges (needs new server-side buckets; largest).
