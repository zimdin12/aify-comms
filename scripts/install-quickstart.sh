#!/usr/bin/env bash
# The quick-start lines install.sh prints for <client> once an install completes. Moved out of install.sh,
# which is held at a measured line ceiling, to pay for the guided-install modes (S2).
CLIENT="${1:-}"
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
