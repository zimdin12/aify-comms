#!/bin/sh
# The sandbox env's whole registry: one service, the sandbox comms, keyed from the environment. With SANDBOX_DEFINE
# set, one managed agent is defined first, so the managed key publishes a row for it and the retirement has a row
# to delete. Nothing here can name the live service: the only endpoint is the sandbox network's alias.
set -e
cat > "$HOME/.aify/services.json" <<'JSON'
{"version":1,"services":{"aify-comms":{"endpoint":"http://comms:8800","agentState":{"path":"/api/v1/agent-state"},"keyEnv":["SANDBOX_COMMS_KEY"]}}}
JSON
if [ -n "$SANDBOX_DEFINE" ]; then
  mkdir -p /opt/aify-env/work  # under the folder aify-env starts in, so it may start the agent
  node /opt/aify-env/bin/aify-env.mjs agents set "$SANDBOX_DEFINE" name="$SANDBOX_DEFINE" role=coder harness=claude \
    mode=managed workspace=/opt/aify-env/work
fi
exec node /opt/aify-env/bin/aify-env.mjs
