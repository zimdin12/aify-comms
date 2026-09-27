#!/usr/bin/env bash
# Adds a launcher directory to the Windows user PATH, which every terminal opened afterwards inherits.
#
#   add-to-user-path.sh <dir>
#
# ONLY THIS PROFILE'S OWN ~/.local/bin. The user PATH belongs to the machine, not to one install: an
# install.sh run with a temporary HOME (a test harness, a reviewer's sandbox) registered that home's
# .local\bin, and on 2026-09-28 the PATH held 84 dead Temp\tmp*\.local\bin entries plus three sandbox
# hermes bins that shadowed the real hermes.exe. At 9,018 characters, `claude-aify` in a new terminal
# reported "runtime CLI 'claude' was not found on PATH"; the same command with those entries removed
# (3,186 characters) found it. The profile is read with `cygpath -F 40`, the known folder, which a
# faked HOME or USERPROFILE does not move.
set -u

dir="${1:-}"
[ -n "$dir" ] || exit 2
command -v cygpath >/dev/null 2>&1 || exit 0
command -v powershell.exe >/dev/null 2>&1 || exit 0

profile="$(cygpath -F 40 2>/dev/null)"
if [ -z "$profile" ]; then
  echo "[install.sh] this profile's folder could not be read; the Windows user PATH was left alone." >&2
  exit 0
fi
lower() { cygpath -u "$1" | tr '[:upper:]' '[:lower:]' | sed 's:/*$::'; }
if [ "$(lower "$dir")" != "$(lower "$profile/.local/bin")" ]; then
  echo "[install.sh] $dir is not this profile's .local/bin; the Windows user PATH was left alone." >&2
  exit 0
fi

AIFY_SHIM_DIR="$(cygpath -w "$dir")" powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '
  $dir = $env:AIFY_SHIM_DIR; if ([string]::IsNullOrWhiteSpace($dir)) { exit 1 }
  $current = [Environment]::GetEnvironmentVariable("Path", "User")
  $parts = @()
  if ($current) { $parts = $current -split ";" }
  $normalized = $dir.Trim().ToLowerInvariant()
  if (-not ($parts | Where-Object { $_.Trim().ToLowerInvariant() -eq $normalized })) {
    $updated = if ([string]::IsNullOrWhiteSpace($current)) { $dir } else { $current.TrimEnd(";") + ";" + $dir }
    [Environment]::SetEnvironmentVariable("Path", $updated, "User")
  }
' >/dev/null 2>&1 || true
