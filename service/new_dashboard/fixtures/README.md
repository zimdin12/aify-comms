# Browser fixtures — the half of the dashboard that Node cannot judge

Some dashboard behaviour only exists in a browser: layout, visibility, `getComputedStyle`, and above
all **DOMPurify**, which needs a real DOM and reports `isSupported === false` under `node --test`.
A unit test of a function that sanitizes therefore exercises its no-DOM ESCAPE FALLBACK and nothing
else — the branch that does not sanitize — while reading as coverage of the formatter.

These fixtures are that missing half. **No suite runs them**, and that is a real gap rather than a
convention: they need a browser, and this repo's five gates are Node and pytest.

## `messenger-browser.mjs` — run it like this

It is an ES module, so it must be served rather than opened from disk, and `.mjs` must arrive with a
JavaScript MIME type. Python's `http.server` sends `text/plain` and the browser refuses the module,
which is the first thing that will waste your time.

```bash
# any static server that types .mjs correctly; this one is two lines of node
cd service/new_dashboard
npx --yes serve -l 8899 .        # or any server sending text/javascript for .mjs
# then open http://127.0.0.1:8899/fixtures/messenger.html and run, in the console:
await window.runTests()
```

`runTests()` resolves to one `{name, ok, error}` per check. **A cached bad response survives a
normal reload** — if the module "does not load", hard-reload ignoring cache before believing it.

## What it covers, and what it proved

Fifteen checks: read-receipt timing against real layout and visibility, the bounded oldest-unread
history search, failure and retry behaviour, and **nine XSS vectors** — `<script>`, `onerror`,
`<svg onload>`, `<iframe srcdoc>`, `<style>`, an entity-encoded `jav&#x61;script:` href, a markdown
`[bad](javascript:)` link, a `data:text/html` href, and the `<math><mtext><table><mglyph><style>`
mutation-XSS clobbering payload. It also asserts no element carries an `on*`, `style`, `id` or
`data-*` attribute, because this dashboard dispatches clicks through one delegated handler keyed on
`data-` attributes — so a message body able to carry one would be planting a control under the
operator's cursor rather than styling itself.

**MEASURED 2026-09-11, Chrome 152, against the live modules: 15 of 15 passed.** Before that date
nothing had ever run this file; its only reference anywhere in the repo was a comment in
`message-format.test.mjs` pointing at it.

**And the green was negative-controlled rather than trusted.** With `ALLOW_DATA_ATTR` flipped to
`true` in `message-format.mjs` and nothing else changed, the suite went to 14 of 15 and the failure
named the exact vector: `unsafe attr data-chat-view`. Restored byte-exact with `cp`, and back to
15 of 15. So these checks fail when the policy is weakened, which is the only evidence that their
passing means anything.

## The standing limit

Running this is a manual act, so it can be skipped and nobody is told. `message-format.test.mjs`
gates the POLICY in Node — that the options handed to DOMPurify are still the strict ones, which is
the realistic regression — and that gate runs every time. This file proves the policy is actually
enforced, and it runs when someone remembers. Treat a change to `message-format.mjs`, the vendored
sanitizer, or `chat-render.mjs`'s body interpolation as a reason to run it.
