# Version Awareness + Docs/Skills Cleanup — Implementation Plan

> Execute task-by-task; steps use `- [ ]`.

**Goal:** (1) Surface "N commits behind origin/main" for the 3 components (backend container, host
bridge, wrappers) — a WARNING, not auto-update. (2) Refresh docs/skills for the 2026-06-07 fix wave.

**Constraints (from research):**
- `.dockerignore` excludes `.git`, and the Dockerfile never `COPY`s it → **the container cannot run
  `git` against its own history**. The SHA must be STAMPED at build time into a file under `service/`
  (which IS COPY'd).
- It is a git repo (github.com/zimdin12/aify-comms), not npm. "Behind" via the GitHub compare API
  (`GET /repos/zimdin12/aify-comms/compare/<localSHA>...main` → `.behind_by`), cached (60 req/hr
  unauth limit). Offline/rate-limited → `behind=null`, UI shows "unavailable", never errors.
- Skill mirrors `.claude/skills/...` and `.agents/skills/...` are byte-identical — EVERY doc edit
  must be applied to BOTH (the `test_skill_mirror_parity` test enforces this).

**Decision (decided): WARNING, not auto-update.** The container has no `.git`/docker socket to rebuild
itself, and a self-mutating installer mid-session would kill live PTYs. Surface the behind-count +
the exact command to run.

---

### Task 1: Build-time SHA stamp (no Dockerfile/.dockerignore coupling)

**Files:** Create `scripts/stamp.sh`, `service/_build_stamp.json` (generated, git-ignored or committed
as `unknown`); Modify `service/config.py`, `redeploy.sh` (and document for `docker compose build`).

- [ ] **Step 1:** `scripts/stamp.sh` — writes `service/_build_stamp.json`:
  `{"sha": "<git rev-parse HEAD>", "short": "<...--short>", "branch": "<rev-parse --abbrev-ref HEAD>",
  "built_at": "<UTC ISO>"}`. Must be safe when run outside a git checkout (fall back to `unknown` /
  the env `GIT_SHA`). Because `service/` is COPY'd into the image, this lands in the container with no
  Dockerfile changes.
- [ ] **Step 2:** `redeploy.sh` (and README's build step): call `bash scripts/stamp.sh` BEFORE
  `docker compose up -d --build`. Add `service/_build_stamp.json` to `.gitignore` (it's per-build) and
  commit a placeholder `{"sha":"unknown",...}` so a fresh checkout/import still parses.
- [ ] **Step 3:** `service/config.py` — load `service/_build_stamp.json` at startup into config
  (`build_sha`, `build_short`, `build_branch`, `built_at`), env-overridable (`AIFY_BUILD_SHA` etc.),
  defaulting to `unknown` when absent. Keep the existing human `version` string.
- [ ] **Step 4:** `python -c "import ast; ast.parse(open('service/config.py').read())"`; commit.

---

### Task 2: `/version` endpoint with cached behind-count

**Files:** Modify `service/routers/health.py`, `service/main.py` (auth allowlist);
Test: `service/tests/test_version_endpoint.py`

- [ ] **Step 1: Write the failing test** — `GET /api/v1/version` (or `/version`, match the health
  router's prefix) returns 200 with `name`, `version`, `sha`, `sha_short`, `branch`, `built_at`, and an
  `update` object (`behind_by` may be null). Patch the GitHub call so the test never hits the network
  (inject the comparer / monkeypatch); assert `behind_by` reflects the stub and that a network failure
  yields `update: {behind_by: null, source: "...", stale: true}` (never raises). Run → FAIL.
- [ ] **Step 2: Implement** `GET /version` in `health.py`: return the stamped fields + an `update` block
  from a module-level cached `_check_update()` (TTL ~20 min) that calls the GitHub compare API for
  `<sha>...main` and parses `behind_by`/`ahead_by`/`status`. Wrap all network in try/except → null on
  any failure (offline, 403 rate-limited, 404 unknown sha). Make the comparer injectable for the test.
- [ ] **Step 3:** Add `/version` to the unauth `skip_paths` allowlist in `service/main.py:31` (next to
  `/health`).
- [ ] **Step 4:** Run the test → PASS; ast-parse; commit.

---

### Task 3: Surface the version + behind-count in the dashboard header

**Files:** `service/dashboard.html`

- [ ] **Step 1:** On load (and on the existing refresh loop), `fetch('/api/v1/version')`; render a small
  badge near the title (`:6` area / the header bar): `v<version> · <sha_short>`. When
  `update.behind_by > 0`, swap to a warning pill: `⚠ <n> commits behind — run git pull && ./redeploy.sh`.
  When `update.behind_by === null`, show nothing extra (or a muted "update status unavailable" on hover).
- [ ] **Step 2:** Commit.

---

### Task 4: Host-side stamp + `aify-comms --version`

**Files:** `install.sh`

- [ ] **Step 1:** In `copy_bridge_to_native_dir()` (~`:175`), after the bridge copy, write
  `$AIFY_NATIVE_BASE/.aify-version` with `sha`/`short`/`branch`/`date` from
  `git -C "$SCRIPT_DIR" rev-parse HEAD` etc. (`SCRIPT_DIR` is the repo checkout WITH `.git`). Guard when
  git is unavailable (write `unknown`). Honor the user memory rule: a host `--version` check should
  `git fetch` fresh before counting behind.
- [ ] **Step 2:** In the `aify-comms` wrapper `--help`/arg block (~`:2410`), add a `--version` branch:
  print the `.aify-version` stamp, then `git -C "$SCRIPT_DIR" fetch -q origin main 2>/dev/null` +
  `git rev-list --count <local>..origin/main` for the host "N behind", and `curl -s
  $AIFY_COMMS_URL/api/v1/version` for the backend SHA/behind. Keep failures silent (offline-safe).
- [ ] **Step 3:** `bash -n install.sh` (and the PS1 heredoc stays parity-safe — no PS1 change needed
  here, host `--version` is bash-wrapper only); commit. NOTE: install.sh changes require re-running
  `install.sh` to take effect — the operator does this when testing.

---

### Task 5: Docs/skills cleanup for the 2026-06-07 wave

Apply to DECISIONS.md / KNOWN_ISSUES.md AND both skill mirrors (`.claude/skills/...` +
`.agents/skills/...`) — keep them byte-identical.

- [ ] **Step 1 (P1):** `references/status.md` — add the missing **`blocked`** row to the canonical
  status table (it lists only 7; `blocked` is the 8th: "Active run + terminal tail looks like it needs
  operator input/a decision"). Mirror to `.agents`.
- [ ] **Step 2 (P1):** `references/status.md` — add a symptom entry: "Managed claude showed `blocked`
  mid-generation" → the spinner-footer short-circuit (a live `✻ … esc to interrupt` / `✻ <verb> for
  <N>s` footer means generating, not awaiting input; decision-flavored subagent/Task prose no longer
  fires `blocked`). (commit `4ef1db3`.) Mirror.
- [ ] **Step 3 (P1):** `references/dispatch-bridge.md` + a DECISIONS.md note — provider **rate-limit
  sender notice** (`11e7a5a`): a failed run whose error is a provider throttle (Anthropic "temporarily
  limiting requests" / "hit your limit", 429/529, overloaded) now delivers the sender a clear
  "retry shortly — provider rate-limiting, not your request" message, not a raw API error. Mirror.
- [ ] **Step 4 (P2):** `references/lifecycle.md` — Stop/Restart/Reset/cli_takeover now **synchronously
  kill** the live managed PTY (session-control enqueues a terminal stop; `TERMINAL_MANAGER.stop()`
  escalates SIGTERM→SIGKILL), no longer leaving a headless orphan for the 60s reaper (`8ef31a2`).
  Amend the orphan entries in `status.md` (lines ~150/232). Mirror.
- [ ] **Step 5 (P2):** DECISIONS.md — terminal churn fix + `terminal_controls` 24h retention
  (`4ef1db3`/`d0a9b35`): "output age ≠ liveness; the terminal owner is retained while the env bridge is
  live"; add `terminal_controls` to the retention-TTL enumeration. KNOWN_ISSUES: note retention now
  covers terminal_controls.
- [ ] **Step 6 (P3):** `references/dashboard-console.md` — console seed glitch fix (`4a0bfb8`):
  the 64KB buffer trims at a clean line boundary so a fresh xterm seed no longer starts on a broken
  ANSI escape → on-screen garbage. Mirror.
- [ ] **Step 7:** Run `service/tests/test_skill_mirror_parity.py` (host or container) to confirm the
  `.claude`/`.agents` trees are still byte-identical. Commit docs.

---

## Self-Review
- **Spec coverage:** behind-count for all 3 components (container stamp+/version Task 1-2; host bridge
  + wrappers Task 4); dashboard surfacing Task 3; docs/skills cleanup Task 5. Auto-update consciously
  declined (warning instead) per the container/.git constraint.
- **Risk:** all additive. The stamp file is generated (gitignored + placeholder). `/version` never
  raises (null on any network failure). install.sh `--version` is offline-safe and bash-only (PS1
  parity untouched). Docs are additive + mirror-parity-tested.
- **No placeholders:** exact files/anchors, the GitHub compare shape, and the mirror requirement are
  all concrete.
