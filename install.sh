#!/bin/bash
# aify-claude installer for Claude Code
#
# Usage:
#   bash install.sh                          # local server (localhost:8800)
#   bash install.sh http://192.168.1.5:8800  # remote server
#   bash install.sh --with-hook              # local + notification hook
#   bash install.sh http://server:8800 --with-hook  # remote + hook
#
# What it does:
#   1. Installs MCP dependencies (npm install)
#   2. Registers the aify-claude MCP server with Claude Code
#   3. Optionally installs the PostToolUse notification hook

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_URL=""
WITH_HOOK=false

for arg in "$@"; do
  case "$arg" in
    --with-hook) WITH_HOOK=true ;;
    http*) SERVER_URL="$arg" ;;
  esac
done

echo "=== aify-claude installer ==="
echo "Repo: $SCRIPT_DIR"
echo "Server: ${SERVER_URL:-local mode (no server)}"
echo ""

# Step 1: npm install
echo "[1/3] Installing MCP dependencies..."
cd "$SCRIPT_DIR/mcp/stdio"
npm install --silent 2>/dev/null
cd "$SCRIPT_DIR"
echo "  Done."

# Step 2: Register MCP server
echo "[2/3] Registering MCP server with Claude Code..."
claude mcp remove aify-claude 2>/dev/null || true

if [ -n "$SERVER_URL" ]; then
  API_KEY_ENV=""
  if [ -n "$CLAUDE_MCP_API_KEY" ]; then
    API_KEY_ENV="-e CLAUDE_MCP_API_KEY=$CLAUDE_MCP_API_KEY"
  fi
  claude mcp add --scope user aify-claude \
    -e CLAUDE_MCP_SERVER_URL="$SERVER_URL" \
    $API_KEY_ENV \
    -- node "$SCRIPT_DIR/mcp/stdio/server.js"
else
  claude mcp add --scope user aify-claude \
    -- node "$SCRIPT_DIR/mcp/stdio/server.js"
fi
echo "  Done."

# Step 3: Optional notification hook
if [ "$WITH_HOOK" = true ]; then
  echo "[3/3] Installing notification hook..."
  SETTINGS_FILE="$HOME/.claude/settings.json"

  if [ ! -f "$SETTINGS_FILE" ]; then
    echo '{}' > "$SETTINGS_FILE"
  fi

  # Add the notification hook to PostToolUse using node
  node -e "
    const fs = require('fs');
    const settings = JSON.parse(fs.readFileSync('$SETTINGS_FILE', 'utf-8'));
    if (!settings.hooks) settings.hooks = {};
    if (!settings.hooks.PostToolUse) settings.hooks.PostToolUse = [];

    // Remove any existing aify-claude hooks
    settings.hooks.PostToolUse = settings.hooks.PostToolUse.filter(
      h => !JSON.stringify(h).includes('notify-check')
    );

    // Add the notification hook (runs on all tool uses)
    settings.hooks.PostToolUse.push({
      hooks: [{
        type: 'command',
        command: 'node \"$SCRIPT_DIR/mcp/stdio/notify-check.js\"'
      }]
    });

    fs.writeFileSync('$SETTINGS_FILE', JSON.stringify(settings, null, 2));
  "
  echo "  Done. Agents will see inbox notifications after each tool call."
else
  echo "[3/3] Notification hook skipped (use --with-hook to enable)."
fi

echo ""
echo "=== Installation complete ==="
echo "Restart Claude Code for changes to take effect."
echo ""
echo "Quick start:"
echo "  /register my-agent coder"
echo "  /agents"
echo "  /send other-agent Hello!"
echo "  /inbox"
