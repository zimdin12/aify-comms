#!/usr/bin/env bash
# Client-specific closing guidance. install.sh reports completion only after its last assets action.
# One argument preserves the quick-start-only form; the installer also supplies server URL and Windows state.
CLIENT="${1:-}"
if [ "${2+x}" = x ]; then
  SERVER_URL="$2"
  WINDOWS="${3:-0}"
  if [ "$CLIENT" = "claude" ]; then
    echo "Restart Claude Code for changes to take effect."
    if [ -n "$SERVER_URL" ]; then
      echo "For resident-session wakeups, start Claude with: claude-aify"
      echo "  (wrapper installed at ~/.local/bin/claude-aify)"
      if [ "$WINDOWS" = 1 ]; then
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
    if [ "$WINDOWS" = 1 ]; then
      echo "  Windows shim installed at %USERPROFILE%\\.local\\bin\\codex-aify.cmd"
    fi
  elif [ "$CLIENT" = "hermes" ]; then
    echo "Restart Hermes Agent for changes to take effect."
    echo "For resident-session wakeups, start Hermes with: hermes-aify"
    echo "  (wrapper installed at ~/.local/bin/hermes-aify)"
    if [ "$WINDOWS" = 1 ]; then
      echo "  Windows shim installed at %USERPROFILE%\\.local\\bin\\hermes-aify.cmd"
    fi
  fi
  echo ""
fi
echo "Quick start:"
if [ "$CLIENT" = "codex" ]; then
  echo "  comms_register(agentId=\"my-agent\", role=\"coder\", runtime=\"codex\", appServerUrl=\"\$AIFY_CODEX_APP_SERVER_URL\")"
  echo "  # Current bridges auto-discover the live Codex thread from the app-server when possible."
  echo "  # Add sessionHandle=\"\$CODEX_THREAD_ID\" only when CODEX_THREAD_ID is non-empty in this same session."
elif [ "$CLIENT" = "claude" ]; then
  echo "  comms_register(agentId=\"my-agent\", role=\"coder\", runtime=\"claude-code\")"
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
