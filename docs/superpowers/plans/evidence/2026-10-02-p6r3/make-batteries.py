"""Writes the mutation batteries for the second P6r repair (review of 6047aa1e / ab316a4, "P6r2").

One mutant per repaired obligation, named by the finding it re-opens. The service half was approved and
is unchanged, so it has no battery here. Run each with
docs/superpowers/plans/evidence/2026-10-01-p2/mutate.py <repo> <battery>.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent

WRAPPER_TESTS = ("node --test tests/codex-takes-model-and-effort-on-its-app-server.test.js "
                 "tests/hermes-takes-model-and-effort.test.js tests/agent-definition-defaults.test.js")
wrapper = [
    # L2, the first round's two, kept.
    {"name": "L2 a control character in the selected value is not looked for",
     "file": "wrappers/codex-aify.sh.in",
     "old": "  case \"$CODEX_AIFY_MODEL$CODEX_AIFY_EFFORT\" in *[[:cntrl:]]*) _codex_control=true ;; esac\n", "new": ""},
    {"name": "L2 a control character is reported and launched anyway",
     "file": "wrappers/codex-aify.sh.in", "old": "      exit 78 ;;\n  esac\n}", "new": "      ;;\n  esac\n}"},
    # L2, this round: a definition's NUL or CR, which never reaches the shell.
    {"name": "L2 the reader names no field",
     "file": "lib/agent-definition-defaults.mjs",
     "old": ".filter((field) => CONTROL.test(result[field]))", "new": ".filter(() => false)"},
    {"name": "L2 codex ignores what the reader names",
     "file": "wrappers/codex-aify.sh.in",
     "old": "    case \" $AIFY_DEF_CONTROL \" in *\" $_codex_field \"*) _codex_control=true ;; esac\n", "new": "    :\n"},
    {"name": "L2 codex refuses the definition's value even when an override replaced it",
     "file": "wrappers/codex-aify.sh.in",
     "old": "  if [ \"$CODEX_HAS_MODEL\" = false ] && [ -z \"${AIFY_MANAGED_MODEL:-}\" ]; then _codex_def_picked=\"model\"; fi",
     "new": "  _codex_def_picked=\"model\""},
    # L1, the first round's, on this round's code.
    {"name": "L1 a plain chat gets nothing",
     "file": "wrappers/hermes-aify.sh.in",
     "old": "if [ \"${HERMES_ARGS[$HERMES_COMMAND_AT]:-}\" = \"chat\" ]; then", "new": "if false; then"},
    {"name": "L1 a plain chat gets the model and not the effort",
     "file": "wrappers/hermes-aify.sh.in",
     "old": "HERMES_CHAT_DEFAULTS+=(--reasoning \"$HERMES_AIFY_EFFORT\")", "new": ":"},
    {"name": "L1 every passthrough command gets them, not only chat",
     "file": "wrappers/hermes-aify.sh.in",
     "old": "if [ \"${HERMES_ARGS[$HERMES_COMMAND_AT]:-}\" = \"chat\" ]; then",
     "new": "if [ ${#HERMES_ARGS[@]} -gt 0 ]; then"},
    # L1, this round: where the subcommand is.
    {"name": "L1 the subcommand is taken to be the first argument",
     "file": "wrappers/hermes-aify.sh.in",
     "old": "HERMES_COMMAND_AT=\"$(hermes_command_index)\"", "new": "HERMES_COMMAND_AT=0"},
    {"name": "L1 a top-level flag's value is taken for the subcommand",
     "file": "wrappers/hermes-aify.sh.in",
     "old": "      -c|--continue|-p|--profile) i=$((i + 2)) ;;", "new": "      -c|--continue|-p|--profile) i=$((i + 1)) ;;"},
    {"name": "L1 the agent's flags go before the subcommand",
     "file": "wrappers/hermes-aify.sh.in",
     "old": "  HERMES_ARGS=(\"${HERMES_ARGS[@]:0:$((HERMES_COMMAND_AT + 1))}\" \"${HERMES_CHAT_DEFAULTS[@]}\" \\\n    \"${HERMES_ARGS[@]:$((HERMES_COMMAND_AT + 1))}\")",
     "new": "  HERMES_ARGS=(\"${HERMES_ARGS[@]:0:$HERMES_COMMAND_AT}\" \"${HERMES_CHAT_DEFAULTS[@]}\" \\\n    \"${HERMES_ARGS[@]:$HERMES_COMMAND_AT}\")"},
    {"name": "L1 an attached -mVALUE is not the operator's model",
     "file": "wrappers/hermes-aify.sh.in",
     "old": "    -m|-m?*|--model|--model=*) HERMES_HAS_MODEL=true ;;", "new": "    -m|--model|--model=*) HERMES_HAS_MODEL=true ;;"},
]
for m in wrapper:
    m["test"] = WRAPPER_TESTS

BRIDGE_TESTS = "node tests/hermes-session-effort.test.js && node --test tests/hermes-managed-host.test.js"
bridge = [
    {"name": "H3 the marker is ignored: always the newest",
     "file": "hermes-session-effort.mjs",
     "old": "  return pickSessionById(activeList, marked) || pickMostRecentSession(activeList);",
     "new": "  return pickMostRecentSession(activeList);"},
    {"name": "H3 a rule of its own: a marker that names no live id sets nothing",
     "file": "hermes-session-effort.mjs",
     "old": "  return pickSessionById(activeList, marked) || pickMostRecentSession(activeList);",
     "new": "  return pickSessionById(activeList, marked) || null;"},
    {"name": "H4 teardown does not stop the effort on entry",
     "file": "hermes-delivery-loop.mjs",
     "old": "  const teardown = () => {\n    stopEffort();\n", "new": "  const teardown = () => {\n"},
    {"name": "H4 giving up does not stop the effort on entry",
     "file": "hermes-delivery-loop.mjs",
     "old": "    stopEffort();\n    await reportGatewayDeadOnce(reason);", "new": "    await reportGatewayDeadOnce(reason);"},
    {"name": "H4 the effort starts before the loop's try, outside its finally",
     "file": "hermes-delivery-loop.mjs",
     "old": "  let stopEffort = () => {};",
     "new": "  let stopEffort = startEffort({ agentId: id, effort: sessionEffort, tempDir: markerDir, openWs });"},
    {"name": "H4 the loop's finally does not stop it",
     "file": "hermes-delivery-loop.mjs",
     "old": "    stopLiveness();\n    stopEffort();\n    stopRepulse();", "new": "    stopLiveness();\n    stopRepulse();"},
    {"name": "H2 a pass starts while another is pending",
     "file": "hermes-session-effort.mjs",
     "old": "    if (stopped || passing) return;", "new": "    if (stopped) return;"},
    {"name": "H4 an answer that arrives after stop still sets the session",
     "file": "hermes-session-effort.mjs",
     "old": "      if (stopped || !sessionId || set.has(sessionId)) return;",
     "new": "      if (!sessionId || set.has(sessionId)) return;"},
]
for m in bridge:
    m["test"] = BRIDGE_TESTS

INSTALL_TESTS = ("python -m pytest -q -p no:warnings service/tests/test_install_hermes_session_rediscover.py "
                 "-k \"exit_status or one_bash_launcher or powershell_launcher_is_removed\"")
install = [
    {"name": "H1 the .cmd ends with endlocal's status, not the launcher's",
     "file": "install.sh",
     "old": "    printf '%s\\r\\n' 'endlocal & exit /b %AIFY_EXIT%'\n", "new": "    printf '%s\\r\\n' 'endlocal'\n"},
    {"name": "H1 an earlier install's PowerShell launcher is left in place",
     "file": "install.sh",
     "old": "  rm -f \"$wrapper_dir/hermes-aify.ps1\"\n", "new": ""},
]
for m in install:
    m["test"] = INSTALL_TESTS

for name, battery in (("wrapper", wrapper), ("bridge", bridge), ("install", install)):
    (HERE / f"mutations-{name}.json").write_text(json.dumps(battery, indent=1) + "\n", encoding="utf-8")
    print(name, len(battery))
