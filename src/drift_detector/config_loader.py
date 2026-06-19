"""Configuration loader for drift detector."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG = {
    "state": {
        "backend": "local",
        "path": "./terraform.tfstate",
    },
    "providers": {
        "aws": {"enabled": True, "region": "us-east-1"},
        "azure": {"enabled": False},
        "gcp": {"enabled": False},
    },
    "scheduler": {
        "enabled": False,
        "cron": "0 */6 * * *",
    },
    "dashboard": {
        "enabled": True,
        "host": "0.0.0.0",
        "port": 5000,
    },
    "output": {
        "format": "table",
        "report_dir": "./reports",
    },
    "detection": {
        "ignore_attributes": [
            "arn", "id", "self_link", "etag",
            "creation_date", "last_modified",
        ],
        "skip_resources": [],
        "include_resources": [],
    },
}


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """Load configuration from YAML file with defaults.

    Args:
        config_path: Path to config.yaml. If None, searches common locations.

    Returns:
        Merged configuration dictionary.
    """
    config = DEFAULT_CONFIG.copy()

    # Find config file
    if config_path is None:
        config_path = _find_config_file()

    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        config = _deep_merge(config, user_config)

    # Apply environment variable overrides
    config = _apply_env_overrides(config)

    return config


def _find_config_file() -> str | None:
    """Search for config file in common locations."""
    search_paths = [
        "./config.yaml",
        "./drift-detector.yaml",
        "./.drift-detector/config.yaml",
        os.path.expanduser("~/.drift-detector/config.yaml"),
    ]
    for path in search_paths:
        if Path(path).exists():
            return path
    return None


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dictionaries, with override taking precedence."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _apply_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    """Apply environment variable overrides to config.

    Environment variables follow the pattern: DRIFT_<SECTION>_<KEY>
    Example: DRIFT_STATE_PATH=./my.tfstate
    """
    env_map = {
        "DRIFT_STATE_PATH": ("state", "path"),
        "DRIFT_STATE_BACKEND": ("state", "backend"),
        "DRIFT_AWS_REGION": ("providers", "aws", "region"),
        "DRIFT_AWS_PROFILE": ("providers", "aws", "profile"),
        "DRIFT_SCHEDULER_CRON": ("scheduler", "cron"),
        "DRIFT_OUTPUT_FORMAT": ("output", "format"),
        "DRIFT_DASHBOARD_PORT": ("dashboard", "port"),
    }

    for env_var, path in env_map.items():
        value = os.environ.get(env_var)
        if value is not None:
            _set_nested(config, path, value)

    return config


def _set_nested(d: dict, path: tuple, value: Any) -> None:
    """Set a nested dictionary value using a tuple path."""
    for key in path[:-1]:
        d = d.setdefault(key, {})
    # Type conversion for known integer fields
    if path[-1] == "port":
        value = int(value)
    d[path[-1]] = value
