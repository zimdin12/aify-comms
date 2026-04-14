#!/bin/bash
# Unified installer for aify-comms on Claude Code, Codex, or OpenCode.
#
# Usage:
#   bash install.sh --client claude
#   bash install.sh --client codex
#   bash install.sh --client codex http://localhost:8800 --with-hook
#   bash install.sh --client opencode http://localhost:8800

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLIENT="claude"
SERVER_URL=""
WITH_HOOK=false

usage() {
  cat <<EOF
Usage:
  bash install.sh --client <claude|codex|opencode> [SERVER_URL] [--with-hook]

Examples:
  bash install.sh --client claude
  bash install.sh --client claude http://localhost:8800 --with-hook
  bash install.sh --client codex http://localhost:8800
  bash install.sh --client opencode http://localhost:8800
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

if [ "$CLIENT" != "claude" ] && [ "$CLIENT" != "codex" ] && [ "$CLIENT" != "opencode" ]; then
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
  local commands_dst="$HOME/.claude/commands/aify-comms"
  mkdir -p "$(dirname "$skill_dst")" "$commands_dst"
  rm -rf "$skill_dst"
  cp -R "$SCRIPT_DIR/.claude/skills/aify-comms" "$skill_dst"
  cp -R "$SCRIPT_DIR/.claude/commands/." "$commands_dst/"
}

install_claude_wrapper() {
  local wrapper_dir="$HOME/.local/bin"
  local wrapper_path="$wrapper_dir/claude-aify"
  mkdir -p "$wrapper_dir"
  cat > "$wrapper_path" <<EOF
#!/bin/bash
set -euo pipefail
MARKER_CWD="\$(pwd)"
node "$SCRIPT_DIR/mcp/stdio/runtime-markers.js" write claude-code "\$MARKER_CWD" "{\"channelEnabled\":true,\"pid\":\$\$}" >/dev/null
cleanup() {
  node "$SCRIPT_DIR/mcp/stdio/runtime-markers.js" remove claude-code "\$MARKER_CWD" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

claude --dangerously-load-development-channels server:aify-comms-channel "\$@"
STATUS=\$?
exit "\$STATUS"
EOF
  chmod +x "$wrapper_path"
}

remove_claude_wrapper() {
  local wrapper_path="$HOME/.local/bin/claude-aify"
  rm -f "$wrapper_path"
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
MARKER_CWD="$(pwd)"

LOG_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/aify-comms"
mkdir -p "$LOG_ROOT"
LOG_FILE="$LOG_ROOT/codex-aify-app-server.log"

codex app-server --listen "$APP_SERVER_URL" >>"$LOG_FILE" 2>&1 &
APP_SERVER_PID=$!
node "__SCRIPT_DIR__/mcp/stdio/runtime-markers.js" write codex "$MARKER_CWD" "{\"appServerUrl\":\"$APP_SERVER_URL\",\"pid\":$$}" >/dev/null

cleanup() {
  node "__SCRIPT_DIR__/mcp/stdio/runtime-markers.js" remove codex "$MARKER_CWD" >/dev/null 2>&1 || true
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

codex --remote "$APP_SERVER_URL" "$@"
STATUS=$?
exit "$STATUS"
EOF
  sed -i.bak "s|__SCRIPT_DIR__|$SCRIPT_DIR|g" "$wrapper_path"
  rm -f "$wrapper_path.bak"
  chmod +x "$wrapper_path"
}

copy_codex_assets() {
  local codex_home="${CODEX_HOME:-$HOME/.codex}"
  local skill_dst="$codex_home/skills/aify-comms"
  mkdir -p "$(dirname "$skill_dst")"
  rm -rf "$skill_dst"
  cp -R "$SCRIPT_DIR/.agents/skills/aify-comms" "$skill_dst"
}

install_opencode_config() {
  local config_root="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
  local config_file="$config_root/opencode.json"
  local api_key="${CLAUDE_MCP_API_KEY:-${AIFY_API_KEY:-}}"
  mkdir -p "$config_root"
  if [ ! -f "$config_file" ]; then
    cat > "$config_file" <<'EOF'
{
  "$schema": "https://opencode.ai/config.json"
}
EOF
  fi

  node -e "
    const fs = require('fs');
    const file = process.argv[1];
    const serverUrl = process.argv[2];
    const apiKey = process.argv[3];
    const serverPath = process.argv[4];
    let data = {};
    try { data = JSON.parse(fs.readFileSync(file, 'utf-8')); } catch (_) {}
    if (!data || typeof data !== 'object') data = {};
    if (!data['\$schema']) data['\$schema'] = 'https://opencode.ai/config.json';
    if (!data.mcp || typeof data.mcp !== 'object' || Array.isArray(data.mcp)) data.mcp = {};
    const environment = {};
    if (serverUrl) environment.CLAUDE_MCP_SERVER_URL = serverUrl;
    if (apiKey) environment.CLAUDE_MCP_API_KEY = apiKey;
    data.mcp['aify-comms'] = {
      type: 'local',
      enabled: true,
      command: ['node', serverPath],
      ...(Object.keys(environment).length ? { environment } : {}),
    };
    fs.writeFileSync(file, JSON.stringify(data, null, 2) + '\n');
  " "$config_file" "$SERVER_URL" "$api_key" "$SCRIPT_DIR/mcp/stdio/server.js"
}

enable_codex_hooks_feature() {
  local codex_home="${CODEX_HOME:-$HOME/.codex}"
  local config_file="$codex_home/config.toml"
  mkdir -p "$codex_home"

  if [ ! -f "$config_file" ]; then
    cat > "$config_file" <<'EOF'
[features]
codex_hooks = true
EOF
    return
  fi

  if grep -Eq '^[[:space:]]*codex_hooks[[:space:]]*=' "$config_file"; then
    awk '
      /^[[:space:]]*codex_hooks[[:space:]]*=/ {
        print "codex_hooks = true"
        next
      }
      { print }
    ' "$config_file" > "$config_file.tmp"
    mv "$config_file.tmp" "$config_file"
    return
  fi

  if grep -Eq '^\[features\][[:space:]]*$' "$config_file"; then
    awk '
      /^\[features\][[:space:]]*$/ && !done {
        print
        print "codex_hooks = true"
        done = 1
        next
      }
      { print }
    ' "$config_file" > "$config_file.tmp"
    mv "$config_file.tmp" "$config_file"
    return
  fi

  printf '\n[features]\ncodex_hooks = true\n' >> "$config_file"
}

install_codex_hook() {
  local codex_home="${CODEX_HOME:-$HOME/.codex}"
  local hooks_file="$codex_home/hooks.json"
  mkdir -p "$codex_home"
  if [ ! -f "$hooks_file" ]; then
    echo '{"hooks":{}}' > "$hooks_file"
  fi

  enable_codex_hooks_feature

  node -e "
    const fs = require('fs');
    const hooksPath = process.argv[1];
    const command = process.argv[2];
    let data = { hooks: {} };
    try {
      data = JSON.parse(fs.readFileSync(hooksPath, 'utf-8'));
    } catch (_) {}
    if (!data || typeof data !== 'object') data = {};
    if (!data.hooks || typeof data.hooks !== 'object') data.hooks = {};
    if (!Array.isArray(data.hooks.PostToolUse)) data.hooks.PostToolUse = [];
    const matcher = 'Bash';
    data.hooks.PostToolUse = data.hooks.PostToolUse.filter(group => {
      if (!group || group.matcher !== matcher || !Array.isArray(group.hooks)) return true;
      return !group.hooks.some(h => h && h.command === command);
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
  " "$hooks_file" "node \"$SCRIPT_DIR/mcp/stdio/notify-check.js\""
}

install_claude_hook() {
  local settings_file="$HOME/.claude/settings.json"
  mkdir -p "$(dirname "$settings_file")"
  if [ ! -f "$settings_file" ]; then
    echo '{}' > "$settings_file"
  fi

  node -e "
    const fs = require('fs');
    const settings = JSON.parse(fs.readFileSync('$settings_file', 'utf-8'));
    if (!settings.hooks) settings.hooks = {};
    if (!settings.hooks.PostToolUse) settings.hooks.PostToolUse = [];
    settings.hooks.PostToolUse = settings.hooks.PostToolUse.filter(
      h => !JSON.stringify(h).includes('notify-check')
    );
    settings.hooks.PostToolUse.push({
      hooks: [{
        type: 'command',
        command: 'node \"$SCRIPT_DIR/mcp/stdio/notify-check.js\"'
      }]
    });
    fs.writeFileSync('$settings_file', JSON.stringify(settings, null, 2));
  "
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
  elif [ "$cli" = "opencode" ]; then
    install_opencode_config
    return
  else
    "$cli" mcp remove "$server_name" >/dev/null 2>&1 || true
  fi

  if [ -n "$SERVER_URL" ] && [ -n "$api_key" ]; then
    "$cli" mcp add "$server_name" \
      "${scope_args[@]}" \
      --env CLAUDE_MCP_SERVER_URL="$SERVER_URL" \
      --env CLAUDE_MCP_API_KEY="$api_key" \
      -- node "$SCRIPT_DIR/mcp/stdio/server.js"
  elif [ -n "$SERVER_URL" ]; then
    "$cli" mcp add "$server_name" \
      "${scope_args[@]}" \
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
      --env CLAUDE_MCP_SERVER_URL="$SERVER_URL" \
      --env CLAUDE_MCP_API_KEY="$api_key" \
      -- node "$SCRIPT_DIR/mcp/stdio/claude-channel.js"
  elif [ -n "$SERVER_URL" ]; then
    "$cli" mcp add --scope user "$server_name" \
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
require_cmd "$CLIENT"

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
echo "  Done."

if [ "$WITH_HOOK" = true ]; then
  echo "[4/4] Installing notification hook..."
  if [ "$CLIENT" = "claude" ]; then
    install_claude_hook
  elif [ "$CLIENT" = "codex" ]; then
    install_codex_hook
  else
    echo "  OpenCode hook install is not implemented yet; skipping."
  fi
  echo "  Done."
else
  echo "[4/4] Notification hook skipped (use --with-hook to enable)."
fi

if [ "$CLIENT" = "claude" ]; then
  if [ -n "$SERVER_URL" ]; then
    install_claude_wrapper
  else
    remove_claude_wrapper
  fi
elif [ "$CLIENT" = "codex" ]; then
  install_codex_wrapper
fi

echo ""
echo "=== Installation complete ==="
if [ "$CLIENT" = "claude" ]; then
  echo "Restart Claude Code for changes to take effect."
  if [ -n "$SERVER_URL" ]; then
    echo "For resident-session wakeups, start Claude with: claude-aify"
    echo "  (wrapper installed at ~/.local/bin/claude-aify)"
  else
    echo "Local-only install: resident Claude wakeups are disabled because no shared server URL was provided."
    echo "No claude-aify wrapper was installed."
  fi
elif [ "$CLIENT" = "codex" ]; then
  echo "Restart Codex for changes to take effect."
  echo "For live resident wakeups, start Codex with: codex-aify"
  echo "  (wrapper installed at ~/.local/bin/codex-aify)"
else
  echo "Restart OpenCode for changes to take effect."
fi
echo ""
echo "Quick start:"
echo "  comms_register(agentId=\"my-agent\", role=\"coder\")"
echo "  comms_agents()"
echo "  comms_send(from=\"my-agent\", to=\"other-agent\", type=\"info\", subject=\"Hello\", body=\"Hi there\")"
echo "  comms_inbox(agentId=\"my-agent\")"
