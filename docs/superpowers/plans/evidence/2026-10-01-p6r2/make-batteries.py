"""Writes the three mutation batteries for the P6r repair (review of 3ada3809 / 9455583).

One mutant per repaired obligation, named by the review finding it re-opens. Run each with
docs/superpowers/plans/evidence/2026-10-01-p2/mutate.py <repo> <battery>.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent

SERVICE_TESTS = ("python -m pytest -q -p no:warnings service/tests/test_an_effort_change_reaches_the_start_it_is_for.py "
                 "service/tests/test_harness_defaults.py service/tests/test_runs_with.py "
                 "service/tests/test_an_agents_effort_changes_from_its_next_start.py")
service = [
    {"name": "C1 the effort change leaves the spawn specs as they were",
     "file": "service/routers/agents/attributes.py",
     "old": "        await _respecify_effort(db, agent_id, req.effort)\n", "new": ""},
    {"name": "C1 a spec keeps its old thinking beside the new effort",
     "file": "service/routers/agents/attributes.py",
     "old": "\"runtimeConfig\": with_effort(metadata.get(\"runtimeConfig\"), effort)}",
     "new": "\"runtimeConfig\": {**(metadata.get(\"runtimeConfig\") or {}), \"effort\": effort}}"},
    {"name": "C2 the effort route reads before it holds the write lock",
     "file": "service/routers/agents/attributes.py",
     "old": "        await db.execute(\"BEGIN IMMEDIATE\")\n        row = await (await db.execute(\"SELECT id, session_mode, runtime_config",
     "new": "        row = await (await db.execute(\"SELECT id, session_mode, runtime_config"},
    {"name": "G1 a host's own AIFY_MANAGED_EFFORT reaches a launch whose effort was cleared",
     "file": "service/api_core/launch_env.py",
     "old": "    \"AIFY_MANAGED_EFFORT\",\n    # The Claude Code session", "new": "    # The Claude Code session"},
    {"name": "G1 a host's own AIFY_MANAGED_MODEL reaches a launch",
     "file": "service/api_core/launch_env.py",
     "old": "    \"AIFY_MANAGED_MODEL\",\n", "new": ""},
    {"name": "C3 the shared reader drops the runtimeConfig.model fallback",
     "file": "service/api_core/model_effort.py",
     "old": "    return _text(model) or _text(_config(runtime_config).get(\"model\"))",
     "new": "    return _text(model)"},
    {"name": "C3 runsWith reads effort its own way again (truthiness before trim)",
     "file": "service/api_core/definition_records.py",
     "old": "record_model(agent_row[\"model\"], config), record_effort(config), \"agent\"",
     "new": "record_model(agent_row[\"model\"], config), str(config.get(\"effort\") or config.get(\"thinking\") or \"\").strip(), \"agent\""},
    {"name": "C3 runsWith ignores runtimeConfig.model",
     "file": "service/api_core/definition_records.py",
     "old": "record_model(agent_row[\"model\"], config), record_effort(config), \"agent\"",
     "new": "str(agent_row[\"model\"] or \"\"), record_effort(config), \"agent\""},
    {"name": "C4 runsWith decodes the stored config strictly",
     "file": "service/api_core/definition_records.py",
     "old": "        config = _json_loads_or(agent_row[\"runtime_config\"], {})",
     "new": "        config = __import__(\"json\").loads(agent_row[\"runtime_config\"] or \"{}\")"},
    {"name": "C3 with_effort leaves thinking behind",
     "file": "service/api_core/model_effort.py",
     "old": "if key != \"thinking\"}", "new": "if True}"},
    {"name": "C3 defaults: a blank effort beside thinking counts as not given",
     "file": "service/api_core/harness_defaults.py",
     "old": "    if default_effort and not record_effort(config):",
     "new": "    if default_effort and not str(config.get(\"effort\") or \"\").strip():"},
    {"name": "C3 defaults: a model given in runtimeConfig is outranked by the default",
     "file": "service/api_core/harness_defaults.py",
     "old": "(\"\" if record_model(model, config) else default_model)", "new": "default_model"},
    {"name": "G2 apply-defaults leaves a model of the record's own",
     "file": "service/routers/settings.py",
     "old": "    config.pop(\"model\", None)\n", "new": ""},
    {"name": "G2 apply-defaults leaves the old thinking",
     "file": "service/routers/settings.py",
     "old": "    config = with_effort(runtime_config, effort)\n",
     "new": "    config = {**(runtime_config if isinstance(runtime_config, dict) else {}), \"effort\": effort}\n"},
]
for m in service:
    m["test"] = SERVICE_TESTS

BRIDGE_TESTS = "node tests/hermes-session-effort.test.js && node --test tests/hermes-managed-host.test.js"
bridge = [
    {"name": "H2 a pass starts while another is pending",
     "file": "mcp/stdio/hermes-session-effort.mjs",
     "old": "    if (stopped || passing) return;", "new": "    if (stopped) return;"},
    {"name": "H2 a pass never ends, so a new session is never set",
     "file": "mcp/stdio/hermes-session-effort.mjs",
     "old": "      passing = false;\n", "new": ""},
    {"name": "H4 an answer that arrives after stop still sets the session",
     "file": "mcp/stdio/hermes-session-effort.mjs",
     "old": "      if (stopped || !sessionId || set.has(sessionId)) return;",
     "new": "      if (!sessionId || set.has(sessionId)) return;"},
    {"name": "H3 the bound session is ignored when one is live",
     "file": "mcp/stdio/hermes-session-effort.mjs",
     "old": "  return pickSessionById(activeList, marked) || (ids.length === 1 ? ids[0] : null);",
     "new": "  return ids.length === 1 ? ids[0] : null;"},
    {"name": "H3 an ambiguous list falls back to the newest",
     "file": "mcp/stdio/hermes-session-effort.mjs",
     "old": "  return pickSessionById(activeList, marked) || (ids.length === 1 ? ids[0] : null);",
     "new": "  return pickSessionById(activeList, marked) || ids[ids.length - 1] || null;"},
    {"name": "H4 the delivery loop does not stop the effort loop",
     "file": "mcp/stdio/hermes-delivery-loop.mjs",
     "old": "    stopEffort();\n", "new": ""},
]
for m in bridge:
    m["file"] = m["file"].removeprefix("mcp/stdio/")
    m["test"] = BRIDGE_TESTS

INSTALL_TESTS = ("python -m pytest -q -p no:warnings service/tests/test_install_hermes_session_rediscover.py "
                 "-k \"one_bash_launcher or powershell_launcher_is_removed\"")
install = [
    {"name": "H1 an earlier install's PowerShell launcher is left in place",
     "file": "install.sh",
     "old": "  rm -f \"$wrapper_dir/hermes-aify.ps1\"\n", "new": ""},
    {"name": "H1 hermes gets no .cmd, so nothing runs the one launcher from cmd or PowerShell",
     "file": "install.sh",
     "old": "  install_windows_cmd_shim \"hermes-aify\" \"$wrapper_dir\"\n", "new": ""},
]
for m in install:
    m["test"] = INSTALL_TESTS

WRAPPER_TESTS = ("node --test tests/codex-takes-model-and-effort-on-its-app-server.test.js "
                 "tests/hermes-takes-model-and-effort.test.js")
wrapper = [
    {"name": "L2 a control character is not looked for",
     "file": "wrappers/codex-aify.sh.in", "old": "    *[[:cntrl:]]*)\n", "new": "    *NEVER-MATCHES*)\n"},
    {"name": "L2 a control character is reported and launched anyway",
     "file": "wrappers/codex-aify.sh.in", "old": "      exit 78 ;;\n  esac\n}", "new": "      ;;\n  esac\n}"},
    {"name": "L1 a plain chat gets nothing",
     "file": "wrappers/hermes-aify.sh.in",
     "old": "if [ \"${HERMES_ARGS[0]:-}\" = \"chat\" ]; then", "new": "if false; then"},
    {"name": "L1 a plain chat gets the model and not the effort",
     "file": "wrappers/hermes-aify.sh.in",
     "old": "HERMES_CHAT_DEFAULTS+=(--reasoning \"$HERMES_AIFY_EFFORT\")", "new": ":"},
    {"name": "L1 the agent's arguments go after the operator's",
     "file": "wrappers/hermes-aify.sh.in",
     "old": "  HERMES_ARGS=(chat \"${HERMES_CHAT_DEFAULTS[@]}\" \"${HERMES_ARGS[@]:1}\")",
     "new": "  HERMES_ARGS=(\"${HERMES_ARGS[@]}\" \"${HERMES_CHAT_DEFAULTS[@]}\")"},
    {"name": "L1 every passthrough command gets them, not only chat",
     "file": "wrappers/hermes-aify.sh.in",
     "old": "if [ \"${HERMES_ARGS[0]:-}\" = \"chat\" ]; then", "new": "if [ ${#HERMES_ARGS[@]} -gt 0 ]; then"},
]
for m in wrapper:
    m["test"] = WRAPPER_TESTS

for name, battery in (("service", service), ("bridge", bridge), ("install", install), ("wrapper", wrapper)):
    (HERE / f"mutations-{name}.json").write_text(json.dumps(battery, indent=1) + "\n", encoding="utf-8")
    print(name, len(battery))
