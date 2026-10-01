"""The model and effort an agent's record holds, read the way its managed start reads them (P0 C12).

ONE READER, because three disagreed. The launch (launch_env.py), the projection the agent list carries
(`runsWith`) and the per-harness defaults each read these two values from a record, and until the 0.8
review each read them its own way: an effort of " " beside `thinking: "high"` projected as empty and
launched high, and an effort of 0 projected `thinking` and launched "0". A record's values are what
these functions return, for every reader.

ONE WRITER for effort, for the same reason: `thinking` is the legacy name the launch still reads after
`effort`, so writing an effort while leaving `thinking` behind lets the old value win the moment the new
one is empty.

Pure: values in, values out.
"""

from __future__ import annotations

from typing import Any


def as_text(value: Any) -> str:
    """A stored value as the launch reads it: None is empty, and surrounding space is not a value."""
    return str(value if value is not None else "").strip()


def _config(runtime_config: Any) -> dict:
    return runtime_config if isinstance(runtime_config, dict) else {}


def record_model(model: Any, runtime_config: Any) -> str:
    """The agent's model column, else `runtimeConfig.model`."""
    return as_text(model) or as_text(_config(runtime_config).get("model"))


def record_effort(runtime_config: Any) -> str:
    """`runtimeConfig.effort`, else the legacy `runtimeConfig.thinking`."""
    config = _config(runtime_config)
    return as_text(config.get("effort")) or as_text(config.get("thinking"))


def with_effort(runtime_config: Any, effort: Any) -> dict:
    """A copy of `runtime_config` whose effort is `effort` and nothing else: `thinking` is removed."""
    config = {key: value for key, value in _config(runtime_config).items() if key != "thinking"}
    config["effort"] = as_text(effort)
    return config
