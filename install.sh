#!/bin/bash
# Unified installer for aify-comms on Claude Code, Codex, Hermes, OpenCode, or Oh My Pi.
#
# Usage:
#   bash install.sh --client claude
#   bash install.sh --client codex
#   bash install.sh --client codex http://192.168.100.10:8800 --with-hook
#   bash install.sh --client hermes http://192.168.100.10:8800 --with-hook
#   bash install.sh --client opencode http://192.168.100.10:8800
#   bash install.sh --client pi http://192.168.100.10:8800

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLIENT="claude"
SERVER_URL=""
WITH_HOOK=false
# Plan 5 (2026-05-25): --prebuild-dry-run exits after running the hermes
# web_dist prebuild branch (no npm invocation, no wrapper writes). Used by
# service/tests/test_install_hermes_prebuild.py to verify the branch's
# detection logic without touching the operator's environment.
PREBUILD_DRY_RUN=false
DEFAULT_AIFY_SERVER_URL="${AIFY_DEFAULT_SERVER_URL:-http://192.168.100.10:8800}"

usage() {
  cat <<EOF
Usage:
  bash install.sh --client <claude|codex|hermes|opencode|pi> [SERVER_URL] [--with-hook]

Examples:
  bash install.sh --client claude
  bash install.sh --client claude http://192.168.100.10:8800 --with-hook
  bash install.sh --client codex http://192.168.100.10:8800
  bash install.sh --client hermes http://192.168.100.10:8800 --with-hook
  bash install.sh --client opencode http://192.168.100.10:8800
  bash install.sh --client pi http://192.168.100.10:8800
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

if [ "$CLIENT" != "claude" ] && [ "$CLIENT" != "codex" ] && [ "$CLIENT" != "hermes" ] && [ "$CLIENT" != "opencode" ] && [ "$CLIENT" != "pi" ]; then
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
CLAUDE_AUTO=false
CLAUDE_AIFY_AGENT_ID="\${AIFY_AGENT_ID:-}"
CLAUDE_AIFY_ROLE="\${AIFY_AGENT_ROLE:-coder}"
# Explicit session-mode opt-in. --resident is the default for human
# invocation; --managed is set by aify-comms when it spawns this wrapper
# as a backing process. If neither flag is passed and AIFY_SESSION_MODE
# is unset, auto-detect via TTY presence on stdin: interactive → resident.
CLAUDE_AIFY_SESSION_MODE="\${AIFY_SESSION_MODE:-}"
CLAUDE_ARGS=()
PREV_ARG=""
for ARG in "\$@"; do
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
  if [ "\$ARG" = "-auto" ] || [ "\$ARG" = "--auto" ]; then
    CLAUDE_AUTO=true
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
  case "\$ARG" in
  --aify-agent=*|--agent-id=*)
    CLAUDE_AIFY_AGENT_ID="\${ARG#*=}"
    continue
    ;;
  --aify-role=*)
    CLAUDE_AIFY_ROLE="\${ARG#*=}"
    continue
    ;;
  esac
  CLAUDE_ARGS+=("\$ARG")
  if [ "\$PREV_ARG" = "--resume" ] || [ "\$PREV_ARG" = "--session-id" ]; then
    CLAUDE_RESUME_ID="\$ARG"
  else
    case "\$ARG" in
    --resume=*|--session-id=*)
      CLAUDE_RESUME_ID="\${ARG#*=}"
      ;;
    esac
  fi
  PREV_ARG="\$ARG"
done
if [ -n "\$CLAUDE_RESUME_ID" ]; then
  export CLAUDE_SESSION_ID="\$CLAUDE_RESUME_ID"
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
    AIFY_SCRIPT_DIR_FWD="\$(cygpath -m "$SCRIPT_DIR")"
  else
    AIFY_SCRIPT_DIR_FWD="$SCRIPT_DIR"
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
  trap 'rm -f "\$AIFY_MCP_CONFIG"' EXIT
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
  local wrapper_dir="$HOME/.local/bin"
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

# Plan 6 B2 (2026-05-26): rediscover the real codex thread id by walking
# ~/.codex/sessions for the newest rollout-*.jsonl and extracting the UUID
# from the filename. Mirrors the Python adapter's discover_session_id at
# service/runtimes/codex.py — the codex app-server does not (yet) expose
# an introspection RPC, so the filesystem scan is the authoritative source.
# Stdout: the discovered uuid (string), or empty on any failure. Caller
# treats empty as "leave env alone".
rediscover_codex_thread_id() {
  local root="$HOME/.codex/sessions"
  [ -d "$root" ] || return 0
  # find . -type f -name '*.jsonl' | newest first | first | extract uuid
  # GNU find supports -printf '%T@ %p\n'; macOS BSD find does not. Use
  # `stat` for portability — slower but works on both. Limit depth to 5
  # so the scan stays bounded if codex's layout changes.
  local newest
  newest="$(find "$root" -maxdepth 5 -type f -name '*.jsonl' -print 2>/dev/null \
    | while IFS= read -r f; do
        # Portable mtime: ls -tr lists oldest first; we'll use perl/python
        # only as fallback. Cheapest portable: ls --time=mtime -lt + head.
        printf '%s\t%s\n' "$(stat -c '%Y' "$f" 2>/dev/null || stat -f '%m' "$f" 2>/dev/null)" "$f"
      done \
    | sort -nr \
    | head -1 \
    | cut -f2-)"
  [ -n "$newest" ] || return 0
  # Filename: rollout-YYYY-MM-DDThh-mm-ss-<uuid>.jsonl OR <uuid>.jsonl
  local base
  base="$(basename "$newest")"
  # Strip .jsonl, then extract trailing UUID (8-4-4-4-12 hex).
  local stem="${base%.jsonl}"
  # Grep the UUID anywhere in the stem; if absent, output the whole stem
  # (covers flat-layout <uuid>.jsonl files).
  local uuid
  uuid="$(printf '%s' "$stem" | grep -oE '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}' | tail -1)"
  if [ -z "$uuid" ]; then
    case "$stem" in
    rollout-*) ;;  # rollout-<no-uuid> — nothing usable, leave empty
    *) uuid="$stem" ;;  # flat layout: stem IS the id
    esac
  fi
  [ -n "$uuid" ] && printf '%s' "$uuid"
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

if command -v setsid >/dev/null 2>&1; then
  setsid codex app-server --listen "$APP_SERVER_URL" </dev/null >>"$LOG_FILE" 2>&1 &
else
  codex app-server --listen "$APP_SERVER_URL" </dev/null >>"$LOG_FILE" 2>&1 &
fi
APP_SERVER_PID=$!

# The runtime marker is written by the long-lived aify-comms MCP bridge
# itself (mcp/stdio/server.js) on startup when it sees
# AIFY_CODEX_APP_SERVER_URL in its environment.

cleanup() {
  if kill -0 "$APP_SERVER_PID" >/dev/null 2>&1; then
    kill "$APP_SERVER_PID" >/dev/null 2>&1 || true
    wait "$APP_SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if ! wait_for_port "$PORT"; then
  echo "codex-aify could not reach the local app-server at $APP_SERVER_URL." >&2
  echo "Check $LOG_FILE for details." >&2
  exit 1
fi

CODEX_PERMISSION_FLAGS=()
CODEX_ARGS=()
CODEX_AUTO=false
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
  # subcommand argument (`codex resume --include-non-interactive <id>`),
  # not as a flag on the top-level `codex --remote` invocation.
  if [ "$PREV_ARG" = "--resume" ] || [ "$PREV_ARG" = "--session-id" ]; then
    CODEX_RESUME_HANDLE="$ARG"
    PREV_ARG=""
    continue
  fi
  if [ "$ARG" = "-auto" ] || [ "$ARG" = "--auto" ]; then
    CODEX_AUTO=true
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

# Plan 6 B2 (2026-05-26; reordered 2026-05-26 per code review): rediscover
# the real codex thread id from ~/.codex/sessions AFTER the arg parser has
# run. The bridge inside the wrapper inherits the same CODEX_THREAD_ID /
# AIFY_SESSION_HANDLE that `exec codex` will see. When the operator passes
# an explicit `--resume <id>` on the command line, the parser populates
# CODEX_RESUME_HANDLE — we honor that as authoritative and skip the
# rediscover-export so the bridge advertises the same handle that codex
# is actually attached to. Without explicit resume, the rediscover wins
# over any stale CODEX_THREAD_ID inherited from the parent shell.
#
# Failure mode: rediscover empty (no sessions on disk yet, or the find
# returns nothing) — leave env as-is, the Plan 6 A1 heartbeat will
# correct any drift within 60s.
if [ -z "${CODEX_RESUME_HANDLE:-}" ]; then
  CODEX_REDISCOVERED_THREAD_ID="$(rediscover_codex_thread_id 2>/dev/null || true)"
  if [ -n "$CODEX_REDISCOVERED_THREAD_ID" ]; then
    if [ "${CODEX_THREAD_ID:-}" != "$CODEX_REDISCOVERED_THREAD_ID" ]; then
      echo "[codex-aify] thread id rediscovered: '${CODEX_THREAD_ID:-}' -> '$CODEX_REDISCOVERED_THREAD_ID' (from ~/.codex/sessions)" >&2
    fi
    export CODEX_THREAD_ID="$CODEX_REDISCOVERED_THREAD_ID"
    export AIFY_SESSION_HANDLE="$CODEX_REDISCOVERED_THREAD_ID"
  fi
else
  # Explicit --resume <id> from operator wins. Make the bridge see the
  # same handle codex will resume so they don't disagree for the first
  # 60s after launch.
  export CODEX_THREAD_ID="$CODEX_RESUME_HANDLE"
  export AIFY_SESSION_HANDLE="$CODEX_RESUME_HANDLE"
fi

if [ "$CODEX_AUTO" = true ]; then
  CODEX_PERMISSION_FLAGS+=(--dangerously-bypass-approvals-and-sandbox)
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
    exec codex --remote "$APP_SERVER_URL" "${CODEX_PERMISSION_FLAGS[@]}" "${CODEX_ARGS[@]}" resume --include-non-interactive "$CODEX_RESUME_HANDLE"
  else
    echo "[codex-aify] saved session $CODEX_RESUME_HANDLE not found in codex storage; starting fresh codex" >&2
  fi
fi
exec codex --remote "$APP_SERVER_URL" "${CODEX_PERMISSION_FLAGS[@]}" "${CODEX_ARGS[@]}"
EOF
  # Substitute the install-time service URL into the wrapper. The
  # heredoc above is single-quoted so `$SERVER_URL` is NOT expanded
  # inside it — using a placeholder + post-substitution lets the
  # wrapper respect a runtime-set AIFY_COMMS_URL while falling back
  # to the URL the operator passed to install.sh. Without this, the
  # wrapper hardcoded 127.0.0.1:8800 regardless of `--client codex <url>`.
  sed -i.bak "s|__AIFY_INSTALL_TIME_URL__|${SERVER_URL:-http://127.0.0.1:8800}|" "$wrapper_path" && rm -f "$wrapper_path.bak"
  chmod +x "$wrapper_path"
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
    PI_REDISCOVERED_SESSION_ID="$(printf '%s' "$AIFY_WATCHDOG_BODY" \
      | grep -oE '"sessionId"[[:space:]]*:[[:space:]]*"[^"]+"' \
      | head -1 \
      | sed -E 's/.*"sessionId"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
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

exec "$PI_RUNTIME_COMMAND" "${PI_ARGS[@]}"
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
prebuild_hermes_web_dist() {
  local hermes_install_root="${AIFY_HERMES_INSTALL_ROOT:-}"
  if [ -z "$hermes_install_root" ]; then
    # `hermes config path` reports something like
    # /c/Users/Administrator/AppData/Local/hermes/hermes-agent/hermes_cli/config.yaml
    # — strip from /hermes_cli/ onward to recover the install root.
    if command -v hermes >/dev/null 2>&1; then
      local cfg_path
      cfg_path="$(hermes config path 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
      if [ -n "$cfg_path" ]; then
        hermes_install_root="${cfg_path%%/hermes_cli/*}"
      fi
    fi
  fi
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

install_hermes_wrapper() {
  local wrapper_dir="$HOME/.local/bin"
  local wrapper_path="$wrapper_dir/hermes-aify"
  local default_server="${SERVER_URL:-$DEFAULT_AIFY_SERVER_URL}"
  mkdir -p "$wrapper_dir"
  cat > "$wrapper_path" <<EOF
#!/bin/bash
set -euo pipefail

HERMES_AIFY_AGENT_ID="\${AIFY_AGENT_ID:-}"
HERMES_AIFY_ROLE="\${AIFY_AGENT_ROLE:-coder}"
HERMES_AIFY_SESSION_MODE="\${AIFY_SESSION_MODE:-}"
HERMES_SESSION_HANDLE="\${HERMES_SESSION_ID:-\${HERMES_SESSION:-\${AIFY_SESSION_HANDLE:-}}}"
HERMES_RUNTIME_COMMAND="\${AIFY_HERMES_COMMAND:-\${HERMES_COMMAND:-hermes}}"
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
    ;;
  -r=*)
    HERMES_SESSION_HANDLE="\${ARG#*=}"
    ;;
  esac
  HERMES_ARGS+=("\$ARG")
  if [ "\$PREV_ARG" = "--resume" ] || [ "\$PREV_ARG" = "--session-id" ] || [ "\$PREV_ARG" = "-r" ]; then
    HERMES_SESSION_HANDLE="\$ARG"
  fi
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
if [ -n "\$HERMES_AIFY_AGENT_ID" ]; then
  export AIFY_AGENT_ID="\$HERMES_AIFY_AGENT_ID"
  export AIFY_AGENT_ROLE="\$HERMES_AIFY_ROLE"
fi
if [ -n "\$HERMES_SESSION_HANDLE" ]; then
  export HERMES_SESSION_ID="\$HERMES_SESSION_HANDLE"
  export AIFY_SESSION_HANDLE="\$HERMES_SESSION_HANDLE"
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

# Plan 5 (2026-05-25): when the wrapper falls back to plain \`hermes\`
# (gateway disabled, port alloc failed, dashboard probe failed, token
# capture failed) AIFY_HERMES_GATEWAY_URL is NOT exported and every
# resident-channel wake for this session reports \`hermes-missing-handle\`.
# Without a visible warning the operator can't tell why their messages
# never arrive — they just see status='online' with no replies. Print
# a one-time WARNING block at fallback so the cause is immediately
# obvious in the shell scrollback, plus point at the dashboard log for
# the underlying error.
aify_hermes_fallback() {
  local reason="\${1:-unknown}"
  echo "" >&2
  echo "[hermes-aify] WARNING: AIFY_HERMES_GATEWAY_URL was NOT exported to this hermes session." >&2
  echo "[hermes-aify]   Reason: \$reason" >&2
  if [ -n "\${AIFY_HERMES_DASHBOARD_LOG:-}" ]; then
    echo "[hermes-aify]   Log:    \$AIFY_HERMES_DASHBOARD_LOG" >&2
  fi
  echo "[hermes-aify]   Effect: comms wake/dispatch to this agent will report 'hermes-missing-handle'." >&2
  echo "[hermes-aify]   Fix:    re-run install.sh --client hermes to prebuild hermes web_dist, or" >&2
  echo "[hermes-aify]           inspect the dashboard log above for the underlying error." >&2
  echo "" >&2
  exec "\$HERMES_RUNTIME_COMMAND" "\${HERMES_ARGS[@]}"
}

# Resident-mode bridge-injection path (mirror of codex-aify install.sh:319-424
# and the claude-channel.js path). When the operator launches hermes-aify
# interactively, we:
#   1. Spawn \`hermes dashboard --tui --port <P> --no-open --skip-build\` in the
#      background. This sets _DASHBOARD_EMBEDDED_CHAT_ENABLED=True in
#      web_server.py and mounts the /api/ws JSON-RPC gateway.
#   2. Wait for the dashboard to bind, then fetch / and parse the ephemeral
#      __HERMES_SESSION_TOKEN__ from the embedded <script> tag.
#   3. Export HERMES_TUI_GATEWAY_URL so the Ink TUI launched by \`hermes chat
#      --tui\` attaches to the running gateway via WebSocket (per
#      ui-tui/src/gatewayClient.ts:resolveGatewayAttachUrl) instead of
#      spawning its own stdio sidecar.
#   4. Export AIFY_HERMES_GATEWAY_URL + AIFY_HERMES_GATEWAY_TOKEN so the
#      aify-comms bridge (loaded inside hermes chat as an MCP server) writes
#      a hermes runtime marker and the resident-channel controller in
#      runtimes.js connects to the same /api/ws for bridge-injected prompts.
#   5. Trap cleanup kills the dashboard child on wrapper exit.
#
# Opt out: AIFY_HERMES_SKIP_GATEWAY=1 falls back to plain \`hermes\` exec
# (no gateway, no bridge-injection — operator-typed only). Use this if the
# dashboard probe is breaking your install and you don't need resident wake.
if [ "\${AIFY_HERMES_SKIP_GATEWAY:-0}" != "1" ]; then
  # Spawn the hermes dashboard backing for BOTH resident and managed
  # invocations. Resident: operator's Ink TUI attaches via the gateway,
  # bridge attaches as a WS peer for dispatch injection. Managed
  # (bridge-spawned via TerminalProcessManager): the dashboard renders
  # the wrapper's Ink TUI via xterm.js, and the bridge can attach to
  # the same gateway. Set AIFY_HERMES_SKIP_GATEWAY=1 to fall back to
  # plain \`hermes\` exec without the dashboard child.
  pick_port() {
    node -e '
      const net = require("net");
      const srv = net.createServer();
      srv.listen(0, "127.0.0.1", () => {
        const p = srv.address().port;
        srv.close(() => { process.stdout.write(String(p)); });
      });
    '
  }

  wait_for_http() {
    local url="\$1"
    local deadline=\$(( \$(date +%s) + 30 ))
    while [ \$(date +%s) -lt "\$deadline" ]; do
      if curl -s -o /dev/null "\$url"; then return 0; fi
      sleep 0.2
    done
    return 1
  }

  # Plan 6 B1 (2026-05-26): rediscover the real hermes session id from the
  # gateway's session.most_recent JSON-RPC over WS. Stdout: the discovered
  # session id (string), or empty on any failure. Failure is silent — the
  # caller treats empty as "leave env alone" and the bridge's discover-first
  # heartbeat (Plan 6 A1) catches drift within 60s.
  #
  # Uses node + the \`ws\` module shipped under mcp/stdio/node_modules. The
  # repo path is baked in at install time from \$SCRIPT_DIR; on Windows git-
  # bash this is the same MSYS-style path node already resolves for the
  # main MCP bridge spawn. 3s hard timeout — the gateway is local so any
  # longer means the dashboard is misbehaving and we should fall back to
  # the env value.
  rediscover_hermes_session_id() {
    local gateway_url="\$1"
    AIFY_GATEWAY_URL="\$gateway_url" node -e '
      let WebSocket;
      try { WebSocket = require("ws"); }
      catch {
        try { WebSocket = require("$SCRIPT_DIR/mcp/stdio/node_modules/ws"); }
        catch { process.exit(0); }
      }
      const url = process.env.AIFY_GATEWAY_URL;
      if (!url) process.exit(0);
      const ws = new WebSocket(url, { perMessageDeflate: false });
      let done = false;
      const finish = () => { if (!done) { done = true; try { ws.terminate(); } catch {} process.exit(0); } };
      const timer = setTimeout(finish, 3000);
      ws.on("open", () => {
        try { ws.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "session.most_recent" })); }
        catch { clearTimeout(timer); finish(); }
      });
      ws.on("message", (raw) => {
        try {
          const msg = JSON.parse(String(raw));
          if (msg && msg.id === 1 && msg.result) {
            const sid = msg.result.sessionId || msg.result.session_id || msg.result.id;
            if (sid) { process.stdout.write(String(sid)); }
          }
        } catch {}
        clearTimeout(timer);
        finish();
      });
      ws.on("error", () => { clearTimeout(timer); finish(); });
    ' 2>/dev/null
  }

  AIFY_HERMES_PORT="\$(pick_port)"
  if [ -z "\$AIFY_HERMES_PORT" ]; then
    echo "hermes-aify: failed to allocate a local port for the dashboard gateway; falling back to plain hermes." >&2
    aify_hermes_fallback "port_alloc_failed"
  fi

  AIFY_HERMES_DASHBOARD_URL="http://127.0.0.1:\$AIFY_HERMES_PORT"
  LOG_ROOT="\${XDG_STATE_HOME:-\$HOME/.local/state}/aify-comms"
  mkdir -p "\$LOG_ROOT"
  AIFY_HERMES_DASHBOARD_LOG="\$LOG_ROOT/hermes-aify-dashboard-\$AIFY_HERMES_PORT.log"

  if command -v setsid >/dev/null 2>&1; then
    setsid "\$HERMES_RUNTIME_COMMAND" dashboard --tui --port "\$AIFY_HERMES_PORT" --host 127.0.0.1 --no-open --skip-build </dev/null >>"\$AIFY_HERMES_DASHBOARD_LOG" 2>&1 &
  else
    "\$HERMES_RUNTIME_COMMAND" dashboard --tui --port "\$AIFY_HERMES_PORT" --host 127.0.0.1 --no-open --skip-build </dev/null >>"\$AIFY_HERMES_DASHBOARD_LOG" 2>&1 &
  fi
  AIFY_HERMES_DASHBOARD_PID=\$!

  cleanup_aify_dashboard() {
    if [ -n "\${AIFY_HERMES_DASHBOARD_PID:-}" ] && kill -0 "\$AIFY_HERMES_DASHBOARD_PID" >/dev/null 2>&1; then
      kill "\$AIFY_HERMES_DASHBOARD_PID" >/dev/null 2>&1 || true
      wait "\$AIFY_HERMES_DASHBOARD_PID" 2>/dev/null || true
    fi
  }
  trap cleanup_aify_dashboard EXIT INT TERM

  if ! wait_for_http "\$AIFY_HERMES_DASHBOARD_URL/"; then
    echo "hermes-aify: dashboard at \$AIFY_HERMES_DASHBOARD_URL did not become reachable. Falling back to plain hermes." >&2
    echo "  log: \$AIFY_HERMES_DASHBOARD_LOG" >&2
    cleanup_aify_dashboard
    aify_hermes_fallback "dashboard_unreachable (likely missing web_dist — run install.sh --client hermes to prebuild)"
  fi

  # web_server.py:3688 injects: <script>window.__HERMES_SESSION_TOKEN__="..."
  AIFY_HERMES_TOKEN="\$(curl -s "\$AIFY_HERMES_DASHBOARD_URL/" | grep -oE '__HERMES_SESSION_TOKEN__="[^"]+"' | head -1 | sed -E 's/.*="([^"]+)"\$/\1/')"
  if [ -z "\$AIFY_HERMES_TOKEN" ]; then
    echo "hermes-aify: could not capture the dashboard session token from \$AIFY_HERMES_DASHBOARD_URL/. Falling back to plain hermes." >&2
    cleanup_aify_dashboard
    aify_hermes_fallback "token_capture_failed"
  fi

  AIFY_HERMES_GATEWAY="ws://127.0.0.1:\$AIFY_HERMES_PORT/api/ws?token=\$AIFY_HERMES_TOKEN"
  export HERMES_TUI_GATEWAY_URL="\$AIFY_HERMES_GATEWAY"
  export AIFY_HERMES_GATEWAY_URL="\$AIFY_HERMES_GATEWAY"
  export AIFY_HERMES_GATEWAY_TOKEN="\$AIFY_HERMES_TOKEN"

  # Plan 6 B1 (2026-05-26): rediscover the real hermes session id from the
  # gateway's session.most_recent RPC. Overwrites HERMES_SESSION_ID /
  # AIFY_SESSION_HANDLE so the inner aify-comms MCP bridge registers with
  # the truthful id — not whatever stale value the operator's shell
  # inherited from a prior hermes session. Failures here are non-fatal:
  # the bridge's discover-first heartbeat (Plan 6 A1) corrects any drift
  # within 60s.
  HERMES_REDISCOVERED_SESSION_ID="\$(rediscover_hermes_session_id "\$AIFY_HERMES_GATEWAY" 2>/dev/null || true)"
  if [ -n "\$HERMES_REDISCOVERED_SESSION_ID" ]; then
    if [ "\$HERMES_REDISCOVERED_SESSION_ID" != "\$HERMES_SESSION_HANDLE" ]; then
      echo "[hermes-aify] session id rediscovered: '\$HERMES_SESSION_HANDLE' -> '\$HERMES_REDISCOVERED_SESSION_ID' (from gateway)" >&2
    fi
    export HERMES_SESSION_ID="\$HERMES_REDISCOVERED_SESSION_ID"
    export AIFY_SESSION_HANDLE="\$HERMES_REDISCOVERED_SESSION_ID"
    HERMES_SESSION_HANDLE="\$HERMES_REDISCOVERED_SESSION_ID"
  fi

  # Default to \`hermes chat --tui\` for the operator's interactive TUI when
  # no explicit subcommand args were passed. If the operator passed args
  # (e.g. \`hermes-aify model list\`), pass them through unchanged.
  if [ \${#HERMES_ARGS[@]} -eq 0 ]; then
    exec "\$HERMES_RUNTIME_COMMAND" chat --tui
  fi
  exec "\$HERMES_RUNTIME_COMMAND" "\${HERMES_ARGS[@]}"
fi

# Plan 5 (2026-05-25): explicit AIFY_HERMES_SKIP_GATEWAY=1 fallback. The
# warning here is lighter than the failure-path banner above — operator
# opted out, so make sure they know wake/dispatch won't work, but skip
# the dashboard-log pointer (there is no log; gateway never started).
aify_hermes_fallback "gateway_disabled (AIFY_HERMES_SKIP_GATEWAY=1)"
EOF
  # Same placeholder-substitute pattern as codex-aify above. Without
  # this the watchdog probe POSTs to 127.0.0.1:8800 regardless of the
  # operator's install-time URL.
  sed -i.bak "s|__AIFY_INSTALL_TIME_URL__|${SERVER_URL:-http://127.0.0.1:8800}|" "$wrapper_path" 2>/dev/null && rm -f "$wrapper_path.bak" || true
  chmod +x "$wrapper_path"
  install_windows_cmd_shim "hermes-aify" "$wrapper_dir"
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
if [ "\${1:-}" = "--help" ] || [ "\${1:-}" = "-h" ]; then
  cat <<'USAGE'
Usage: aify-comms [server-url] [extra-root ...]

Starts the local environment bridge for dashboard-managed agents.
The current directory is always an allowed workspace root. Extra roots are
optional safety boundaries.
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
exec node "$SCRIPT_DIR/mcp/stdio/server.js" --environment-bridge
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

path_for_node() {
  local value="$1"
  if is_git_bash_windows && command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$value"
    return
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
  if command -v hermes >/dev/null 2>&1; then
    local cfg_path=""
    cfg_path="$(hermes config path 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
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
  node_server_path="$(path_for_node "$SCRIPT_DIR/mcp/stdio/server.js")"

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
  node_server_path="$(path_for_node "$SCRIPT_DIR/mcp/stdio/server.js")"

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
  node_server_path="$(path_for_node "$SCRIPT_DIR/mcp/stdio/server.js")"

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
      `      AIFY_RUNTIME: \"\${AIFY_RUNTIME}\"`,
      `      AIFY_TERMINAL_ID: \"\${AIFY_TERMINAL_ID}\"`,
      `      AIFY_MANAGED_VIA_WRAPPER: \"\${AIFY_MANAGED_VIA_WRAPPER}\"`,
      `      HERMES_SESSION_ID: \"\${HERMES_SESSION_ID}\"`,
      `      AIFY_HERMES_GATEWAY_URL: \"\${AIFY_HERMES_GATEWAY_URL}\"`,
      `      AIFY_HERMES_GATEWAY_TOKEN: \"\${AIFY_HERMES_GATEWAY_TOKEN}\"`,
      `      HERMES_TUI_GATEWAY_URL: \"\${HERMES_TUI_GATEWAY_URL}\"`,
      ...(serverUrl ? [`      AIFY_SERVER_URL: ${JSON.stringify(serverUrl)}`, `      CLAUDE_MCP_SERVER_URL: ${JSON.stringify(serverUrl)}`] : []),
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
  node_notify_script="$(path_for_node "$SCRIPT_DIR/mcp/stdio/notify-check.js")"
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
  mkdir -p "$hook_dir"
  touch "$config_file"
  node_notify_script="$(path_for_node "$SCRIPT_DIR/mcp/stdio/notify-check.js")"

  cat > "$hook_path" <<EOF
#!/usr/bin/env bash
node $(shell_quote "$node_notify_script")
EOF
  chmod +x "$hook_path"

  MSYS_NO_PATHCONV=1 node -e '
    const fs = require("fs");
    const file = process.argv[1];
    const hookPath = process.argv[2];
    let text = "";
    try { text = fs.readFileSync(file, "utf8"); } catch (_) {}
    if (text.includes("aify-notify.sh")) process.exit(0);
    const entry = [
      "    - matcher: \".*\"",
      `      command: ${JSON.stringify(hookPath)}`,
      "      timeout: 3",
    ];
    const lines = text.replace(/\s*$/, "").split(/\r?\n/);
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
  ' "$(path_for_node "$config_file")" "$(path_for_node "$hook_path")"
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
  # hook exists for shell hooks; the existing 120s server-side
  # turn_busy stale window (or the per-process exit signal for
  # managed hermes dispatches) handles cleanup.
  local config_root="$(hermes_config_root)"
  local config_file="$config_root/config.yaml"
  local hook_dir="$config_root/agent-hooks"
  local hook_path="$hook_dir/aify-turn-start.sh"
  mkdir -p "$hook_dir"
  touch "$config_file"
  cat > "$hook_path" <<EOF
#!/usr/bin/env bash
if [ -n "\${AIFY_AGENT_ID:-}" ] && [ -n "\${AIFY_COMMS_URL:-}" ]; then
  curl -sS --max-time 2 -X POST "\${AIFY_COMMS_URL%/}/api/v1/agents/\${AIFY_AGENT_ID}/turn-start" >/dev/null 2>&1 || true
fi
EOF
  chmod +x "$hook_path"
  MSYS_NO_PATHCONV=1 node -e '
    const fs = require("fs");
    const file = process.argv[1];
    const hookPath = process.argv[2];
    let text = "";
    try { text = fs.readFileSync(file, "utf8"); } catch (_) {}
    if (text.includes("aify-turn-start.sh")) process.exit(0);
    const entry = [
      "    - matcher: \".*\"",
      `      command: ${JSON.stringify(hookPath)}`,
      "      timeout: 3",
    ];
    const lines = text.replace(/\s*$/, "").split(/\r?\n/);
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
  ' "$(path_for_node "$config_file")" "$(path_for_node "$hook_path")"
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
    if (!Array.isArray(settings.hooks.UserPromptSubmit)) settings.hooks.UserPromptSubmit = [];
    settings.hooks.UserPromptSubmit = settings.hooks.UserPromptSubmit.filter(
      h => !JSON.stringify(h).includes('/api/v1/agents/\${AIFY_AGENT_ID}/turn-start')
    );
    settings.hooks.UserPromptSubmit.push({
      hooks: [{
        type: 'command',
        command,
        timeout: 3
      }]
    });
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
  local hook_command
  hook_command='if [ -n "${AIFY_AGENT_ID:-}" ] && [ -n "${AIFY_COMMS_URL:-}" ]; then curl -sS --max-time 2 -X POST "${AIFY_COMMS_URL%/}/api/v1/agents/${AIFY_AGENT_ID}/turn-end" >/dev/null 2>&1 || true; fi'
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
  node_notify_script="$(path_for_node "$SCRIPT_DIR/mcp/stdio/notify-check.js")"
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
      -- node "$SCRIPT_DIR/mcp/stdio/server.js"
  elif [ -n "$SERVER_URL" ]; then
    "$cli" mcp add "$server_name" \
      "${scope_args[@]}" \
      --env AIFY_SERVER_URL="$SERVER_URL" \
      --env CLAUDE_MCP_SERVER_URL="$SERVER_URL" \
      -- node "$SCRIPT_DIR/mcp/stdio/server.js"
  else
    "$cli" mcp add "$server_name" \
      "${scope_args[@]}" \
      -- node "$SCRIPT_DIR/mcp/stdio/server.js"
  fi
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
      -- node "$SCRIPT_DIR/mcp/stdio/claude-channel.js"
  elif [ -n "$SERVER_URL" ]; then
    "$cli" mcp add --scope user "$server_name" \
      --env AIFY_SERVER_URL="$SERVER_URL" \
      --env CLAUDE_MCP_SERVER_URL="$SERVER_URL" \
      -- node "$SCRIPT_DIR/mcp/stdio/claude-channel.js"
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
  prebuild_hermes_web_dist || true
  if [ "$PREBUILD_DRY_RUN" = true ]; then
    # Dry-run: only the prebuild branch was exercised. Skip wrapper writes,
    # MCP registration, and post-install steps so tests don't touch the
    # operator's environment or invoke npm/hermes.
    exit 0
  fi
fi

require_cmd node
require_cmd npm
if [ "$CLIENT" = "pi" ]; then
  require_cmd omp
else
  require_cmd "$CLIENT"
fi

echo "[1/4] Installing MCP dependencies..."
cd "$SCRIPT_DIR/mcp/stdio"
npm install --silent
cd "$SCRIPT_DIR"
echo "  Done."

echo "[2/4] Installing agent guidance..."
if [ "$CLIENT" = "claude" ]; then
  copy_claude_assets
elif [ "$CLIENT" = "codex" ]; then
  copy_codex_assets
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
  install_hermes_wrapper
  # Symmetric turn-start hook for hermes-aify direct typing via the
  # pre_llm_call shell-hook event. No matching turn-end hook because
  # upstream hermes shell-hooks don't expose one; the 120s server-side
  # turn_busy stale window handles cleanup.
  install_hermes_turn_hooks
elif [ "$CLIENT" = "pi" ]; then
  install_pi_wrapper
fi

echo ""
echo "=== Installation complete ==="
echo "Environment bridge launcher installed: aify-comms"
echo "  Run it on each host/WSL environment you want visible in the dashboard."
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
  echo "  comms_register(agentId=\"my-agent\", role=\"coder\", runtime=\"codex\", sessionHandle=\"\$CODEX_THREAD_ID\", appServerUrl=\"\$AIFY_CODEX_APP_SERVER_URL\")"
  echo "  # If those live env vars are unavailable, fall back to: comms_register(agentId=\"my-agent\", role=\"coder\", runtime=\"codex\")"
elif [ "$CLIENT" = "claude" ]; then
  echo "  comms_register(agentId=\"my-agent\", role=\"coder\", runtime=\"claude-code\")"
elif [ "$CLIENT" = "pi" ]; then
  echo "  comms_register(agentId=\"my-agent\", role=\"coder\", runtime=\"pi\", sessionHandle=\"\$PI_SESSION_ID\")"
  echo "  # If PI_SESSION_ID is unavailable, omit sessionHandle; resident Pi will be visible but not resumable until bound."
elif [ "$CLIENT" = "hermes" ]; then
  echo "  comms_register(agentId=\"my-agent\", role=\"coder\", runtime=\"hermes\", sessionHandle=\"\$HERMES_SESSION_ID\")"
  echo "  # If HERMES_SESSION_ID is unavailable, omit sessionHandle; resident Hermes will be visible but not resumable until bound."
else
  echo "  comms_register(agentId=\"my-agent\", role=\"coder\")"
fi
echo "  comms_agents()"
echo "  comms_send(from=\"my-agent\", to=\"other-agent\", type=\"info\", subject=\"Hello\", body=\"Hi there\")"
echo "  comms_inbox(agentId=\"my-agent\", mode=\"headers\")"
echo "  comms_inbox(agentId=\"my-agent\", messageId=\"<message id>\")"
