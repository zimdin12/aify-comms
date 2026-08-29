"""
Unified configuration loader.

Loads from: defaults -> config/service.json -> environment variables.
Environment variables always win.

Use .env for deployment/infrastructure settings (ports, resources, credentials).
Use config/service.json for service definition (containers, custom config).
"""

import json
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

# What the build stamp OWNS, and which `service.json` therefore may not set. Named as one set so the
# rule is a single readable fact rather than five conditions, and so a test can assert the set is
# complete against the stamp's own keys.
_STAMP_OWNED_KEYS = frozenset({"version", "build_sha", "build_short", "build_branch", "built_at"})


@dataclass
class ServiceConfig:
    """Central configuration for the service."""

    # Identity
    name: str = "aify-comms"
    # Release version. Do NOT hand-edit — the one source is the repo-root VERSION file,
    # baked into service/_build_stamp.json by scripts/stamp.sh (the container has no repo
    # root, same reason the sha is stamped). This default is only the never-stamped
    # fallback, and is deliberately an obviously-unreal value: the previous default was
    # "4.0.0", which looked plausible enough that nobody noticed it had no relationship to
    # any release. Env SERVICE_VERSION still wins, for a one-off override.
    version: str = "0.0.0-dev"
    description: str = "Dashboard and bridge for spawning, messaging, monitoring, and controlling headless coding agents across connected environments"

    # Build stamp (loaded from service/_build_stamp.json at startup; the file is
    # written by scripts/stamp.sh before each build because the container has no
    # .git of its own). Env-overridable; defaults to "unknown".
    build_sha: str = "unknown"

    #: Stamp-owned fields an environment variable overrode, in the order they were applied.
    #:
    #: Empty is the normal case and the honest one: the build identity came from the stamp. A name
    #: here means a human or a pipeline supplied it, so no comparison against a checkout proves what
    #: is running.
    stamp_overrides: list = field(default_factory=list)
    build_short: str = "unknown"
    build_branch: str = "unknown"
    built_at: str = ""

    # Network
    port: int = 8800
    #: NO `host`. It was declared, settable through `HOST` and through `service.json`, and read by
    #: nothing -- so an operator setting it got no change and no warning. The bind address is not
    #: this file's to decide: `Dockerfile`'s CMD passes `--host 0.0.0.0` to uvicorn, and what is
    #: REACHABLE is decided by the compose port mapping. Binding to loopback means publishing the
    #: port as `127.0.0.1:8800:8800`, which is what `aify-comms doctor`'s `api-exposure` check says
    #: in its own fix text. A knob that cannot move anything is worse than an absent one: an absent
    #: knob is obviously absent.

    # Paths
    data_dir: str = "/data"
    config_dir: str = "/app/config"

    # MCP
    mcp_enabled: bool = True
    mcp_path_prefix: str = "/mcp"
    #: NO `mcp_user_id` / `mcp_app_name` either, for the same reason: both were settable and read
    #: by nothing, in the service and in `mcp/` alike.

    # Security
    api_key: str = ""
    # Proves a caller may act on ANOTHER agent's behalf (unsend/channel-delete/artifact-unshare).
    # SEPARATE from api_key on purpose: every bridge holds the api key, so it can never
    # distinguish the dashboard from an agent. Empty means no caller can claim operator
    # privilege at all — see service/api_core/operator_authz.py for why that fails closed.
    operator_key: str = ""
    cors_origins: list[str] = field(default_factory=lambda: ["*"])

    # Logging
    log_level: str = "info"
    log_format: str = "json"

    # Custom config from service.json (containers config, etc.)
    custom: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "ServiceConfig":
        """Load config with precedence: env vars > service.json > defaults."""
        config = cls()

        # Load the build stamp (service/_build_stamp.json). It lives next to
        # this module (service/) which IS COPY'd into the image. Missing or
        # malformed → keep the "unknown" defaults; never raise at startup.
        stamp_path = Path(__file__).resolve().parent / "_build_stamp.json"
        if stamp_path.exists():
            try:
                with open(stamp_path) as f:
                    stamp = json.load(f)
                if not isinstance(stamp, dict):
                    raise ValueError("build stamp is not a JSON object")
                config.build_sha = str(stamp.get("sha", config.build_sha) or "unknown")
                config.build_short = str(stamp.get("short", config.build_short) or "unknown")
                config.build_branch = str(stamp.get("branch", config.build_branch) or "unknown")
                config.built_at = str(stamp.get("built_at", config.built_at) or "")
                # A stamp written before the version field existed has no "version" key —
                # keep the fallback rather than blanking the identity the API reports.
                config.version = str(stamp.get("version", config.version) or config.version)
            except (json.JSONDecodeError, OSError, ValueError, AttributeError) as e:
                # Never raise at startup on a malformed stamp (bughunt 2026-07-03): a
                # valid-but-non-object file (null/[]/…) previously raised uncaught.
                import logging
                logging.getLogger(__name__).warning(f"Invalid _build_stamp.json: {e}")

        # Load service.json if exists
        json_path = Path(os.getenv("CONFIG_DIR", config.config_dir)) / "service.json"
        if json_path.exists():
            try:
                with open(json_path) as f:
                    data = json.load(f)
                # A valid-but-non-object service.json (null/[]/"str") must not crash boot
                # (bughunt 2026-07-03) — the README tells operators to hand-edit this file.
                if not isinstance(data, dict):
                    raise ValueError("service.json is not a JSON object")
                config.custom = data.get("custom", {})
                for key, value in data.items():
                    if key in _STAMP_OWNED_KEYS:
                        # A SECOND override hole of exactly the .env SERVICE_VERSION class, reported
                        # from another instance 2026-08-17: this loop set ANY attribute that happened
                        # to exist on the config, so a `version` key left in a hand-edited or stale
                        # service.json silently beat the build stamp — that instance's service
                        # reported 3.6.6 while running 0.5.4.
                        #
                        # The version was the mild half. The same loop also reached `build_sha`, and
                        # that value is what `aify-comms doctor`'s `service` check compares against
                        # repo HEAD to answer "is the container serving my code". A service.json
                        # could therefore make the one instrument this project has for detecting a
                        # stale deploy agree with a sha nothing was ever built from — a false green
                        # in the exact place the tool exists to prevent one.
                        #
                        # These five fields are OBSERVATIONS of the build, not configuration. There is
                        # no operator intent a hand-edit could express here that would be true.
                        continue
                    if key not in ("custom", "containers") and hasattr(config, key):
                        setattr(config, key, value)
            except (json.JSONDecodeError, ValueError, AttributeError) as e:
                import logging
                logging.getLogger(__name__).error(f"Invalid service.json: {e}")

        # Environment variables override everything
        env_map = {
            "SERVICE_NAME": "name",
            "SERVICE_VERSION": "version",
            "SERVICE_DESCRIPTION": "description",
            "AIFY_BUILD_SHA": "build_sha",
            "AIFY_BUILD_SHORT": "build_short",
            "AIFY_BUILD_BRANCH": "build_branch",
            "AIFY_BUILT_AT": "built_at",
            "SERVICE_PORT": ("port", int),
            "DATA_DIR": "data_dir",
            "CONFIG_DIR": "config_dir",
            "MCP_ENABLED": ("mcp_enabled", lambda v: v.lower() in ("true", "1", "yes")),
            "MCP_PATH_PREFIX": "mcp_path_prefix",
            "API_KEY": "api_key",
            "OPERATOR_KEY": "operator_key",
            "CORS_ORIGINS": ("cors_origins", lambda v: [s.strip() for s in v.split(",")]),
            "LOG_LEVEL": "log_level",
            "LOG_FORMAT": "log_format",
        }

        for env_key, target in env_map.items():
            val = os.getenv(env_key)
            if val is not None:
                if isinstance(target, tuple):
                    attr_name, converter = target
                    setattr(config, attr_name, converter(val))
                else:
                    setattr(config, target, val)
                # RECORDED, NOT REFUSED. `service.json` is refused these five outright, because a
                # hand-edited file can make the stale-deploy check agree with a sha nothing was built
                # from. Env can do exactly the same and is NOT refused: `SERVICE_VERSION` is a
                # documented one-off override, and a CI image built outside this repo may legitimately
                # stamp its own sha this way. What was missing is that the override left no trace, so
                # `/health` reported a build identity indistinguishable from one the stamp produced --
                # and doctor's `service` check, the one instrument that detects a stale deploy, would
                # certify it as matching repo HEAD.
                if target in _STAMP_OWNED_KEYS:
                    config.stamp_overrides.append(str(target))

        # Ensure compose_project_name in custom matches env var
        compose_name = os.getenv("COMPOSE_PROJECT_NAME", config.custom.get("compose_project_name", "aify"))
        config.custom["compose_project_name"] = compose_name
        if "network_name" not in config.custom:
            config.custom["network_name"] = f"{compose_name}-network"

        return config


_config: ServiceConfig | None = None


def get_config() -> ServiceConfig:
    global _config
    if _config is None:
        _config = ServiceConfig.load()
    return _config
