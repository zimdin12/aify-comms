# `mcp/stdio/pi-session.js` — decomposition proof packet

**Status:** submitted for ruling. Measured at `25e34e7e`. **No extraction performed.** This was the last of
the twelve target files never analysed; the reviewer's standing rule is that JS needs a reviewed packet
before anything moves, so this is measurement only.

**The conclusion is a hard number, and it is negative:** moving **every one** of the 19 top-level functions
out of this file — including its public API, which would be absurd — leaves **1,030 lines**. Relocation
cannot clear `pi-session.js`. It joins `app.js` and `hermes-managed-host.js` at the ceiling the reviewer
has already ruled on, and it gets there for a different reason than either.

---

## 1. Composition

| | lines | share |
|---|---|---|
| `class PiSession` (L269–L1228) | 960 | 74% |
| 19 top-level functions | 268 | 21% |
| imports, constants, comments, blanks | 71 | 5% |
| **total** | **1,299** | |

The class holds **36 methods / 915 lines**, leaving 45 lines of class overhead. The largest methods are
`_onStdoutLine` 89, `_onChildExit` 75, `ensureStarted` 52, `_maybeHealMissingSessionForTurn` 48,
`stop` 43, `_runTurnImpl` 42.

**Zero module-level `let`.** This file has no mutable module state at all — the contrast with `server.js`
(35) and the delivery loop is worth stating, because it means the usual blocker is absent here. What blocks
this file is that three quarters of it is one class, and a class is not relocatable in pieces without
changing `this`.

---

## 2. Why relocation cannot clear it

Simulated exactly rather than estimated: remove all 19 function declarations and the blank separators they
leave behind, and **1,030 lines remain**. The file is still over the threshold with nothing left to move
that is not the class.

And most of those 19 must not move regardless. `acquirePiSession`, `getPiSession`,
`shutdownAllPiSessions`, `__resetPiSessionPoolForTests`, `__piSessionPoolSize` and
`__piSessionPoolEntriesForTests` are the module's public API and its session-pool management — moving them
would relocate the file's identity, not decompose it.

---

## 3. The one coherent group that IS movable

Eight of the 19 are pi-event → terminal-text rendering, and they form a real subject:

| lines | function | exported | called from the class |
|---|---|---|---|
| 107 | `formatPiEventAsTerminalFrame` | yes | 1 |
| 19 | `formatToolResultBrief` | no | 0 |
| 17 | `briefJsonInline` | no | 0 |
| 11 | `formatTokenUsage` | no | 0 |
| 9 | `boundText` | no | 3 |
| 4 | `appendBounded` | no | 6 |
| 4 | `colorize` | no | 12 |
| 3 | `formatToolInputBrief` | no | 0 |

**~174 lines**, and they are the good kind of extraction: pure text transformation, already exercised
indirectly, and exactly the shape the `doctor-predicates.js` standard asks for — a module that EXPORTS what
it extracts, with real unit tests that call it. Four of them have no in-class caller at all, which means
their only current exercise is through a live pi session.

**It still does not clear the file:** 1,299 − ~174 − separators ≈ **1,109**. So this extraction is worth
doing on its own merits — testability of the rendering layer — and not as a route to the line target. Those
are different justifications and the packet should not blur them.

---

## 4. What clearing the file would actually require

Splitting `class PiSession`. The realistic shapes:

- **Extract a collaborator class** (e.g. the child-process lifecycle: `_spawnChild`, `_onChildExit`,
  `_teardownChild`, `ensureStarted` ≈ 200 lines) and hold it as a field. Bodies change — `this.x` becomes
  `this.child.x` — so this is NOT a byte-identical move and there is no JS equivalent of the Python
  extract-method inline-back gate to prove it.
- **Convert methods to free functions** taking the session as a parameter. Same objection, more of it.

Either is a behaviour-preserving reshape rather than a relocation, which is the category the reviewer has
already excluded from v0.5.x for `app.js` and `hermes-managed-host.js`. The consistent answer is that
`pi-session.js` is excluded on the same grounds.

**One caveat worth stating plainly:** the JS side has no inline-back gate. The Python method splits this
release were safe because `service/tests/extract_method.py` refuses what it cannot judge. A JS class split
would rest on the existing pi tests plus whatever characterization tests were written first, which is a
materially weaker net than the Python work had.

---

## 5. Risk

Host code: `pi-session.js` runs in the bridge, not the container, so anything here is inert until
`install.sh` is re-run and the wrappers relaunch. The persistent-RPC session lifecycle is the part of this
file with a live-incident history (the race in `PiSession.stop()`), and the child-process cluster is
precisely what a class split would touch.

---

## 6. Asking

1. Confirm `pi-session.js` is excluded from v0.5.x on the same grounds as `app.js` and
   `hermes-managed-host.js` — relocation provably cannot clear it (§2), and the only thing that can is a
   class split (§4).
2. Independently: is the ~174-line pi-event formatting module worth doing on TESTABILITY grounds, given it
   does not clear the file? I lean yes but would not start it inside a slice justified by line count, and
   four of those eight functions currently have no test that calls them directly.
