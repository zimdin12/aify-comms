#!/bin/bash
# Optional upstream app discovery, never installation or server startup.
# Default: files only. --probe: bounded `--version` on the discovered executable.
# `missing` is scoped to HERDR_INSTALL_DIR, not a claim about the whole machine.
# No hit in standard locations is UNKNOWN: portable/package-manager installs may live elsewhere.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/report-json.sh"
probe=false
case "${1:-}" in
  --probe) probe=true ;;
  "") ;;
  *) echo "usage: herdr-state.sh [--probe]" >&2; exit 2 ;;
esac

installed=unknown runnable=unknown version=unknown on_path=false scope=known-locations
candidate="$(command -v herdr 2>/dev/null || true)"
[ -z "$candidate" ] || on_path=true
if [ -n "${HERDR_INSTALL_DIR:-}" ]; then
  scope=configured-directory
  installed=missing
  directories=("$HERDR_INSTALL_DIR")
else
  # Official v0.9.0 distribution/install.{sh,ps1}; custom/package-manager installs use PATH.
  directories=("$HOME/.local/bin" "${HERDR_HOME:-$HOME/.herdr}/packages/standalone/current")
  [ -z "${LOCALAPPDATA:-}" ] || directories+=("${LOCALAPPDATA//\\//}/Programs/Herdr/bin")
fi
if [ -z "$candidate" ]; then
  for directory in "${directories[@]}"; do
    directory="${directory//\\//}"
    for name in herdr herdr.exe; do
      if [ -f "$directory/$name" ]; then candidate="$directory/$name"; break 2; fi
    done
  done
fi
if [ -n "$candidate" ]; then
  installed=installed
  # Source-audited upstream main.rs exits for --version before server/TUI startup.
  # Executability bits alone do not prove runnable on Windows or with missing shared libraries.
  if [ "$probe" = true ] && command -v timeout >/dev/null 2>&1; then
    output="$(timeout 5 "$candidate" --version 2>/dev/null)"
    status=$?
    if [ "$status" -eq 0 ]; then
      runnable=yes
      output="${output//$'\r'/}"
      if [[ "$output" =~ ^herdr\ ([0-9]+\.[0-9]+\.[0-9]+[^[:space:]]*)$ ]]; then
        version="${BASH_REMATCH[1]}"
      fi
    elif [ "$status" -ne 124 ] && [ "$status" -ne 137 ]; then
      runnable=no
    fi
  fi
fi
printf '{"installed":"%s","runnable":"%s","version":' "$installed" "$runnable"
json_string "$version"
printf ',"onPath":%s,"path":' "$on_path"
json_string "$candidate"
printf ',"scope":"%s"}\n' "$scope"
