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

if [ "$CODEX_AUTO" = true ]; then
  CODEX_PERMISSION_FLAGS+=(--dangerously-bypass-approvals-and-sandbox)
fi

# Plan 1: try-resume, fall back to fresh codex if the saved session
# file has been GC'd by codex itself (os error 2). The wrapper does not
# abort on a stale handle — the operator gets a fresh codex shell and
# the bridge heartbeat will report the new session id within 60s.
if [ -n "${CODEX_RESUME_HANDLE:-}" ]; then
  if [ -f "$HOME/.codex/sessions/$CODEX_RESUME_HANDLE.jsonl" ]; then
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
if [ "$PI_AIFY_STANDALONE" != true ] && [ -n "$PI_AIFY_AGENT_ID" ] && [ -n "${AIFY_COMMS_URL:-}" ]; then
  AIFY_WATCHDOG_URL="${AIFY_COMMS_URL%/}/api/v1/agents/${PI_AIFY_AGENT_ID}/pi-session-state"
  AIFY_WATCHDOG_HEADERS=()
  if [ -n "${AIFY_API_KEY:-}" ]; then
    AIFY_WATCHDOG_HEADERS+=("-H" "X-API-Key: ${AIFY_API_KEY}")
  fi
  AIFY_WATCHDOG_BODY="$(curl -sS --max-time 2 "${AIFY_WATCHDOG_HEADERS[@]}" "$AIFY_WATCHDOG_URL" 2>/dev/null || true)"
  if [ -n "$AIFY_WATCHDOG_BODY" ] && printf '%s' "$AIFY_WATCHDOG_BODY" | grep -q '"bridgeOwned":[[:space:]]*true'; then
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

  AIFY_HERMES_PORT="\$(pick_port)"
  if [ -z "\$AIFY_HERMES_PORT" ]; then
    echo "hermes-aify: failed to allocate a local port for the dashboard gateway; falling back to plain hermes." >&2
    exec "\$HERMES_RUNTIME_COMMAND" "\${HERMES_ARGS[@]}"
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
    exec "\$HERMES_RUNTIME_COMMAND" "\${HERMES_ARGS[@]}"
  fi

  # web_server.py:3688 injects: <script>window.__HERMES_SESSION_TOKEN__="..."
  AIFY_HERMES_TOKEN="\$(curl -s "\$AIFY_HERMES_DASHBOARD_URL/" | grep -oE '__HERMES_SESSION_TOKEN__="[^"]+"' | head -1 | sed -E 's/.*="([^"]+)"\$/\1/')"
  if [ -z "\$AIFY_HERMES_TOKEN" ]; then
    echo "hermes-aify: could not capture the dashboard session token from \$AIFY_HERMES_DASHBOARD_URL/. Falling back to plain hermes." >&2
    cleanup_aify_dashboard
    exec "\$HERMES_RUNTIME_COMMAND" "\${HERMES_ARGS[@]}"
  fi

  AIFY_HERMES_GATEWAY="ws://127.0.0.1:\$AIFY_HERMES_PORT/api/ws?token=\$AIFY_HERMES_TOKEN"
  export HERMES_TUI_GATEWAY_URL="\$AIFY_HERMES_GATEWAY"
  export AIFY_HERMES_GATEWAY_URL="\$AIFY_HERMES_GATEWAY"
  export AIFY_HERMES_GATEWAY_TOKEN="\$AIFY_HERMES_TOKEN"

  # Default to \`hermes chat --tui\` for the operator's interactive TUI when
  # no explicit subcommand args were passed. If the operator passed args
  # (e.g. \`hermes-aify model list\`), pass them through unchanged.
  if [ \${#HERMES_ARGS[@]} -eq 0 ]; then
    exec "\$HERMES_RUNTIME_COMMAND" chat --tui
  fi
  exec "\$HERMES_RUNTIME_COMMAND" "\${HERMES_ARGS[@]}"
fi

exec "\$HERMES_RUNTIME_COMMAND" "\${HERMES_ARGS[@]}"
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

install_hermes_config() {
  local config_root="$(hermes_config_root)"
  local config_file="$config_root/config.yaml"
  local node_config_file=""
  local node_server_path=""
  mkdir -p "$config_root"
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
    if (/^[ \t]*aify-comms:[ \t]*$/m.test(text) && /^[ \t]*mcp_servers:[ \t]*$/m.test(text)) {
      process.exit(0);
    }
    // Hermes filters env-vars to stdio MCP children: only PATH HOME etc
    // pass through by default (tools/mcp_tool.py _SAFE_ENV_KEYS). The
    // hermes-aify wrapper exports the gateway vars to hermes itself but
    // without explicit propagation here those vars never reach the
    // aify-comms MCP server child. Hermes does support templated env
    // resolution at MCP-spawn time so we use that to inject the
    // current value of each var per launch.
    const entry = [
      "  aify-comms:",
      "    command: \"node\"",
      "    args:",
      `      - ${JSON.stringify(serverPath)}`,
      "    env:",
      `      AIFY_HERMES_GATEWAY_URL: \"\${AIFY_HERMES_GATEWAY_URL}\"`,
      `      AIFY_HERMES_GATEWAY_TOKEN: \"\${AIFY_HERMES_GATEWAY_TOKEN}\"`,
      `      HERMES_TUI_GATEWAY_URL: \"\${HERMES_TUI_GATEWAY_URL}\"`,
      ...(serverUrl ? [`      AIFY_SERVER_URL: ${JSON.stringify(serverUrl)}`, `      CLAUDE_MCP_SERVER_URL: ${JSON.stringify(serverUrl)}`] : []),
    ];
    const lines = text.replace(/\s*$/, "").split(/\r?\n/);
    const mcpIndex = lines.findIndex((line) => /^[ \t]*mcp_servers:[ \t]*$/.test(line));
    if (mcpIndex >= 0) {
      lines.splice(mcpIndex + 1, 0, ...entry);
      fs.writeFileSync(file, lines.join("\n") + "\n");
    } else {
      fs.writeFileSync(file, lines.filter(Boolean).join("\n") + `${lines.some(Boolean) ? "\n\n" : ""}mcp_servers:\n${entry.join("\n")}\n`);
    }
  ' "$node_config_file" "$node_server_path" "$SERVER_URL"
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
