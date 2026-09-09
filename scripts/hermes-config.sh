#!/bin/bash
# Shared installer command and profile-root resolution. Source only.
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
    local cfg_status=0
    cfg_path="$("$hermes_bin" config path 2>/dev/null)" || cfg_status=$?
    # Preserve the legacy install fallback, but never trust a failed inventory probe.
    if [ "${1:-}" = "--require-resolved" ] && [ "$cfg_status" -ne 0 ]; then return 2; fi
    cfg_path="$(printf '%s\n' "$cfg_path" | tr -d '\r' | tail -n 1)"
    if [ -n "$cfg_path" ]; then
      dirname "$cfg_path"
      return
    fi
  fi
  # Install keeps its historical fallback; inventory must not claim it was resolved.
  [ "${1:-}" != "--require-resolved" ] || return 2
  printf '%s\n' "$HOME/.hermes"
}
