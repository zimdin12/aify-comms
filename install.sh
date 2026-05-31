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
#   bash install.sh --client codex http://192.168.100.10:8800 --with-hook
#   bash install.sh --client hermes http://192.168.100.10:8800 --with-hook

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
  bash install.sh --client <claude|codex|hermes> [SERVER_URL] [--with-hook]

Examples:
  bash install.sh --client claude
  bash install.sh --client claude http://192.168.100.10:8800 --with-hook
  bash install.sh --client codex http://192.168.100.10:8800
  bash install.sh --client hermes http://192.168.100.10:8800 --with-hook

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

require_hermes_cmd() {
  if ! hermes_cmd >/dev/null 2>&1; then
    echo "Missing required command: hermes"
    echo "Set AIFY_HERMES_COMMAND to the Hermes 'hermes' executable path if Hermes is not on PATH."
    echo "Note: hermes-agent / hermes-acp are NOT acceptable substitutes — they do not implement 'dashboard --tui'."
    echo "If hermes' 2026-05-27 release rotated your binary, reinstall hermes upstream so 'hermes' is recreated."
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
  node "$SCRIPT_DIR/mcp/stdio/reap-managed-claude.js" "\$CLAUDE_RESUME_ID" "\$CLAUDE_AIFY_AGENT_ID" >/dev/null 2>&1 || true
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
  # subcommand argument (`codex resume --include-non-interactive <id>`),
  # not as a flag on the top-level `codex --remote` invocation.
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
  local wrapper_dir="$HOME/.local/bin"
  local wrapper_path="$wrapper_dir/hermes-aify"
  local default_server="${SERVER_URL:-$DEFAULT_AIFY_SERVER_URL}"
  local hermes_plugin_path="$SCRIPT_DIR/integrations/hermes-aify-plugin"
  if hermes_runtime_is_native_windows; then
    hermes_plugin_path="$(path_for_windows_runtime "$hermes_plugin_path")"
  fi
  # Path to the host-side MCP stdio bridges (hermes-daemon-cli.js +
  # hermes-channel.js). path_for_node so Git-Bash node opens it (drive-letter).
  local hermes_stdio_dir
  hermes_stdio_dir="$(path_for_node "$SCRIPT_DIR/mcp/stdio")"
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
  cat > "$wrapper_path" <<EOF
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
# command approval prompts"). Default off; opt in with --auto/-auto/--yolo.
HERMES_AUTO=false
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
if [ -n "\$HERMES_AIFY_AGENT_ID" ]; then
  export AIFY_AGENT_ID="\$HERMES_AIFY_AGENT_ID"
  export AIFY_AGENT_ROLE="\$HERMES_AIFY_ROLE"
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
AIFY_HERMES_CHANNEL_JS="\$AIFY_HERMES_STDIO_DIR/hermes-channel.js"
# Managed visible-TUI model (Plan "managed-hermes visible-TUI", 2026-05-31): the
# per-agent hidden gateway host + background delivery loop live here. The managed
# branch brings up the gateway host, starts the delivery loop, and then EXECs a
# real \`hermes --tui\` into THIS PTY (rendered windowless in the dashboard).
AIFY_HERMES_MANAGED_HOST_JS="\$AIFY_HERMES_STDIO_DIR/hermes-managed-host.js"
# Prebuilt ui-tui bundle dir (baked at install time). When non-empty and it
# holds dist/entry.js, the managed branch exports it as HERMES_TUI_DIR so
# \`hermes --tui\` runs the prebuilt bundle and skips the per-launch
# \`npm run build\`. Empty → hermes builds/locates the TUI as before (no break).
AIFY_HERMES_TUI_DIR="$hermes_tui_dir"

# Bring up (idempotently) the per-agent api_server daemon for this agent. On
# failure print the LOUD daemon error and exit non-zero — a silent no-op daemon
# is exactly the failure mode this design eliminates. Echoes the daemon-cli's
# one-line JSON endpoint to stderr for operator visibility.
aify_hermes_ensure_daemon() {
  local agent_id="\$1"
  local out=""
  if ! out="\$(node "\$AIFY_HERMES_DAEMON_CLI" "\$agent_id")"; then
    echo "[hermes-aify] FATAL: per-agent api_server daemon for '\$agent_id' did not come up." >&2
    echo "[hermes-aify]   (node \$AIFY_HERMES_DAEMON_CLI \$agent_id exited non-zero — see the error above)" >&2
    exit 1
  fi
  AIFY_HERMES_DAEMON_ENDPOINT="\$out"
  echo "[hermes-aify] api_server daemon ready: \$out" >&2
}

# Kill any prior sidecar/daemon-cli for THIS agent before launching, so a
# relaunch never leaves two sidecars claiming the same agent (proliferation
# guard, mirrors the intent of the claude/codex wrappers' per-agent ownership).
aify_hermes_kill_prior() {
  local agent_id="\$1"
  [ -n "\$agent_id" ] || return 0
  # Match the channel sidecar (and daemon-cli) invocation carrying this agentId.
  # pkill -f matches the full command line; the AIFY_AGENT_ID marker is the most
  # specific token. Best-effort: no pkill / nothing matched is fine.
  if command -v pkill >/dev/null 2>&1; then
    pkill -f "hermes-channel.js.*\$agent_id" >/dev/null 2>&1 || true
    pkill -f "AIFY_AGENT_ID=\$agent_id.*hermes-channel.js" >/dev/null 2>&1 || true
    # Managed visible-TUI model: reap a prior background delivery loop
    # (\`hermes-managed-host.js run <agent>\`) for this agent. Its SIGTERM
    # teardown then kills the hidden gateway host it owns.
    pkill -f "hermes-managed-host.js run \$agent_id" >/dev/null 2>&1 || true
    # Best-effort: reap any orphaned gateway host left listening on this agent's
    # dashboard/api port (a prior SIGKILL bypasses the loop's teardown handler).
    if command -v lsof >/dev/null 2>&1; then
      local host_port
      host_port="\$(node -e 'import("'"\$AIFY_HERMES_STDIO_DIR"'/hermes-endpoint.js").then(m=>process.stdout.write(String(m.agentPort(process.argv[1]))))' "\$agent_id" 2>/dev/null || true)"
      if [ -n "\$host_port" ]; then
        lsof -ti tcp:"\$host_port" 2>/dev/null | xargs -r kill >/dev/null 2>&1 || true
      fi
    fi
  fi
  # Also reap the prior per-agent DAEMON for this agentId. A prior hard-kill
  # (SIGKILL — untrappable, so the sidecar's teardown handler never ran) can
  # leave an orphan \`hermes gateway run\` bound to the agent's api_server port.
  # stopDaemon resolves that port and kills the listener (best-effort, exits 0).
  node "\$AIFY_HERMES_DAEMON_CLI" stop "\$agent_id" >/dev/null 2>&1 || true
}

# MANAGED launch (visible-TUI model, Plan 2026-05-31): \`--aify-agent\` present
# AND session-mode resolved to managed (bridge-spawned in the dashboard PTY).
#   1. kill-prior: reap a stale delivery loop + gateway host for this agent.
#   2. ensure-host: bring up the HIDDEN per-agent \`hermes dashboard --tui\`
#      gateway host (windowsHide) and learn its {port,token,wsUrl}.
#   3. start the background delivery loop (detached, survives the exec below): it
#      claims dispatch runs and prompt.submits them into the TUI's session.
#   4. exec \`hermes --tui\` IN THIS PTY, attached to the gateway host and
#      resuming the STABLE session \`aify-<agentId>\` — the REAL TUI renders
#      windowless in the dashboard console. The in-session agent self-replies via
#      comms_send (wake-only; symmetric with claude).
if [ -n "\$HERMES_AIFY_AGENT_ID" ] && [ "\$HERMES_AIFY_SESSION_MODE" = "managed" ] && [ \${#HERMES_ARGS[@]} -eq 0 ]; then
  aify_hermes_kill_prior "\$HERMES_AIFY_AGENT_ID"
  export AIFY_AGENT_ID="\$HERMES_AIFY_AGENT_ID"
  export AIFY_CHANNELS_ENABLED=1
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
  # The STABLE resume key MUST match the delivery loop's pickSessionForKey key.
  # ensure-host emits the canonical pinnedSessionId as \`resumeKey\` so we DON'T
  # reimplement (and risk diverging from) the sanitization in shell.
  AIFY_HERMES_PINNED_SESSION="\$(printf '%s' "\$HERMES_HOST_JSON" | sed -E 's/.*"resumeKey"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
  if [ -z "\$AIFY_HERMES_PINNED_SESSION" ] || [ "\$AIFY_HERMES_PINNED_SESSION" = "\$HERMES_HOST_JSON" ]; then
    # Fallback to the local sanitization (matches hermes-session-id.js for the
    # common [a-zA-Z0-9_-] agentId case) if the field is somehow absent.
    AIFY_HERMES_PINNED_SESSION="aify-\$(printf '%s' "\$HERMES_AIFY_AGENT_ID" | tr -c 'a-zA-Z0-9_-' '-' | sed -E 's/^-+|-+\$//g')"
  fi
  # (3) Background delivery loop — detached, survives the exec below.
  nohup node "\$AIFY_HERMES_MANAGED_HOST_JS" run "\$HERMES_AIFY_AGENT_ID" >/dev/null 2>&1 &
  disown 2>/dev/null || true
  # (4) The VISIBLE TUI in this PTY, attached to the gateway host + stable session.
  export HERMES_TUI_GATEWAY_URL="\$HERMES_TUI_WS_URL"
  export HERMES_TUI_RESUME="\$AIFY_HERMES_PINNED_SESSION"
  # Use the prebuilt ui-tui bundle when available so the managed TUI does NOT
  # run \`npm run build\` on every launch (slow + noisy in the dashboard
  # console). \`hermes --tui\` skips the build when HERMES_TUI_DIR points at a
  # dir with dist/entry.js. Guard at runtime too in case the dist was removed
  # after install — never break the TUI launch.
  if [ -n "\$AIFY_HERMES_TUI_DIR" ] && [ -f "\$AIFY_HERMES_TUI_DIR/dist/entry.js" ]; then
    export HERMES_TUI_DIR="\$AIFY_HERMES_TUI_DIR"
  fi
  # Resume the STABLE session (\`aify-<agentId>\`) so a relaunch reuses the SAME
  # transcript instead of forging a new session every time (no duplication).
  # \`hermes --tui\` STRIPS HERMES_TUI_RESUME unless it is passed as
  # \`--resume <id>\` (main.py: env.pop("HERMES_TUI_RESUME") then re-add only when
  # argparse resolved a resume id), so the env var alone is a no-op — the flag is
  # required. ensure-host has already pre-seeded the row so resume resolves on
  # first launch. \`--resume\` MUST precede the operator's passthrough flags.
  exec "\$HERMES_RUNTIME_COMMAND" --tui --resume "\$AIFY_HERMES_PINNED_SESSION" "\${HERMES_PERMISSION_FLAGS[@]}"
fi

# RESIDENT/interactive launch with an agent id: attach an operator TUI to THIS
# agent's pinned session. \`--resume <pinned session>\` resumes the SAME stable
# DB session (\`aify-<agentId>\`) the managed model drives, so the operator sees
# one continuous transcript.
# TODO(managed-hermes visible-TUI, Phase 1 follow-up): migrate this resident path
# off the api_server \`hermes gateway run\` daemon onto the same hidden
# \`hermes dashboard --tui\` gateway-host model the managed branch now uses (so it
# attaches via HERMES_TUI_GATEWAY_URL too). For now it keeps using
# aify_hermes_ensure_daemon so resident launch is NOT broken by this change.
if [ -n "\$HERMES_AIFY_AGENT_ID" ] && [ \${#HERMES_ARGS[@]} -eq 0 ]; then
  aify_hermes_ensure_daemon "\$HERMES_AIFY_AGENT_ID"
  AIFY_HERMES_PINNED_SESSION="aify-\$(printf '%s' "\$HERMES_AIFY_AGENT_ID" | tr -c 'a-zA-Z0-9_-' '-' | sed -E 's/^-+|-+\$//g')"
  exec "\$HERMES_RUNTIME_COMMAND" --tui "\${HERMES_PERMISSION_FLAGS[@]}" --resume "\$AIFY_HERMES_PINNED_SESSION"
fi

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
  sed -i.bak "s|__AIFY_INSTALL_TIME_URL__|${SERVER_URL:-http://127.0.0.1:8800}|" "$wrapper_path" 2>/dev/null && rm -f "$wrapper_path.bak" || true
  chmod +x "$wrapper_path"
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
  hermes_stdio_dir_win="$(path_for_windows_runtime "$SCRIPT_DIR/mcp/stdio")"
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

if (\$HermesAifyAgentId) {
  \$env:AIFY_AGENT_ID = \$HermesAifyAgentId
  \$env:AIFY_AGENT_ROLE = \$HermesAifyRole
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
\$AifyHermesChannelJs = Join-Path \$AifyHermesStdioDir 'hermes-channel.js'
# Managed visible-TUI model (Plan 2026-05-31): the per-agent hidden gateway host
# (ensure-host) + background delivery loop (run) live here.
\$AifyHermesManagedHostJs = Join-Path \$AifyHermesStdioDir 'hermes-managed-host.js'
# Prebuilt ui-tui bundle dir (baked at install time). When set + dist/entry.js
# exists, the managed branch exports HERMES_TUI_DIR so 'hermes --tui' runs the
# prebuilt bundle and skips the per-launch 'npm run build'. Empty → hermes
# builds/locates the TUI as before (no break).
\$AifyHermesTuiDir = '$hermes_tui_dir_win'

# Bring up (idempotently) the per-agent api_server daemon. On failure print the
# LOUD daemon error and exit non-zero. Returns the daemon-cli's one-line JSON.
function Invoke-AifyHermesEnsureDaemon {
  param([string]\$AgentId)
  \$out = & node \$AifyHermesDaemonCli \$AgentId
  if (\$LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine("[hermes-aify] FATAL: per-agent api_server daemon for '\$AgentId' did not come up.")
    [Console]::Error.WriteLine("[hermes-aify]   (node \$AifyHermesDaemonCli \$AgentId exited \$LASTEXITCODE -- see the error above)")
    exit 1
  }
  [Console]::Error.WriteLine("[hermes-aify] api_server daemon ready: \$out")
  return \$out
}

# Kill any prior sidecar for THIS agent before launching (proliferation guard).
function Invoke-AifyHermesKillPrior {
  param([string]\$AgentId)
  if (-not \$AgentId) { return }
  try {
    Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" -ErrorAction SilentlyContinue |
      Where-Object { \$_.CommandLine -and \$_.CommandLine -match 'hermes-channel\\.js' -and \$_.CommandLine -match [regex]::Escape(\$AgentId) } |
      ForEach-Object { try { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }
  } catch {}
  # Managed visible-TUI model: reap a prior background delivery loop
  # ('hermes-managed-host.js run <agent>') for this agent. Its SIGTERM teardown
  # then kills the hidden gateway host it owns. Match the managed-host script +
  # the agent id on the command line.
  try {
    Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" -ErrorAction SilentlyContinue |
      Where-Object { \$_.CommandLine -and \$_.CommandLine -match 'hermes-managed-host\\.js' -and \$_.CommandLine -match [regex]::Escape(\$AgentId) } |
      ForEach-Object { try { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }
  } catch {}
  # Best-effort: reap any orphaned gateway host left listening on this agent's
  # dashboard/api port (a prior force-kill bypasses the loop's teardown handler).
  try {
    \$hostPort = & node -e 'import(process.argv[1]).then(m=>process.stdout.write(String(m.agentPort(process.argv[2]))))' (Join-Path \$AifyHermesStdioDir 'hermes-endpoint.js') \$AgentId 2>\$null
    if (\$hostPort) {
      Get-NetTCPConnection -State Listen -LocalPort ([int]\$hostPort) -ErrorAction SilentlyContinue |
        ForEach-Object { try { Stop-Process -Id \$_.OwningProcess -Force -ErrorAction SilentlyContinue } catch {} }
    }
  } catch {}
  # Also reap the prior per-agent DAEMON for this agentId. A prior hard-kill
  # (the sidecar's SIGTERM/SIGINT teardown can be bypassed by a force-kill) can
  # leave an orphan 'hermes gateway run' bound to the agent's api_server port.
  # stopDaemon resolves that port and kills the listener (best-effort, exits 0).
  try { & node \$AifyHermesDaemonCli stop \$AgentId 2>\$null | Out-Null } catch {}
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
#      STABLE session 'aify-<agentId>' — the REAL TUI renders windowless in the
#      dashboard console. The in-session agent self-replies via comms_send.
if (\$HermesAifyAgentId -and \$HermesAifySessionMode -eq 'managed' -and \$HermesArgs.Count -eq 0) {
  Invoke-AifyHermesKillPrior \$HermesAifyAgentId
  \$env:AIFY_AGENT_ID = \$HermesAifyAgentId
  \$env:AIFY_CHANNELS_ENABLED = '1'
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
  # The STABLE resume key MUST match the delivery loop's pickSessionForKey key.
  # ensure-host emits the canonical pinnedSessionId as 'resumeKey' so we DON'T
  # reimplement (and risk diverging from) the sanitization in PowerShell.
  if (\$hermesHost.resumeKey) {
    \$pinnedSession = \$hermesHost.resumeKey
  } else {
    \$pinnedSession = 'aify-' + ((\$HermesAifyAgentId -replace '[^a-zA-Z0-9_-]+', '-') -replace '^-+|-+\$', '')
  }
  # (3) Background delivery loop — hidden window, survives this script.
  Start-Process -WindowStyle Hidden -FilePath node \`
    -ArgumentList @(\$AifyHermesManagedHostJs, 'run', \$HermesAifyAgentId) | Out-Null
  # (4) The VISIBLE TUI in this PTY, attached to the gateway host + stable session.
  \$env:HERMES_TUI_GATEWAY_URL = \$hermesHost.wsUrl
  \$env:HERMES_TUI_RESUME = \$pinnedSession
  # Use the prebuilt ui-tui bundle when present so the managed TUI does NOT run
  # 'npm run build' on every launch. Guard at runtime in case the dist was
  # removed after install — never break the TUI launch.
  if (\$AifyHermesTuiDir -and (Test-Path (Join-Path \$AifyHermesTuiDir 'dist/entry.js'))) {
    \$env:HERMES_TUI_DIR = \$AifyHermesTuiDir
  }
  # Resume the STABLE session ('aify-<agentId>') so a relaunch reuses the SAME
  # transcript instead of forging a new session each time (no duplication).
  # 'hermes --tui' STRIPS HERMES_TUI_RESUME unless passed as '--resume <id>'
  # (main.py env.pop then re-add only when argparse resolved a resume id), so the
  # env var alone is a no-op — the flag is required. ensure-host has already
  # pre-seeded the row so resume resolves on first launch.
  Invoke-HermesRuntime @('--tui', '--resume', \$pinnedSession)
  exit \$script:HermesRuntimeExitCode
}

# RESIDENT/interactive launch with an agent id: attach an operator TUI to THIS
# agent's pinned session ('aify-<agentId>') — the SAME stable DB session the
# managed model drives, so the operator sees one continuous transcript.
# TODO(managed-hermes visible-TUI, Phase 1 follow-up): migrate this resident path
# off the api_server 'hermes gateway run' daemon onto the same hidden
# 'hermes dashboard --tui' gateway-host model the managed branch now uses. For
# now it keeps using Invoke-AifyHermesEnsureDaemon so resident launch is NOT
# broken by this change.
if (\$HermesAifyAgentId -and \$HermesArgs.Count -eq 0) {
  Invoke-AifyHermesEnsureDaemon \$HermesAifyAgentId | Out-Null
  \$pinnedSession = 'aify-' + ((\$HermesAifyAgentId -replace '[^a-zA-Z0-9_-]+', '-') -replace '^-+|-+\$', '')
  Invoke-HermesRuntime @('--tui', '--resume', \$pinnedSession)
  exit \$script:HermesRuntimeExitCode
}

# Remaining paths: no --aify-agent (plain interactive TUI) or explicit
# passthrough args (e.g. 'hermes-aify model list'). Go straight to the runtime
# with no gateway-host wiring.
if (\$HermesArgs.Count -eq 0) {
  if (\$HermesExplicitSessionHandle -and \$HermesSessionHandle) {
    Invoke-HermesRuntime @('--tui', '--resume', \$HermesSessionHandle)
    exit \$script:HermesRuntimeExitCode
  }
  Invoke-HermesRuntime @('--tui')
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
  resolved="$(command -v "$hermes_bin" 2>/dev/null || printf '%s\n' "$hermes_bin")"
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
  # Only convert to a Windows drive path when hermes actually runs as a native
  # Windows binary. On WSL with a Linux hermes, path_for_windows_runtime would
  # emit "D:\..." which Linux node can't open — the aify-comms MCP child then
  # exits instantly ("Connection closed"), so no in-hermes bridge claims
  # channel dispatches and managed hermes never answers. Mirror of the plugin
  # path guard in install_hermes_wrapper.
  node_server_path="$SCRIPT_DIR/mcp/stdio/server.js"
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
  local hook_command_path=""
  local hook_command=""
  mkdir -p "$hook_dir"
  touch "$config_file"
  node_notify_script="$(path_for_node "$SCRIPT_DIR/mcp/stdio/notify-check.js")"

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
  # hook exists for shell hooks; the existing 120s server-side
  # turn_busy stale window (or the per-process exit signal for
  # managed hermes dispatches) handles cleanup.
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
elif [ "$CLIENT" = "hermes" ]; then
  require_hermes_cmd
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
  # Plan 1.4 (2026-05-30): the dead `patch_hermes_gateway_visible_bind` source
  # patch (and its TUI active-session-file companion) is REMOVED. Managed/
  # resident hermes delivery now flows through the per-agent api_server daemon
  # + the hermes-channel.js sidecar (no WS visible-session bind), so the old
  # tui_gateway/server.py patch is dead. The Codex stream NoneType SDK-bug
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
  echo "Hermes managed delivery: per-agent api_server daemon is ensured at launch"
  echo "  by hermes-aify (node mcp/stdio/hermes-daemon-cli.js <agentId>); on failure"
  echo "  the wrapper prints a FATAL error and exits non-zero (no silent no-op)."
  if command -v node >/dev/null 2>&1; then
    if node --check "$SCRIPT_DIR/mcp/stdio/hermes-daemon-cli.js" >/dev/null 2>&1 \
      && node --check "$SCRIPT_DIR/mcp/stdio/hermes-channel.js" >/dev/null 2>&1; then
      echo "  Bridges verified: hermes-daemon-cli.js + hermes-channel.js parse OK."
    else
      echo "  ERROR: hermes-daemon-cli.js / hermes-channel.js failed node --check — fix before launch." >&2
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
