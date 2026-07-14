#!/bin/bash
# Unified installer for aify-comms on Claude Code, Codex, or Hermes.
# Pi/OMP managed delivery uses the environment bridge plus plain `omp --mode rpc`;
# resident `omp-aify` / `pi-aify` wrapper install is disabled by default because
# OMP is single-client and cannot receive live wake injection into an open TUI.
# OpenCode install is also disabled by default until its resident/managed
# integration gets a focused validation pass.
#
# Usage:
#   bash install.sh --client claude
#   bash install.sh --client codex
#   bash install.sh --client codex http://localhost:8800 --with-hook
#   bash install.sh --client hermes http://localhost:8800 --with-hook

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Native client-install location for the host-side MCP bridge runtime. The repo
# may sit on a slow filesystem (e.g. a WSL2 9p Docker bind-mount, where reading
# the ~3900 node_modules files cold takes ~5s — which blows hermes' hardcoded
# 0.75s MCP tool-discovery window so its comms_* tools never enter the model's
# schema). install.sh therefore COPIES the bridge (mcp/stdio + node_modules)
# into a native dotfolder and bakes THAT path into every wrapper + MCP config —
# a proper client install (like ~/.claude, ~/.codex, ~/.hermes), self-contained
# (works with no repo / no local backend), and re-synced on each install so
# security fixes still flow. Override the base with AIFY_HOME.
AIFY_NATIVE_BASE="${AIFY_HOME:-$HOME/.aify-comms}"
AIFY_BRIDGE_DIR="$AIFY_NATIVE_BASE/mcp/stdio"
CLIENT="claude"
SERVER_URL=""
WITH_HOOK=false
# Plan 5 (2026-05-25): --prebuild-dry-run exits after running the hermes
# web_dist prebuild branch (no npm invocation, no wrapper writes). Used by
# service/tests/test_install_hermes_prebuild.py to verify the branch's
# detection logic without touching the operator's environment.
PREBUILD_DRY_RUN=false
# Test hook (WS1 Task 1.5): --emit-hermes-wrappers <dir> generates ONLY the
# hermes-aify bash + PowerShell wrappers into <dir> and exits, touching nothing
# in the operator's environment and launching no npm/hermes/loop. Used by
# service/tests/test_install_hermes_loop_gate.py to inspect the generated text.
EMIT_HERMES_WRAPPERS_DIR=""
# Test hook (FIX 7, 2026-06-03): --emit-codex-wrappers <dir> mirrors the hermes
# emit hook — generates ONLY the codex-aify wrapper into <dir> and exits, so the
# codex bypass flag (CODEX_AUTO / --dangerously-bypass-approvals-and-sandbox) can
# be asserted deterministically and can't silently drop. Used by
# mcp/stdio/tests/codex-wrapper-determinism.test.js.
EMIT_CODEX_WRAPPERS_DIR=""
DEFAULT_AIFY_SERVER_URL="${AIFY_DEFAULT_SERVER_URL:-http://127.0.0.1:8800}"

usage() {
  cat <<EOF
Usage:
  bash install.sh --client <claude|codex|hermes> [SERVER_URL] [--with-hook]

Examples:
  bash install.sh --client claude
  bash install.sh --client claude http://localhost:8800 --with-hook
  bash install.sh --client codex http://localhost:8800
  bash install.sh --client hermes http://localhost:8800 --with-hook

Pi/OMP note:
  --client pi is intentionally disabled. Managed Pi works through the
  environment bridge's persistent `omp --mode rpc` child; resident
  `omp-aify` / `pi-aify` wrappers are presence-only and not installed by
  default because OMP has no multi-client resident wake surface.

OpenCode note:
  --client opencode is intentionally disabled until the integration gets a
  focused validation pass. Existing adapter code remains for future work.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --client)
      CLIENT="${2:-}"
      shift 2
      ;;
    --with-hook)
      WITH_HOOK=true
      shift
      ;;
    --prebuild-dry-run)
      PREBUILD_DRY_RUN=true
      shift
      ;;
    --emit-hermes-wrappers)
      EMIT_HERMES_WRAPPERS_DIR="${2:-}"
      shift 2
      ;;
    --emit-codex-wrappers)
      EMIT_CODEX_WRAPPERS_DIR="${2:-}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    http*)
      SERVER_URL="$1"
      shift
      ;;
    *)
      echo "Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

# Default the server URL when none was passed positionally. Without this, a
# documented invocation like `install.sh --client claude` (no URL) left
# SERVER_URL empty, which made the claude gating below run remove_claude_wrapper
# and DELETE ~/.local/bin/claude-aify even though the install otherwise printed
# "Installation complete" — the live incident: claude-aify missing post-install
# while codex-aify/hermes-aify were present. The hermes wrapper already defaulted
# via DEFAULT_AIFY_SERVER_URL (see default_server in install_hermes_wrapper), so
# claude was the inconsistent path. Apply the same default here so every client
# gets a usable URL and the claude wrapper is installed (not removed).
if [ -z "$SERVER_URL" ]; then
  SERVER_URL="$DEFAULT_AIFY_SERVER_URL"
fi

if [ "$CLIENT" = "pi" ]; then
  echo "Pi/OMP resident wrapper install is disabled."
  echo "Managed Pi remains supported through the environment bridge using plain 'omp --mode rpc'."
  echo "Reason: OMP is single-client, so omp-aify/pi-aify cannot provide live resident wake into an open TUI."
  exit 1
fi

if [ "$CLIENT" = "opencode" ]; then
  echo "OpenCode integration install is disabled until it receives a focused validation pass."
  echo "Existing OpenCode adapter/controller code remains in the repo for future work."
  exit 1
fi

if [ "$CLIENT" != "claude" ] && [ "$CLIENT" != "codex" ] && [ "$CLIENT" != "hermes" ]; then
  echo "Unsupported client: $CLIENT"
  usage
  exit 1
fi

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1"
    exit 1
  fi
}

hermes_cmd() {
  local configured="${AIFY_HERMES_COMMAND:-${HERMES_COMMAND:-}}"
  if [ -n "$configured" ] && command -v "$configured" >/dev/null 2>&1; then
    printf '%s\n' "$configured"
    return 0
  fi
  # Stale AIFY_HERMES_COMMAND tolerance: fall through to PATH instead of
  # exiting, since the operator's env may still point at a vanished
  # hermes.exe (e.g. hermes' 2026-05-27 release rotated binaries).
  # NOTE: do NOT probe `hermes-agent` here. It's a separate hermes entry
  # point (headless agent loop) and does not implement `dashboard --tui`,
  # so accepting it would silently break the wrapper.
  command -v hermes 2>/dev/null
}

resolve_hermes_real_bin() {
  # Task #174: resolve the hermes launcher to its REAL file. `command -v`
  # output may be a symlink (~/.local/bin/hermes -> .../venv/bin/hermes) or a
  # pipx entry script; install-root detection needs the real location so the
  # venv python next to it can be found. Chain: command -v -> readlink -f ->
  # manual readlink loop (stock macOS readlink lacks -f). Never fails: prints
  # the best-resolved path, or the input unchanged, so the plain-PATH
  # executable case behaves exactly as before.
  local candidate="${1:-}"
  [ -z "$candidate" ] && return 0
  local resolved
  resolved="$(command -v "$candidate" 2>/dev/null || printf '%s\n' "$candidate")"
  if command -v readlink >/dev/null 2>&1; then
    local canonical=""
    canonical="$(readlink -f "$resolved" 2>/dev/null || true)"
    if [ -z "$canonical" ]; then
      # readlink without -f support: follow the symlink chain manually.
      canonical="$resolved"
      local hops=0 target=""
      while [ -L "$canonical" ] && [ "$hops" -lt 10 ]; do
        target="$(readlink "$canonical" 2>/dev/null || true)"
        [ -z "$target" ] && break
        case "$target" in
          /*) canonical="$target" ;;
          *) canonical="$(dirname "$canonical")/$target" ;;
        esac
        hops=$((hops + 1))
      done
    fi
    [ -n "$canonical" ] && [ -e "$canonical" ] && resolved="$canonical"
  fi
  printf '%s\n' "$resolved"
}

hermes_shebang_python() {
  # Task #174: pipx / venv entry-point scripts carry the venv python in their
  # shebang (e.g. #!/home/u/.local/pipx/venvs/hermes-agent/bin/python). That
  # interpreter IS the hermes venv python, so surface it directly. Only
  # accepts interpreter paths that are themselves a python (never
  # /usr/bin/env — a bare `env python3` shebang points at the SYSTEM python,
  # which does not have hermes_cli installed). Best-effort: prints nothing on
  # any miss.
  local script="${1:-}"
  { [ -n "$script" ] && [ -f "$script" ]; } || return 0
  local first_line=""
  first_line="$(head -n 1 "$script" 2>/dev/null | tr -d '\r')"
  case "$first_line" in
    '#!'*)
      local interp="${first_line#\#!}"
      # Trim leading whitespace, then drop shebang args after the interpreter.
      interp="${interp#"${interp%%[![:space:]]*}"}"
      interp="${interp%% *}"
      case "$interp" in
        */python*)
          [ -x "$interp" ] && printf '%s\n' "$interp"
          ;;
      esac
      ;;
  esac
  return 0
}

require_hermes_cmd() {
  if ! hermes_cmd >/dev/null 2>&1; then
    echo "Missing required command: hermes"
    echo "Set AIFY_HERMES_COMMAND to the Hermes 'hermes' executable path if Hermes is not on PATH."
    echo "Note: hermes-agent / hermes-acp are NOT acceptable substitutes — they do not implement 'dashboard --tui'."
    echo "If hermes' 2026-05-27 release rotated your binary, reinstall hermes upstream so 'hermes' is recreated."
    exit 1
  fi
}

copy_bridge_to_native_dir() {
  # Mirror the host-side bridge runtime (server.js + all bridges + node_modules)
  # from the repo into the native ext4 dotfolder $AIFY_BRIDGE_DIR. Spawns from a
  # native fs load in ~0.3s vs ~5s over a 9p bind-mount, so hermes' 0.75s
  # MCP-discovery window is met and its comms_* tools reach the model. Re-synced
  # every install (exact mirror) so fixes flow; self-contained for repo-less
  # clients. Skip the copy if the repo IS already the native dir (dev on ext4).
  local src="$SCRIPT_DIR/mcp/stdio"
  if [ "$(cd "$src" 2>/dev/null && pwd -P)" = "$(cd "$AIFY_BRIDGE_DIR" 2>/dev/null && pwd -P 2>/dev/null)" ] && [ -d "$AIFY_BRIDGE_DIR" ]; then
    echo "  Bridge already at native dir ($AIFY_BRIDGE_DIR); skipping copy."
    return 0
  fi
  mkdir -p "$AIFY_NATIVE_BASE/mcp"
  if command -v rsync >/dev/null 2>&1; then
    # Linux / WSL / macOS: rsync -a preserves symlinks (node_modules/.bin shims)
    # natively. This is the path WSL+Mac take.
    rsync -a --delete "$src/" "$AIFY_BRIDGE_DIR/"
  else
    # Windows Git-Bash / MSYS (no rsync): `cp` cannot recreate POSIX symlinks
    # without symlink privilege (git core.symlinks=false, no winsymlinks), so a
    # plain `cp -R` of node_modules ABORTS the whole install under `set -e`.
    # Additionally an orphaned npm atomic-install temp symlink (e.g. a dangling
    # .bin/.pkg-XXXXXX) makes even `cp -L` fail because its target is gone.
    # Make the fallback cross-platform-robust: (1) prune ONLY dangling symlinks
    # (broken cruft — the real file beside them remains), then (2) DEREFERENCE
    # with -L so symlink targets are copied as plain files and no symlink is ever
    # created. node_modules/.bin shims are the only symlinks and the bridge runtime
    # never uses them, so copying their targets as files is functionally identical.
    rm -rf "$AIFY_BRIDGE_DIR"
    mkdir -p "$AIFY_BRIDGE_DIR"
    find "$src" -type l ! -exec test -e {} \; -exec rm -f {} \; 2>/dev/null || true
    cp -RL "$src/." "$AIFY_BRIDGE_DIR/"
  fi
  echo "  Bridge runtime installed to $AIFY_BRIDGE_DIR (native, fast load)."

  # Stamp the installed host bridge with the repo's git identity ($SCRIPT_DIR is
  # the repo checkout and HAS .git; the native copy does not). `aify-comms
  # --version` reads this to report the installed host bridge SHA and to compute
  # "N commits behind origin/main". Guard for git being unavailable or $SCRIPT_DIR
  # not being a git repo -> write "unknown" so --version never errors.
  local _stamp="$AIFY_NATIVE_BASE/.aify-version"
  local _vsha="unknown" _vshort="unknown" _vbranch="unknown" _vdate="unknown"
  if command -v git >/dev/null 2>&1 && git -C "$SCRIPT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    _vsha="$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
    _vshort="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    _vbranch="$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
    _vdate="$(git -C "$SCRIPT_DIR" log -1 --format=%cI HEAD 2>/dev/null || echo unknown)"
  fi
  printf 'sha=%s\nshort=%s\nbranch=%s\ndate=%s\n' \
    "$_vsha" "$_vshort" "$_vbranch" "$_vdate" > "$_stamp" 2>/dev/null || true
  echo "  Host bridge version stamp written to $_stamp ($_vshort)."
}

copy_claude_assets() {
  local skill_dst="$HOME/.claude/skills/aify-comms"
  local debug_skill_dst="$HOME/.claude/skills/aify-comms-debug"
  local commands_dst="$HOME/.claude/commands/aify-comms"
  mkdir -p "$(dirname "$skill_dst")" "$commands_dst"
  rm -rf "$skill_dst"
  rm -rf "$debug_skill_dst"
  cp -R "$SCRIPT_DIR/.claude/skills/aify-comms" "$skill_dst"
  cp -R "$SCRIPT_DIR/.claude/skills/aify-comms-debug" "$debug_skill_dst"
  cp -R "$SCRIPT_DIR/.claude/commands/." "$commands_dst/"
}

copy_hermes_assets() {
  # Install the aify-comms usage + debug skills into the Hermes skills tree so
  # `/aify` surfaces them (parity with claude/codex — previously hermes got NO
  # skills from install.sh, only the MCP server/plugin). Hermes discovers skills
  # under <hermes-home>/skills/<category>/<skill>/SKILL.md; we use the
  # autonomous-ai-agents category (where the hermes-native aify-comms-teamwork
  # skill also lives) and only replace our own two dirs — never touch others.
  local hermes_home="${HERMES_HOME:-$HOME/.hermes}"
  local cat_dir="$hermes_home/skills/autonomous-ai-agents"
  mkdir -p "$cat_dir"
  rm -rf "$cat_dir/aify-comms" "$cat_dir/aify-comms-debug"
  cp -R "$SCRIPT_DIR/.agents/skills/aify-comms" "$cat_dir/aify-comms"
  cp -R "$SCRIPT_DIR/.agents/skills/aify-comms-debug" "$cat_dir/aify-comms-debug"
  echo "  Installed aify-comms + aify-comms-debug skills to $cat_dir"
}

install_claude_wrapper() {
  local wrapper_dir="$HOME/.local/bin"
  local wrapper_path="$wrapper_dir/claude-aify"
  mkdir -p "$wrapper_dir"
  # The runtime marker is written by the long-lived aify-comms-channel MCP
  # bridge itself (mcp/stdio/claude-channel.js), not by this wrapper.
  # Previously the wrapper wrote the marker with bash \$\$ as the pid, which
  # on Git Bash for Windows is an MSYS shell PID that process.kill() cannot
  # see — isProcessAlive would auto-delete the marker on first read and
  # every claude-aify session on Windows fell back to claude-needs-channel.
  cat > "$wrapper_path" <<EOF
#!/bin/bash
set -euo pipefail

CLAUDE_RESUME_ID="\${CLAUDE_SESSION_ID:-}"
# Bypass permissions by DEFAULT for every *-aify wrapper (2026-06-02 decision):
# aify agents run unattended, so they must not stall on approval prompts. Each
# wrapper uses its harness-native bypass flag (claude --dangerously-skip-
# permissions, codex --dangerously-bypass-approvals-and-sandbox, hermes --yolo,
# omp --auto-approve). Opt out per-launch with --safe / --no-auto.
CLAUDE_AUTO=true
CLAUDE_AIFY_AGENT_ID="\${AIFY_AGENT_ID:-}"
CLAUDE_AIFY_ROLE="\${AIFY_AGENT_ROLE:-coder}"
# Explicit session-mode opt-in. --resident is the default for human
# invocation; --managed is set by aify-comms when it spawns this wrapper
# as a backing process. If neither flag is passed and AIFY_SESSION_MODE
# is unset, auto-detect via TTY presence on stdin: interactive → resident.
CLAUDE_AIFY_SESSION_MODE="\${AIFY_SESSION_MODE:-}"
CLAUDE_ARGS=()
CLAUDE_RESUME_FROM_ARG=false
CLAUDE_RESUME_FLAG="--resume"
CLAUDE_HAS_MODEL=false
CLAUDE_HAS_EFFORT=false
PREV_ARG=""
for ARG in "\$@"; do
  if [ "\$PREV_ARG" = "--resume" ] || [ "\$PREV_ARG" = "--session-id" ] || [ "\$PREV_ARG" = "-r" ]; then
    CLAUDE_RESUME_ID="\$ARG"
    CLAUDE_RESUME_FLAG="\$PREV_ARG"
    CLAUDE_RESUME_FROM_ARG=true
    PREV_ARG=""
    continue
  fi
  if [ "\$PREV_ARG" = "--aify-agent" ] || [ "\$PREV_ARG" = "--agent-id" ]; then
    CLAUDE_AIFY_AGENT_ID="\$ARG"
    PREV_ARG=""
    continue
  fi
  if [ "\$PREV_ARG" = "--aify-role" ]; then
    CLAUDE_AIFY_ROLE="\$ARG"
    PREV_ARG=""
    continue
  fi
  if [ "\$ARG" = "-auto" ] || [ "\$ARG" = "--auto" ] || [ "\$ARG" = "--yolo" ]; then
    CLAUDE_AUTO=true
    continue
  fi
  if [ "\$ARG" = "--safe" ] || [ "\$ARG" = "--no-auto" ]; then
    CLAUDE_AUTO=false
    continue
  fi
  if [ "\$ARG" = "--resident" ]; then
    CLAUDE_AIFY_SESSION_MODE="resident"
    continue
  fi
  if [ "\$ARG" = "--managed" ]; then
    CLAUDE_AIFY_SESSION_MODE="managed"
    continue
  fi
  if [ "\$ARG" = "--aify-agent" ] || [ "\$ARG" = "--agent-id" ] || [ "\$ARG" = "--aify-role" ]; then
    PREV_ARG="\$ARG"
    continue
  fi
  if [ "\$ARG" = "--model" ]; then
    CLAUDE_HAS_MODEL=true
  fi
  if [ "\$ARG" = "--effort" ]; then
    CLAUDE_HAS_EFFORT=true
  fi
  case "\$ARG" in
  --aify-agent=*|--agent-id=*)
    CLAUDE_AIFY_AGENT_ID="\${ARG#*=}"
    continue
    ;;
  --aify-role=*)
    CLAUDE_AIFY_ROLE="\${ARG#*=}"
    continue
    ;;
  --model=*)
    CLAUDE_HAS_MODEL=true
    ;;
  --effort=*)
    CLAUDE_HAS_EFFORT=true
    ;;
  --resume=*|--session-id=*|-r=*)
    CLAUDE_RESUME_ID="\${ARG#*=}"
    CLAUDE_RESUME_FLAG="\${ARG%%=*}"
    CLAUDE_RESUME_FROM_ARG=true
    continue
    ;;
  esac
  if [ "\$ARG" = "--resume" ] || [ "\$ARG" = "--session-id" ] || [ "\$ARG" = "-r" ]; then
    PREV_ARG="\$ARG"
    continue
  fi
  CLAUDE_ARGS+=("\$ARG")
  PREV_ARG="\$ARG"
done
if [ -n "\${AIFY_MANAGED_MODEL:-}" ] && [ "\$CLAUDE_HAS_MODEL" = false ]; then
  CLAUDE_ARGS+=(--model "\$AIFY_MANAGED_MODEL")
fi
if [ -n "\${AIFY_MANAGED_EFFORT:-}" ] && [ "\$CLAUDE_HAS_EFFORT" = false ]; then
  CLAUDE_ARGS+=(--effort "\$AIFY_MANAGED_EFFORT")
fi

# Plan 6 B4 (2026-05-26): validate CLAUDE_SESSION_ID against the on-disk
# transcript. Claude stores transcripts at
# ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl; if the operator's
# shell has a stale CLAUDE_SESSION_ID from a prior session that's been
# GC'd (or from a different cwd), the inner aify-comms MCP bridge would
# register with that stale id and dispatch would fail with "session not
# found" until the next heartbeat cycle (Plan 6 A1) corrected it.
#
# We don't try to reconstruct the encoded-cwd from bash — Windows-native
# Claude encodes "C:\Docker\foo" while git-bash sees "/c/Docker/foo", and
# matching either reliably is brittle. Instead: scan
# ~/.claude/projects/*/ for ANY <id>.jsonl. If none exists the env value
# is stale; unset so claude creates a fresh session and the bridge's
# discover picks it up.
validate_claude_session_id() {
  local id="\$1"
  [ -z "\$id" ] && return 1
  local root="\$HOME/.claude/projects"
  [ -d "\$root" ] || return 1
  # Cheap glob: any project dir containing <id>.jsonl.
  local hit
  hit="\$(find "\$root" -maxdepth 2 -type f -name "\${id}.jsonl" 2>/dev/null | head -1)"
  [ -n "\$hit" ]
}

if [ -n "\${CLAUDE_RESUME_ID:-}" ] && ! validate_claude_session_id "\$CLAUDE_RESUME_ID"; then
  echo "[claude-aify] CLAUDE_SESSION_ID '\$CLAUDE_RESUME_ID' has no transcript under ~/.claude/projects/...; clearing (claude will create a fresh session)" >&2
  unset CLAUDE_RESUME_ID
  unset CLAUDE_SESSION_ID
fi
if [ -n "\${CLAUDE_RESUME_ID:-}" ]; then
  export CLAUDE_SESSION_ID="\$CLAUDE_RESUME_ID"
  if [ "\$CLAUDE_RESUME_FROM_ARG" = true ]; then
    CLAUDE_ARGS+=("\${CLAUDE_RESUME_FLAG:---resume}" "\$CLAUDE_RESUME_ID")
  fi
fi
# IDENTITY RECOVERY (2026-07-14). EVERY turn-state path is gated on AIFY_AGENT_ID: the
# bridge's turn detector (server.js), the Stop / UserPromptSubmit / PostToolUse hooks, and
# the session-store capture hook. So a session started WITHOUT an agent id registers,
# messages and heartbeats normally — but its status LATCHES FOREVER: the channel sidecar
# (which carries the id in its own config) still SETS `working` on an inbound wake, and
# nothing left alive can ever CLEAR it. That is a silent, total status failure that looks
# healthy from every other angle; it cost days of "general-manager is always working" before
# the missing env var was found. Claude's own /resume picker offers a SESSION, never an agent,
# so an operator naturally resumes by session id — exactly the path that strips the identity.
#
# hermes has recovered its agent from a bare `--resume <handle>` since 2026-06-03 (see the
# hermes block below); the comment there claimed "same idea is wired into claude/codex" — it
# never was. This is that wiring, mirrored: ask the SERVICE which agent owns this session
# handle (authoritative, and unlike the /tmp store it survives a reboot), then fall back to
# the local session store (offline-robust when the service is down). If the id is STILL
# unknown, say so out loud rather than degrade in silence. Anonymous sessions remain legal —
# a plain claude+comms session is a real use case — they just can't be silent about it.
if [ -z "\$CLAUDE_AIFY_AGENT_ID" ] && [ -n "\${CLAUDE_RESUME_ID:-}" ]; then
  if command -v node >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
    _aify_rec="\$(curl -sS --max-time 2 "\${AIFY_COMMS_URL:-http://localhost:8800}/api/v1/agents" 2>/dev/null | node -e 'let d="";process.stdin.on("data",c=>d+=c).on("end",()=>{try{const a=(JSON.parse(d).agents)||{};const h=process.argv[1];for(const k in a){const v=a[k]||{};const sh=String(v.sessionHandle||v.session_handle||"");if(sh&&sh===h&&String(v.runtime||"")==="claude-code"){process.stdout.write(k);break;}}}catch{}})' "\$CLAUDE_RESUME_ID" 2>/dev/null)"
    if [ -n "\$_aify_rec" ]; then
      CLAUDE_AIFY_AGENT_ID="\$_aify_rec"
      echo "[claude-aify] resolved aify agent '\$CLAUDE_AIFY_AGENT_ID' from session handle '\$CLAUDE_RESUME_ID' (service lookup)." >&2
    fi
  fi
fi
if [ -z "\$CLAUDE_AIFY_AGENT_ID" ] && [ -n "\${CLAUDE_RESUME_ID:-}" ]; then
  for _aify_store in "\${TMPDIR:-/tmp}"/aify-claude-session-*.json; do
    [ -f "\$_aify_store" ] || continue
    if grep -q "\"sessionId\":\"\$CLAUDE_RESUME_ID\"" "\$_aify_store" 2>/dev/null; then
      _aify_recovered="\$(basename "\$_aify_store" .json)"
      CLAUDE_AIFY_AGENT_ID="\${_aify_recovered#aify-claude-session-}"
      echo "[claude-aify] recovered agent id '\$CLAUDE_AIFY_AGENT_ID' from the session store for --resume \$CLAUDE_RESUME_ID" >&2
      break
    fi
  done
fi
if [ -z "\$CLAUDE_AIFY_AGENT_ID" ]; then
  echo "[claude-aify] NO AGENT ID: aify turn/status detection is DISABLED for this session (status will latch). Pass --aify-agent <id> if this is a registered agent." >&2
fi
export AIFY_RUNTIME="claude-code"
if [ -n "\$CLAUDE_AIFY_AGENT_ID" ]; then
  export AIFY_AGENT_ID="\$CLAUDE_AIFY_AGENT_ID"
  export AIFY_AGENT_ROLE="\$CLAUDE_AIFY_ROLE"
fi
# Expose the aify service URL to Claude's process tree so the Stop hook
# (installed by install.sh's install_claude_turn_end_hook) can POST a
# turn-end signal to the bridge when each assistant turn ends. Without
# this, the hook no-ops and the working-status pulse waits out the
# 120s server-side stale window after every reply.
# Caller env wins — a bridge-spawned managed PTY can override the
# install-time default by exporting AIFY_COMMS_URL beforehand.
export AIFY_COMMS_URL="\${AIFY_COMMS_URL:-${SERVER_URL:-http://127.0.0.1:8800}}"

# Session-mode resolution: explicit flag/env > TTY auto-detect.
# Resident = a human runs this wrapper in their own terminal (interactive
# stdin); aify-comms-channel notifications wake the model and chat
# delivery uses channels. Managed = aify-comms spawned this wrapper as
# a backing process (no human at the keyboard); same wrapper, same
# channels, but the service knows there's no operator typing in this
# session.
if [ -z "\$CLAUDE_AIFY_SESSION_MODE" ]; then
  if [ -t 0 ]; then
    CLAUDE_AIFY_SESSION_MODE="resident"
  else
    CLAUDE_AIFY_SESSION_MODE="managed"
  fi
fi
export AIFY_SESSION_MODE="\$CLAUDE_AIFY_SESSION_MODE"
# claude-aify ALWAYS activates aify-comms-channel as a dev channel below
# (--dangerously-load-development-channels), so tell the registration path
# that channels are enabled. This stops the service-side _row_capabilities
# strip from removing resident-run from this agent's capabilities.
export AIFY_CHANNELS_ENABLED="1"

CLAUDE_PERMISSION_FLAGS=()
if [ "\$CLAUDE_AUTO" = true ]; then
  CLAUDE_PERMISSION_FLAGS+=(--dangerously-skip-permissions)
fi

# MCP server config: default is "load the operator's full ~/.claude.json
# mcpServers list" (the install_claude_config function has already merged
# aify-comms + aify-comms-channel into that file at install time, so the
# wrapper still gets channel wake — it just also gets aify-project-graph,
# github, browsermcp, and every other server the operator configured).
#
# Escape hatch: set AIFY_CLAUDE_STRICT_MCP=1 in the launching shell to
# revert to the legacy strict two-server config (aify-comms +
# aify-comms-channel only, via --strict-mcp-config). Use this when the
# Claude Code MCP init race (upstream issues #38462, #21341) re-bites and
# channel notifications stop delivering because slower MCP servers leave
# aify-comms-channel stuck in "still connecting" state. The legacy
# strict mode trades operator-visible MCP servers inside the wrapper for
# guaranteed channel wake.
CLAUDE_MCP_FLAGS=()
AIFY_MCP_CONFIG=""
if [ "\${AIFY_CLAUDE_STRICT_MCP:-0}" = "1" ]; then
  # Convert install dir to a Windows-native path (C:/Docker/aify-comms)
  # if cygpath is available, otherwise use SCRIPT_DIR as-is. The MSYS
  # path /c/Docker/aify-comms is unreadable by native-Windows Claude
  # spawning the MCP server children — they fail to start with the
  # MSYS-style path even though bash itself reads it fine.
  if command -v cygpath >/dev/null 2>&1; then
    AIFY_SCRIPT_DIR_FWD="\$(cygpath -m "$AIFY_NATIVE_BASE")"
  else
    AIFY_SCRIPT_DIR_FWD="$AIFY_NATIVE_BASE"
  fi
  AIFY_MCP_CONFIG="\$(mktemp -t aify-mcp.XXXXXX.json 2>/dev/null || mktemp -t aify-mcp)"
  cat > "\$AIFY_MCP_CONFIG" <<JSON
{
  "mcpServers": {
    "aify-comms": {
      "command": "node",
      "args": ["\${AIFY_SCRIPT_DIR_FWD}/mcp/stdio/server.js"],
      "env": { "AIFY_SERVER_URL": "${SERVER_URL:-}", "CLAUDE_MCP_SERVER_URL": "${SERVER_URL:-}" }
    },
    "aify-comms-channel": {
      "command": "node",
      "args": ["\${AIFY_SCRIPT_DIR_FWD}/mcp/stdio/claude-channel.js"],
      "env": { "AIFY_SERVER_URL": "${SERVER_URL:-}", "CLAUDE_MCP_SERVER_URL": "${SERVER_URL:-}" }
    }
  }
}
JSON
  # Strict mode (AIFY_CLAUDE_STRICT_MCP=1): only the two-server config
  # above is visible to this claude process.
  CLAUDE_MCP_FLAGS+=(--strict-mcp-config --mcp-config "\$AIFY_MCP_CONFIG")
fi

# Session-id truth capture (2026-05-30, #138): install SessionStart +
# UserPromptSubmit hooks that record THIS claude session's own id. Claude
# passes session_id to hooks on stdin; the hook keys it by AIFY_AGENT_ID
# (inherited from this wrapper's env) so the bridge reads back the agent's
# OWN session instead of a machine-global filesystem guess (the cause of
# cross-agent session bleed when a whole team runs in one directory).
# Always on — both strict and default MCP modes.
if command -v cygpath >/dev/null 2>&1; then
  AIFY_SCRIPT_DIR_FWD="\$(cygpath -m "$SCRIPT_DIR")"
else
  AIFY_SCRIPT_DIR_FWD="$SCRIPT_DIR"
fi
AIFY_HOOK_SETTINGS="\$(mktemp -t aify-hooks.XXXXXX.json 2>/dev/null || mktemp -t aify-hooks)"
cat > "\$AIFY_HOOK_SETTINGS" <<JSON
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "node \"\${AIFY_SCRIPT_DIR_FWD}/mcp/stdio/claude-session-hook.js\"" } ] }
    ],
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "node \"\${AIFY_SCRIPT_DIR_FWD}/mcp/stdio/claude-session-hook.js\"" } ] }
    ]
  }
}
JSON
CLAUDE_MCP_FLAGS+=(--settings "\$AIFY_HOOK_SETTINGS")
trap 'rm -f "\$AIFY_MCP_CONFIG" "\$AIFY_HOOK_SETTINGS" 2>/dev/null' EXIT

# Managed kill-prior (2026-05-31): exactly one managed claude per agent. Reap
# any orphaned claude.exe still bound to this agent's stable --resume handle
# before launching the new one. Managed claude churns terminals (each
# dispatch/recover/restart spawns a fresh PTY and marks the prior 'failed');
# a server-marked-'failed' terminal leaves the bridge with no live handle, so
# the old native claude.exe is never reaped and N siblings accumulate, each
# polling /dispatch/claim under the same channel-sidecar bridge id -> a
# dispatch is delivered to a RANDOM sibling, not the console. Reaping by the
# per-agent resume handle collapses that to one (mirrors hermes kill-prior).
# Managed-only: resident is the operator's own visible window.
if [ "\$AIFY_SESSION_MODE" = "managed" ] && [ -n "\${CLAUDE_RESUME_ID:-}" ] && [ -n "\${CLAUDE_AIFY_AGENT_ID:-}" ]; then
  # AGENT-SCOPED reap (safety, 2026-05-31): pass the agent id so the reaper only
  # kills THIS agent's prior managed claude (verified via the candidate's parent
  # --aify-agent wrapper), never another agent or a resident operator session
  # that happens to share the same --resume session id (handle collision).
  node "$AIFY_BRIDGE_DIR/reap-managed-claude.js" "\$CLAUDE_RESUME_ID" "\$CLAUDE_AIFY_AGENT_ID" >/dev/null 2>&1 || true
fi

claude --dangerously-load-development-channels server:aify-comms-channel "\${CLAUDE_MCP_FLAGS[@]}" "\${CLAUDE_PERMISSION_FLAGS[@]}" "\${CLAUDE_ARGS[@]}"
STATUS=\$?
exit "\$STATUS"
EOF
  chmod +x "$wrapper_path"
  install_windows_cmd_shim "claude-aify" "$wrapper_dir"
}

remove_claude_wrapper() {
  local wrapper_path="$HOME/.local/bin/claude-aify"
  local shim_path="$HOME/.local/bin/claude-aify.cmd"
  rm -f "$wrapper_path"
  rm -f "$shim_path"
}

install_codex_wrapper() {
  # FIX 7 (2026-06-03): honor EMIT_CODEX_WRAPPERS_DIR so the determinism test can
  # render the wrapper into a tmp dir (mirrors install_hermes_wrapper's emit dir).
  local wrapper_dir="${EMIT_CODEX_WRAPPERS_DIR:-$HOME/.local/bin}"
  local wrapper_path="$wrapper_dir/codex-aify"
  mkdir -p "$wrapper_dir"
  cat > "$wrapper_path" <<'EOF'
#!/bin/bash
set -euo pipefail

pick_port() {
  node -e '
    const net = require("net");
    const server = net.createServer();
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      console.log(address && address.port ? String(address.port) : "");
      server.close();
    });
    server.on("error", () => process.exit(1));
  '
}

wait_for_port() {
  local port="$1"
  node -e '
    const net = require("net");
    const port = Number(process.argv[1]);
    const deadline = Date.now() + 10000;
    function attempt() {
      const socket = net.createConnection({ host: "127.0.0.1", port });
      socket.on("connect", () => {
        socket.end();
        process.exit(0);
      });
      socket.on("error", () => {
        socket.destroy();
        if (Date.now() > deadline) process.exit(1);
        setTimeout(attempt, 150);
      });
    }
    attempt();
  ' "$port"
}

PORT="$(pick_port)"
if [ -z "$PORT" ]; then
  echo "Failed to allocate a local port for codex app-server." >&2
  exit 1
fi

APP_SERVER_URL="ws://127.0.0.1:$PORT"
export AIFY_CODEX_APP_SERVER_URL="$APP_SERVER_URL"

LOG_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/aify-comms"
mkdir -p "$LOG_ROOT"
LOG_FILE="$LOG_ROOT/codex-aify-app-server-$PORT.log"

CODEX_AUTO=true
for ARG in "$@"; do
  case "$ARG" in
    -auto|--auto)
      CODEX_AUTO=true
      ;;
    --safe|--no-auto|--no-dangerous-permissions)
      CODEX_AUTO=false
      ;;
  esac
done

CODEX_PERMISSION_FLAGS=()
if [ "$CODEX_AUTO" = true ]; then
  CODEX_PERMISSION_FLAGS+=(--dangerously-bypass-approvals-and-sandbox)
fi

if command -v setsid >/dev/null 2>&1; then
  setsid codex "${CODEX_PERMISSION_FLAGS[@]}" app-server --listen "$APP_SERVER_URL" </dev/null >>"$LOG_FILE" 2>&1 &
else
  codex "${CODEX_PERMISSION_FLAGS[@]}" app-server --listen "$APP_SERVER_URL" </dev/null >>"$LOG_FILE" 2>&1 &
fi
APP_SERVER_PID=$!
RUNTIME_PID=""

# The runtime marker is written by the long-lived aify-comms MCP bridge
# itself (mcp/stdio/server.js) on startup when it sees
# AIFY_CODEX_APP_SERVER_URL in its environment.

cleanup() {
  if [ -n "${RUNTIME_PID:-}" ] && kill -0 "$RUNTIME_PID" >/dev/null 2>&1; then
    kill "$RUNTIME_PID" >/dev/null 2>&1 || true
    wait "$RUNTIME_PID" 2>/dev/null || true
  fi
  if kill -0 "$APP_SERVER_PID" >/dev/null 2>&1; then
    kill "$APP_SERVER_PID" >/dev/null 2>&1 || true
    wait "$APP_SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM HUP

run_codex_foreground() {
  set +e
  # Keep Codex in the foreground. In a non-interactive bash wrapper, async
  # commands (`codex ... &`) receive /dev/null on stdin, so the Codex TUI exits
  # with "stdin is not a terminal" even when the operator launched from a TTY.
  codex "$@"
  local status=$?
  set -e
  RUNTIME_PID=""
  return "$status"
}

if ! wait_for_port "$PORT"; then
  echo "codex-aify could not reach the local app-server at $APP_SERVER_URL." >&2
  echo "Check $LOG_FILE for details." >&2
  exit 1
fi

CODEX_ARGS=()
CODEX_AIFY_AGENT_ID="${AIFY_AGENT_ID:-}"
CODEX_AIFY_ROLE="${AIFY_AGENT_ROLE:-coder}"
CODEX_AIFY_SESSION_MODE="${AIFY_SESSION_MODE:-}"
CODEX_RESUME_HANDLE=""
PREV_ARG=""
for ARG in "$@"; do
  if [ "$PREV_ARG" = "--aify-agent" ] || [ "$PREV_ARG" = "--agent-id" ]; then
    CODEX_AIFY_AGENT_ID="$ARG"
    PREV_ARG=""
    continue
  fi
  if [ "$PREV_ARG" = "--aify-role" ]; then
    CODEX_AIFY_ROLE="$ARG"
    PREV_ARG=""
    continue
  fi
  # Plan 1: pull --resume <handle> out of the arg stream into
  # CODEX_RESUME_HANDLE. The dashboard Console now passes the stored
  # codex session id this way (Task 11). We consume the token rather
  # than forwarding it because codex itself takes the handle as a
  # POSITIONAL argument to its `resume` subcommand (`codex resume <id>`),
  # not as a flag on the top-level `codex --remote` invocation. The
  # positional <id> is what actually resumes the rollout; the
  # --include-non-interactive flag we pass alongside it (below) is a no-op
  # when an explicit id is given and only matters for id-less resume.
  if [ "$PREV_ARG" = "--resume" ] || [ "$PREV_ARG" = "--session-id" ]; then
    CODEX_RESUME_HANDLE="$ARG"
    PREV_ARG=""
    continue
  fi
  if [ "$ARG" = "-auto" ] || [ "$ARG" = "--auto" ]; then
    continue
  fi
  if [ "$ARG" = "--safe" ] || [ "$ARG" = "--no-auto" ] || [ "$ARG" = "--no-dangerous-permissions" ]; then
    continue
  fi
  if [ "$ARG" = "--resident" ]; then
    CODEX_AIFY_SESSION_MODE="resident"
    continue
  fi
  if [ "$ARG" = "--managed" ]; then
    CODEX_AIFY_SESSION_MODE="managed"
    continue
  fi
  if [ "$ARG" = "--aify-agent" ] || [ "$ARG" = "--agent-id" ] || [ "$ARG" = "--aify-role" ]; then
    PREV_ARG="$ARG"
    continue
  fi
  if [ "$ARG" = "--resume" ] || [ "$ARG" = "--session-id" ]; then
    PREV_ARG="$ARG"
    continue
  fi
  case "$ARG" in
  --aify-agent=*|--agent-id=*)
    CODEX_AIFY_AGENT_ID="${ARG#*=}"
    continue
    ;;
  --aify-role=*)
    CODEX_AIFY_ROLE="${ARG#*=}"
    continue
    ;;
  --resume=*|--session-id=*)
    CODEX_RESUME_HANDLE="${ARG#*=}"
    continue
    ;;
  esac
  CODEX_ARGS+=("$ARG")
done
export AIFY_RUNTIME="codex"
if [ -n "$CODEX_AIFY_AGENT_ID" ]; then
  export AIFY_AGENT_ID="$CODEX_AIFY_AGENT_ID"
  export AIFY_AGENT_ROLE="$CODEX_AIFY_ROLE"
fi
# Expose the aify service URL so codex's UserPromptSubmit / Stop hooks
# (installed by install.sh's install_codex_turn_hooks) can POST the
# turn-start / turn-end signals to the bridge symmetrically with
# claude-aify. Without this, the hooks would no-op because they gate
# on \${AIFY_COMMS_URL:-}.
export AIFY_COMMS_URL="${AIFY_COMMS_URL:-__AIFY_INSTALL_TIME_URL__}"

# Session-mode resolution: explicit flag/env > TTY auto-detect. See
# claude-aify for the rationale; same contract applies here.
if [ -z "$CODEX_AIFY_SESSION_MODE" ]; then
  if [ -t 0 ]; then
    CODEX_AIFY_SESSION_MODE="resident"
  else
    CODEX_AIFY_SESSION_MODE="managed"
  fi
fi
export AIFY_SESSION_MODE="$CODEX_AIFY_SESSION_MODE"

# Fresh codex-aify launch must not infer a current thread from historical
# ~/.codex/sessions files. That scan can only find an old rollout, not the
# thread this just-started --remote TUI is attached to. Explicit --resume
# remains authoritative and is exported below so the bridge and Codex agree.
if [ -z "${CODEX_RESUME_HANDLE:-}" ]; then
  : # Fresh codex-aify launch: leave CODEX_THREAD_ID/AIFY_SESSION_HANDLE unset.
else
  # Explicit --resume <id> from operator wins. Make the bridge see the
  # same handle codex will resume so they don't disagree for the first
  # 60s after launch.
  export CODEX_THREAD_ID="$CODEX_RESUME_HANDLE"
  export AIFY_SESSION_HANDLE="$CODEX_RESUME_HANDLE"
fi

# Plan 6 follow-up (2026-05-26): dashboard-spawned managed codex wrappers
# must boot without operator approval gates. The TUI's hooks-trust gate
# blocks startup until "Trust all hooks and continue" is selected manually,
# which leaves the wrapper PTY visible+attached but never loads the inner
# aify-comms MCP server — every dispatch sits queued forever (observed
# 2026-05-26 with graph-senior-dev). Bypass hook-trust on managed wrappers
# only. Operator-launched resident codex-aify keeps its normal trust UX.
if [ "${AIFY_MANAGED_VIA_WRAPPER:-}" = "1" ]; then
  CODEX_PERMISSION_FLAGS+=(--dangerously-bypass-hook-trust)
fi

# Plan 1: try-resume, fall back to fresh codex if the saved session
# file has been GC'd by codex itself (os error 2). The wrapper does not
# abort on a stale handle — the operator gets a fresh codex shell and
# the bridge heartbeat will report the new session id within 60s.
#
# Plan 4 (2026-05-25) — codex's session storage layout varies:
#   - flat: ~/.codex/sessions/<id>.jsonl
#   - date-sharded: ~/.codex/sessions/YYYY/MM/DD/rollout-<iso-ts>-<id>.jsonl
#   - dir-per-session: ~/.codex/sessions/<id>/...
# Accept any of these layouts when probing for a saved session.
CODEX_SESSION_FOUND=""
if [ -n "${CODEX_RESUME_HANDLE:-}" ]; then
  # Try flat layout first (cheapest check)
  if [ -f "$HOME/.codex/sessions/$CODEX_RESUME_HANDLE.jsonl" ]; then
    CODEX_SESSION_FOUND="$HOME/.codex/sessions/$CODEX_RESUME_HANDLE.jsonl"
  # Try dir-per-session
  elif [ -d "$HOME/.codex/sessions/$CODEX_RESUME_HANDLE" ]; then
    CODEX_SESSION_FOUND="$HOME/.codex/sessions/$CODEX_RESUME_HANDLE"
  # Try date-sharded — search recursively for files containing the handle
  else
    CODEX_SESSION_FOUND="$(find "$HOME/.codex/sessions" -type f -name "*$CODEX_RESUME_HANDLE*" 2>/dev/null | head -1)"
  fi
fi

if [ -n "${CODEX_RESUME_HANDLE:-}" ]; then
  if [ -n "$CODEX_SESSION_FOUND" ]; then
    run_codex_foreground --remote "$APP_SERVER_URL" "${CODEX_PERMISSION_FLAGS[@]}" "${CODEX_ARGS[@]}" resume --include-non-interactive "$CODEX_RESUME_HANDLE"
    exit $?
  else
    echo "[codex-aify] saved session $CODEX_RESUME_HANDLE not found in codex storage; starting fresh codex" >&2
  fi
fi
run_codex_foreground --remote "$APP_SERVER_URL" "${CODEX_PERMISSION_FLAGS[@]}" "${CODEX_ARGS[@]}"
exit $?
EOF
  # Substitute the install-time service URL into the wrapper. The
  # heredoc above is single-quoted so `$SERVER_URL` is NOT expanded
  # inside it — using a placeholder + post-substitution lets the
  # wrapper respect a runtime-set AIFY_COMMS_URL while falling back
  # to the URL the operator passed to install.sh. Without this, the
  # wrapper hardcoded 127.0.0.1:8800 regardless of `--client codex <url>`.
  sed -i.bak "s|__AIFY_INSTALL_TIME_URL__|${SERVER_URL:-http://127.0.0.1:8800}|" "$wrapper_path" && rm -f "$wrapper_path.bak"
  chmod +x "$wrapper_path"
  # FIX 7 (2026-06-03): in emit mode, stop after writing the wrapper text — skip
  # the Windows shim / MCP-config / launch so the test can inspect the wrapper in
  # isolation without touching the operator's environment.
  if [ -n "$EMIT_CODEX_WRAPPERS_DIR" ]; then
    return 0
  fi
  install_windows_cmd_shim "codex-aify" "$wrapper_dir"
}

install_pi_wrapper() {
  local wrapper_dir="$HOME/.local/bin"
  local wrapper_path="$wrapper_dir/pi-aify"
  local alias_path="$wrapper_dir/omp-aify"
  mkdir -p "$wrapper_dir"
  cat > "$wrapper_path" <<'EOF'
#!/bin/bash
set -euo pipefail

PI_AIFY_AGENT_ID="${AIFY_AGENT_ID:-}"
PI_AIFY_ROLE="${AIFY_AGENT_ROLE:-coder}"
PI_AIFY_SESSION_MODE="${AIFY_SESSION_MODE:-}"
PI_SESSION_HANDLE="${PI_SESSION_ID:-${OMP_SESSION_ID:-${AIFY_PI_SESSION_ID:-}}}"
PI_RUNTIME_COMMAND="${AIFY_PI_COMMAND:-${PI_COMMAND:-omp}}"
PI_AIFY_STANDALONE=false
# Bypass approval prompts by DEFAULT (2026-06-02 — every *-aify bypasses so
# unattended agents never stall). omp's native flag is --auto-approve. Opt out
# per-launch with --safe / --no-auto.
PI_AUTO=true
PI_ARGS=()
PREV_ARG=""
for ARG in "$@"; do
  if [ "$PREV_ARG" = "--aify-agent" ] || [ "$PREV_ARG" = "--agent-id" ]; then
    PI_AIFY_AGENT_ID="$ARG"
    PREV_ARG=""
    continue
  fi
  if [ "$PREV_ARG" = "--aify-role" ]; then
    PI_AIFY_ROLE="$ARG"
    PREV_ARG=""
    continue
  fi
  if [ "$ARG" = "--resident" ]; then
    PI_AIFY_SESSION_MODE="resident"
    continue
  fi
  if [ "$ARG" = "--managed" ]; then
    PI_AIFY_SESSION_MODE="managed"
    continue
  fi
  if [ "$ARG" = "--standalone" ]; then
    # Operator override of the Phase 4 watchdog. Lets you launch a parallel
    # OMP session on a different session-id while the bridge keeps driving
    # the dashboard one. Pass --resume <other-id> alongside if you actually
    # want to attach to a saved session.
    PI_AIFY_STANDALONE=true
    continue
  fi
  if [ "$ARG" = "-auto" ] || [ "$ARG" = "--auto" ] || [ "$ARG" = "--yolo" ]; then
    PI_AUTO=true
    continue
  fi
  if [ "$ARG" = "--safe" ] || [ "$ARG" = "--no-auto" ]; then
    PI_AUTO=false
    continue
  fi
  if [ "$ARG" = "--aify-agent" ] || [ "$ARG" = "--agent-id" ] || [ "$ARG" = "--aify-role" ]; then
    PREV_ARG="$ARG"
    continue
  fi
  case "$ARG" in
  --aify-agent=*|--agent-id=*)
    PI_AIFY_AGENT_ID="${ARG#*=}"
    continue
    ;;
  --aify-role=*)
    PI_AIFY_ROLE="${ARG#*=}"
    continue
    ;;
  --resume=*|--session-id=*)
    PI_SESSION_HANDLE="${ARG#*=}"
    ;;
  -r=*)
    PI_SESSION_HANDLE="${ARG#*=}"
    ;;
  esac
  PI_ARGS+=("$ARG")
  if [ "$PREV_ARG" = "--resume" ] || [ "$PREV_ARG" = "--session-id" ] || [ "$PREV_ARG" = "-r" ]; then
    PI_SESSION_HANDLE="$ARG"
  fi
  PREV_ARG="$ARG"
done

export AIFY_RUNTIME="pi"
if [ -n "$PI_AIFY_AGENT_ID" ]; then
  export AIFY_AGENT_ID="$PI_AIFY_AGENT_ID"
  export AIFY_AGENT_ROLE="$PI_AIFY_ROLE"
fi
if [ -n "$PI_SESSION_HANDLE" ]; then
  export PI_SESSION_ID="$PI_SESSION_HANDLE"
  export AIFY_SESSION_HANDLE="$PI_SESSION_HANDLE"
fi
# Symmetric env-var for future pi turn hooks (omp does not currently
# expose user-prompt-submit/stop hook surfaces, but exposing the
# service URL means a future omp hook surface — or operator-written
# tooling that wraps pi-aify — can call /turn-start /turn-end without
# additional setup). The placeholder is sed-replaced with the actual
# install-time URL after the heredoc closes (this heredoc is single-
# quoted so `$SERVER_URL` is NOT expanded here).
export AIFY_COMMS_URL="${AIFY_COMMS_URL:-__AIFY_INSTALL_TIME_URL__}"

# Session-mode resolution: explicit flag/env > TTY auto-detect.
# Same contract as claude-aify; works on Ubuntu bash and Git Bash for
# Windows (POSIX [-t 0] test). Bridge-spawned wrappers always have
# AIFY_SESSION_MODE set explicitly via terminalChildEnv.
if [ -z "$PI_AIFY_SESSION_MODE" ]; then
  if [ -t 0 ]; then
    PI_AIFY_SESSION_MODE="resident"
  else
    PI_AIFY_SESSION_MODE="managed"
  fi
fi
export AIFY_SESSION_MODE="$PI_AIFY_SESSION_MODE"

# Phase 4 watchdog: refuse to launch OMP if the aify-comms bridge is already
# driving this agent's pi session through its persistent RPC child. The
# upstream RPC channel has no multiplexing — two processes on the same
# session-id step on each other. Soft mutex: this is a single HTTP read
# that fails open (timeout / network error / non-pi runtime → exec normally).
# Override with --standalone if you intentionally want a parallel session
# on a different session-id (pass --resume <other-id> too).
#
# Plan 6 follow-up (2026-05-26): when the dashboard's TerminalProcessManager
# spawns this wrapper as the managed backing (AIFY_MANAGED_VIA_WRAPPER=1),
# the bridgeOwned=true response is the DASHBOARD itself. Skipping the
# watchdog in that case lets the wrapper-managed PTY actually start —
# without this, the dashboard-spawned pi-aify exits within ~1s and the
# operator sees a Console widget that never attaches. (Observed
# 2026-05-26 with graph-tester-pi: 4 spawn attempts in 3 minutes, each
# stopped after 1s.) Rediscover still runs so the bridge's stored session
# id stays truthful.
if [ "${AIFY_MANAGED_VIA_WRAPPER:-}" = "1" ]; then
  PI_AIFY_SKIP_BRIDGE_GUARD=true
else
  PI_AIFY_SKIP_BRIDGE_GUARD=false
fi
if [ "$PI_AIFY_STANDALONE" != true ] && [ -n "$PI_AIFY_AGENT_ID" ] && [ -n "${AIFY_COMMS_URL:-}" ]; then
  AIFY_WATCHDOG_URL="${AIFY_COMMS_URL%/}/api/v1/agents/${PI_AIFY_AGENT_ID}/pi-session-state"
  AIFY_WATCHDOG_HEADERS=()
  if [ -n "${AIFY_API_KEY:-}" ]; then
    AIFY_WATCHDOG_HEADERS+=("-H" "X-API-Key: ${AIFY_API_KEY}")
  fi
  AIFY_WATCHDOG_BODY="$(curl -sS --max-time 2 "${AIFY_WATCHDOG_HEADERS[@]}" "$AIFY_WATCHDOG_URL" 2>/dev/null || true)"
  # Plan 6 B3 (2026-05-26): the pi-session-state response carries the
  # runtime's authoritative sessionId (set by Plan 4). Reuse the body we
  # just captured for the bridgeOwned check — no second HTTP call needed.
  # Overwrites PI_SESSION_ID / AIFY_SESSION_HANDLE so the inner aify-comms
  # MCP bridge registers with the truthful id, not a stale value from the
  # operator's parent shell. Failures non-fatal: empty result leaves env
  # alone and the bridge heartbeat (Plan 6 A1) corrects drift within 60s.
  PI_REDISCOVERED_SESSION_ID=""
  if [ -n "$AIFY_WATCHDOG_BODY" ]; then
    # Plan 6 follow-up (2026-05-26): the pi-session-state body legitimately
    # omits the sessionId key when no PTY is live yet (e.g. fresh
    # dashboard-spawned managed wrapper) — grep returns 1, and `set -euo
    # pipefail` then kills the whole wrapper SILENTLY before exec'ing omp.
    # That's the "pi-aify exits in 2s with no output" bug operators observed
    # 2026-05-26 with graph-tester-pi. Disable pipefail JUST around this
    # capture so a missing sessionId is empty (the intended behavior).
    set +o pipefail
    PI_REDISCOVERED_SESSION_ID="$(printf '%s' "$AIFY_WATCHDOG_BODY" \
      | grep -oE '"sessionId"[[:space:]]*:[[:space:]]*"[^"]+"' \
      | head -1 \
      | sed -E 's/.*"sessionId"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
    set -o pipefail
  fi
  if [ -n "$PI_REDISCOVERED_SESSION_ID" ]; then
    if [ "${PI_SESSION_ID:-}" != "$PI_REDISCOVERED_SESSION_ID" ]; then
      echo "[pi-aify] session id rediscovered: '${PI_SESSION_ID:-}' -> '$PI_REDISCOVERED_SESSION_ID' (from pi-session-state)" >&2
    fi
    export PI_SESSION_ID="$PI_REDISCOVERED_SESSION_ID"
    export AIFY_SESSION_HANDLE="$PI_REDISCOVERED_SESSION_ID"
    PI_SESSION_HANDLE="$PI_REDISCOVERED_SESSION_ID"
  fi
  if [ "$PI_AIFY_SKIP_BRIDGE_GUARD" != true ] && [ -n "$AIFY_WATCHDOG_BODY" ] && printf '%s' "$AIFY_WATCHDOG_BODY" | grep -q '"bridgeOwned":[[:space:]]*true'; then
    cat >&2 <<EOM
Agent '${PI_AIFY_AGENT_ID}' is currently driven by aify-comms (visible in dashboard terminal). Stop it from the dashboard or use \`omp-aify --standalone --aify-agent ${PI_AIFY_AGENT_ID}\` to launch a parallel session on a different session-id.
EOM
    exit 1
  fi
fi

PI_PERMISSION_FLAGS=()
if [ "$PI_AUTO" = true ]; then
  # omp global flag: auto-approve all tool calls (skip approval prompts).
  PI_PERMISSION_FLAGS+=(--auto-approve)
fi
exec "$PI_RUNTIME_COMMAND" "${PI_PERMISSION_FLAGS[@]}" "${PI_ARGS[@]}"
EOF
  # Same placeholder-substitute pattern as codex-aify above. Without
  # this the watchdog probe POSTs to 127.0.0.1:8800 regardless of the
  # operator's install-time URL.
  sed -i.bak "s|__AIFY_INSTALL_TIME_URL__|${SERVER_URL:-http://127.0.0.1:8800}|" "$wrapper_path" && rm -f "$wrapper_path.bak"
  chmod +x "$wrapper_path"
  cp "$wrapper_path" "$alias_path"
  chmod +x "$alias_path"
  install_windows_cmd_shim "pi-aify" "$wrapper_dir"
  install_windows_cmd_shim "omp-aify" "$wrapper_dir"
}

# Plan 5 (2026-05-25): pre-build hermes web_dist at install time.
#
# Without this, `hermes-aify` spawns `hermes dashboard --tui --skip-build`
# which dies with "✗ --skip-build was passed but no web dist found at: ..."
# on every fresh hermes install. The wrapper then falls through to plain
# `hermes`, AIFY_HERMES_GATEWAY_URL is never exported, and every resident
# hermes wake reports `hermes-missing-handle` (observed 2026-05-25 —
# see ~/.local/state/aify-comms/hermes-aify-dashboard-*.log).
#
# Detection order: AIFY_HERMES_INSTALL_ROOT > `hermes config path` parsed
# up to /hermes_cli > skip cleanly. Idempotent: noop if web_dist/index.html
# exists. Dry-run (--prebuild-dry-run) logs intent but skips npm.
# Resolve the Hermes install tree (the directory containing hermes_cli/,
# ui-tui/, web/, tui_gateway/). Detection order:
#   1. AIFY_HERMES_INSTALL_ROOT override.
#   2. Ask Hermes' own venv Python for hermes_cli's PROJECT_ROOT — this is
#      exactly what main.py uses to locate ui-tui/web_dist, so it is correct
#      for editable AND source layouts regardless of how `config path` behaves.
#   3. Legacy: parse `hermes config path` and strip /hermes_cli/ onward. Kept
#      for older Hermes builds where config path lived under the install tree.
#      (Hermes 0.14.0 moved it to the user-config dir, breaking this method.)
# Prints the resolved root on stdout, or nothing if it can't be found.
detect_hermes_install_root() {
  if [ -n "${AIFY_HERMES_INSTALL_ROOT:-}" ] && [ -d "$AIFY_HERMES_INSTALL_ROOT" ]; then
    printf '%s\n' "$AIFY_HERMES_INSTALL_ROOT"
    return 0
  fi
  local hermes_bin=""
  hermes_bin="$(hermes_cmd 2>/dev/null || true)"
  if [ -n "$hermes_bin" ]; then
    # The venv Python sits next to the hermes launcher (…/Scripts/ or …/bin/).
    local bin_dir
    bin_dir="$(dirname "$hermes_bin")"
    local venv_py=""
    if [ -x "$bin_dir/python.exe" ]; then
      venv_py="$bin_dir/python.exe"
    elif [ -x "$bin_dir/python" ]; then
      venv_py="$bin_dir/python"
    fi
    # Linux/macOS/WSL: `hermes` on PATH is often a thin launcher shim
    # (~/.local/bin/hermes) that execs the REAL venv hermes elsewhere
    # (e.g. ~/.hermes/hermes-agent/venv/bin/hermes). The venv python is then
    # NOT next to the launcher, so the checks above miss it. Follow the shim's
    # exec target to find the venv python next to the real hermes binary.
    if [ -z "$venv_py" ]; then
      local exec_target=""
      exec_target="$(grep -oE '/[^"[:space:]]*/venv/bin/hermes' "$hermes_bin" 2>/dev/null | head -n 1 || true)"
      if [ -n "$exec_target" ] && [ -x "${exec_target%/hermes}/python" ]; then
        venv_py="${exec_target%/hermes}/python"
      fi
    fi
    # Task #174 hardening: the launcher may instead be a SYMLINK into the venv
    # (~/.local/bin/hermes -> ~/.hermes/hermes-agent/venv/bin/hermes) or a
    # pipx-style entry script whose SHEBANG points at the venv python. Neither
    # is a shell shim, so the shim-grep branch above misses both. Resolve the
    # real file (command -v + readlink -f fallback chain) and re-probe for a
    # python next to it; failing that, take the shebang interpreter itself.
    # Runs strictly AFTER the original branches, so a plain PATH executable
    # resolves exactly as before.
    if [ -z "$venv_py" ]; then
      local real_bin=""
      real_bin="$(resolve_hermes_real_bin "$hermes_bin")"
      if [ -n "$real_bin" ] && [ "$real_bin" != "$hermes_bin" ]; then
        local real_dir
        real_dir="$(dirname "$real_bin")"
        if [ -x "$real_dir/python.exe" ]; then
          venv_py="$real_dir/python.exe"
        elif [ -x "$real_dir/python" ]; then
          venv_py="$real_dir/python"
        fi
      fi
      if [ -z "$venv_py" ]; then
        venv_py="$(hermes_shebang_python "${real_bin:-$hermes_bin}")"
      fi
    fi
    if [ -n "$venv_py" ]; then
      local proj_root
      proj_root="$("$venv_py" -c "from hermes_cli import main; print(main.PROJECT_ROOT)" 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
      if [ -n "$proj_root" ] && [ -d "$proj_root" ]; then
        printf '%s\n' "$proj_root"
        return 0
      fi
    fi
    # Legacy fallback: config path → strip /hermes_cli/ onward.
    local cfg_path
    cfg_path="$("$hermes_bin" config path 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
    if [ -n "$cfg_path" ] && [ "$cfg_path" != "${cfg_path%%/hermes_cli/*}" ]; then
      local legacy_root="${cfg_path%%/hermes_cli/*}"
      if [ -d "$legacy_root" ]; then
        printf '%s\n' "$legacy_root"
        return 0
      fi
    fi
  fi
  # Standard-location fallback (Linux, macOS, WSL): the hermes source install
  # lives under the hermes home, which defaults to ~/.hermes. This covers
  # pip/pipx installs where the launcher is a shim and the venv python could
  # not be located above. HERMES_HOME overrides the default home.
  local std_root="${HERMES_HOME:-$HOME/.hermes}/hermes-agent"
  if [ -d "$std_root/hermes_cli" ] || [ -d "$std_root/web" ]; then
    printf '%s\n' "$std_root"
    return 0
  fi
  return 0
}

prebuild_hermes_web_dist() {
  local hermes_install_root
  hermes_install_root="$(detect_hermes_install_root)"
  if [ -z "$hermes_install_root" ] || [ ! -d "$hermes_install_root" ]; then
    echo "[install.sh] hermes install root not found; skipping web_dist prebuild" >&2
    return 0
  fi
  local web_dist="$hermes_install_root/hermes_cli/web_dist"
  local web_src="$hermes_install_root/web"
  if [ -f "$web_dist/index.html" ]; then
    echo "[install.sh] hermes web_dist already present at $web_dist" >&2
    return 0
  fi
  if [ ! -d "$web_src" ]; then
    echo "[install.sh] hermes web source not found at $web_src; cannot prebuild" >&2
    return 0
  fi
  echo "[install.sh] prebuilding hermes web_dist (one-time; runs npm install + npm run build)" >&2
  if [ "$PREBUILD_DRY_RUN" = true ]; then
    echo "[install.sh] --prebuild-dry-run: skipping npm invocation; would have run cd '$web_src' && npm install && npm run build" >&2
    return 0
  fi
  if ! command -v npm >/dev/null 2>&1; then
    echo "[install.sh] npm not on PATH; hermes web_dist prebuild requires Node.js. Install Node and re-run install.sh." >&2
    return 1
  fi
  (cd "$web_src" && npm install && npm run build) || {
    echo "[install.sh] hermes web_dist prebuild failed — hermes-aify dashboard probe will continue to fall back. Re-run install.sh after fixing." >&2
    return 1
  }
  echo "[install.sh] hermes web_dist prebuilt at $web_dist" >&2
}

patch_hermes_codex_stream_none_fallback() {
  local hermes_install_root="$1"
  local codex_runtime_py="$hermes_install_root/agent/codex_runtime.py"
  if [ ! -f "$codex_runtime_py" ]; then
    echo "[install.sh] hermes agent/codex_runtime.py not found at $codex_runtime_py; skipping Codex stream NoneType fallback patch" >&2
    return 0
  fi
  node - "$codex_runtime_py" <<'NODE'
const fs = require("fs");
const file = process.argv[2];
let text = fs.readFileSync(file, "utf8");
let changed = false;

const marker = "Responses stream hit SDK NoneType iterable bug; falling back to create(stream=True)";
if (!text.includes(marker)) {
  const needle = `        except RuntimeError as exc:
            err_text = str(exc)
`;
  const patch = `        except TypeError as exc:
            err_text = str(exc)
            if "NoneType" in err_text and "iterable" in err_text:
                logger.debug(
                    "Responses stream hit SDK NoneType iterable bug; falling back to create(stream=True). %s err=%s",
                    agent._client_log_context(),
                    err_text,
                )
                return agent._run_codex_create_stream_fallback(api_kwargs, client=active_client)
            raise
`;
  if (text.includes(needle)) {
    text = text.replace(needle, patch + needle);
    changed = true;
  }
}

const noneOutputMarker = "Codex fallback stream: backfilled %d output items";
if (text.includes(noneOutputMarker)) {
  const oldMain = `                if isinstance(_out, list) and not _out:
                    if collected_output_items:
                        final_response.output = list(collected_output_items)
`;
  const newMain = `                if not isinstance(_out, list) or not _out:
                    if collected_output_items:
                        final_response.output = list(collected_output_items)
`;
  if (text.includes(oldMain)) {
    text = text.replace(oldMain, newMain);
    changed = true;
  }

  const oldFallback = `                _out = getattr(terminal_response, "output", None)
                if isinstance(_out, list) and not _out:
                    if collected_output_items:
                        terminal_response.output = list(collected_output_items)
`;
  const newFallback = `                _out = getattr(terminal_response, "output", None)
                if not isinstance(_out, list) or not _out:
                    if collected_output_items:
                        terminal_response.output = list(collected_output_items)
`;
  if (text.includes(oldFallback)) {
    text = text.replace(oldFallback, newFallback);
    changed = true;
  }
}

if (changed) {
  fs.copyFileSync(file, `${file}.aify-codex-stream-bak`);
  fs.writeFileSync(file, text);
  console.error(`[install.sh] patched Hermes Codex stream NoneType fallback in ${file}`);
} else if (text.includes(marker)) {
  console.error(`[install.sh] Hermes Codex stream NoneType fallback already present in ${file}`);
} else {
  console.error(`[install.sh] could not patch Hermes Codex stream NoneType fallback in ${file}`);
}
NODE
}

install_hermes_wrapper() {
  local wrapper_dir="${EMIT_HERMES_WRAPPERS_DIR:-$HOME/.local/bin}"
  local wrapper_path="$wrapper_dir/hermes-aify"
  local default_server="${SERVER_URL:-$DEFAULT_AIFY_SERVER_URL}"
  local hermes_plugin_path="$SCRIPT_DIR/integrations/hermes-aify-plugin"
  if hermes_runtime_is_native_windows; then
    hermes_plugin_path="$(path_for_windows_runtime "$hermes_plugin_path")"
  fi
  # Path to the host-side MCP stdio bridges (hermes-daemon-cli.js +
  # hermes-managed-host.js). path_for_node so Git-Bash node opens it (drive-letter).
  local hermes_stdio_dir
  hermes_stdio_dir="$(path_for_node "$AIFY_BRIDGE_DIR")"
  # Prebuilt ui-tui bundle dir (so the managed `hermes --tui` runs the existing
  # dist instead of rebuilding it on every launch — slow + noisy `npm run build`
  # observed on managed launches). `hermes --tui` skips the build entirely when
  # HERMES_TUI_DIR points at a dir containing dist/entry.js (main.py
  # _make_tui_argv prebuilt-bundle branch). `--skip-build` is NOT a valid
  # top-level `hermes --tui` flag (it belongs to `hermes dashboard`), so we set
  # the env var instead. Bake the value only when the dist actually exists; an
  # empty value means "let hermes locate/build it as before" (never breaks).
  local hermes_tui_dir=""
  local _hermes_root_for_tui
  _hermes_root_for_tui="$(detect_hermes_install_root)"
  if [ -n "$_hermes_root_for_tui" ] && [ -f "$_hermes_root_for_tui/ui-tui/dist/entry.js" ]; then
    hermes_tui_dir="$_hermes_root_for_tui/ui-tui"
  fi
  mkdir -p "$wrapper_dir"
  # Write atomically via a temp file + mv so reinstalling while a hermes-aify
  # session is running doesn't fail with ETXTBSY ("Text file busy") — rename
  # replaces the dir entry; any running process keeps its old inode.
  local wrapper_tmp="$wrapper_path.tmp.$$"
  cat > "$wrapper_tmp" <<EOF
#!/bin/bash
set -euo pipefail

HERMES_AIFY_AGENT_ID="\${AIFY_AGENT_ID:-}"
HERMES_AIFY_ROLE="\${AIFY_AGENT_ROLE:-coder}"
HERMES_AIFY_SESSION_MODE="\${AIFY_SESSION_MODE:-}"
HERMES_INHERITED_SESSION_HANDLE="\${HERMES_SESSION_ID:-\${HERMES_SESSION:-\${AIFY_SESSION_HANDLE:-}}}"
HERMES_SESSION_HANDLE=""
HERMES_EXPLICIT_SESSION_HANDLE="false"
if [ "\${AIFY_MANAGED_VIA_WRAPPER:-}" = "1" ] && [ -n "\$HERMES_INHERITED_SESSION_HANDLE" ]; then
  HERMES_SESSION_HANDLE="\$HERMES_INHERITED_SESSION_HANDLE"
  HERMES_EXPLICIT_SESSION_HANDLE="true"
fi
HERMES_RUNTIME_COMMAND="\${AIFY_HERMES_COMMAND:-\${HERMES_COMMAND:-hermes}}"
# Symmetric with claude-aify (--auto -> --dangerously-skip-permissions) and
# codex-aify (CODEX_AUTO -> --dangerously-bypass-approvals-and-sandbox). For
# hermes the bypass is --yolo (HERMES_YOLO_MODE=1, "bypass all dangerous
# command approval prompts"). Default ON (2026-06-02 — every *-aify bypasses by
# default so unattended agents never stall on approvals); opt out with
# --safe / --no-auto.
HERMES_AUTO=true
HERMES_ARGS=()
PREV_ARG=""
for ARG in "\$@"; do
  if [ "\$PREV_ARG" = "--aify-agent" ] || [ "\$PREV_ARG" = "--agent-id" ]; then
    HERMES_AIFY_AGENT_ID="\$ARG"
    PREV_ARG=""
    continue
  fi
  if [ "\$PREV_ARG" = "--aify-role" ]; then
    HERMES_AIFY_ROLE="\$ARG"
    PREV_ARG=""
    continue
  fi
  if [ "\$PREV_ARG" = "--resume" ] || [ "\$PREV_ARG" = "--session-id" ] || [ "\$PREV_ARG" = "-r" ]; then
    HERMES_SESSION_HANDLE="\$ARG"
    HERMES_EXPLICIT_SESSION_HANDLE="true"
    PREV_ARG=""
    continue
  fi
  # Checked AFTER the value-consumption branches above so a value-expecting flag
  # (e.g. --resume) claims its next token first, consistent with --aify-agent.
  if [ "\$ARG" = "-auto" ] || [ "\$ARG" = "--auto" ] || [ "\$ARG" = "--yolo" ]; then
    HERMES_AUTO=true
    continue
  fi
  if [ "\$ARG" = "--safe" ] || [ "\$ARG" = "--no-auto" ]; then
    HERMES_AUTO=false
    continue
  fi
  if [ "\$ARG" = "--resident" ]; then
    HERMES_AIFY_SESSION_MODE="resident"
    continue
  fi
  if [ "\$ARG" = "--managed" ]; then
    HERMES_AIFY_SESSION_MODE="managed"
    continue
  fi
  if [ "\$ARG" = "--aify-agent" ] || [ "\$ARG" = "--agent-id" ] || [ "\$ARG" = "--aify-role" ]; then
    PREV_ARG="\$ARG"
    continue
  fi
  case "\$ARG" in
  --aify-agent=*|--agent-id=*)
    HERMES_AIFY_AGENT_ID="\${ARG#*=}"
    continue
    ;;
  --aify-role=*)
    HERMES_AIFY_ROLE="\${ARG#*=}"
    continue
    ;;
  --resume=*|--session-id=*)
    HERMES_SESSION_HANDLE="\${ARG#*=}"
    HERMES_EXPLICIT_SESSION_HANDLE="true"
    continue
    ;;
  -r=*)
    HERMES_SESSION_HANDLE="\${ARG#*=}"
    HERMES_EXPLICIT_SESSION_HANDLE="true"
    continue
    ;;
  esac
  if [ "\$ARG" = "--resume" ] || [ "\$ARG" = "--session-id" ] || [ "\$ARG" = "-r" ]; then
    PREV_ARG="\$ARG"
    continue
  fi
  HERMES_ARGS+=("\$ARG")
  PREV_ARG="\$ARG"
done

export AIFY_RUNTIME="hermes"
export AIFY_SERVER_URL="\${AIFY_SERVER_URL:-$default_server}"
export CLAUDE_MCP_SERVER_URL="\${CLAUDE_MCP_SERVER_URL:-\$AIFY_SERVER_URL}"
# Expose AIFY_COMMS_URL too so the pre_llm_call shell hook installed by
# install_hermes_turn_hooks can POST /turn-start symmetrically with
# claude-aify and codex-aify. The hook gates on \\\${AIFY_COMMS_URL:-} so
# without this export it would silently no-op.
export AIFY_COMMS_URL="\${AIFY_COMMS_URL:-\$AIFY_SERVER_URL}"
# Hermes is a Python app that can launch Node/Python helpers whose output may
# contain UTF-8 bytes. Windows non-UTF-8 consoles have produced cp125x decode
# crashes in subprocess reader threads, so force UTF-8 for this process tree.
export PYTHONUTF8="\${PYTHONUTF8:-1}"
export PYTHONIOENCODING="\${PYTHONIOENCODING:-utf-8}"
# GATEWAY-ATTACH DETERMINISM (Task 2.1, 2026-06-03). The Hermes Ink TUI's
# gatewayClient.ts resolveGatewayAttachUrl() reads \$HERMES_TUI_GATEWAY_URL: when
# it is set the TUI ATTACHES to that gateway HOST as a WS client (startAttachedGateway);
# when it is EMPTY/UNSET the TUI spawns its OWN tui_gateway (startSpawnedGateway).
# An operator shell very often still carries a STALE HERMES_TUI_GATEWAY_URL /
# AIFY_HERMES_GATEWAY_URL from a PRIOR hermes session (pointing at a now-dead port).
# If such a stale value leaks into a launch path that does NOT re-export a fresh one
# (the plain/passthrough fallback below, or a path that fell out of the gateway
# branch), the visible TUI either attaches to a DEAD gateway or — after that WS
# fails — runs its OWN tui_gateway, so the delivery loop's gateway host shows
# session.active_list = 0 and every message strands (the ci-9136 incident). CLEAR
# any inherited value up front; the GATEWAY-HOST branch below re-exports the CORRECT
# fresh URL right before its exec, and the fallback paths then correctly run a
# self-spawned-gateway TUI (no managed delivery there anyway). This is the single
# determinism guarantee that the TUI never attaches to a stale/foreign gateway.
unset HERMES_TUI_GATEWAY_URL AIFY_HERMES_GATEWAY_URL AIFY_HERMES_GATEWAY_TOKEN 2>/dev/null || true
# Recover the aify agent id from a session handle when --aify-agent wasn't given
# (2026-06-03). After closing + reopening, hermes' OWN resume picker offers
# \`--resume <session>\`, not \`--aify-agent\`, so an operator naturally resumes by
# session — and would otherwise land in a plain TUI with NO gateway (send-only,
# not deliverable). RESIDENT-LAUNCH FIX (FIX B, 2026-06-03): an interactive
# \`hermes-aify\` relaunch with NO \`--resume\` (just an INHERITED AIFY_SESSION_HANDLE
# in the operator's shell) skipped this recovery entirely (it only consulted the
# EXPLICIT handle), so HERMES_AIFY_AGENT_ID stayed empty and the launch fell through
# to the PLAIN TUI path — which exports NONE of the gateway/agent/active-session env
# the managed path does, so the resident TUI never attached to its gateway host and
# nothing was deliverable. This is the live "resident TUI has only AIFY_HERMES_PLUGIN
# / AIFY_SESSION_MODE / AIFY_SESSION_HANDLE, missing the five+ gateway vars" symptom.
# Consider the inherited handle too (HERMES_RECOVER_HANDLE = explicit OR inherited)
# so resident relaunches resolve their agent and engage the SAME gateway-host branch
# as managed (the launch mechanism is unified; this just makes resident REACH it).
# Map the handle back to its agent:
#   1. the stable session is named \`aify-<agentId>\` → the id is the suffix (exact,
#      offline-robust — this is what hermes' picker shows for an aify agent).
#   2. otherwise ask the aify service which hermes agent owns this session handle.
# Best-effort; never blocks the launch. (Claude got the same wiring on 2026-07-14 — this
# comment previously claimed claude/codex already had it, which was FALSE and is exactly how
# general-manager ran for weeks with no agent id and a permanently latched status. CODEX
# still has no such recovery: its operator path is covered by the resume command now carrying
# --aify-agent, but a hand-typed `codex-aify --resume <id>` is still identity-less.)
HERMES_RECOVER_HANDLE="\$HERMES_SESSION_HANDLE"
if [ -z "\$HERMES_RECOVER_HANDLE" ]; then
  HERMES_RECOVER_HANDLE="\$HERMES_INHERITED_SESSION_HANDLE"
fi
if [ -z "\$HERMES_AIFY_AGENT_ID" ] && [ -n "\$HERMES_RECOVER_HANDLE" ]; then
  case "\$HERMES_RECOVER_HANDLE" in
    aify-*) HERMES_AIFY_AGENT_ID="\${HERMES_RECOVER_HANDLE#aify-}" ;;
    *)
      if command -v node >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
        _aify_rec="\$(curl -sS --max-time 2 "\${AIFY_SERVER_URL%/}/api/v1/agents" 2>/dev/null | node -e 'let d="";process.stdin.on("data",c=>d+=c).on("end",()=>{try{const a=(JSON.parse(d).agents)||{};const h=process.argv[1];for(const k in a){const v=a[k]||{};const sh=String(v.sessionHandle||v.session_handle||"");if(sh&&sh===h&&String(v.runtime||"")==="hermes"){process.stdout.write(k);break;}}}catch{}})' "\$HERMES_RECOVER_HANDLE" 2>/dev/null)"
        [ -n "\$_aify_rec" ] && HERMES_AIFY_AGENT_ID="\$_aify_rec"
      fi
      ;;
  esac
  if [ -n "\$HERMES_AIFY_AGENT_ID" ]; then
    echo "[hermes-aify] resolved aify agent '\$HERMES_AIFY_AGENT_ID' from session handle '\$HERMES_RECOVER_HANDLE' — engaging gateway-host model (resuming the agent's real hermes session id)." >&2
  fi
fi
if [ -n "\$HERMES_AIFY_AGENT_ID" ]; then
  export AIFY_AGENT_ID="\$HERMES_AIFY_AGENT_ID"
  export AIFY_AGENT_ROLE="\$HERMES_AIFY_ROLE"
  # FIX 2 (2026-06-03): export the wrapper's cwd so hermes' \${AIFY_AGENT_CWD}
  # interpolation in the config.yaml MCP env block resolves to a real path
  # (the wrapper runs in the agent's working directory).
  export AIFY_AGENT_CWD="\${AIFY_AGENT_CWD:-\$PWD}"
fi
if [ "\$HERMES_EXPLICIT_SESSION_HANDLE" = "true" ] && [ -n "\$HERMES_SESSION_HANDLE" ]; then
  export HERMES_SESSION_ID="\$HERMES_SESSION_HANDLE"
  export AIFY_SESSION_HANDLE="\$HERMES_SESSION_HANDLE"
  export AIFY_EXPLICIT_SESSION_HANDLE="true"
else
  unset HERMES_SESSION_ID HERMES_SESSION AIFY_SESSION_HANDLE
  unset AIFY_EXPLICIT_SESSION_HANDLE
fi

# Session-mode resolution: explicit flag/env > TTY auto-detect.
if [ -z "\$HERMES_AIFY_SESSION_MODE" ]; then
  if [ -t 0 ]; then
    HERMES_AIFY_SESSION_MODE="resident"
  else
    HERMES_AIFY_SESSION_MODE="managed"
  fi
fi
export AIFY_SESSION_MODE="\$HERMES_AIFY_SESSION_MODE"

# Load aify-comms' durable Hermes runtime shim. This keeps the visible-session
# bind method, active-session-file preservation, and Codex streaming fallback
# outside the Hermes install tree, so a Hermes update does not erase them.
# Disable for A/B testing with:
#   AIFY_HERMES_DISABLE_PLUGIN=1 hermes-aify ...
AIFY_HERMES_PLUGIN_PATH="$hermes_plugin_path"
if [ "\${AIFY_HERMES_DISABLE_PLUGIN:-0}" != "1" ]; then
  export AIFY_HERMES_PLUGIN="\${AIFY_HERMES_PLUGIN:-1}"
  export AIFY_HERMES_PLUGIN_PATH
  PYTHONPATH_SEP=":"
  case "\$AIFY_HERMES_PLUGIN_PATH" in
    ?:*) PYTHONPATH_SEP=";" ;;
  esac
  if [ -n "\${PYTHONPATH:-}" ]; then
    export PYTHONPATH="\$AIFY_HERMES_PLUGIN_PATH\$PYTHONPATH_SEP\$PYTHONPATH"
  else
    export PYTHONPATH="\$AIFY_HERMES_PLUGIN_PATH"
  fi
else
  unset AIFY_HERMES_PLUGIN
fi

# Node >=22 guarantee. The Hermes Ink TUI's gateway client attaches over a
# WebSocket using the global \`WebSocket\` constructor, which only exists in
# Node 22+. When the aify-comms bridge spawns this wrapper as a managed
# worker it runs under a non-interactive login shell (e.g. \`zsh -lc\`), which
# does NOT source .zshrc — so an nvm default of Node 22 is invisible and PATH
# falls back to a system Node 20. The TUI then dies with "gateway exited",
# but the SAME wrapper works when launched from an interactive terminal. Pin
# a Node >=22 here so managed and interactive launches behave identically.
# Opt out with AIFY_HERMES_SKIP_NODE_CHECK=1.
aify_node_major_of() {
  local nbin="\$1" ver
  ver="\$("\$nbin" -v 2>/dev/null)" || return 1
  ver="\${ver#v}"
  printf '%s' "\${ver%%.*}"
}

aify_ensure_node_ge_22() {
  [ "\${AIFY_HERMES_SKIP_NODE_CHECK:-0}" = "1" ] && return 0
  local current_major=""
  if command -v node >/dev/null 2>&1; then
    current_major="\$(aify_node_major_of node || true)"
  fi
  if [ -n "\$current_major" ] && [ "\$current_major" -ge 22 ] 2>/dev/null; then
    return 0
  fi
  # 1. Honor an explicitly configured HERMES_NODE if it is >=22.
  if [ -n "\${HERMES_NODE:-}" ] && [ -x "\${HERMES_NODE:-}" ]; then
    local hm
    hm="\$(aify_node_major_of "\$HERMES_NODE" || true)"
    if [ -n "\$hm" ] && [ "\$hm" -ge 22 ] 2>/dev/null; then
      PATH="\$(dirname "\$HERMES_NODE"):\$PATH"
      export PATH HERMES_NODE
      return 0
    fi
  fi
  # 2. Scan nvm-installed versions for the highest major >=22 (parse the
  #    version dir name so we don't exec every candidate during the scan).
  local nvm_root="\${NVM_DIR:-\$HOME/.nvm}"
  local best_bin="" best_major=0 candidate vdir cand_major
  if [ -d "\$nvm_root/versions/node" ]; then
    for candidate in "\$nvm_root"/versions/node/v*/bin/node; do
      [ -x "\$candidate" ] || continue
      vdir="\$(basename "\$(dirname "\$(dirname "\$candidate")")")"
      cand_major="\${vdir#v}"; cand_major="\${cand_major%%.*}"
      case "\$cand_major" in ''|*[!0-9]*) continue ;; esac
      if [ "\$cand_major" -ge 22 ] && [ "\$cand_major" -gt "\$best_major" ]; then
        best_major="\$cand_major"
        best_bin="\$candidate"
      fi
    done
  fi
  if [ -n "\$best_bin" ]; then
    PATH="\$(dirname "\$best_bin"):\$PATH"
    export PATH
    export HERMES_NODE="\$best_bin"
    return 0
  fi
  # 3. Nothing suitable found. Warn; the TUI may fail with "gateway exited".
  echo "[hermes-aify] WARNING: Node >=22 not found (current node: \${current_major:-none})." >&2
  echo "[hermes-aify]   The Hermes TUI gateway client needs Node 22+ (global WebSocket)." >&2
  echo "[hermes-aify]   Install via nvm (nvm install 22) or set HERMES_NODE to a Node>=22 binary." >&2
  return 0
}
aify_ensure_node_ge_22

# Bypass flags for the default TUI launch only. Mirrors claude-aify's
# CLAUDE_PERMISSION_FLAGS — applied to the interactive chat/TUI launch, not to
# explicit passthrough subcommands like \`hermes-aify model list\`.
HERMES_PERMISSION_FLAGS=()
if [ "\$HERMES_AUTO" = true ]; then
  HERMES_PERMISSION_FLAGS+=(--yolo)
fi

# Per-agent daemon + channel-sidecar model (Plan 1.4, 2026-05-30). Replaces the
# old \`hermes dashboard --tui\` + \`hermes --tui\` dual-spawn. The repo's
# host-side MCP bridges live here (NEVER copied elsewhere — that is how every
# session gets security fixes automatically).
AIFY_HERMES_STDIO_DIR="$hermes_stdio_dir"
AIFY_HERMES_DAEMON_CLI="\$AIFY_HERMES_STDIO_DIR/hermes-daemon-cli.js"
# Managed visible-TUI model (Plan "managed-hermes visible-TUI", 2026-05-31): the
# per-agent hidden gateway host + background delivery loop live here. The managed
# branch brings up the gateway host, starts the delivery loop, and then EXECs a
# real \`hermes --tui\` into THIS PTY (rendered windowless in the dashboard).
AIFY_HERMES_MANAGED_HOST_JS="\$AIFY_HERMES_STDIO_DIR/hermes-managed-host.js"
# Loop ready-marker helper (WS1 Task 1.5). The delivery loop writes
# \`aify-hermes-loop-ready-<agent>\` into os.tmpdir() once it is a LIVE CLAIMER;
# the managed branch health-gates on this marker before exec'ing the visible TUI
# so a TUI that can't receive work never shows (visible-TUI HARD requirement).
AIFY_HERMES_LOOP_READY_JS="\$AIFY_HERMES_STDIO_DIR/hermes-loop-ready.js"
# Prebuilt ui-tui bundle dir (baked at install time). When non-empty and it
# holds dist/entry.js, the managed branch exports it as HERMES_TUI_DIR so
# \`hermes --tui\` runs the prebuilt bundle and skips the per-launch
# \`npm run build\`. Empty → hermes builds/locates the TUI as before (no break).
AIFY_HERMES_TUI_DIR="$hermes_tui_dir"

# MCP tool-exposure pre-warm gate (2026-06-03). Hermes snapshots the agent's MCP
# tool schema only ~0.75s after the session starts (hardcoded in hermes); if the
# bridge loads slowly (cold page cache / slow fs), its comms_* tools miss that
# window and never reach the model — the agent then can't call comms_send /
# comms_register and resorts to hand-rolling its own MCP client. Warm the bridge
# ONCE here (bounded; a side-effect-free module import — the entrypoint guard in
# server.js means NO register/heartbeat/network runs) so the OS page cache is hot
# before hermes spawns its OWN MCP child; that spawn then loads from cache and
# lands inside the window. The native install already makes this ~0.3s — this is
# the belt to that suspenders, so a slow load can never silently drop the tools.
aify_hermes_warm_bridge() {
  command -v node >/dev/null 2>&1 || return 0
  local _probe="\$AIFY_HERMES_STDIO_DIR/server.js"
  [ -f "\$_probe" ] || return 0
  local _warm="import('file://\$_probe').then(()=>process.exit(0)).catch(()=>process.exit(0))"
  # Bound the warm import when a timeout tool exists. Stock macOS has neither;
  # coreutils installs GNU timeout as \`gtimeout\`. With neither, run unbounded
  # (the import is side-effect-free and self-exits, so this is safe).
  local _warm_to=""
  if command -v timeout >/dev/null 2>&1; then
    _warm_to="timeout 25"
  elif command -v gtimeout >/dev/null 2>&1; then
    _warm_to="gtimeout 25"
  fi
  \$_warm_to env -u AIFY_AGENT_ID -u AIFY_HERMES_GATEWAY_URL node --input-type=module -e "\$_warm" >/dev/null 2>&1 || true
}

# (The retired \`aify_hermes_ensure_daemon\` helper — which brought up the old
# per-agent api_server daemon — was removed in the native-session-id + gateway
# cleanup. Managed/resident hermes now delivers via the hidden gateway host +
# \`hermes-managed-host.js run <agent>\` delivery loop; there is no api_server
# daemon to ensure. \`AIFY_HERMES_DAEMON_CLI\` remains for kill-prior's \`stop\`.)

# Kill any prior sidecar/daemon-cli for THIS agent before launching, so a
# relaunch never leaves two sidecars claiming the same agent (proliferation
# guard, mirrors the intent of the claude/codex wrappers' per-agent ownership).
aify_hermes_kill_prior() {
  local agent_id="\$1"
  # WS1 Task 1.5: optional PID of the delivery loop THIS wrapper just spawned.
  # kill-prior must EXCLUDE it so a concurrent same-agent relaunch can't reap
  # the loop we just started (the self-reap race). Empty when called before the
  # spawn (the pre-spawn call has nothing of ours to protect).
  local exclude_pid="\${2:-}"
  [ -n "\$agent_id" ] || return 0
  # Reap a prior delivery loop carrying this agentId. pkill -f matches the full
  # command line; the AIFY_AGENT_ID marker is the most specific token.
  # Best-effort: no pkill / nothing matched is fine. (The retired
  # hermes-channel.js api_server sidecar is no longer spawned, so it is no longer
  # matched here — the live delivery process is the gateway loop below.)
  if command -v pkill >/dev/null 2>&1; then
    # Managed visible-TUI model: reap a prior background delivery loop
    # (\`hermes-managed-host.js run <agent>\`) for this agent. Its SIGTERM
    # teardown then kills the hidden gateway host it owns. EXCLUDE the
    # just-spawned loop PID so we never kill our own fresh loop.
    if [ -n "\$exclude_pid" ] && command -v pgrep >/dev/null 2>&1; then
      # Enumerate matching loops and kill all EXCEPT the excluded PID.
      local _pid
      for _pid in \$(pgrep -f "hermes-managed-host.js run \$agent_id" 2>/dev/null || true); do
        [ "\$_pid" = "\$exclude_pid" ] && continue
        kill "\$_pid" >/dev/null 2>&1 || true
      done
    else
      pkill -f "hermes-managed-host.js run \$agent_id" >/dev/null 2>&1 || true
    fi
    # Best-effort: reap any orphaned gateway host left listening on this agent's
    # dashboard/api port (a prior SIGKILL bypasses the loop's teardown handler).
    # GATED to the PRE-spawn call ONLY (exclude_pid empty). The POST-spawn call
    # runs AFTER ensure-host started the CURRENT gateway on this port, so port-
    # killing here would kill the live gateway the TUI is about to attach to —
    # the 2026-06-02 "gateway websocket connection failed" root cause.
    if [ -z "\$exclude_pid" ] && command -v lsof >/dev/null 2>&1; then
      local host_port
      host_port="\$(node -e 'import("'"\$AIFY_HERMES_STDIO_DIR"'/hermes-endpoint.js").then(m=>process.stdout.write(String(m.agentPort(process.argv[1]))))' "\$agent_id" 2>/dev/null || true)"
      if [ -n "\$host_port" ]; then
        lsof -ti tcp:"\$host_port" 2>/dev/null | xargs kill >/dev/null 2>&1 || true
      fi
    fi
    # Managed visible-TUI leak fix (MC3, 2026-06-06): reap a prior
    # \`hermes --tui --resume <id>\` visible TUI for THIS agent. The launch now resumes
    # the agent's REAL native session id (timestamp), NOT the retired synthetic
    # \`aify-<agent>\` key — so the old matcher matched nothing (dead matcher) and could
    # leak a duplicate resume-TUI per relaunch. Match the REAL resume id from the per-agent
    # session marker (the id the prior TUI was launched with). AGENT-SCOPED: the timestamp
    # id is unique per session, so this can never hit another agent's TUI. PRE-spawn ONLY
    # (exclude_pid empty): the new TUI does not exist yet, so the only match is the prior
    # one; the post-spawn call must NOT run this. (The lsof/port reap above already catches
    # the prior TUI as a gateway-port client on platforms with lsof; this is the
    # cross-platform, cmdline-based backstop.)
    if [ -z "\$exclude_pid" ] && command -v pkill >/dev/null 2>&1; then
      local _prior_id
      _prior_id="\$(node -e 'import("'"\$AIFY_HERMES_STDIO_DIR"'/hermes-endpoint.js").then(m=>process.stdout.write(String(m.readSessionIdMarker(process.argv[1])||"")))' "\$agent_id" 2>/dev/null || true)"
      if [ -n "\$_prior_id" ]; then
        pkill -f -- "--tui --resume \$_prior_id" >/dev/null 2>&1 || true
      fi
    fi
  fi
  # Also reap the prior per-agent DAEMON for this agentId — PRE-spawn call only
  # (exclude_pid empty), so the post-spawn call never kills the daemon/gateway the
  # current launch just brought up.
  if [ -z "\$exclude_pid" ]; then
    node "\$AIFY_HERMES_DAEMON_CLI" stop "\$agent_id" >/dev/null 2>&1 || true
  fi
}

# Pre-warm the bridge before ANY interactive TUI launch (no passthrough args) so
# hermes' 0.75s MCP discovery sees the comms_* tools. Cheap on the native install
# (~0.3s, page cache); bounded + best-effort so it never blocks the launch.
if [ \${#HERMES_ARGS[@]} -eq 0 ]; then aify_hermes_warm_bridge; fi

# GATEWAY-HOST launch (visible-TUI model) — serves BOTH managed and resident
# agent-id launches (convergence 2026-06-02). A normal \`hermes --tui\` spawns its
# tui_gateway over STDIO, which no external WS injector can reach, so an injected
# aify message can never render in it. The ONLY topology that renders in a visible
# TUI is: one shared \`hermes dashboard --tui\` gateway host that BOTH the visible
# TUI attaches to AND the delivery loop injects into (prompt.submit against the
# visible TUI's discovered sid). The former resident path used the REST api_server
# daemon (renders nothing) + started no delivery loop — that was the resident
# "nothing arrives in my terminal" bug.
#   1. kill-prior: reap a stale delivery loop + gateway host for this agent.
#   2. ensure-host: bring up the HIDDEN per-agent \`hermes dashboard --tui\`
#      gateway host (windowsHide) and learn its {port,token,wsUrl}.
#   3. start the background delivery loop (detached, survives the exec below): it
#      claims channel/resident dispatch runs and prompt.submits them into the
#      visible TUI's session.
#   4. exec \`hermes --tui\` IN THIS PTY, attached to the gateway host and
#      resuming the agent's REAL native session id (from the agent-keyed marker),
#      or a FRESH session on first launch. For a managed launch the PTY
#      is the dashboard console (windowless); for a resident launch the PTY is the
#      operator's own terminal (visible). Same exec, same delivery — the only
#      difference is who owns the PTY. The agent self-replies via comms_send.
# The agent still REGISTERS with its resolved sessionMode (AIFY_SESSION_MODE,
# exported at line ~1270 before this branch), so residents stay resident and
# managed stays managed — only the LAUNCH mechanism is now shared.
if [ -n "\$HERMES_AIFY_AGENT_ID" ] && [ \${#HERMES_ARGS[@]} -eq 0 ]; then
  aify_hermes_kill_prior "\$HERMES_AIFY_AGENT_ID"
  export AIFY_AGENT_ID="\$HERMES_AIFY_AGENT_ID"
  export AIFY_CHANNELS_ENABLED=1
  # Per-agent TUI active-session file: the visible hermes session writes its REAL
  # native session id here, and the in-session MCP bridge (adapters/hermes.js
  # discoverSessionId) reads it to register a real handle on FIRST launch — instead
  # of an EMPTY handle that only gets bound later by the delivery loop's fallback.
  # The plugin (patches.py:_launch_tui) gates the redirect on
  # HERMES_TUI_ACTIVE_SESSION_FILE; the bridge prefers AIFY_HERMES_ACTIVE_SESSION_FILE
  # then falls back to HERMES_TUI_ACTIVE_SESSION_FILE — point BOTH at the SAME path so
  # the writer (TUI/gateway host) and the reader (bridge) agree. Use the marker tmp dir
  # (TEMP||TMP||os.tmpdir(), == \${TMPDIR:-/tmp} here). Exported BEFORE ensure-host and
  # the TUI exec so the gateway host, the exec'd TUI, and the MCP child all inherit it.
  HERMES_AIFY_ACTIVE_SESSION_FILE="\${TMPDIR:-/tmp}/aify-hermes-active-\$HERMES_AIFY_AGENT_ID.json"
  export HERMES_TUI_ACTIVE_SESSION_FILE="\$HERMES_AIFY_ACTIVE_SESSION_FILE"
  export AIFY_HERMES_ACTIVE_SESSION_FILE="\$HERMES_AIFY_ACTIVE_SESSION_FILE"
  # (2) Hidden gateway host → capture {port,token,wsUrl} as ONE JSON line.
  if ! HERMES_HOST_JSON="\$(node "\$AIFY_HERMES_MANAGED_HOST_JS" ensure-host "\$HERMES_AIFY_AGENT_ID")"; then
    echo "[hermes-aify] FATAL: managed gateway host for '\$HERMES_AIFY_AGENT_ID' did not come up." >&2
    echo "[hermes-aify]   (node \$AIFY_HERMES_MANAGED_HOST_JS ensure-host \$HERMES_AIFY_AGENT_ID failed — see the error above)" >&2
    exit 1
  fi
  # Parse the JSON line (mirror the pi-session-state grep/sed extraction above).
  HERMES_TUI_WS_URL="\$(printf '%s' "\$HERMES_HOST_JSON" | sed -E 's/.*"wsUrl"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
  if [ -z "\$HERMES_TUI_WS_URL" ] || [ "\$HERMES_TUI_WS_URL" = "\$HERMES_HOST_JSON" ]; then
    echo "[hermes-aify] FATAL: could not parse wsUrl from gateway-host output: \$HERMES_HOST_JSON" >&2
    exit 1
  fi
  echo "[hermes-aify] managed gateway host ready: \$HERMES_HOST_JSON" >&2
  # Resolve the agent's REAL native hermes session id from the agent-keyed marker
  # \`aify-hermes-session-<agentId>\` (written by the bridge on register/bind via
  # hermes-endpoint.js writeSessionIdMarker). If present we resume that SAME real
  # session — a continuous transcript, symmetric with claude's UUID / codex's
  # thread id. If absent (first launch), we start a FRESH session: hermes assigns
  # a new real id and the bridge captures+stores it on register. The synthetic
  # \`aify-<agentId>\` resume key is gone (no more pre-seed/rename dance).
  HERMES_RESUME_REAL_ID="\$(node -e 'import("'"\$AIFY_HERMES_STDIO_DIR"'/hermes-endpoint.js").then(m=>process.stdout.write(m.readSessionIdMarker(process.argv[1])||"")).catch(()=>{})' "\$HERMES_AIFY_AGENT_ID" 2>/dev/null || true)"
  # (3) Background delivery loop — detached, survives the exec below. Capture
  # its PID so kill-prior can EXCLUDE it (self-reap race) and so we can report
  # which loop we are health-gating on.
  nohup node "\$AIFY_HERMES_MANAGED_HOST_JS" run "\$HERMES_AIFY_AGENT_ID" >/dev/null 2>&1 &
  HERMES_LOOP_PID="\$!"
  disown 2>/dev/null || true
  # Re-run kill-prior EXCLUDING our just-spawned loop, so any racing same-agent
  # relaunch's loop is cleaned up while ours is protected (the pre-spawn call
  # above could not yet know this PID).
  aify_hermes_kill_prior "\$HERMES_AIFY_AGENT_ID" "\$HERMES_LOOP_PID"
  # ORPHAN-LIFECYCLE FIX (FIX SET A1, 2026-06-03): the delivery loop above is
  # detached (nohup … & disown) so it survives the TUI exec — but if we \`exec\`
  # the visible TUI, closing the terminal ORPHANS the loop (and the gateway host
  # it owns) to init: the orphan gateway keeps its own headless session in
  # session.active_list, so the agent stays \`online\` and the loop polls the wrong
  # session (split / no live render). Instead of \`exec\`, run the TUI as a CHILD
  # of this wrapper with an EXIT/INT/TERM trap that SIGTERMs the loop when the TUI
  # ends. Closing the TUI reaps the loop, whose own SIGTERM teardown
  # (installTeardown in hermes-managed-host.js) reaps the gateway host it owns —
  # so the agent flips resident-lost and nothing orphans. This reaps correctly for
  # BOTH the managed (windowless dashboard PTY) and resident (operator terminal)
  # launches: the managed reap was already correct, this keeps it correct.
  _aify_hermes_on_exit() { kill "\$HERMES_LOOP_PID" >/dev/null 2>&1 || true; }
  trap _aify_hermes_on_exit EXIT INT TERM
  # NOTE (2026-06-02 hotfix/restore-hermes-tui): the former "(3a) HEALTH-GATE"
  # (a bounded 30s poll on the loop-ready marker before launching the TUI) was
  # REMOVED. Even non-fatal, it injected wrapper log output into the dashboard
  # PTY ahead of the TUI and could stall the console for up to 30s, so the
  # managed console showed wrapper chatter instead of the clean TUI. The loop is
  # spawned detached above and keeps retrying the gateway + /dispatch/claim on
  # its own; server-side the claimer-lease gate reflects deliverability until the
  # loop is live. Flow is now: spawn loop (+ kill-prior exclude) → exec TUI.
  # (4) The VISIBLE TUI in this PTY, attached to the gateway host + stable session.
  export HERMES_TUI_GATEWAY_URL="\$HERMES_TUI_WS_URL"
  # The aify-comms MCP child (server.js) reads AIFY_HERMES_GATEWAY_URL to set
  # runtime_config.gatewayUrl on register — that is the precondition for
  # resident-run / wakeMode=hermes-live (runtimes.js: gatewayOk = !!gatewayUrl).
  # The v0.15 gateway-host rewrite exported only HERMES_TUI_GATEWAY_URL, so the
  # MCP config '\${AIFY_HERMES_GATEWAY_URL}' interpolated to the literal
  # placeholder and every resident hermes registered as hermes-missing-handle.
  # Export it (same gateway WS URL) + its embedded token before the TUI/MCP
  # child spawn so the bridge registers a real ws:// gatewayUrl.
  export AIFY_HERMES_GATEWAY_URL="\$HERMES_TUI_WS_URL"
  export AIFY_HERMES_GATEWAY_TOKEN="\$(printf '%s' "\$HERMES_TUI_WS_URL" | sed -E 's/.*[?&]token=([^&]+).*/\1/;t;s/.*//')"
  # Use the prebuilt ui-tui bundle when available so the managed TUI does NOT
  # run \`npm run build\` on every launch (slow + noisy in the dashboard
  # console). \`hermes --tui\` skips the build when HERMES_TUI_DIR points at a
  # dir with dist/entry.js. Guard at runtime too in case the dist was removed
  # after install — never break the TUI launch.
  if [ -n "\$AIFY_HERMES_TUI_DIR" ] && [ -f "\$AIFY_HERMES_TUI_DIR/dist/entry.js" ]; then
    export HERMES_TUI_DIR="\$AIFY_HERMES_TUI_DIR"
  fi
  # SESSION CONVERGENCE (FIX C, 2026-06-03): the agent-keyed marker can be DAYS
  # stale, so resuming it blindly makes the VISIBLE TUI view a dead/old session
  # while the agent's real work lands in a gateway-host session the TUI never views
  # (the three-id desync: marker != active-session-file != live gateway session).
  # Now that the gateway is up + its URL exported, ask the gateway for GROUND TRUTH
  # (\`session.active_list\`): prefer the marker id IF it is a live row, else the
  # gateway's most-recent live session. resolve-session PERSISTS the resolved id to
  # the marker AND seeds the per-agent active-session file, so:
  #   visible-TUI resume == delivery-loop target == marker == active-session file.
  # Only runs when the operator did NOT pass an explicit \`--resume\` (that wins).
  # Best-effort + bounded; an empty result (no live session yet) falls through to
  # the marker / fresh-session paths below exactly as before — never blocks launch.
  if [ "\$HERMES_EXPLICIT_SESSION_HANDLE" != "true" ]; then
    HERMES_RESOLVED_SESSION_ID="\$(node "\$AIFY_HERMES_MANAGED_HOST_JS" resolve-session "\$HERMES_AIFY_AGENT_ID" 2>/dev/null | head -n1 | tr -d '[:space:]' || true)"
    if [ -n "\$HERMES_RESOLVED_SESSION_ID" ]; then
      HERMES_RESUME_REAL_ID="\$HERMES_RESOLVED_SESSION_ID"
      echo "[hermes-aify] session convergence: agent '\$HERMES_AIFY_AGENT_ID' resumes live gateway session '\$HERMES_RESUME_REAL_ID' (active_list ground truth)." >&2
    fi
  fi
  # Resume target precedence: (a) an EXPLICIT operator \`--resume <id>\` handle wins
  # (the operator asked for that specific session); (b) else the agent's REAL native
  # session id resolved above — the live gateway session when one exists (FIX C),
  # else the marker (continuous transcript); (c) else a FRESH session with NO
  # \`--resume\` so hermes assigns a new real id (the bridge captures+stores it on
  # register). \`--resume\` MUST precede the operator's passthrough flags.
  if [ "\$HERMES_EXPLICIT_SESSION_HANDLE" = "true" ] && [ -n "\$HERMES_SESSION_HANDLE" ]; then
    # EXPLICIT-RESUME AUTHORITY (BUG 2, 2026-06-03): the operator passed
    # \`--resume <id>\` — <id> is AUTHORITATIVE for the REGISTERED handle, not just
    # the visible TUI. PROACTIVELY SEED the per-agent active-session file AND the
    # session marker (\`aify-hermes-session-<agent>\`) with <id> BEFORE launching the
    # TUI, so the in-session bridge's discoverSessionId reads <id> (active-file
    # primary) and a stale marker can NEVER override it. resolve-session --explicit
    # DB-VALIDATES the id against the gateway SessionDB (2026-06-05) and seeds the
    # active-session file + marker. CRITICAL: we now USE its result as the resume
    # target. A REAL id resumes; a GC'd/dead id yields EMPTY, so we start FRESH cleanly
    # instead of launching --resume on a dead id, which errors 'session not found' and
    # strands the console (the cms-senior-dev / next-* incident). A flaky/absent gateway
    # returns the id unchanged, preserving operator intent. (The bridge ALSO sees
    # AIFY_EXPLICIT_SESSION_HANDLE=true + AIFY_SESSION_HANDLE=<id> as a fallback.)
    HERMES_EXPLICIT_RESOLVED="\$(node "\$AIFY_HERMES_MANAGED_HOST_JS" resolve-session "\$HERMES_AIFY_AGENT_ID" --explicit "\$HERMES_SESSION_HANDLE" 2>/dev/null | head -n1 | tr -d '[:space:]' || true)"
    # FIX SET A1: run as a CHILD (not exec) so the EXIT/INT/TERM trap reaps the
    # delivery loop (and its gateway host) when the TUI closes.
    if [ -n "\$HERMES_EXPLICIT_RESOLVED" ]; then
      echo "[hermes-aify] resuming explicit hermes session '\$HERMES_EXPLICIT_RESOLVED' for agent '\$HERMES_AIFY_AGENT_ID' (DB-validated)." >&2
      # The validated id may differ from the requested handle (durable vs ephemeral) — re-export
      # it so the in-session bridge heartbeats the SESSION ACTUALLY RESUMED, not the stale request.
      export HERMES_SESSION_ID="\$HERMES_EXPLICIT_RESOLVED"
      export AIFY_SESSION_HANDLE="\$HERMES_EXPLICIT_RESOLVED"
      "\$HERMES_RUNTIME_COMMAND" --tui --resume "\$HERMES_EXPLICIT_RESOLVED" "\${HERMES_PERMISSION_FLAGS[@]}"
    else
      echo "[hermes-aify] explicit session '\$HERMES_SESSION_HANDLE' is not resumable (gone from the SessionDB) — starting fresh." >&2
      # CRITICAL (2026-06-05): the requested handle is DEAD and we are starting fresh, so CLEAR
      # the exported handle. Otherwise the in-session bridge heartbeats the dead handle back, aify
      # stores it, and the env bridge re-resumes it on every restart — an infinite dead-handle
      # cycle that never captures the fresh session (the next-tech-lead 'fresh on every stop+start'
      # bug). Cleared, the bridge DISCOVERS the fresh session's real id and reports THAT.
      unset HERMES_SESSION_ID AIFY_SESSION_HANDLE AIFY_EXPLICIT_SESSION_HANDLE HERMES_EXPLICIT_SESSION_HANDLE
      "\$HERMES_RUNTIME_COMMAND" --tui "\${HERMES_PERMISSION_FLAGS[@]}"
    fi
    _aify_hermes_on_exit
    exit \$?
  fi
  if [ -n "\$HERMES_RESUME_REAL_ID" ]; then
    echo "[hermes-aify] resuming real hermes session '\$HERMES_RESUME_REAL_ID' for agent '\$HERMES_AIFY_AGENT_ID'." >&2
    # FIX SET A1: run as a CHILD (not exec) so the EXIT/INT/TERM trap reaps the
    # delivery loop (and its gateway host) when the TUI closes.
    "\$HERMES_RUNTIME_COMMAND" --tui --resume "\$HERMES_RESUME_REAL_ID" "\${HERMES_PERMISSION_FLAGS[@]}"
    _aify_hermes_on_exit
    exit \$?
  fi
  echo "[hermes-aify] no stored session for agent '\$HERMES_AIFY_AGENT_ID' — starting a fresh hermes session (bridge will capture its real id on register)." >&2
  # FIX SET A1: run as a CHILD (not exec) so the EXIT/INT/TERM trap reaps the
  # delivery loop (and its gateway host) when the TUI closes.
  "\$HERMES_RUNTIME_COMMAND" --tui "\${HERMES_PERMISSION_FLAGS[@]}"
  _aify_hermes_on_exit
  exit \$?
fi

# RESIDENT agent-id launch: handled by the unified GATEWAY-HOST branch above
# (convergence 2026-06-02). The former REST api_server-daemon resident path was
# removed — it rendered nothing in the visible TUI (api_server chat does not emit
# to tui_gateway WS clients) and started no delivery loop, so injected aify
# messages never appeared in the operator's terminal. \`aify_hermes_ensure_daemon\`
# / \`AIFY_HERMES_DAEMON_CLI\` remain defined above for any explicit fallback use
# but are no longer the resident default.

aify_hermes_exec_plain_or_tui() {
  # Default to hermes --tui for the operator's interactive TUI when
  # no explicit subcommand args were passed. If the operator passed args
  # (e.g. hermes-aify model list), pass them through unchanged. This helper
  # is used by both the gateway-backed path and the plain-Hermes fallback, so
  # explicit --resume keeps working even when gateway startup fails.
  if [ \${#HERMES_ARGS[@]} -eq 0 ]; then
    if [ "\$HERMES_EXPLICIT_SESSION_HANDLE" = "true" ] && [ -n "\$HERMES_SESSION_HANDLE" ]; then
      exec "\$HERMES_RUNTIME_COMMAND" --tui "\${HERMES_PERMISSION_FLAGS[@]}" --resume "\$HERMES_SESSION_HANDLE"
    fi
    exec "\$HERMES_RUNTIME_COMMAND" --tui "\${HERMES_PERMISSION_FLAGS[@]}"
  fi
  exec "\$HERMES_RUNTIME_COMMAND" "\${HERMES_ARGS[@]}"
}

# Remaining paths (the managed + resident branches above already exec'd/exit'd
# for the agent-id launches): either no --aify-agent was given (operator running
# a plain interactive hermes TUI with no managed identity) or explicit
# passthrough args were supplied (e.g. \`hermes-aify model list\`). Both go
# straight to the runtime with no gateway-host wiring.
aify_hermes_exec_plain_or_tui
EOF
  # Same placeholder-substitute pattern as codex-aify above. Without
  # this the watchdog probe POSTs to 127.0.0.1:8800 regardless of the
  # operator's install-time URL.
  sed -i.bak "s|__AIFY_INSTALL_TIME_URL__|${SERVER_URL:-http://127.0.0.1:8800}|" "$wrapper_tmp" 2>/dev/null && rm -f "$wrapper_tmp.bak" || true
  chmod +x "$wrapper_tmp"
  # Atomic swap over any running wrapper (avoids ETXTBSY on in-place rewrite).
  mv -f "$wrapper_tmp" "$wrapper_path"
  install_windows_cmd_shim "hermes-aify" "$wrapper_dir"
  install_hermes_windows_tui_shim "$wrapper_dir" "$default_server" "$hermes_plugin_path"
}

install_hermes_windows_tui_shim() {
  local wrapper_dir="$1"
  local default_server="$2"
  local hermes_plugin_path="$3"
  # Windows-style path to the repo's host-side MCP stdio bridges, consumed by
  # the native Windows node launched from the .ps1 wrapper.
  local hermes_stdio_dir_win
  hermes_stdio_dir_win="$(path_for_windows_runtime "$AIFY_BRIDGE_DIR")"
  # Prebuilt ui-tui bundle dir (Windows path) so the managed `hermes --tui`
  # skips its per-launch `npm run build` (it runs the prebuilt dist when
  # HERMES_TUI_DIR points at a dir with dist/entry.js). Empty when the dist is
  # not present → hermes builds/locates the TUI as before (never breaks).
  local hermes_tui_dir_win=""
  local _hermes_root_for_tui_win
  _hermes_root_for_tui_win="$(detect_hermes_install_root)"
  if [ -n "$_hermes_root_for_tui_win" ] && [ -f "$_hermes_root_for_tui_win/ui-tui/dist/entry.js" ]; then
    hermes_tui_dir_win="$(path_for_windows_runtime "$_hermes_root_for_tui_win/ui-tui")"
  fi
  local windows_wrapper_dir=""
  local ps_path=""
  local cmd_path=""
  local windows_ps_path=""

  windows_wrapper_dir="$(path_for_windows_runtime "$wrapper_dir")"
  case "$windows_wrapper_dir" in
    [A-Za-z]:\\*) ;;
    *) return 0 ;;
  esac

  ps_path="$wrapper_dir/hermes-aify.ps1"
  cmd_path="$wrapper_dir/hermes-aify.cmd"
  windows_ps_path="$windows_wrapper_dir\\hermes-aify.ps1"

  # Windows PowerShell 5.1 (powershell.exe) decodes a BOM-less .ps1 as the system
  # ANSI codepage (e.g. Windows-1252), which mangles any non-ASCII byte and breaks
  # string literals (an em-dash crashed the wrapper on launch). Emit a UTF-8 BOM so
  # the wrapper is always decoded as UTF-8 regardless of host codepage.
  printf '\xEF\xBB\xBF' > "$ps_path"
  cat >> "$ps_path" <<EOF
\$ErrorActionPreference = 'Stop'
\$InputArgs = @(\$args)

\$HermesAifyAgentId = if (\$env:AIFY_AGENT_ID) { \$env:AIFY_AGENT_ID } else { '' }
\$HermesAifyRole = if (\$env:AIFY_AGENT_ROLE) { \$env:AIFY_AGENT_ROLE } else { 'coder' }
\$HermesAifySessionMode = if (\$env:AIFY_SESSION_MODE) { \$env:AIFY_SESSION_MODE } else { '' }
\$HermesInheritedSessionHandle = if (\$env:HERMES_SESSION_ID) { \$env:HERMES_SESSION_ID } elseif (\$env:HERMES_SESSION) { \$env:HERMES_SESSION } elseif (\$env:AIFY_SESSION_HANDLE) { \$env:AIFY_SESSION_HANDLE } else { '' }
\$HermesSessionHandle = ''
\$HermesExplicitSessionHandle = \$false
if (\$env:AIFY_MANAGED_VIA_WRAPPER -eq '1' -and \$HermesInheritedSessionHandle) {
  \$HermesSessionHandle = \$HermesInheritedSessionHandle
  \$HermesExplicitSessionHandle = \$true
}

function Resolve-HermesRuntimeCommand {
  # Honour explicit env vars only when they actually resolve to a file —
  # hermes' 2026-05-27 release rotated entry points, leaving operator
  # AIFY_HERMES_COMMAND envs pointing at vanished hermes.exe paths.
  # Fall back to a PATH probe of 'hermes' so a stale env doesn't wedge
  # the wrapper, but do NOT auto-substitute hermes-agent / hermes-acp:
  # they are separate entry points that don't implement 'dashboard --tui'.
  foreach (\$candidate in @(\$env:AIFY_HERMES_COMMAND, \$env:HERMES_COMMAND)) {
    if (\$candidate -and (Test-Path -LiteralPath \$candidate)) { return \$candidate }
    if (\$candidate -and (Get-Command \$candidate -ErrorAction SilentlyContinue)) { return \$candidate }
  }
  if (Get-Command hermes -ErrorAction SilentlyContinue) { return 'hermes' }
  return 'hermes'
}
\$HermesRuntimeCommand = Resolve-HermesRuntimeCommand
\$HermesArgs = @()
# Bypass approval prompts by DEFAULT (2026-06-02 — every *-aify bypasses so
# unattended agents never stall). hermes' native flag is --yolo. Opt out with
# --safe / --no-auto. (Parity with the bash hermes-aify wrapper.)
\$HermesAuto = \$true
\$PrevArg = ''
foreach (\$Arg in \$InputArgs) {
  if (\$PrevArg -eq '--aify-agent' -or \$PrevArg -eq '--agent-id') {
    \$HermesAifyAgentId = \$Arg
    \$PrevArg = ''
    continue
  }
  if (\$PrevArg -eq '--aify-role') {
    \$HermesAifyRole = \$Arg
    \$PrevArg = ''
    continue
  }
  if (\$PrevArg -eq '--resume' -or \$PrevArg -eq '--session-id' -or \$PrevArg -eq '-r') {
    \$HermesSessionHandle = \$Arg
    \$HermesExplicitSessionHandle = \$true
    \$PrevArg = ''
    continue
  }
  if (\$Arg -eq '--resident') {
    \$HermesAifySessionMode = 'resident'
    continue
  }
  if (\$Arg -eq '--managed') {
    \$HermesAifySessionMode = 'managed'
    continue
  }
  if (\$Arg -eq '-auto' -or \$Arg -eq '--auto' -or \$Arg -eq '--yolo') {
    \$HermesAuto = \$true
    continue
  }
  if (\$Arg -eq '--safe' -or \$Arg -eq '--no-auto') {
    \$HermesAuto = \$false
    continue
  }
  if (\$Arg -eq '--aify-agent' -or \$Arg -eq '--agent-id' -or \$Arg -eq '--aify-role') {
    \$PrevArg = \$Arg
    continue
  }
  if (\$Arg -like '--aify-agent=*' -or \$Arg -like '--agent-id=*') {
    \$HermesAifyAgentId = (\$Arg -replace '^[^=]*=', '')
    continue
  }
  if (\$Arg -like '--aify-role=*') {
    \$HermesAifyRole = (\$Arg -replace '^[^=]*=', '')
    continue
  }
  if (\$Arg -like '--resume=*' -or \$Arg -like '--session-id=*' -or \$Arg -like '-r=*') {
    \$HermesSessionHandle = (\$Arg -replace '^[^=]*=', '')
    \$HermesExplicitSessionHandle = \$true
    continue
  }
  if (\$Arg -eq '--resume' -or \$Arg -eq '--session-id' -or \$Arg -eq '-r') {
    \$PrevArg = \$Arg
    continue
  }
  \$HermesArgs += \$Arg
  \$PrevArg = \$Arg
}

\$env:AIFY_RUNTIME = 'hermes'
if (-not \$env:AIFY_SERVER_URL) { \$env:AIFY_SERVER_URL = '$default_server' }
if (-not \$env:CLAUDE_MCP_SERVER_URL) { \$env:CLAUDE_MCP_SERVER_URL = \$env:AIFY_SERVER_URL }
if (-not \$env:AIFY_COMMS_URL) { \$env:AIFY_COMMS_URL = \$env:AIFY_SERVER_URL }
\$env:PYTHONUTF8 = if (\$env:PYTHONUTF8) { \$env:PYTHONUTF8 } else { '1' }
\$env:PYTHONIOENCODING = if (\$env:PYTHONIOENCODING) { \$env:PYTHONIOENCODING } else { 'utf-8' }

# GATEWAY-ATTACH DETERMINISM (Task 2.1, parity with the bash wrapper). The Ink
# TUI attaches to HERMES_TUI_GATEWAY_URL when set, else spawns its OWN tui_gateway.
# A stale inherited value (from a prior hermes session, dead port) on a path that
# doesn't re-export a fresh one makes the TUI attach to a dead gateway or run its
# own — so the delivery loop's gateway host shows active_list=0 and messages strand.
# Clear any inherited value up front; the GATEWAY-HOST branch below re-exports the
# correct fresh URL right before its launch.
Remove-Item Env:\\HERMES_TUI_GATEWAY_URL -ErrorAction SilentlyContinue
Remove-Item Env:\\AIFY_HERMES_GATEWAY_URL -ErrorAction SilentlyContinue
Remove-Item Env:\\AIFY_HERMES_GATEWAY_TOKEN -ErrorAction SilentlyContinue

# Harness-native bypass flag, applied to every interactive --tui launch below
# (NOT to passthrough subcommands like 'hermes-aify model list').
\$HermesPermissionFlags = if (\$HermesAuto) { @('--yolo') } else { @() }

if (\$HermesAifyAgentId) {
  \$env:AIFY_AGENT_ID = \$HermesAifyAgentId
  \$env:AIFY_AGENT_ROLE = \$HermesAifyRole
  # FIX 2 (2026-06-03): export the wrapper's cwd so hermes' \${AIFY_AGENT_CWD}
  # interpolation in the config.yaml MCP env block resolves to a real path
  # (the wrapper runs in the agent's working directory).
  if (-not \$env:AIFY_AGENT_CWD) { \$env:AIFY_AGENT_CWD = (Get-Location).Path }
}
if (\$HermesExplicitSessionHandle -and \$HermesSessionHandle) {
  \$env:HERMES_SESSION_ID = \$HermesSessionHandle
  \$env:AIFY_SESSION_HANDLE = \$HermesSessionHandle
  \$env:AIFY_EXPLICIT_SESSION_HANDLE = 'true'
} else {
  Remove-Item Env:\\HERMES_SESSION_ID -ErrorAction SilentlyContinue
  Remove-Item Env:\\HERMES_SESSION -ErrorAction SilentlyContinue
  Remove-Item Env:\\AIFY_SESSION_HANDLE -ErrorAction SilentlyContinue
  Remove-Item Env:\\AIFY_EXPLICIT_SESSION_HANDLE -ErrorAction SilentlyContinue
}

if (-not \$HermesAifySessionMode) {
  \$HermesAifySessionMode = if ([Console]::IsInputRedirected) { 'managed' } else { 'resident' }
}
\$env:AIFY_SESSION_MODE = \$HermesAifySessionMode

\$env:AIFY_HERMES_PLUGIN = if (\$env:AIFY_HERMES_PLUGIN) { \$env:AIFY_HERMES_PLUGIN } else { '1' }
\$env:AIFY_HERMES_PLUGIN_PATH = '$hermes_plugin_path'
if (\$env:AIFY_HERMES_DISABLE_PLUGIN -eq '1') {
  Remove-Item Env:\\AIFY_HERMES_PLUGIN -ErrorAction SilentlyContinue
} elseif (\$env:AIFY_HERMES_PLUGIN_PATH) {
  if (\$env:PYTHONPATH) {
    \$env:PYTHONPATH = "\$env:AIFY_HERMES_PLUGIN_PATH;\$env:PYTHONPATH"
  } else {
    \$env:PYTHONPATH = \$env:AIFY_HERMES_PLUGIN_PATH
  }
}

function Invoke-HermesRuntime {
  param([string[]]\$RunArgs)
  # The interactive hermes TUI writes to stderr during a normal session; under the
  # script-level \$ErrorActionPreference='Stop', PowerShell 5.1 escalates native
  # stderr to a TERMINATING NativeCommandError, which would kill the wrapper (and
  # reap the delivery loop) mid-session. Relax to 'Continue' here — preference vars
  # are function-scoped, so this reverts automatically on return and does not affect
  # the rest of the script's fail-fast behavior.
  \$ErrorActionPreference = 'Continue'
  & \$HermesRuntimeCommand @RunArgs
  if (\$null -eq \$global:LASTEXITCODE) {
    \$script:HermesRuntimeExitCode = 0
  } else {
    \$script:HermesRuntimeExitCode = [int]\$global:LASTEXITCODE
  }
}

# Per-agent daemon + channel-sidecar model (Plan 1.4, 2026-05-30). Replaces the
# old 'hermes dashboard --tui' + 'hermes --tui' dual-spawn. Bridges live in the
# repo (never copied — security fixes flow automatically).
\$AifyHermesStdioDir = '$hermes_stdio_dir_win'
\$AifyHermesDaemonCli = Join-Path \$AifyHermesStdioDir 'hermes-daemon-cli.js'
# Managed visible-TUI model (Plan 2026-05-31): the per-agent hidden gateway host
# (ensure-host) + background delivery loop (run) live here.
\$AifyHermesManagedHostJs = Join-Path \$AifyHermesStdioDir 'hermes-managed-host.js'
# Loop ready-marker helper (WS1 Task 1.5): the wrapper health-gates on
# 'aify-hermes-loop-ready-<agent>' before launching the visible TUI so a TUI
# that can't receive work never shows (visible-TUI HARD requirement).
\$AifyHermesLoopReadyJs = Join-Path \$AifyHermesStdioDir 'hermes-loop-ready.js'
# Prebuilt ui-tui bundle dir (baked at install time). When set + dist/entry.js
# exists, the managed branch exports HERMES_TUI_DIR so 'hermes --tui' runs the
# prebuilt bundle and skips the per-launch 'npm run build'. Empty → hermes
# builds/locates the TUI as before (no break).
\$AifyHermesTuiDir = '$hermes_tui_dir_win'

# (The retired Invoke-AifyHermesEnsureDaemon helper — the PowerShell mirror of the
# removed bash aify_hermes_ensure_daemon — was removed in the native-session-id +
# gateway cleanup. Managed/resident hermes now delivers via the hidden gateway host
# + hermes-managed-host.js delivery loop; there is no api_server daemon to ensure.
# \$AifyHermesDaemonCli remains for kill-prior's \`stop\`.)

# Kill any prior sidecar for THIS agent before launching (proliferation guard).
function Invoke-AifyHermesKillPrior {
  # WS1 Task 1.5: \$ExcludeLoopPid is the PID of the delivery loop THIS wrapper
  # just spawned. kill-prior must EXCLUDE it so a concurrent same-agent relaunch
  # can't reap the loop we just started (the self-reap race). 0 / empty when
  # called before the spawn (nothing of ours to protect yet).
  param([string]\$AgentId, [int]\$ExcludeLoopPid = 0)
  if (-not \$AgentId) { return }
  try {
    Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" -ErrorAction SilentlyContinue |
      Where-Object { \$_.CommandLine -and \$_.CommandLine -match 'hermes-channel\\.js' -and \$_.CommandLine -match [regex]::Escape(\$AgentId) } |
      ForEach-Object { try { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }
  } catch {}
  # Managed visible-TUI model: reap a prior background delivery loop
  # ('hermes-managed-host.js run <agent>') for this agent. Its SIGTERM teardown
  # then kills the hidden gateway host it owns. Match the managed-host script +
  # the agent id on the command line, but EXCLUDE the just-spawned loop PID.
  try {
    Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" -ErrorAction SilentlyContinue |
      Where-Object { \$_.CommandLine -and \$_.CommandLine -match 'hermes-managed-host\\.js' -and \$_.CommandLine -match [regex]::Escape(\$AgentId) } |
      Where-Object { \$ExcludeLoopPid -le 0 -or \$_.ProcessId -ne \$ExcludeLoopPid } |
      ForEach-Object { try { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }
  } catch {}
  # Best-effort: reap any orphaned gateway host left listening on this agent's
  # dashboard/api port. GATED to the PRE-spawn call ONLY (\$ExcludeLoopPid -le 0):
  # the POST-spawn call runs AFTER ensure-host started the CURRENT gateway on this
  # port, so port-killing here would kill the live gateway the TUI is about to
  # attach to — the 2026-06-02 "gateway websocket connection failed" root cause.
  if (\$ExcludeLoopPid -le 0) {
    try {
      \$hostPort = & node -e 'import(process.argv[1]).then(m=>process.stdout.write(String(m.agentPort(process.argv[2]))))' (Join-Path \$AifyHermesStdioDir 'hermes-endpoint.js') \$AgentId 2>\$null
      if (\$hostPort) {
        Get-NetTCPConnection -State Listen -LocalPort ([int]\$hostPort) -ErrorAction SilentlyContinue |
          ForEach-Object { try { Stop-Process -Id \$_.OwningProcess -Force -ErrorAction SilentlyContinue } catch {} }
      }
    } catch {}
    # Also reap the prior per-agent DAEMON for this agentId (pre-spawn only, so the
    # post-spawn call never kills the daemon/gateway the current launch brought up).
    try { & node \$AifyHermesDaemonCli stop \$AgentId 2>\$null | Out-Null } catch {}
    # Managed visible-TUI leak fix (MC3, 2026-06-06): reap a prior
    # 'hermes(.exe)? --tui --resume <id>' visible TUI for THIS agent. The launch resumes
    # the agent's REAL native session id (timestamp), NOT the retired 'aify-<agent>' key —
    # so the old matcher matched nothing and leaked a duplicate resume-TUI per relaunch
    # (and the port reap above only catches the gateway LISTENER, not the TUI client, on
    # Windows). Match the REAL resume id from the per-agent session marker (what the prior
    # TUI was launched with). AGENT-SCOPED: the timestamp id is unique per session. PRE-spawn
    # ONLY (\$ExcludeLoopPid -le 0): the new TUI does not exist yet, so only the prior matches.
    try {
      \$priorId = & node -e 'import(process.argv[1]).then(m=>process.stdout.write(String(m.readSessionIdMarker(process.argv[2])||"")))' (Join-Path \$AifyHermesStdioDir 'hermes-endpoint.js') \$AgentId 2>\$null
      \$priorId = if (\$priorId) { "\$priorId".Trim() } else { '' }
      if (\$priorId) {
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
          Where-Object { \$_.CommandLine -and \$_.CommandLine -match '--tui\\s+--resume\\s+' + [regex]::Escape(\$priorId) + '(\\s|\$)' } |
          ForEach-Object { try { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }
      }
    } catch {}
  }
}

\$script:HermesRuntimeExitCode = 0

# MANAGED launch (visible-TUI model, Plan 2026-05-31): --aify-agent present AND
# session-mode managed AND no passthrough args.
#   1. kill-prior: reap a stale delivery loop + gateway host for this agent.
#   2. ensure-host: bring up the HIDDEN per-agent 'hermes dashboard --tui' gateway
#      host (windowsHide) and learn its {port,token,wsUrl}.
#   3. start the background delivery loop (hidden window, survives this script):
#      it claims dispatch runs and prompt.submits them into the TUI's session.
#   4. run 'hermes --tui' IN THIS PTY, attached to the gateway host + resuming the
#      agent's REAL native hermes session id (native-session-id model, 2026-06-03):
#      explicit operator --resume wins, else the resolved/live gateway session
#      (resolve-session ground truth) or the agent-keyed marker, else a FRESH
#      session — the REAL TUI renders windowless in the dashboard console. The
#      in-session agent self-replies via comms_send.
if (\$HermesAifyAgentId -and \$HermesArgs.Count -eq 0) {
  Invoke-AifyHermesKillPrior \$HermesAifyAgentId
  \$env:AIFY_AGENT_ID = \$HermesAifyAgentId
  \$env:AIFY_CHANNELS_ENABLED = '1'
  # Per-agent active-session file (parity with bash, 2026-06-03). The plugin
  # (patches.py:_launch_tui) gates the redirect on HERMES_TUI_ACTIVE_SESSION_FILE;
  # the bridge prefers AIFY_HERMES_ACTIVE_SESSION_FILE then falls back to
  # HERMES_TUI_ACTIVE_SESSION_FILE — point BOTH at the SAME path so the writer
  # (TUI/gateway host) and the reader (bridge) agree. Use the marker tmp dir
  # (TEMP||TMP||GetTempPath, == \${TMPDIR:-/tmp} on the bash side). Exported BEFORE
  # ensure-host and the TUI launch so the gateway host, the TUI, and the MCP child
  # all inherit it. PS 5.1-safe (no ?? operator — the .cmd shim runs Windows
  # PowerShell).
  \$hermesActiveTmpDir = \$env:TEMP
  if ([string]::IsNullOrEmpty(\$hermesActiveTmpDir)) { \$hermesActiveTmpDir = \$env:TMP }
  if ([string]::IsNullOrEmpty(\$hermesActiveTmpDir)) { \$hermesActiveTmpDir = [System.IO.Path]::GetTempPath() }
  \$hermesActiveFile = Join-Path \$hermesActiveTmpDir ("aify-hermes-active-" + \$HermesAifyAgentId + ".json")
  \$env:HERMES_TUI_ACTIVE_SESSION_FILE = \$hermesActiveFile
  \$env:AIFY_HERMES_ACTIVE_SESSION_FILE = \$hermesActiveFile
  # (2) Hidden gateway host → capture {port,token,wsUrl} as ONE JSON line.
  \$hermesHostJson = & node \$AifyHermesManagedHostJs ensure-host \$HermesAifyAgentId
  if (\$LASTEXITCODE -ne 0 -or -not \$hermesHostJson) {
    [Console]::Error.WriteLine("[hermes-aify] FATAL: managed gateway host for '\$HermesAifyAgentId' did not come up.")
    [Console]::Error.WriteLine("[hermes-aify]   (node \$AifyHermesManagedHostJs ensure-host \$HermesAifyAgentId failed -- see the error above)")
    exit 1
  }
  try {
    \$hermesHost = \$hermesHostJson | ConvertFrom-Json
  } catch {
    [Console]::Error.WriteLine("[hermes-aify] FATAL: could not parse gateway-host output: \$hermesHostJson")
    exit 1
  }
  if (-not \$hermesHost.wsUrl) {
    [Console]::Error.WriteLine("[hermes-aify] FATAL: gateway-host output missing wsUrl: \$hermesHostJson")
    exit 1
  }
  [Console]::Error.WriteLine("[hermes-aify] managed gateway host ready: \$hermesHostJson")
  # Resolve target for the agent's REAL native hermes session id. Populated by the
  # \`resolve-session\` convergence call below (after the gateway is up): it returns
  # the live gateway session when one exists, else FALLS BACK to the agent-keyed
  # marker (\`aify-hermes-session-<agentId>\`, written by the bridge on register), so
  # a separate marker read here is redundant. (We deliberately do NOT read the
  # marker via an inline \`node -e\` here: PowerShell 5.1's native-argument passing
  # mangles a -e script that contains embedded double-quotes — e.g. require("url"),
  # ||"" — handing node a malformed script that fails with [eval]:1 and kills the
  # wrapper. \`resolve-session\` is invoked with simple args, so it is PS-safe.)
  # If neither a live session nor a marker exists (first launch), this stays empty
  # and we start a FRESH session; the bridge captures+stores its real id on register.
  \$hermesResumeRealId = ""
  # (3) Background delivery loop — hidden window, survives this script. Capture
  # its PID (-PassThru) so kill-prior can EXCLUDE it (self-reap race) and so we
  # can health-gate on the loop we actually started.
  \$hermesLoopProc = Start-Process -WindowStyle Hidden -FilePath node \`
    -ArgumentList @(\$AifyHermesManagedHostJs, 'run', \$HermesAifyAgentId) -PassThru
  \$hermesLoopPid = if (\$hermesLoopProc) { [int]\$hermesLoopProc.Id } else { 0 }
  # Re-run kill-prior EXCLUDING our just-spawned loop, so any racing same-agent
  # relaunch's loop is cleaned up while ours is protected (the pre-spawn call
  # could not yet know this PID).
  Invoke-AifyHermesKillPrior \$HermesAifyAgentId \$hermesLoopPid
  # NOTE (2026-06-02 hotfix/restore-hermes-tui): the former "(3a) HEALTH-GATE"
  # (a bounded 30s poll on the loop-ready marker before launching the TUI) was
  # REMOVED. Even non-fatal, it injected wrapper log output into the dashboard
  # PTY ahead of the TUI and could stall the console for up to 30s, so the
  # managed console showed wrapper chatter instead of the clean TUI. The loop is
  # spawned hidden above and keeps retrying the gateway + /dispatch/claim on its
  # own; server-side the claimer-lease gate reflects deliverability until the
  # loop is live. Flow is now: spawn loop (+ kill-prior exclude) → Invoke TUI.
  # (4) The VISIBLE TUI in this PTY, attached to the gateway host + real session.
  \$env:HERMES_TUI_GATEWAY_URL = \$hermesHost.wsUrl
  # The aify-comms MCP child (server.js) reads AIFY_HERMES_GATEWAY_URL to set
  # runtime_config.gatewayUrl on register — the precondition for resident-run /
  # wakeMode=hermes-live (runtimes.js: gatewayOk = !!gatewayUrl). Export it (same
  # gateway WS URL) + its embedded token so the bridge registers a real ws://
  # gatewayUrl instead of the literal '\${AIFY_HERMES_GATEWAY_URL}' placeholder.
  \$env:AIFY_HERMES_GATEWAY_URL = \$hermesHost.wsUrl
  if (\$hermesHost.wsUrl -match '[?&]token=([^&]+)') { \$env:AIFY_HERMES_GATEWAY_TOKEN = \$Matches[1] }
  # Use the prebuilt ui-tui bundle when present so the managed TUI does NOT run
  # 'npm run build' on every launch. Guard at runtime in case the dist was
  # removed after install — never break the TUI launch.
  if (\$AifyHermesTuiDir -and (Test-Path (Join-Path \$AifyHermesTuiDir 'dist/entry.js'))) {
    \$env:HERMES_TUI_DIR = \$AifyHermesTuiDir
  }
  # SESSION CONVERGENCE (FIX C, 2026-06-03): the agent-keyed marker can be DAYS
  # stale, so resuming it blindly makes the VISIBLE TUI view a dead/old session
  # while the agent's real work lands in a gateway-host session the TUI never views.
  # Now that the gateway is up + its URL exported, ask the gateway for GROUND TRUTH
  # (session.active_list): prefer the marker id IF it is a live row, else the
  # gateway's most-recent live session. resolve-session PERSISTS the resolved id to
  # the marker AND seeds the per-agent active-session file, so visible-TUI resume ==
  # delivery-loop target == marker == active-session file. Only runs when the
  # operator did NOT pass an explicit --resume (that wins). Best-effort + bounded;
  # an empty result falls through to the marker / fresh-session paths below.
  if (-not (\$HermesExplicitSessionHandle -and \$HermesSessionHandle)) {
    # PowerShell 5.1 escalates a native command's stderr to a TERMINATING
    # NativeCommandError under \$ErrorActionPreference='Stop' (even with 2>\$null).
    # resolve-session logs progress to stderr, so relax EAP to 'Continue' for just
    # this best-effort call: stderr is dropped, stdout (the resolved id) is captured,
    # and a normal stderr log can no longer kill the wrapper.
    \$eapPrev = \$ErrorActionPreference; \$ErrorActionPreference = 'Continue'
    \$hermesResolvedSessionId = & node \$AifyHermesManagedHostJs resolve-session \$HermesAifyAgentId 2>\$null | Select-Object -First 1
    \$ErrorActionPreference = \$eapPrev
    if (\$null -ne \$hermesResolvedSessionId) { \$hermesResolvedSessionId = ("\$hermesResolvedSessionId").Trim() }
    if (-not [string]::IsNullOrEmpty(\$hermesResolvedSessionId)) {
      \$hermesResumeRealId = \$hermesResolvedSessionId
      [Console]::Error.WriteLine("[hermes-aify] session convergence: agent '\$HermesAifyAgentId' resumes live gateway session '\$hermesResumeRealId' (active_list ground truth).")
    }
  }
  # Resume target precedence: (a) an EXPLICIT operator --resume <id> handle wins;
  # (b) else the agent's REAL native session id resolved above — the live gateway
  # session when one exists (FIX C), else the marker (continuous transcript);
  # (c) else a FRESH session with NO --resume so hermes assigns a new real id (the
  # bridge captures+stores it on register).
  # ORPHAN-LIFECYCLE FIX (parity with the bash trap, 2026-06-03): the delivery
  # loop above is a detached hidden process that survives this script — but if the
  # TUI exits (or is Ctrl-C'd) and we just \`exit\`, the loop (and the hidden gateway
  # host it owns) is ORPHANED: the orphan gateway keeps a headless session in
  # session.active_list, so the agent stays \`online\` and the loop polls the wrong
  # session. Wrap the TUI in try/finally so closing the TUI ALWAYS reaps the loop;
  # the loop's own SIGTERM teardown then reaps the gateway host — nothing orphans.
  try {
    if (\$HermesExplicitSessionHandle -and \$HermesSessionHandle) {
      # EXPLICIT-RESUME AUTHORITY + DB-VALIDATE (PS1 parity with bash 16de796 + 98bcc91,
      # 2026-06-05): resolve-session --explicit DB-validates <id> against the gateway
      # SessionDB and seeds the active-session file + marker. We USE its result as the
      # resume target: a REAL id resumes; a GC'd/dead id yields EMPTY so we start FRESH
      # cleanly (launching --resume on a dead id errors 'session not found' and strands
      # the console). A flaky/absent gateway returns <id> unchanged (operator intent kept).
      # (EAP relaxed: PS 5.1 turns native stderr into a terminating error under 'Stop'.)
      \$eapPrev = \$ErrorActionPreference; \$ErrorActionPreference = 'Continue'
      \$HermesExplicitResolved = (& node \$AifyHermesManagedHostJs resolve-session \$HermesAifyAgentId --explicit \$HermesSessionHandle 2>\$null | Select-Object -First 1)
      \$ErrorActionPreference = \$eapPrev
      if (\$HermesExplicitResolved) { \$HermesExplicitResolved = \$HermesExplicitResolved.Trim() }
      if (-not [string]::IsNullOrEmpty(\$HermesExplicitResolved)) {
        [Console]::Error.WriteLine("[hermes-aify] resuming explicit hermes session '\$HermesExplicitResolved' for agent '\$HermesAifyAgentId' (DB-validated).")
        # Re-export the VALIDATED id (durable vs ephemeral) so the in-session bridge
        # heartbeats the session actually resumed, not the stale request.
        \$env:HERMES_SESSION_ID = \$HermesExplicitResolved
        \$env:AIFY_SESSION_HANDLE = \$HermesExplicitResolved
        Invoke-HermesRuntime (@('--tui', '--resume', \$HermesExplicitResolved) + \$HermesPermissionFlags)
      } else {
        [Console]::Error.WriteLine("[hermes-aify] explicit session '\$HermesSessionHandle' is not resumable (gone from the SessionDB) -- starting fresh.")
        # CRITICAL (parity with bash 98bcc91): the requested handle is DEAD and we start
        # fresh, so CLEAR the exported handle. Otherwise the in-session bridge heartbeats
        # the dead handle back, aify stores it, and the env bridge re-resumes it on every
        # restart -- the dead-handle cycle that never captures the fresh session.
        Remove-Item Env:HERMES_SESSION_ID -ErrorAction SilentlyContinue
        Remove-Item Env:AIFY_SESSION_HANDLE -ErrorAction SilentlyContinue
        Remove-Item Env:AIFY_EXPLICIT_SESSION_HANDLE -ErrorAction SilentlyContinue
        Remove-Item Env:HERMES_EXPLICIT_SESSION_HANDLE -ErrorAction SilentlyContinue
        Invoke-HermesRuntime (@('--tui') + \$HermesPermissionFlags)
      }
    } elseif (-not [string]::IsNullOrEmpty(\$hermesResumeRealId)) {
      [Console]::Error.WriteLine("[hermes-aify] resuming real hermes session '\$hermesResumeRealId' for agent '\$HermesAifyAgentId'.")
      Invoke-HermesRuntime (@('--tui', '--resume', \$hermesResumeRealId) + \$HermesPermissionFlags)
    } else {
      [Console]::Error.WriteLine("[hermes-aify] no stored session for agent '\$HermesAifyAgentId' -- starting a fresh hermes session (bridge will capture its real id on register).")
      Invoke-HermesRuntime (@('--tui') + \$HermesPermissionFlags)
    }
  } finally {
    if (\$hermesLoopPid -gt 0) {
      try { Stop-Process -Id \$hermesLoopPid -Force -ErrorAction SilentlyContinue } catch {}
    }
  }
  exit \$script:HermesRuntimeExitCode
}

# RESIDENT agent-id launch: handled by the unified GATEWAY-HOST branch above
# (convergence 2026-06-02, parity with the bash wrapper). The former REST
# api_server-daemon resident path was removed — it rendered nothing in the
# visible TUI (api_server chat does not emit to tui_gateway WS clients) and
# started no delivery loop, so injected aify messages never appeared in the
# operator's terminal.

# Remaining paths: no --aify-agent (plain interactive TUI) or explicit
# passthrough args (e.g. 'hermes-aify model list'). Go straight to the runtime
# with no gateway-host wiring.
if (\$HermesArgs.Count -eq 0) {
  if (\$HermesExplicitSessionHandle -and \$HermesSessionHandle) {
    Invoke-HermesRuntime (@('--tui', '--resume', \$HermesSessionHandle) + \$HermesPermissionFlags)
    exit \$script:HermesRuntimeExitCode
  }
  Invoke-HermesRuntime (@('--tui') + \$HermesPermissionFlags)
  exit \$script:HermesRuntimeExitCode
}
Invoke-HermesRuntime \$HermesArgs
exit \$script:HermesRuntimeExitCode
EOF

  cat > "$cmd_path" <<EOF
@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$windows_ps_path" %*
set "AIFY_EXIT=%ERRORLEVEL%"
endlocal & exit /b %AIFY_EXIT%
EOF
}

install_bridge_launcher() {
  local wrapper_dir="$HOME/.local/bin"
  local wrapper_path="$wrapper_dir/aify-comms"
  local default_server="${SERVER_URL:-$DEFAULT_AIFY_SERVER_URL}"
  mkdir -p "$wrapper_dir"
cat > "$wrapper_path" <<EOF
#!/bin/bash
set -euo pipefail

SAFE_CWD="\$(pwd -P 2>/dev/null || true)"
if [ -z "\$SAFE_CWD" ] || [ ! -d "\$SAFE_CWD" ]; then
  echo "aify-comms: current directory no longer exists; using \$HOME as the bridge root." >&2
  cd "\$HOME"
  SAFE_CWD="\$(pwd -P)"
fi

SERVER_URL="\${AIFY_SERVER_URL:-$default_server}"
if [ "\${1:-}" = "--version" ] || [ "\${1:-}" = "-V" ]; then
  # Host bridge version stamp (baked at install time). $AIFY_NATIVE_BASE and
  # $SCRIPT_DIR below are the install-time literals; everything network/git is
  # best-effort and MUST fail silently (offline-safe).
  STAMP_FILE="$AIFY_NATIVE_BASE/.aify-version"
  REPO_DIR="$SCRIPT_DIR"
  echo "aify-comms host bridge:"
  if [ -f "\$STAMP_FILE" ]; then
    sed 's/^/  /' "\$STAMP_FILE" 2>/dev/null || cat "\$STAMP_FILE" 2>/dev/null || true
  else
    echo "  (no version stamp; reinstall to generate \$STAMP_FILE)"
  fi
  # Fresh remote check: fetch before counting behind (honor the version-check rule).
  LOCAL_SHA="\$(grep '^sha=' "\$STAMP_FILE" 2>/dev/null | cut -d= -f2- || true)"
  if command -v git >/dev/null 2>&1 && [ -n "\${LOCAL_SHA:-}" ] && [ "\$LOCAL_SHA" != "unknown" ] \\
     && git -C "\$REPO_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "\$REPO_DIR" fetch -q origin main 2>/dev/null || true
    BEHIND="\$(git -C "\$REPO_DIR" rev-list --count "\$LOCAL_SHA"..origin/main 2>/dev/null || true)"
    if [ -n "\${BEHIND:-}" ]; then
      if [ "\$BEHIND" = "0" ]; then
        echo "  host: up to date with origin/main"
      else
        echo "  host: \$BEHIND commit(s) behind origin/main — run git pull && ./redeploy.sh"
      fi
    fi
  fi
  # Backend version (best-effort; silent on any failure).
  if command -v curl >/dev/null 2>&1; then
    BACKEND_JSON="\$(curl -s --max-time 3 "\$SERVER_URL/version" 2>/dev/null || true)"
    if [ -n "\${BACKEND_JSON:-}" ]; then
      echo "  backend: \$BACKEND_JSON"
    fi
  fi
  exit 0
fi
if [ "\${1:-}" = "--help" ] || [ "\${1:-}" = "-h" ]; then
  cat <<'USAGE'
Usage: aify-comms [server-url] [extra-root ...]

Starts the local environment bridge for dashboard-managed agents.
The current directory is always an allowed workspace root. Extra roots are
optional safety boundaries.

  --version, -V   Print the installed host bridge SHA and check origin/main
                  and the backend for a behind-count (offline-safe).
USAGE
  exit 0
fi
if [ "\${1:-}" != "" ] && [[ "\${1:-}" == http* ]]; then
  SERVER_URL="\$1"
  shift
fi
if [ "\${1:-}" != "" ] && [[ "\${1:-}" == -* ]]; then
  echo "aify-comms: unknown option '\$1'. Run 'aify-comms --help' for usage." >&2
  exit 2
fi

ROOTS="\$(node - "\$SAFE_CWD" "\${AIFY_CWD_ROOTS:-}" "\$@" <<'NODE'
const path = require("path");
const [cwd, envRoots, ...extraRoots] = process.argv.slice(2);
const roots = [cwd];
if (envRoots) roots.push(...String(envRoots).split(path.delimiter));
roots.push(...extraRoots);
const seen = new Set();
const result = [];
const skipped = [];
for (const raw of roots) {
  const value = String(raw || "").trim();
  if (value.startsWith("-")) {
    skipped.push(value);
    continue;
  }
  if (!value || seen.has(value)) continue;
  seen.add(value);
  result.push(value);
}
if (skipped.length) {
  console.error("aify-comms: ignored invalid root argument(s): " + skipped.join(", "));
}
console.log(result.join(path.delimiter));
NODE
)"

export AIFY_SERVER_URL="\$SERVER_URL"
export AIFY_CWD_ROOTS="\$ROOTS"

echo "aify-comms bridge"
echo "  server: \$AIFY_SERVER_URL"
echo "  roots:  \$AIFY_CWD_ROOTS"
echo "  stop:   Ctrl+C"
cd "\$SAFE_CWD"
exec node "$AIFY_BRIDGE_DIR/server.js" --environment-bridge
EOF
  chmod +x "$wrapper_path"
  install_windows_cmd_shim "aify-comms" "$wrapper_dir"
}

is_git_bash_windows() {
  case "$(uname -s 2>/dev/null || echo '')" in
    MINGW*|MSYS*|CYGWIN*) return 0 ;;
    *) return 1 ;;
  esac
}

hermes_runtime_is_native_windows() {
  # True when the resolved `hermes` binary will execute under native Windows.
  # On WSL, `wslpath` is always available, but hermes may be EITHER a Linux
  # binary installed inside WSL OR a Windows .exe reached via WSL interop.
  # path_for_windows_runtime would convert paths for the Windows case; for
  # Linux hermes on WSL, those paths are meaningless and the plugin silently
  # fails to load, surfacing downstream as "gateway exited" in the TUI.
  if is_git_bash_windows; then
    return 0
  fi
  local hermes_bin resolved
  hermes_bin="$(hermes_cmd 2>/dev/null || true)"
  [ -z "$hermes_bin" ] && return 1
  # Task #174: resolve through symlinks so a WSL symlink pointing at a
  # Windows hermes.exe under /mnt/<drive>/ is classified correctly. For a
  # plain PATH executable this resolves to the same path as before.
  resolved="$(resolve_hermes_real_bin "$hermes_bin")"
  case "$resolved" in
    *.exe|*.EXE|*.cmd|*.CMD|*.bat|*.BAT) return 0 ;;
    /mnt/[a-zA-Z]/*) return 0 ;;
  esac
  return 1
}

path_for_node() {
  local value="$1"
  if is_git_bash_windows && command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$value"
    return
  fi
  printf '%s\n' "$value"
}

path_for_windows_runtime() {
  # Paths embedded into native Windows runtime config/env are consumed later
  # by Windows Node/Python, not by this installer process. Under WSL the
  # installer can read /mnt/wsl/docker-desktop-bind-mounts/..., but native
  # Hermes cannot. Prefer a drive-letter path when wslpath can resolve one.
  local value="$1"
  local converted=""
  if is_git_bash_windows && command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$value"
    return
  fi
  if command -v wslpath >/dev/null 2>&1; then
    converted="$(wslpath -w "$value" 2>/dev/null || true)"
    case "$converted" in
      [A-Za-z]:\\*) printf '%s\n' "$converted"; return ;;
    esac
  fi
  printf '%s\n' "$value"
}

shell_quote() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

hermes_config_root() {
  # Hermes home is profile-/install-aware.  Native Windows Hermes commonly
  # runs with HERMES_HOME under AppData\Local\hermes, so writing unconditionally
  # to ~/.hermes leaves the active Hermes with no MCP server configured.
  if [ -n "${HERMES_HOME:-}" ]; then
    printf '%s\n' "$HERMES_HOME"
    return
  fi
  local hermes_bin=""
  hermes_bin="$(hermes_cmd 2>/dev/null || true)"
  if [ -n "$hermes_bin" ]; then
    local cfg_path=""
    cfg_path="$("$hermes_bin" config path 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
    if [ -n "$cfg_path" ]; then
      dirname "$cfg_path"
      return
    fi
  fi
  printf '%s\n' "$HOME/.hermes"
}

hook_command_for_node_script() {
  local node_script="$1"
  if is_git_bash_windows; then
    printf 'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath $env:USERPROFILE; & node %s"' "$(shell_quote "$node_script")"
    return
  fi
  if command -v env >/dev/null 2>&1 && env --help 2>/dev/null | grep -q -- ' -C'; then
    printf 'env -C "$HOME" node %s' "$(shell_quote "$node_script")"
    return
  fi
  printf 'sh -lc %s _ %s' "$(shell_quote 'cd "$HOME" 2>/dev/null || cd /; exec node "$1"')" "$(shell_quote "$node_script")"
}

install_windows_cmd_shim() {
  local wrapper_name="$1"
  local wrapper_dir="$2"
  local wrapper_path="$wrapper_dir/$wrapper_name"
  local shim_path="$wrapper_dir/$wrapper_name.cmd"
  local bash_path=""
  local windows_wrapper_path=""
  local windows_wrapper_dir=""

  if ! is_git_bash_windows; then
    return 0
  fi
  if ! command -v cygpath >/dev/null 2>&1; then
    return 0
  fi

  bash_path="$(cygpath -w "$(command -v bash)")"
  windows_wrapper_path="$(cygpath -w "$wrapper_path")"
  windows_wrapper_dir="$(cygpath -w "$wrapper_dir")"

  cat > "$shim_path" <<EOF
@echo off
setlocal
for %%I in ("$bash_path") do set "AIFY_BASH_DIR=%%~dpI"
set "PATH=%AIFY_BASH_DIR%;%AIFY_BASH_DIR%..\usr\bin;%AIFY_BASH_DIR%..\..\bin;%PATH%"
"$bash_path" "$windows_wrapper_path" %*
endlocal
EOF

  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '
      param([string]$dir)
      $current = [Environment]::GetEnvironmentVariable("Path", "User")
      $parts = @()
      if ($current) { $parts = $current -split ";" }
      $normalized = $dir.Trim().ToLowerInvariant()
      if (-not ($parts | Where-Object { $_.Trim().ToLowerInvariant() -eq $normalized })) {
        $updated = if ([string]::IsNullOrWhiteSpace($current)) { $dir } else { $current.TrimEnd(";") + ";" + $dir }
        [Environment]::SetEnvironmentVariable("Path", $updated, "User")
      }
    ' "$windows_wrapper_dir" >/dev/null 2>&1 || true
  fi
}

copy_codex_assets() {
  local codex_home="${CODEX_HOME:-$HOME/.codex}"
  local skill_dst="$codex_home/skills/aify-comms"
  local debug_skill_dst="$codex_home/skills/aify-comms-debug"
  mkdir -p "$(dirname "$skill_dst")"
  rm -rf "$skill_dst"
  rm -rf "$debug_skill_dst"
  cp -R "$SCRIPT_DIR/.agents/skills/aify-comms" "$skill_dst"
  cp -R "$SCRIPT_DIR/.agents/skills/aify-comms-debug" "$debug_skill_dst"
}

install_opencode_config() {
  local config_root="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
  local config_file="$config_root/opencode.json"
  local node_config_file=""
  local node_server_path=""
  local api_key="${CLAUDE_MCP_API_KEY:-${AIFY_API_KEY:-}}"
  mkdir -p "$config_root"
  if [ ! -f "$config_file" ]; then
    cat > "$config_file" <<'EOF'
{
  "$schema": "https://opencode.ai/config.json"
}
EOF
  fi

  node_config_file="$(path_for_node "$config_file")"
  node_server_path="$(path_for_node "$AIFY_BRIDGE_DIR/server.js")"

  MSYS_NO_PATHCONV=1 node -e "
    const fs = require('fs');
    const file = process.argv[1];
    const serverUrl = process.argv[2];
    const apiKey = process.argv[3];
    const serverPath = process.argv[4];
    let data = {};
    try {
      data = JSON.parse(fs.readFileSync(file, 'utf-8'));
    } catch (err) {
      try {
        if (fs.existsSync(file)) {
          const bak = file + '.aify-bak-' + Date.now();
          fs.copyFileSync(file, bak);
          console.error('[aify-install] WARN: ' + file + ' was malformed (' + err.message + '); backed up to ' + bak + ' and rebuilt with the aify entry only.');
        }
      } catch (_) {}
    }
    if (!data || typeof data !== 'object') data = {};
    if (!data['\$schema']) data['\$schema'] = 'https://opencode.ai/config.json';
    if (!data.mcp || typeof data.mcp !== 'object' || Array.isArray(data.mcp)) data.mcp = {};
    const environment = {};
    if (serverUrl) {
      environment.AIFY_SERVER_URL = serverUrl;
      environment.CLAUDE_MCP_SERVER_URL = serverUrl;
    }
    if (apiKey) {
      environment.AIFY_API_KEY = apiKey;
      environment.CLAUDE_MCP_API_KEY = apiKey;
    }
    data.mcp['aify-comms'] = {
      type: 'local',
      enabled: true,
      command: ['node', serverPath],
      ...(Object.keys(environment).length ? { environment } : {}),
    };
    fs.writeFileSync(file, JSON.stringify(data, null, 2) + '\n');
  " "$node_config_file" "$SERVER_URL" "$api_key" "$node_server_path"
}

install_pi_config() {
  local config_root="$HOME/.omp/agent"
  local config_file="$config_root/mcp.json"
  local node_config_file=""
  local node_server_path=""
  local api_key="${CLAUDE_MCP_API_KEY:-${AIFY_API_KEY:-}}"
  mkdir -p "$config_root"
  if [ ! -f "$config_file" ]; then
    cat > "$config_file" <<'EOF'
{
  "$schema": "https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/coding-agent/src/config/mcp-schema.json",
  "mcpServers": {}
}
EOF
  fi

  node_config_file="$(path_for_node "$config_file")"
  node_server_path="$(path_for_node "$AIFY_BRIDGE_DIR/server.js")"

  MSYS_NO_PATHCONV=1 node -e "
    const fs = require('fs');
    const file = process.argv[1];
    const serverUrl = process.argv[2];
    const apiKey = process.argv[3];
    const serverPath = process.argv[4];
    let data = {};
    try {
      data = JSON.parse(fs.readFileSync(file, 'utf-8'));
    } catch (err) {
      try {
        if (fs.existsSync(file)) {
          const bak = file + '.aify-bak-' + Date.now();
          fs.copyFileSync(file, bak);
          console.error('[aify-install] WARN: ' + file + ' was malformed (' + err.message + '); backed up to ' + bak + ' and rebuilt with the aify entry only.');
        }
      } catch (_) {}
    }
    if (!data || typeof data !== 'object') data = {};
    if (!data['\$schema']) data['\$schema'] = 'https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/coding-agent/src/config/mcp-schema.json';
    if (!data.mcpServers || typeof data.mcpServers !== 'object' || Array.isArray(data.mcpServers)) data.mcpServers = {};
    const env = {};
    if (serverUrl) {
      env.AIFY_SERVER_URL = serverUrl;
      env.CLAUDE_MCP_SERVER_URL = serverUrl;
    }
    if (apiKey) {
      env.AIFY_API_KEY = apiKey;
      env.CLAUDE_MCP_API_KEY = apiKey;
    }
    data.mcpServers['aify-comms'] = {
      type: 'stdio',
      command: 'node',
      args: [serverPath],
      ...(Object.keys(env).length ? { env } : {}),
    };
    fs.writeFileSync(file, JSON.stringify(data, null, 2) + '\n');
  " "$node_config_file" "$SERVER_URL" "$api_key" "$node_server_path"
}

_patch_hermes_config_at() {
  # Patch a single hermes config.yaml with the aify-comms MCP entry.
  # Idempotent: skips if `aify-comms:` already exists under `mcp_servers:`.
  local config_file="$1"
  local config_dir=""
  local node_config_file=""
  local node_server_path=""
  config_dir="$(dirname "$config_file")"
  mkdir -p "$config_dir"
  touch "$config_file"
  node_config_file="$(path_for_node "$config_file")"
  # Only convert to a Windows drive path when hermes actually runs as a native
  # Windows binary. On WSL with a Linux hermes, path_for_windows_runtime would
  # emit "D:\..." which Linux node can't open — the aify-comms MCP child then
  # exits instantly ("Connection closed"), so no in-hermes bridge claims
  # channel dispatches and managed hermes never answers. Mirror of the plugin
  # path guard in install_hermes_wrapper.
  node_server_path="$AIFY_BRIDGE_DIR/server.js"
  if hermes_runtime_is_native_windows; then
    node_server_path="$(path_for_windows_runtime "$node_server_path")"
  fi

  MSYS_NO_PATHCONV=1 node -e '
    const fs = require("fs");
    const file = process.argv[1];
    const serverPath = process.argv[2];
    const serverUrl = process.argv[3] || "";
    let text = "";
    try { text = fs.readFileSync(file, "utf8"); } catch (_) {}
    // Hermes filters env-vars to stdio MCP children: only PATH HOME etc
    // pass through by default (tools/mcp_tool.py _SAFE_ENV_KEYS). The
    // hermes-aify wrapper exports the gateway vars to hermes itself but
    // without explicit propagation here those vars never reach the
    // aify-comms MCP server child. Hermes does support templated env
    // resolution at MCP-spawn time so we use that to inject the
    // current value of each var per launch.
    //
    // Plan 6 follow-up (2026-05-26): AIFY_AGENT_ID + AIFY_SESSION_MODE
    // + AIFY_MANAGED_VIA_WRAPPER added — without them the inner bridge
    // never registers in bridge_instances and dispatch sits queued
    // forever (observed 2026-05-26 with hermes-test managed:
    // wrapper PTY attached, hermes TUI rendered, MCP server loaded,
    // but no /agents POST). AIFY_COMMS_AGENT_ID + AIFY_TERMINAL_ID kept
    // in sync for symmetry with terminalChildEnv.
    const entry = [
      "  aify-comms:",
      "    command: \"node\"",
      "    args:",
      `      - ${JSON.stringify(serverPath)}`,
      "    env:",
      `      AIFY_AGENT_ID: \"\${AIFY_AGENT_ID}\"`,
      `      AIFY_COMMS_AGENT_ID: \"\${AIFY_COMMS_AGENT_ID}\"`,
      `      AIFY_AGENT_ROLE: \"\${AIFY_AGENT_ROLE}\"`,
      `      AIFY_AGENT_CWD: \"\${AIFY_AGENT_CWD}\"`,
      `      AIFY_SESSION_MODE: \"\${AIFY_SESSION_MODE}\"`,
      `      AIFY_SESSION_HANDLE: \"\${AIFY_SESSION_HANDLE}\"`,
      `      AIFY_EXPLICIT_SESSION_HANDLE: \"\${AIFY_EXPLICIT_SESSION_HANDLE}\"`,
      `      AIFY_RUNTIME: \"\${AIFY_RUNTIME}\"`,
      `      AIFY_TERMINAL_ID: \"\${AIFY_TERMINAL_ID}\"`,
      `      AIFY_MANAGED_VIA_WRAPPER: \"\${AIFY_MANAGED_VIA_WRAPPER}\"`,
      `      HERMES_SESSION_ID: \"\${HERMES_SESSION_ID}\"`,
      `      AIFY_HERMES_GATEWAY_URL: \"\${AIFY_HERMES_GATEWAY_URL}\"`,
      `      AIFY_HERMES_GATEWAY_TOKEN: \"\${AIFY_HERMES_GATEWAY_TOKEN}\"`,
      `      HERMES_TUI_GATEWAY_URL: \"\${HERMES_TUI_GATEWAY_URL}\"`,
      // The aify-comms MCP child runs in HTTP mode against the service ONLY when
      // CLAUDE_MCP_SERVER_URL / AIFY_SERVER_URL is set (server.js:94 — else it
      // silently falls back to the local .messages/ FILE store and replies never
      // reach the service). The MCP child is spawned by the (managed) hidden
      // gateway host / (resident) hermes process, BOTH of which inherit the
      // hermes-aify wrapper exported AIFY_SERVER_URL/CLAUDE_MCP_SERVER_URL
      // (install.sh bash:1160-1161, PS:1578-1579 — baked default_server). So we
      // ALWAYS emit these two keys: a literal URL when one was given at install
      // (most robust — no env dependency), otherwise the \${VAR} interpolation
      // hermes resolves at MCP-spawn time from the wrapper-exported env
      // (mcp_tool.py _interpolate_env_vars). Either way HTTP mode is guaranteed;
      // the prior omit-when-empty left the child in file mode.
      `      AIFY_SERVER_URL: ${serverUrl ? JSON.stringify(serverUrl) : "\"\${AIFY_SERVER_URL}\""}`,
      `      CLAUDE_MCP_SERVER_URL: ${serverUrl ? JSON.stringify(serverUrl) : "\"\${CLAUDE_MCP_SERVER_URL}\""}`,
    ];
    // Plan 6 follow-up: rewrite the aify-comms entry in place when it
    // exists, so re-running install.sh refreshes the env block. The
    // previous skip-if-exists guard meant operators who installed
    // before the env-block expansion never picked up the new keys
    // (only AIFY_HERMES_GATEWAY_URL was propagated, breaking managed
    // delivery).
    const lines = text.replace(/\s*$/, "").split(/\r?\n/);
    const mcpIndex = lines.findIndex((line) => /^[ \t]*mcp_servers:[ \t]*$/.test(line));
    let existingStart = -1;
    let existingEnd = -1;
    for (let i = 0; i < lines.length; i++) {
      if (/^[ \t]+aify-comms:[ \t]*$/.test(lines[i])) {
        existingStart = i;
        const baseIndent = (lines[i].match(/^[ \t]+/) || [""])[0].length;
        existingEnd = lines.length;
        for (let j = i + 1; j < lines.length; j++) {
          if (lines[j].trim() === "") continue;
          const indent = (lines[j].match(/^[ \t]*/) || [""])[0].length;
          if (indent <= baseIndent) { existingEnd = j; break; }
        }
        break;
      }
    }
    if (existingStart >= 0) {
      lines.splice(existingStart, existingEnd - existingStart, ...entry);
      fs.writeFileSync(file, lines.join("\n") + "\n");
    } else if (mcpIndex >= 0) {
      lines.splice(mcpIndex + 1, 0, ...entry);
      fs.writeFileSync(file, lines.join("\n") + "\n");
    } else {
      fs.writeFileSync(file, lines.filter(Boolean).join("\n") + `${lines.some(Boolean) ? "\n\n" : ""}mcp_servers:\n${entry.join("\n")}\n`);
    }
  ' "$node_config_file" "$node_server_path" "$SERVER_URL"
}

install_hermes_plugin() {
  # Install the aify-comms shim as a first-class Hermes plugin under
  # <hermes_home>/plugins/aify-comms/. This is the RELIABLE load path:
  # cmd_dashboard calls discover_plugins() inside the gateway process, and
  # plugin discovery does NOT depend on PYTHONPATH. Hermes relaunches the
  # dashboard and drops PYTHONPATH, so the sitecustomize.py-on-PYTHONPATH
  # mechanism never patched tui_gateway.server in the gateway — the visible
  # session bind then failed with "unknown method: aify.session.bind_transport"
  # and managed/resident hermes never answered. The plugin's register() calls
  # aify_hermes_plugin.bootstrap.install(), which installs the same import-time
  # patcher that registers the gateway methods. The thin loader keeps the real
  # shim in the repo (AIFY_HERMES_PLUGIN_PATH), so a hermes update can't erase
  # it; the baked path is only a fallback when the env var is absent.
  local plugin_src="$SCRIPT_DIR/integrations/hermes-aify-plugin"
  if hermes_runtime_is_native_windows; then
    plugin_src="$(path_for_windows_runtime "$plugin_src")"
  fi
  local plugin_dir="$(hermes_config_root)/plugins/aify-comms"
  mkdir -p "$plugin_dir"
  cat > "$plugin_dir/plugin.yaml" <<'YAML'
name: aify-comms
version: 1.0.0
description: "aify-comms hermes runtime shim — registers the gateway visible-session bind/render methods and gateway-URL env publication so dashboard-managed and resident hermes delivery works. Loads the durable shim from the aify-comms repo. Active only under hermes-aify (AIFY_HERMES_PLUGIN=1)."
author: "aify-comms"
YAML
  # __init__.py: thin loader. __AIFY_PLUGIN_PATH__ is replaced with the repo
  # path at install time and used only as a fallback when the env var is unset.
  cat > "$plugin_dir/__init__.py" <<'PYEOF'
"""aify-comms hermes plugin (thin loader).

discover_plugins() invokes register() in every hermes process that loads
plugins — including the dashboard/gateway process where hermes has stripped
PYTHONPATH. We add the repo shim path to sys.path and install the import-time
patcher so tui_gateway.server (and hermes_cli.main / web_server) get patched
when imported. No-op unless AIFY_HERMES_PLUGIN=1, so normal hermes is untouched.
"""
from __future__ import annotations
import os, sys


def register(ctx) -> None:  # noqa: ANN001 - hermes PluginContext
    if os.environ.get("AIFY_HERMES_PLUGIN", "").strip() != "1":
        return
    plugin_path = os.environ.get("AIFY_HERMES_PLUGIN_PATH", "").strip() or r"__AIFY_PLUGIN_PATH__"
    if plugin_path and plugin_path not in sys.path:
        sys.path.insert(0, plugin_path)
    try:
        from aify_hermes_plugin.bootstrap import install
        install()
    except Exception as exc:  # never break hermes startup
        sys.stderr.write("[aify-comms-plugin] shim load failed: %s\n" % exc)
PYEOF
  # Substitute the baked fallback path (python raw string; backslashes safe).
  MSYS_NO_PATHCONV=1 node -e '
    const fs = require("fs");
    const file = process.argv[1];
    const p = process.argv[2];
    let t = fs.readFileSync(file, "utf8");
    t = t.split("__AIFY_PLUGIN_PATH__").join(p.replace(/\\/g, "\\\\"));
    fs.writeFileSync(file, t);
  ' "$plugin_dir/__init__.py" "$plugin_src" 2>/dev/null || \
    sed -i.bak "s|__AIFY_PLUGIN_PATH__|$plugin_src|g" "$plugin_dir/__init__.py" 2>/dev/null && rm -f "$plugin_dir/__init__.py.bak" 2>/dev/null || true

  # Enable it (opt-in allow-list). Prefer the CLI; fall back to patching
  # config.yaml's plugins.enabled list directly if the CLI is unavailable.
  local hermes_bin=""
  hermes_bin="$(hermes_cmd 2>/dev/null || true)"
  if [ -n "$hermes_bin" ] && "$hermes_bin" plugins enable aify-comms >/dev/null 2>&1; then
    echo "Hermes plugin 'aify-comms' installed and enabled at $plugin_dir"
  else
    _enable_hermes_plugin_in_config "$(hermes_config_root)/config.yaml" "aify-comms"
    _enable_hermes_plugin_in_config "$HOME/.hermes/config.yaml" "aify-comms"
    echo "Hermes plugin 'aify-comms' installed at $plugin_dir (enabled via config.yaml)"
  fi
}

_enable_hermes_plugin_in_config() {
  # Add <name> to plugins.enabled in a hermes config.yaml without disturbing
  # other keys. Idempotent. Best-effort (node-based YAML-ish edit).
  local config_file="$1"
  local name="$2"
  [ -f "$config_file" ] || return 0
  MSYS_NO_PATHCONV=1 node -e '
    const fs = require("fs");
    const [file, name] = [process.argv[1], process.argv[2]];
    let text = "";
    try { text = fs.readFileSync(file, "utf8"); } catch (_) { return; }
    const lines = text.replace(/\s*$/, "").split(/\r?\n/);
    // Find a top-level "plugins:" block.
    let pIdx = lines.findIndex((l) => /^plugins:\s*$/.test(l));
    // Replace a malformed "plugins: []" / "plugins:" inline form.
    const inlineIdx = lines.findIndex((l) => /^plugins:\s*\[\s*\]\s*$/.test(l));
    if (inlineIdx >= 0) { lines.splice(inlineIdx, 1, "plugins:", "  enabled:", `    - ${name}`); fs.writeFileSync(file, lines.join("\n") + "\n"); return; }
    if (pIdx < 0) { lines.push("plugins:", "  enabled:", `    - ${name}`); fs.writeFileSync(file, lines.join("\n") + "\n"); return; }
    // Within the plugins block, find enabled:.
    let enIdx = -1, end = lines.length;
    for (let i = pIdx + 1; i < lines.length; i++) {
      if (/^\S/.test(lines[i])) { end = i; break; }
      if (/^\s+enabled:\s*$/.test(lines[i])) { enIdx = i; }
      if (/^\s+enabled:\s*\[\s*\]\s*$/.test(lines[i])) { lines.splice(i, 1, "  enabled:", `    - ${name}`); fs.writeFileSync(file, lines.join("\n") + "\n"); return; }
    }
    if (enIdx < 0) { lines.splice(pIdx + 1, 0, "  enabled:", `    - ${name}`); fs.writeFileSync(file, lines.join("\n") + "\n"); return; }
    // enabled: block exists — check membership, append if missing.
    let listEnd = end;
    for (let j = enIdx + 1; j < end; j++) {
      const m = lines[j].match(/^(\s+)-\s+(.*\S)\s*$/);
      if (!m) { listEnd = j; break; }
      if (m[2] === name) return; // already enabled
      listEnd = j + 1;
    }
    lines.splice(listEnd, 0, `    - ${name}`);
    fs.writeFileSync(file, lines.join("\n") + "\n");
  ' "$config_file" "$name" 2>/dev/null || true
}

install_hermes_config() {
  # Hermes reads config from two locations depending on how the binary
  # was launched: the path reported by `hermes config path` (often
  # ~/AppData/Local/hermes/config.yaml on Windows under HERMES_HOME) AND
  # ~/.hermes/config.yaml (the legacy/default fallback many operators
  # still use). If we only patch one, an operator whose active hermes
  # reads the other ends up with no AIFY_HERMES_GATEWAY_URL env block
  # in their MCP entry and the resident-hermes wake fails with
  # hermes-missing-handle (follow-up #115).
  #
  # We dual-write: patch the canonical `hermes_config_root` path and
  # the secondary `~/.hermes/config.yaml`. Deduplicate by realpath so we
  # do not double-patch when both targets resolve to the same file.
  local primary_file="$(hermes_config_root)/config.yaml"
  local secondary_file="$HOME/.hermes/config.yaml"
  local primary_real=""
  local secondary_real=""

  mkdir -p "$(dirname "$primary_file")"
  touch "$primary_file"
  mkdir -p "$(dirname "$secondary_file")"
  touch "$secondary_file"

  if command -v realpath >/dev/null 2>&1; then
    primary_real="$(realpath "$primary_file" 2>/dev/null || printf '%s' "$primary_file")"
    secondary_real="$(realpath "$secondary_file" 2>/dev/null || printf '%s' "$secondary_file")"
  else
    primary_real="$primary_file"
    secondary_real="$secondary_file"
  fi

  _patch_hermes_config_at "$primary_file"
  if [ "$primary_real" != "$secondary_real" ]; then
    _patch_hermes_config_at "$secondary_file"
  fi
}

migrate_codex_hooks_key() {
  # Recent Codex CLI renamed [features].codex_hooks -> [features].hooks.
  # Rename the key in place if present, preserving the original value.
  # Safe to run unconditionally; a no-op when nothing matches.
  local codex_home="${CODEX_HOME:-$HOME/.codex}"
  local config_file="$codex_home/config.toml"
  [ -f "$config_file" ] || return 0
  grep -Eq '^[[:space:]]*codex_hooks[[:space:]]*=' "$config_file" || return 0
  awk '
    /^\[/ { in_features = ($0 ~ /^\[features\][[:space:]]*$/); print; next }
    in_features && /^[[:space:]]*codex_hooks[[:space:]]*=/ {
      sub(/codex_hooks/, "hooks"); print; next
    }
    { print }
  ' "$config_file" > "$config_file.tmp" && mv "$config_file.tmp" "$config_file"
}

enable_codex_hooks_feature() {
  local codex_home="${CODEX_HOME:-$HOME/.codex}"
  local config_file="$codex_home/config.toml"
  mkdir -p "$codex_home"

  if [ ! -f "$config_file" ]; then
    cat > "$config_file" <<'EOF'
[features]
hooks = true
EOF
    return
  fi

  migrate_codex_hooks_key

  # Ensure [features].hooks = true exists exactly once.
  awk '
    BEGIN { in_features = 0; injected = 0 }
    /^\[/ {
      if (in_features && !injected) { print "hooks = true"; injected = 1 }
      in_features = ($0 ~ /^\[features\][[:space:]]*$/)
      print; next
    }
    in_features && /^[[:space:]]*hooks[[:space:]]*=/ {
      if (!injected) { print "hooks = true"; injected = 1 }
      next
    }
    { print }
    END {
      if (in_features && !injected) { print "hooks = true"; injected = 1 }
      if (!injected) { print ""; print "[features]"; print "hooks = true" }
    }
  ' "$config_file" > "$config_file.tmp"
  mv "$config_file.tmp" "$config_file"
}

install_codex_hook() {
  local codex_home="${CODEX_HOME:-$HOME/.codex}"
  local hooks_file="$codex_home/hooks.json"
  local node_hooks_file=""
  local node_notify_script=""
  local hook_command=""
  mkdir -p "$codex_home"
  if [ ! -f "$hooks_file" ]; then
    echo '{"hooks":{}}' > "$hooks_file"
  fi

  enable_codex_hooks_feature

  node_hooks_file="$(path_for_node "$hooks_file")"
  node_notify_script="$(path_for_node "$AIFY_BRIDGE_DIR/notify-check.js")"
  hook_command="$(hook_command_for_node_script "$node_notify_script")"

  MSYS_NO_PATHCONV=1 node -e "
    const fs = require('fs');
    const hooksPath = process.argv[1];
    const command = process.argv[2];
    const notifyPattern = /(^|[\\\/])notify-check\.js([\"']|\s|$)/i;
    function isAifyNotifyHook(hook) {
      if (!hook || hook.type !== 'command') return false;
      const value = String(hook.command || '');
      return notifyPattern.test(value);
    }
    let data = { hooks: {} };
    try {
      data = JSON.parse(fs.readFileSync(hooksPath, 'utf-8'));
    } catch (_) {}
    if (!data || typeof data !== 'object') data = {};
    if (!data.hooks || typeof data.hooks !== 'object') data.hooks = {};
    if (!Array.isArray(data.hooks.PostToolUse)) data.hooks.PostToolUse = [];
    // matcher .* fires on every tool call (Bash + Edit + Read + Write + ...).
    // notify-check.js has its own 10s rate limit so the volume is bounded,
    // and the heartbeat needs to fire on non-Bash tools to keep turn_busy
    // fresh during stretches of file-only work (operator-reported 2026-05-24:
    // status flipped to online mid-task when no Bash hook fired for >120s).
    const matcher = '.*';
    data.hooks.PostToolUse = data.hooks.PostToolUse.filter(group => {
      if (!group || !Array.isArray(group.hooks)) return true;
      const keptHooks = group.hooks.filter(h => !isAifyNotifyHook(h));
      if (keptHooks.length === 0) return false;
      group.hooks = keptHooks;
      return true;
    });
    data.hooks.PostToolUse.push({
      matcher,
      hooks: [{
        type: 'command',
        command,
        statusMessage: 'Checking aify unread messages',
        timeout: 3
      }]
    });
    fs.writeFileSync(hooksPath, JSON.stringify(data, null, 2) + '\n');
  " "$node_hooks_file" "$hook_command"
}

install_hermes_hook() {
  local config_root="$(hermes_config_root)"
  local config_file="$config_root/config.yaml"
  local hook_dir="$config_root/agent-hooks"
  local hook_path="$hook_dir/aify-notify.sh"
  local node_notify_script=""
  local hook_command_path=""
  local hook_command=""
  mkdir -p "$hook_dir"
  touch "$config_file"
  node_notify_script="$(path_for_node "$AIFY_BRIDGE_DIR/notify-check.js")"

  cat > "$hook_path" <<EOF
#!/usr/bin/env bash
node $(shell_quote "$node_notify_script")
EOF
  chmod +x "$hook_path"
  hook_command_path="$(path_for_node "$hook_path" | sed 's#\\\\#/#g')"
  hook_command="bash \"$hook_command_path\""

  MSYS_NO_PATHCONV=1 node -e '
    const fs = require("fs");
    const file = process.argv[1];
    const hookCommand = process.argv[2];
    let text = "";
    try { text = fs.readFileSync(file, "utf8"); } catch (_) {}
    let lines = text.replace(/\s*$/, "").split(/\r?\n/);
    const commandLine = `      command: ${JSON.stringify(hookCommand)}`;
    let replaced = false;
    lines = lines.map((line) => {
      const m = line.match(/^([ \t]*)command:[ \t]*.*aify-notify\.sh/);
      if (m) {
        replaced = true;
        // Preserve the existing command line indentation. Hardcoding a fixed
        // indent corrupts blocks whose matcher/timeout siblings use a different
        // indent (observed: a 2-space "- matcher" item with 4-space keys got a
        // 6-space command -> "mapping values are not allowed here").
        return `${m[1]}command: ${JSON.stringify(hookCommand)}`;
      }
      return line;
    });
    if (replaced) {
      fs.writeFileSync(file, lines.join("\n") + "\n");
      process.exit(0);
    }
    const entry = [
      "    - matcher: \".*\"",
      commandLine,
      "      timeout: 3",
    ];
    const postIndex = lines.findIndex((line) => /^[ \t]*post_tool_call:[ \t]*$/.test(line));
    if (postIndex >= 0) {
      lines.splice(postIndex + 1, 0, ...entry);
      fs.writeFileSync(file, lines.join("\n") + "\n");
      process.exit(0);
    }
    const hooksIndex = lines.findIndex((line) => /^[ \t]*hooks:[ \t]*$/.test(line));
    if (hooksIndex >= 0) {
      lines.splice(hooksIndex + 1, 0, "  post_tool_call:", ...entry);
      fs.writeFileSync(file, lines.join("\n") + "\n");
      process.exit(0);
    }
    fs.writeFileSync(file, lines.filter(Boolean).join("\n") + `${lines.some(Boolean) ? "\n\n" : ""}hooks:\n  post_tool_call:\n${entry.join("\n")}\n`);
  ' "$(path_for_node "$config_file")" "$hook_command"
}

install_codex_turn_hooks() {
  # Symmetric to install_claude_turn_*_hook. Codex's hooks.json
  # supports the same hook event schema as Claude Code. Adding
  # UserPromptSubmit + Stop entries lets direct codex-aify CLI typing
  # flip the dashboard to "working" mid-turn AND clear it cleanly when
  # the turn ends — matching the claude path. If a particular codex CLI
  # version doesn't recognize these event names yet, the entries are
  # inert (no harm).
  local codex_home="${CODEX_HOME:-$HOME/.codex}"
  local hooks_file="$codex_home/hooks.json"
  mkdir -p "$codex_home"
  if [ ! -f "$hooks_file" ]; then
    echo '{"hooks":{}}' > "$hooks_file"
  fi
  enable_codex_hooks_feature
  local node_hooks_file
  node_hooks_file="$(path_for_node "$hooks_file")"
  local start_command
  start_command='if [ -n "${AIFY_AGENT_ID:-}" ] && [ -n "${AIFY_COMMS_URL:-}" ]; then curl -sS --max-time 2 -X POST "${AIFY_COMMS_URL%/}/api/v1/agents/${AIFY_AGENT_ID}/turn-start" >/dev/null 2>&1 || true; fi'
  local end_command
  end_command='if [ -n "${AIFY_AGENT_ID:-}" ] && [ -n "${AIFY_COMMS_URL:-}" ]; then curl -sS --max-time 2 -X POST "${AIFY_COMMS_URL%/}/api/v1/agents/${AIFY_AGENT_ID}/turn-end" >/dev/null 2>&1 || true; fi'
  MSYS_NO_PATHCONV=1 node -e "
    const fs = require('fs');
    const hooksPath = process.argv[1];
    const startCmd = process.argv[2];
    const endCmd = process.argv[3];
    let data = { hooks: {} };
    try {
      data = JSON.parse(fs.readFileSync(hooksPath, 'utf-8'));
    } catch (err) {
      try {
        if (fs.existsSync(hooksPath)) {
          const bak = hooksPath + '.aify-bak-' + Date.now();
          fs.copyFileSync(hooksPath, bak);
          console.error('[aify-install] WARN: ' + hooksPath + ' was malformed (' + err.message + '); backed up to ' + bak + ' and rebuilt with the aify hook only.');
        }
      } catch (_) {}
    }
    if (!data || typeof data !== 'object') data = {};
    if (!data.hooks || typeof data.hooks !== 'object') data.hooks = {};
    const wire = (eventKey, cmd, marker) => {
      if (!Array.isArray(data.hooks[eventKey])) data.hooks[eventKey] = [];
      data.hooks[eventKey] = data.hooks[eventKey].filter(
        group => !JSON.stringify(group).includes(marker)
      );
      data.hooks[eventKey].push({
        hooks: [{ type: 'command', command: cmd, timeout: 3 }],
      });
    };
    wire('UserPromptSubmit', startCmd, '/api/v1/agents/\${AIFY_AGENT_ID}/turn-start');
    wire('Stop', endCmd, '/api/v1/agents/\${AIFY_AGENT_ID}/turn-end');
    fs.writeFileSync(hooksPath, JSON.stringify(data, null, 2) + '\n');
  " "$node_hooks_file" "$start_command" "$end_command"
}

install_hermes_turn_hooks() {
  # Hermes-side symmetric hook. Hermes shell hooks support events
  # pre_tool_call / post_tool_call / pre_llm_call / subagent_stop
  # (see `hermes hooks --help`). `pre_llm_call` fires before each
  # LLM call — close enough to a user-prompt-submit signal that
  # the dashboard flips to "working" the moment the operator
  # submits a prompt in hermes-aify. No clean upstream turn-end
  # hook exists for shell hooks, so RESIDENT hermes has no
  # event-driven turn-end (pure-event-status change #6, 2026-06-02):
  # it relies on the single LONG status ceiling
  # (TURN_BUSY_BACKSTOP_SECONDS, 30m) to self-heal status off
  # 'working', and on the short 120s claim-gate window so a queued
  # send is not stranded. (Managed hermes dispatches still get the
  # per-process exit signal as a precise turn-end.) Unlike claude,
  # resident hermes is NOT covered by the transcript turn-END detector
  # (that keys on the claude transcript), so the long ceiling is its
  # only status backstop -- intentional, no behaviour change here.
  local config_root="$(hermes_config_root)"
  local config_file="$config_root/config.yaml"
  local hook_dir="$config_root/agent-hooks"
  local hook_path="$hook_dir/aify-turn-start.sh"
  local hook_command_path=""
  local hook_command=""
  mkdir -p "$hook_dir"
  touch "$config_file"
  cat > "$hook_path" <<EOF
#!/usr/bin/env bash
if [ -n "\${AIFY_AGENT_ID:-}" ] && [ -n "\${AIFY_COMMS_URL:-}" ]; then
  curl -sS --max-time 2 -X POST "\${AIFY_COMMS_URL%/}/api/v1/agents/\${AIFY_AGENT_ID}/turn-start" >/dev/null 2>&1 || true
fi
EOF
  chmod +x "$hook_path"
  hook_command_path="$(path_for_node "$hook_path" | sed 's#\\\\#/#g')"
  hook_command="bash \"$hook_command_path\""
  MSYS_NO_PATHCONV=1 node -e '
    const fs = require("fs");
    const file = process.argv[1];
    const hookCommand = process.argv[2];
    let text = "";
    try { text = fs.readFileSync(file, "utf8"); } catch (_) {}
    let lines = text.replace(/\s*$/, "").split(/\r?\n/);
    const commandLine = `      command: ${JSON.stringify(hookCommand)}`;
    let replaced = false;
    lines = lines.map((line) => {
      const m = line.match(/^([ \t]*)command:[ \t]*.*aify-turn-start\.sh/);
      if (m) {
        replaced = true;
        // Preserve existing indentation — see aify-notify replace above.
        return `${m[1]}command: ${JSON.stringify(hookCommand)}`;
      }
      return line;
    });
    if (replaced) {
      fs.writeFileSync(file, lines.join("\n") + "\n");
      process.exit(0);
    }
    const entry = [
      "    - matcher: \".*\"",
      commandLine,
      "      timeout: 3",
    ];
    const preIndex = lines.findIndex((line) => /^[ \t]*pre_llm_call:[ \t]*$/.test(line));
    if (preIndex >= 0) {
      lines.splice(preIndex + 1, 0, ...entry);
      fs.writeFileSync(file, lines.join("\n") + "\n");
      process.exit(0);
    }
    const hooksIndex = lines.findIndex((line) => /^[ \t]*hooks:[ \t]*$/.test(line));
    if (hooksIndex >= 0) {
      lines.splice(hooksIndex + 1, 0, "  pre_llm_call:", ...entry);
      fs.writeFileSync(file, lines.join("\n") + "\n");
      process.exit(0);
    }
    fs.writeFileSync(file, lines.filter(Boolean).join("\n") + `${lines.some(Boolean) ? "\n\n" : ""}hooks:\n  pre_llm_call:\n${entry.join("\n")}\n`);
  ' "$(path_for_node "$config_file")" "$hook_command"
}

install_claude_turn_start_hook() {
  # Symmetric counterpart to install_claude_turn_end_hook (Stop hook).
  # Claude Code's UserPromptSubmit hook fires when the operator submits
  # a prompt to the resident CLI — exactly the moment "working" should
  # flip on, even when the prompt didn't come through aify-comms's
  # dispatch path (i.e., operator typed directly into the CLI). Without
  # this hook, only channel-route dispatches set turn_busy and direct
  # CLI typing left the dashboard showing "online" while the assistant
  # was mid-turn. Operator-asked 2026-05-22 to make the two surfaces
  # symmetric.
  #
  # The hook is a no-op when AIFY_AGENT_ID isn't set, so a regular
  # `claude` session (no aify wrapper) is unaffected.
  local settings_file="$HOME/.claude/settings.json"
  mkdir -p "$(dirname "$settings_file")"
  if [ ! -f "$settings_file" ]; then
    echo '{}' > "$settings_file"
  fi
  local node_settings_file
  node_settings_file="$(path_for_node "$settings_file")"
  local hook_command
  hook_command='if [ -n "${AIFY_AGENT_ID:-}" ] && [ -n "${AIFY_COMMS_URL:-}" ]; then curl -sS --max-time 2 -X POST "${AIFY_COMMS_URL%/}/api/v1/agents/${AIFY_AGENT_ID}/turn-start" >/dev/null 2>&1 || true; fi'
  MSYS_NO_PATHCONV=1 node -e "
    const fs = require('fs');
    const settingsPath = process.argv[1];
    const command = process.argv[2];
    let settings = {};
    try {
      settings = JSON.parse(fs.readFileSync(settingsPath, 'utf-8'));
    } catch (err) {
      // Malformed JSON would otherwise be silently overwritten with a
      // fresh aify-only file — losing every operator setting/hook.
      // Back up and warn before we rebuild the file.
      try {
        if (fs.existsSync(settingsPath)) {
          const bak = settingsPath + '.aify-bak-' + Date.now();
          fs.copyFileSync(settingsPath, bak);
          console.error('[aify-install] WARN: ' + settingsPath + ' was malformed (' + err.message + '); backed up to ' + bak + ' and rebuilt with the aify hook only.');
        }
      } catch (_) {}
    }
    if (!settings || typeof settings !== 'object') settings = {};
    if (!settings.hooks) settings.hooks = {};
    // Wire /turn-start on UserPromptSubmit AND PostToolUse (proof-based turn signal,
    // 2026-06-18). POSTing /turn-start is an idempotent RE-ASSERT of the engine in_turn
    // sub-state (it does not start a fresh turn; it refreshes last_event_at).
    //
    // WHY PostToolUse is back (reverses pure-event #4, 2026-06-02): that change wired
    // UserPromptSubmit ONLY, on the premise that turn_busy stays set until the turn-END
    // event (the Stop hook), making a re-assert redundant. Two findings invalidated that
    // premise. (1) UserPromptSubmit does NOT fire for an MCP/channel-WOKEN managed turn
    // (only the channel claim sets turn_busy there). (2) The Stop hook is NOT a clean
    // once-per-turn signal: it fires prematurely / multiple times within one logical turn
    // (Claude Code issue 54360) and around transient API errors such as rate-limit retries,
    // clearing turn_busy mid-work. With no mid-turn re-assert, a still-working managed
    // claude then fell to online until the operator opened the Console (the only other
    // backstop, the console-spinner lease, was observed 30 MIN stale on live agents -- it
    // needs a rendered PTY, so it is not a real backstop). See task 224 + KNOWN_ISSUES.
    //
    // WHY this can NOT re-pin an idle agent (the original removal fear): PostToolUse fires
    // ONLY on a real tool call. An idle agent runs no tools, fires no hook, no re-assert,
    // so in_turn clears on the Stop hook and stays cleared. A tool call firing AFTER a Stop
    // means the turn was NOT actually over (premature Stop) -- re-asserting is CORRECT there.
    // A genuinely missed turn-END still self-heals at the single long ceiling, unchanged.
    // No time-window is introduced. Idempotent: filtered by the turn-start marker.
    const wireTurnStart = (eventKey) => {
      if (!Array.isArray(settings.hooks[eventKey])) settings.hooks[eventKey] = [];
      settings.hooks[eventKey] = settings.hooks[eventKey].filter(
        h => !JSON.stringify(h).includes('/api/v1/agents/\${AIFY_AGENT_ID}/turn-start')
      );
      settings.hooks[eventKey].push({
        hooks: [{ type: 'command', command, timeout: 3 }]
      });
    };
    wireTurnStart('UserPromptSubmit');
    wireTurnStart('PostToolUse');
    fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + '\n');
  " "$node_settings_file" "$hook_command"
}

install_claude_turn_end_hook() {
  # Architectural turn-end signal for resident claude-aify sessions.
  # claude-channel.js delivers dispatches but has no native turn-end
  # signal (unlike codex's turn/completed, pi's agent_end, hermes's
  # process exit). Without it, "working" status in the dashboard
  # waits out the 120s turn_busy stale window even when claude is
  # actually idle. Claude Code's Stop hook fires exactly when the
  # assistant turn ends (after all tool calls + final text), so it's
  # the canonical signal. The hook command no-ops if AIFY_AGENT_ID
  # isn't set, so a regular `claude` session (no aify wrapper) is
  # unaffected.
  local settings_file="$HOME/.claude/settings.json"
  mkdir -p "$(dirname "$settings_file")"
  if [ ! -f "$settings_file" ]; then
    echo '{}' > "$settings_file"
  fi
  local node_settings_file
  node_settings_file="$(path_for_node "$settings_file")"
  # SECONDARY pure-event fix (2026-06-19): route the Stop hook through claude-stop-gate.js, which
  # SUPPRESSES a premature/duplicate Stop fired mid-turn (it reads the transcript tail and only
  # posts /turn-end when the turn is NOT still in-flight). FAIL-SAFE: if node or the gate file is
  # unavailable, fall back to the original raw curl /turn-end — worst case is exactly the old
  # behavior, never a stuck-`working`. The curl-fallback string also keeps the dedup filter below
  # matching this hook on re-install.
  local gate_path="$AIFY_BRIDGE_DIR/claude-stop-gate.js"
  local hook_command
  hook_command='if [ -n "${AIFY_AGENT_ID:-}" ] && [ -n "${AIFY_COMMS_URL:-}" ]; then if command -v node >/dev/null 2>&1 && [ -f "'"$gate_path"'" ]; then node "'"$gate_path"'" 2>/dev/null || true; else curl -sS --max-time 2 -X POST "${AIFY_COMMS_URL%/}/api/v1/agents/${AIFY_AGENT_ID}/turn-end" >/dev/null 2>&1 || true; fi; fi'
  MSYS_NO_PATHCONV=1 node -e "
    const fs = require('fs');
    const settingsPath = process.argv[1];
    const command = process.argv[2];
    let settings = {};
    try {
      settings = JSON.parse(fs.readFileSync(settingsPath, 'utf-8'));
    } catch (err) {
      // Malformed JSON would otherwise be silently overwritten with a
      // fresh aify-only file — losing every operator setting/hook.
      // Back up and warn before we rebuild the file.
      try {
        if (fs.existsSync(settingsPath)) {
          const bak = settingsPath + '.aify-bak-' + Date.now();
          fs.copyFileSync(settingsPath, bak);
          console.error('[aify-install] WARN: ' + settingsPath + ' was malformed (' + err.message + '); backed up to ' + bak + ' and rebuilt with the aify hook only.');
        }
      } catch (_) {}
    }
    if (!settings || typeof settings !== 'object') settings = {};
    if (!settings.hooks) settings.hooks = {};
    if (!Array.isArray(settings.hooks.Stop)) settings.hooks.Stop = [];
    settings.hooks.Stop = settings.hooks.Stop.filter(
      h => !JSON.stringify(h).includes('aify-comms/api/v1/agents') && !JSON.stringify(h).includes('/api/v1/agents/\${AIFY_AGENT_ID}/turn-end')
    );
    settings.hooks.Stop.push({
      hooks: [{
        type: 'command',
        command,
        timeout: 3
      }]
    });
    fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + '\n');
  " "$node_settings_file" "$hook_command"
}

install_claude_hook() {
  local settings_file="$HOME/.claude/settings.json"
  local node_settings_file=""
  local node_notify_script=""
  local hook_command=""
  mkdir -p "$(dirname "$settings_file")"
  if [ ! -f "$settings_file" ]; then
    echo '{}' > "$settings_file"
  fi

  node_settings_file="$(path_for_node "$settings_file")"
  node_notify_script="$(path_for_node "$AIFY_BRIDGE_DIR/notify-check.js")"
  hook_command="$(hook_command_for_node_script "$node_notify_script")"

  MSYS_NO_PATHCONV=1 node -e "
    const fs = require('fs');
    const settingsPath = process.argv[1];
    const command = process.argv[2];
    let settings = {};
    try {
      settings = JSON.parse(fs.readFileSync(settingsPath, 'utf-8'));
    } catch (err) {
      // Malformed JSON would otherwise be silently overwritten with a
      // fresh aify-only file — losing every operator setting/hook.
      // Back up and warn before we rebuild the file.
      try {
        if (fs.existsSync(settingsPath)) {
          const bak = settingsPath + '.aify-bak-' + Date.now();
          fs.copyFileSync(settingsPath, bak);
          console.error('[aify-install] WARN: ' + settingsPath + ' was malformed (' + err.message + '); backed up to ' + bak + ' and rebuilt with the aify hook only.');
        }
      } catch (_) {}
    }
    if (!settings || typeof settings !== 'object') settings = {};
    if (!settings.hooks) settings.hooks = {};
    if (!settings.hooks.PostToolUse) settings.hooks.PostToolUse = [];
    settings.hooks.PostToolUse = settings.hooks.PostToolUse.filter(
      h => !JSON.stringify(h).includes('notify-check')
    );
    // matcher .* fires on every tool call so notify-check.js heartbeat
    // refreshes turn_busy during stretches of file-only work (Edit/Read/
    // Write/Grep). Previous matcher 'Bash' only fired on Bash calls,
    // which let turn_busy stale out (120s window) when claude spent a
    // long stretch reading/editing without shell invocations — operator
    // saw status flip to online mid-task. notify-check.js has its own
    // 10s rate limit so heartbeat volume is bounded.
    settings.hooks.PostToolUse.push({
      matcher: '.*',
      hooks: [{
        type: 'command',
        command,
        timeout: 3
      }]
    });
    fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + '\n');
  " "$node_settings_file" "$hook_command"
}

register_stdio_server() {
  local cli="$1"
  local server_name="aify-comms"
  local api_key="${CLAUDE_MCP_API_KEY:-${AIFY_API_KEY:-}}"
  local -a scope_args=()

  if [ "$cli" = "claude" ]; then
    scope_args=(--scope user)
    "$cli" mcp remove --scope local "$server_name" >/dev/null 2>&1 || true
    "$cli" mcp remove --scope project "$server_name" >/dev/null 2>&1 || true
    "$cli" mcp remove --scope user "$server_name" >/dev/null 2>&1 || true
  elif [ "$cli" = "hermes" ]; then
    install_hermes_config
    return
  elif [ "$cli" = "opencode" ]; then
    install_opencode_config
    return
  elif [ "$cli" = "pi" ]; then
    install_pi_config
    return
  else
    "$cli" mcp remove "$server_name" >/dev/null 2>&1 || true
  fi

  if [ -n "$SERVER_URL" ] && [ -n "$api_key" ]; then
    "$cli" mcp add "$server_name" \
      "${scope_args[@]}" \
      --env AIFY_SERVER_URL="$SERVER_URL" \
      --env CLAUDE_MCP_SERVER_URL="$SERVER_URL" \
      --env AIFY_API_KEY="$api_key" \
      --env CLAUDE_MCP_API_KEY="$api_key" \
      -- node "$AIFY_BRIDGE_DIR/server.js"
  elif [ -n "$SERVER_URL" ]; then
    "$cli" mcp add "$server_name" \
      "${scope_args[@]}" \
      --env AIFY_SERVER_URL="$SERVER_URL" \
      --env CLAUDE_MCP_SERVER_URL="$SERVER_URL" \
      -- node "$AIFY_BRIDGE_DIR/server.js"
  else
    "$cli" mcp add "$server_name" \
      "${scope_args[@]}" \
      -- node "$AIFY_BRIDGE_DIR/server.js"
  fi

  # Plan 6 follow-up (2026-05-26): for codex, the `[mcp_servers.X.env]` block
  # written by `codex mcp add --env` REPLACES the inherited environment for
  # the spawned MCP server (per codex-rs/rmcp-client/src/utils.rs
  # create_env_for_mcp_server). Without env-passthrough, the inner
  # mcp/stdio/server.js never sees AIFY_AGENT_ID / AIFY_SESSION_MODE /
  # AIFY_MANAGED_VIA_WRAPPER etc. and either registers under the wrong
  # agent_id or fails to advertise channel-mode in executionModes. Use
  # codex's `env_vars` mechanism (TOML array of names; passes values
  # through from parent codex's env) to forward what the wrapper exports.
  # Symmetric with the hermes install_hermes_config env-block (commit
  # aca4391). Idempotent: replaces an existing env_vars line if present.
  if [ "$cli" = "codex" ]; then
    install_codex_mcp_env_vars
  fi
}

install_codex_mcp_env_vars() {
  local codex_home="${CODEX_HOME:-$HOME/.codex}"
  local config_file="$codex_home/config.toml"
  local node_config_file=""
  [ -f "$config_file" ] || return 0
  node_config_file="$(path_for_node "$config_file")"

  MSYS_NO_PATHCONV=1 node -e '
    const fs = require("fs");
    const file = process.argv[1];
    // Names of env vars the wrapper exports that the inner aify-comms MCP
    // server child needs to register correctly. Kept in sync with the
    // codex-aify wrapper exports (install.sh:186-237) + the bridge spawn
    // env in mcp/stdio/terminal-env.js (AIFY_MANAGED_VIA_WRAPPER, etc.).
    // PATH/HOME are forwarded by codex by default (DEFAULT_ENV_VARS in
    // codex-rs/rmcp-client/src/utils.rs), so we do not list them here.
    const desired = [
      "AIFY_AGENT_ID",
      "AIFY_AGENT_ROLE",
      "AIFY_AGENT_CWD",
      "AIFY_SESSION_MODE",
      "AIFY_SESSION_HANDLE",
      "AIFY_RUNTIME",
      "AIFY_TERMINAL_ID",
      "AIFY_MANAGED_VIA_WRAPPER",
      "AIFY_COMMS_AGENT_ID",
      "AIFY_COMMS_URL",
      "AIFY_API_KEY",
      "CODEX_THREAD_ID",
      "AIFY_CODEX_APP_SERVER_URL",
    ];
    let text = "";
    try { text = fs.readFileSync(file, "utf8"); } catch (_) { process.exit(0); }
    const lines = text.split(/\r?\n/);
    const headerRe = /^\[mcp_servers\.aify-comms\]\s*$/;
    let headerIdx = -1;
    for (let i = 0; i < lines.length; i++) {
      if (headerRe.test(lines[i])) { headerIdx = i; break; }
    }
    if (headerIdx < 0) process.exit(0);
    // section end = next "[..." section OR EOF
    let endIdx = lines.length;
    for (let i = headerIdx + 1; i < lines.length; i++) {
      if (/^\[/.test(lines[i])) { endIdx = i; break; }
    }
    // Remove any existing env_vars line (handles multi-line inline arrays too)
    for (let i = headerIdx + 1; i < endIdx; i++) {
      if (/^\s*env_vars\s*=/.test(lines[i])) {
        let j = i;
        let bracketBalance = 0;
        for (; j < endIdx; j++) {
          for (const ch of lines[j]) {
            if (ch === "[") bracketBalance++;
            else if (ch === "]") bracketBalance--;
          }
          if (bracketBalance <= 0 && j >= i) break;
        }
        lines.splice(i, j - i + 1);
        endIdx -= (j - i + 1);
        i--;
      }
    }
    const envVarsLine = "env_vars = [" + desired.map((n) => JSON.stringify(n)).join(", ") + "]";
    lines.splice(headerIdx + 1, 0, envVarsLine);
    fs.writeFileSync(file, lines.join("\n"));
  ' "$node_config_file"
}

register_claude_channel_server() {
  local cli="$1"
  local server_name="aify-comms-channel"
  local api_key="${CLAUDE_MCP_API_KEY:-${AIFY_API_KEY:-}}"

  "$cli" mcp remove --scope local "$server_name" >/dev/null 2>&1 || true
  "$cli" mcp remove --scope project "$server_name" >/dev/null 2>&1 || true
  "$cli" mcp remove --scope user "$server_name" >/dev/null 2>&1 || true

  if [ -n "$SERVER_URL" ] && [ -n "$api_key" ]; then
    "$cli" mcp add --scope user "$server_name" \
      --env AIFY_SERVER_URL="$SERVER_URL" \
      --env CLAUDE_MCP_SERVER_URL="$SERVER_URL" \
      --env AIFY_API_KEY="$api_key" \
      --env CLAUDE_MCP_API_KEY="$api_key" \
      -- node "$AIFY_BRIDGE_DIR/claude-channel.js"
  elif [ -n "$SERVER_URL" ]; then
    "$cli" mcp add --scope user "$server_name" \
      --env AIFY_SERVER_URL="$SERVER_URL" \
      --env CLAUDE_MCP_SERVER_URL="$SERVER_URL" \
      -- node "$AIFY_BRIDGE_DIR/claude-channel.js"
  else
    "$cli" mcp remove --scope local "$server_name" >/dev/null 2>&1 || true
    "$cli" mcp remove --scope project "$server_name" >/dev/null 2>&1 || true
    "$cli" mcp remove --scope user "$server_name" >/dev/null 2>&1 || true
    return
  fi
}

echo "=== aify-comms installer ==="
echo "Repo: $SCRIPT_DIR"
echo "Client: $CLIENT"
echo "Server: ${SERVER_URL:-local mode (no shared server)}"
echo ""

# Plan 5 (2026-05-25): pre-build hermes web_dist BEFORE the heavy install
# steps so a fresh hermes install doesn't fall through to plain `hermes`
# (which leaves AIFY_HERMES_GATEWAY_URL unexported and every resident
# wake mode reporting `hermes-missing-handle`). Also handles
# --prebuild-dry-run, used by tests to exercise just this branch without
# mutating the operator's env.
if [ "$CLIENT" = "hermes" ]; then
  # Test hook (WS1 Task 1.5): generate ONLY the hermes wrappers into the given
  # dir and exit. No prebuild, no npm, no MCP registration, no env mutation —
  # purely so tests can assert on the generated wrapper text.
  if [ -n "$EMIT_HERMES_WRAPPERS_DIR" ]; then
    mkdir -p "$EMIT_HERMES_WRAPPERS_DIR"
    install_hermes_wrapper
    exit 0
  fi
  prebuild_hermes_web_dist || true
  if [ "$PREBUILD_DRY_RUN" = true ]; then
    # Dry-run: only the prebuild branch was exercised. Skip wrapper writes,
    # MCP registration, and post-install steps so tests don't touch the
    # operator's environment or invoke npm/hermes.
    exit 0
  fi
fi

# FIX 7 (2026-06-03): codex emit hook — mirror the hermes one. Generate ONLY the
# codex-aify wrapper into the given dir and exit. No npm, no MCP registration, no
# env mutation, no codex-presence check — purely so the determinism test can
# assert on the rendered wrapper text (CODEX_AUTO / bypass flag).
if [ "$CLIENT" = "codex" ] && [ -n "$EMIT_CODEX_WRAPPERS_DIR" ]; then
  mkdir -p "$EMIT_CODEX_WRAPPERS_DIR"
  install_codex_wrapper
  exit 0
fi

require_cmd node
require_cmd npm
if [ "$CLIENT" = "pi" ]; then
  require_cmd omp
elif [ "$CLIENT" = "hermes" ]; then
  require_hermes_cmd
else
  require_cmd "$CLIENT"
fi

echo "[1/4] Installing MCP dependencies..."
cd "$SCRIPT_DIR/mcp/stdio"
npm install --silent
cd "$SCRIPT_DIR"
# Copy the bridge runtime (with node_modules) into the native dotfolder that
# every wrapper + MCP config points at. Re-synced on every install (mirror with
# --delete) so security fixes flow; self-contained so a client needs no repo.
copy_bridge_to_native_dir
echo "  Done."

echo "[2/4] Installing agent guidance..."
if [ "$CLIENT" = "claude" ]; then
  copy_claude_assets
elif [ "$CLIENT" = "codex" ]; then
  copy_codex_assets
elif [ "$CLIENT" = "hermes" ]; then
  copy_hermes_assets
fi
echo "  Done."

echo "[3/4] Registering MCP server..."
register_stdio_server "$CLIENT"
if [ "$CLIENT" = "claude" ]; then
  register_claude_channel_server "$CLIENT"
fi
if [ "$CLIENT" = "codex" ]; then
  migrate_codex_hooks_key
fi
install_bridge_launcher
echo "  Done."

if [ "$WITH_HOOK" = true ]; then
  echo "[4/4] Installing notification hook..."
  if [ "$CLIENT" = "claude" ]; then
    install_claude_hook
  elif [ "$CLIENT" = "codex" ]; then
    install_codex_hook
  elif [ "$CLIENT" = "hermes" ]; then
    install_hermes_hook
  else
    echo "  Notification hook install is not implemented for $CLIENT yet; skipping."
  fi
  echo "  Done."
else
  echo "[4/4] Notification hook skipped (use --with-hook to enable)."
fi

if [ "$CLIENT" = "claude" ]; then
  if [ -n "$SERVER_URL" ]; then
    install_claude_wrapper
    # Always install the Stop + UserPromptSubmit hooks (not gated on
    # --with-hook). Stop is the architectural turn-end signal; UserPromptSubmit
    # is its symmetric counterpart so direct CLI typing (not just channel-
    # route dispatches) flips the dashboard to "working". Both hooks are
    # no-ops for regular `claude` sessions without AIFY_AGENT_ID set, so
    # safe to install user-scoped.
    install_claude_turn_end_hook
    install_claude_turn_start_hook
  else
    remove_claude_wrapper
  fi
elif [ "$CLIENT" = "codex" ]; then
  install_codex_wrapper
  # Symmetric turn-start/turn-end hooks for direct codex-aify typing,
  # mirroring claude-aify. Codex's hooks.json shares the Claude Code
  # schema (UserPromptSubmit, Stop). Inert if a particular codex CLI
  # version doesn't recognize the events yet.
  install_codex_turn_hooks
elif [ "$CLIENT" = "hermes" ]; then
  # Plan 1.4 (2026-05-30): the dead `patch_hermes_gateway_visible_bind` source
  # patch (and its TUI active-session-file companion) is REMOVED. Managed/
  # resident hermes delivery now flows through the per-agent hidden gateway host
  # + the `hermes-managed-host.js run <agent>` delivery loop (no WS visible-
  # session bind), so the old tui_gateway/server.py patch is dead. The Codex
  # stream NoneType SDK-bug
  # fallback is unrelated to delivery and still useful, so keep it under the
  # legacy gate (off by default).
  if [ "${AIFY_HERMES_LEGACY_SOURCE_PATCH:-0}" = "1" ]; then
    _hermes_root="$(detect_hermes_install_root)"
    if [ -n "$_hermes_root" ] && [ -d "$_hermes_root" ]; then
      patch_hermes_codex_stream_none_fallback "$_hermes_root"
    fi
  else
    echo "Hermes source patching skipped; hermes-aify loads integrations/hermes-aify-plugin at runtime."
    echo "  Set AIFY_HERMES_LEGACY_SOURCE_PATCH=1 before install for the legacy Codex-stream source patch."
  fi
  # Install the shim as a Hermes plugin so it loads in the gateway process
  # (where hermes strips PYTHONPATH). The aify-comms MCP server is registered
  # into hermes' config.yaml mcp_servers by register_stdio_server above, which
  # is what gives the in-session hermes agent the comms_* tools for self-reply.
  install_hermes_plugin
  install_hermes_wrapper
  # Symmetric turn-start hook for hermes-aify direct typing via the
  # pre_llm_call shell-hook event. No matching turn-end hook because
  # upstream hermes shell-hooks don't expose one; the 120s server-side
  # turn_busy stale window handles cleanup.
  install_hermes_turn_hooks
  # Post-install LOUD probe (Plan 1.4 Step 4): there is no silent success path.
  # We cannot ensure a real per-agent daemon at install time without an agent
  # id, but we MUST tell the operator the daemon is brought up lazily at launch
  # and how it fails loudly if it can't — replacing the old patch's silent path.
  echo "Hermes delivery (managed AND resident): hermes-aify brings up a per-agent"
  echo "  hidden tui_gateway host (node mcp/stdio/hermes-managed-host.js ensure-host"
  echo "  <agentId>) + a background delivery loop that prompt.submits into the visible"
  echo "  TUI's session; on failure the wrapper prints a FATAL error and exits"
  echo "  non-zero (no silent no-op). Both modes share this gateway-host path so"
  echo "  injected messages render in the visible terminal (2026-06-02 convergence)."
  if command -v node >/dev/null 2>&1; then
    if node --check "$AIFY_BRIDGE_DIR/hermes-daemon-cli.js" >/dev/null 2>&1 \
      && node --check "$AIFY_BRIDGE_DIR/hermes-managed-host.js" >/dev/null 2>&1; then
      echo "  Bridges verified: hermes-daemon-cli.js + hermes-managed-host.js parse OK."
    else
      echo "  ERROR: hermes-daemon-cli.js / hermes-managed-host.js failed node --check — fix before launch." >&2
    fi
  fi
elif [ "$CLIENT" = "pi" ]; then
  install_pi_wrapper
fi

echo ""
echo "=== Installation complete ==="
echo "Environment bridge launcher installed: aify-comms"
echo "  Run it on each host/runtime environment you want visible in the dashboard."
echo "  Default:  aify-comms"
echo "  Extra root: aify-comms /path/to/extra/root"
echo "  Remote service: aify-comms http://host:8800 /path/to/extra/root"
if is_git_bash_windows; then
  echo "  Windows shim installed at %USERPROFILE%\\.local\\bin\\aify-comms.cmd"
fi
if [ "$CLIENT" = "claude" ]; then
  echo "Restart Claude Code for changes to take effect."
  if [ -n "$SERVER_URL" ]; then
    echo "For resident-session wakeups, start Claude with: claude-aify"
    echo "  (wrapper installed at ~/.local/bin/claude-aify)"
    if is_git_bash_windows; then
      echo "  Windows shim installed at %USERPROFILE%\\.local\\bin\\claude-aify.cmd"
    fi
  else
    echo "Local-only install: resident Claude wakeups are disabled because no shared server URL was provided."
    echo "No claude-aify wrapper was installed."
  fi
elif [ "$CLIENT" = "codex" ]; then
  echo "Restart Codex for changes to take effect."
  echo "For live resident wakeups, start Codex with: codex-aify"
  echo "  (wrapper installed at ~/.local/bin/codex-aify)"
  if is_git_bash_windows; then
    echo "  Windows shim installed at %USERPROFILE%\\.local\\bin\\codex-aify.cmd"
  fi
elif [ "$CLIENT" = "hermes" ]; then
  echo "Restart Hermes Agent for changes to take effect."
  echo "For resident-session wakeups, start Hermes with: hermes-aify"
  echo "  (wrapper installed at ~/.local/bin/hermes-aify)"
  if is_git_bash_windows; then
    echo "  Windows shim installed at %USERPROFILE%\\.local\\bin\\hermes-aify.cmd"
  fi
else
  if [ "$CLIENT" = "opencode" ]; then
    echo "Restart OpenCode for changes to take effect."
  else
    echo "Restart Oh My Pi for changes to take effect."
    echo "For resident-session wakeups, start Pi with: omp-aify (alias: pi-aify)"
    echo "  (wrappers installed at ~/.local/bin/omp-aify and ~/.local/bin/pi-aify)"
    if is_git_bash_windows; then
      echo "  Windows shims installed at %USERPROFILE%\\.local\\bin\\omp-aify.cmd and pi-aify.cmd"
    fi
  fi
fi
echo ""
echo "Quick start:"
if [ "$CLIENT" = "codex" ]; then
  echo "  comms_register(agentId=\"my-agent\", role=\"coder\", runtime=\"codex\", appServerUrl=\"\$AIFY_CODEX_APP_SERVER_URL\")"
  echo "  # Current bridges auto-discover the live Codex thread from the app-server when possible."
  echo "  # Add sessionHandle=\"\$CODEX_THREAD_ID\" only when CODEX_THREAD_ID is non-empty in this same session."
elif [ "$CLIENT" = "claude" ]; then
  echo "  comms_register(agentId=\"my-agent\", role=\"coder\", runtime=\"claude-code\")"
elif [ "$CLIENT" = "pi" ]; then
  echo "  comms_register(agentId=\"my-agent\", role=\"coder\", runtime=\"pi\", sessionHandle=\"\$PI_SESSION_ID\")"
  echo "  # If PI_SESSION_ID is unavailable, omit sessionHandle; resident Pi will be visible but not resumable until bound."
elif [ "$CLIENT" = "hermes" ]; then
  echo "  comms_register(agentId=\"my-agent\", role=\"coder\", runtime=\"hermes\")"
  echo "  # Add sessionHandle=\"\$HERMES_SESSION_ID\" only after explicit hermes-aify --resume <id> in this same terminal."
else
  echo "  comms_register(agentId=\"my-agent\", role=\"coder\")"
fi
echo "  comms_agents()"
echo "  comms_send(from=\"my-agent\", to=\"other-agent\", type=\"info\", subject=\"Hello\", body=\"Hi there\")"
echo "  comms_inbox(agentId=\"my-agent\", mode=\"headers\")"
echo "  comms_inbox(agentId=\"my-agent\", messageId=\"<message id>\")"
