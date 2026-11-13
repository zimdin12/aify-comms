// E1 selects syntax-check targets by the interpreter the installed script declares, not its command name.
export const CHECK_SHELL_LAUNCHERS = `for f in "$HOME"/.local/bin/*; do
  [ -f "$f" ] || continue
  first="$(head -n 1 "$f")" || exit "$?"
  case "$first" in
    '#!/bin/bash'|'#!/usr/bin/env bash')
      bash -n "$f" || exit "$?"
      printf 'syntax-ok %s\\n' "$(basename "$f")"
      ;;
  esac
done`;
