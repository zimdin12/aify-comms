# `apiBase` — the last leaf blocking app.js, and why it needs a ruling rather than a script

**Status:** decision packet. Nothing here is implemented. Measured 2026-08-14 against `app.js` at 4,761
lines.

## Where this sits

Three shared leaf names in `app.js` blocked every subject slice, because a module extracted from `app.js`
cannot import them back — that is the upward import this series forbids everywhere, and here it would also
be a cycle. Two are now resolved:

| leaf | resolution |
|---|---|
| `state` | `state.mjs`, with `state-identity.test.mjs` as the one-owner gate |
| `byId` | joined `ui.js`, the existing DOM-helper owner |
| **`apiBase`** | **open — this packet** |

With the first two done, twelve render groups became fully closed and the first of them
(`session-rail.mjs`, 110 lines) has shipped. `apiBase` gates the three largest remaining groups:

| seed | declarations | lines |
|---|---|---|
| `mountChatConsole` | 15 | 769 |
| `renderSessionConsole` | 13 | 724 |
| `mountXtermForTerminal` | 10 | 476 |

## Why it cannot just be moved

```js
const apiOrigin = resolveApiOrigin();          // reads location, localStorage, document — AT MODULE LOAD
const apiBase = `${apiOrigin}/api/v1`;
```

`resolveApiOrigin()` runs when the module is evaluated. A module holding these two declarations is
therefore **unimportable in Node** — and worse, every module that imported it would become unimportable
too, which would cascade straight through the testability the last three slices were bought for.
`session-rail.test.mjs` exists precisely because `session-rail.mjs` imports cleanly.

The harness already states this rule: *"every extracted module has NO module-scope browser globals"*, whose
message says such a module is "as unimportable as app.js and defeats the point of extracting into it".

Its check would not actually catch this — it scans depth-0 lines for *literal* browser globals, and
`resolveApiOrigin()` names none. **That is a hole in the check, not permission.** Passing on that
technicality is the move this series exists to stop.

## Why it cannot be made lazy without a ruling

The obvious fix — compute the base on first use — renames `apiBase` to `apiBase()` at its readers. There
are only five left in `app.js`:

```
 298  <a href="${apiBase}/shared/...">Download</a>
 424  await fetch(`${apiBase}${path}`, { ... })          ← the api() wrapper
2330  const url = `${apiBase}/agents/${...}/session-mode`
3862  await fetch(`${apiBase}/shared`, { method: 'POST', ... })
3865  const link = `${apiBase}/shared/${...}`
```

Five edits is nothing. The obstacle is the proof, not the work: `extraction-proof.test.mjs` reconstructs
`app.js` from the current file plus every extracted module and requires **byte-identity with the pristine
fixture**. Retained bodies cannot be edited at all, and a moved declaration must move unchanged. A lazy
`apiBase` violates both.

## The three options

**A. Declared substitution in the proof.** Extend the harness with a reviewed list of
`(before, after)` line pairs it reverts before comparing — the same shape as the existing `importWas`, and
as the declared-removal mechanism added for the hermes dead-import sweep, where the claim is *checked*
rather than merely tolerated. Cost: each declared edit weakens the proof slightly, and the mechanism will
be reused by whoever comes next. Benefit: `apiBase` becomes lazy, its module stays importable, and the
three large groups unblock.

**B. Accept one browser-only module.** Move the two declarations as they are into `api-origin.mjs` and
exempt that one path from the purity rule, the way `oversized-allowlist.json` carries explicit,
reason-bearing exemptions. Cost: that module and everything importing it are untestable in Node — which is
most of what remains. This looks cheapest and is probably the worst of the three.

**C. Leave `apiBase` in app.js.** The twelve fully-closed groups still ship (~600 unique lines). app.js
lands around 4,100–4,200 and stays on the allowlist with this measurement attached. The three large groups
wait for a render-flow decision that is out of v0.5.x scope anyway.

## Recommendation

**A**, with the substitution list kept to these two declarations and a test asserting the list is not
growing — the same discipline applied to the dead-import carve-out, which was pinned at a number, forced to
justify itself, and then deleted when the file was cleaned. It buys ~1,900 lines of unblocked subject work
and keeps every extracted module importable, which is the property that made the last three slices testable
for the first time.

C is the honest fallback and needs no decision; it is what happens by default if this packet is not ruled
on.

**B should be refused** unless someone wants to argue for it: it trades the one property that has made this
whole decomposition verifiable for the convenience of not touching five lines.

---

## SUPERSEDED 2026-08-14 — no ruling needed, options A/B/C are moot

**Do not act on the three options above.** Their shared premise is wrong: they assume unblocking means
making `apiBase` lazy and therefore renaming it to `apiBase()` at its readers, which the reconstruction
proof would forbid. Nothing needs renaming.

`apiBase` stays exactly as it is in app.js for its four direct URL builders. `api-client.mjs` keeps its
OWN binding, seeded once by app.js, and exports it as a live binding for the modules that build a URL
directly. Two mechanisms that already existed carry it:

* **`marker` accepts multiple lines and verifies each VERBATIM before splicing it out**, so the seeding
  call `setApiBase(apiBase);` rides in the slice's marker. `importLine` is executable code the harness
  already removes this way — the same contract, not a loosening.
* **`export let` is a live ESM binding**: importers see the seeded value and cannot assign back.

Shipped on this basis: `api-client.mjs`, `shared-files.mjs`, `api-origin.mjs`. app.js 3,838 -> 3,729,
reconstruction byte-identical throughout.
