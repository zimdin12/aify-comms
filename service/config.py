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


@dataclass
class ServiceConfig:
    """Central configuration for the service."""

    # Identity
    name: str = "aify-comms"
    version: str = "0.1.0"
    description: str = "Dashboard and bridge for spawning, messaging, monitoring, and controlling headless coding agents across connected environments"

    # Network
    port: int = 8800
    host: str = "0.0.0.0"

    # Paths
    data_dir: str = "/data"
    config_dir: str = "/app/config"

    # MCP
    mcp_enabled: bool = True
    mcp_path_prefix: str = "/mcp"
    mcp_user_id: str = "default"
    mcp_app_name: str = "claude-code"

    # Security
    api_key: str = ""
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

        # Load service.json if exists
        json_path = Path(os.getenv("CONFIG_DIR", config.config_dir)) / "service.json"
        if json_path.exists():
            try:
                with open(json_path) as f:
                    data = json.load(f)
                config.custom = data.get("custom", {})
                for key, value in data.items():
                    if key not in ("custom", "containers") and hasattr(config, key):
                        setattr(config, key, value)
            except json.JSONDecodeError as e:
                import logging
                logging.getLogger(__name__).error(f"Invalid service.json: {e}")

        # Environment variables override everything
        env_map = {
            "SERVICE_NAME": "name",
            "SERVICE_VERSION": "version",
            "SERVICE_DESCRIPTION": "description",
            "SERVICE_PORT": ("port", int),
            "HOST": "host",
            "DATA_DIR": "data_dir",
            "CONFIG_DIR": "config_dir",
            "MCP_ENABLED": ("mcp_enabled", lambda v: v.lower() in ("true", "1", "yes")),
            "MCP_PATH_PREFIX": "mcp_path_prefix",
            "MCP_USER_ID": "mcp_user_id",
            "MCP_APP_NAME": "mcp_app_name",
            "API_KEY": "api_key",
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
