"""Parse Terraform state files and extract resource metadata."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .models import ResourceMetadata

logger = logging.getLogger(__name__)


class StateParser:
    """Parse Terraform state files (v4 format) into normalized ResourceMetadata."""

    SUPPORTED_VERSIONS = [4]

    def __init__(self, ignore_attributes: list[str] | None = None):
        self.ignore_attributes = set(ignore_attributes or [])

    def parse_file(self, state_path: str) -> list[ResourceMetadata]:
        """Parse a local terraform.tfstate file."""
        path = Path(state_path)
        if not path.exists():
            raise FileNotFoundError(f"State file not found: {state_path}")

        with open(path, "r", encoding="utf-8") as f:
            state_data = json.load(f)

        return self.parse_state(state_data)

    def parse_state(self, state_data: dict[str, Any]) -> list[ResourceMetadata]:
        """Parse terraform state JSON data into ResourceMetadata list."""
        version = state_data.get("version")
        if version not in self.SUPPORTED_VERSIONS:
            raise ValueError(
                f"Unsupported state version: {version}. Supported: {self.SUPPORTED_VERSIONS}"
            )

        resources: list[ResourceMetadata] = []

        for resource_block in state_data.get("resources", []):
            mode = resource_block.get("mode", "managed")
            if mode != "managed":
                continue  # Skip data sources

            resource_type = resource_block.get("type", "")
            provider_raw = resource_block.get("provider", "")
            provider = self._extract_provider(provider_raw)
            name = resource_block.get("name", "")

            for instance in resource_block.get("instances", []):
                attributes = instance.get("attributes", {})
                index_key = instance.get("index_key")

                resource_id = self._extract_resource_id(attributes, resource_type)
                resource_name = self._extract_resource_name(attributes, name, index_key)
                tags = self._extract_tags(attributes)
                region = self._extract_region(attributes, provider)
                filtered_attrs = self._filter_attributes(attributes)

                metadata = ResourceMetadata(
                    resource_type=resource_type,
                    resource_id=resource_id,
                    resource_name=resource_name,
                    provider=provider,
                    region=region,
                    attributes=filtered_attrs,
                    tags=tags,
                    raw=attributes,
                )
                resources.append(metadata)
                logger.debug(f"Parsed resource: {metadata.get_key()}")

        logger.info(f"Parsed {len(resources)} managed resources from state")
        return resources

    def _extract_provider(self, provider_str: str) -> str:
        """Extract provider name from provider string like 'provider["registry.terraform.io/hashicorp/aws"]'."""
        # Remove wrapping like: provider["..."]
        cleaned = provider_str.replace('provider[', '').replace(']', '')
        cleaned = cleaned.strip('"').strip("'")
        if "/" in cleaned:
            return cleaned.split("/")[-1]
        return cleaned

    def _extract_resource_id(self, attributes: dict[str, Any], resource_type: str) -> str:
        """Extract the primary resource identifier."""
        # Try common ID fields in order of preference
        for key in ["id", "arn", "self_link", "name", "resource_id"]:
            if key in attributes and attributes[key]:
                return str(attributes[key])
        return f"{resource_type}-unknown"

    def _extract_resource_name(
        self, attributes: dict[str, Any], tf_name: str, index_key: Any
    ) -> str:
        """Extract a human-readable name for the resource."""
        # Try name-like attributes
        for key in ["name", "display_name", "bucket", "function_name", "cluster_name"]:
            if key in attributes and attributes[key]:
                name = str(attributes[key])
                if index_key is not None:
                    return f"{name}[{index_key}]"
                return name

        if index_key is not None:
            return f"{tf_name}[{index_key}]"
        return tf_name

    def _extract_tags(self, attributes: dict[str, Any]) -> dict[str, str]:
        """Extract tags from attributes."""
        tags = attributes.get("tags") or {}
        if isinstance(tags, dict):
            return {str(k): str(v) for k, v in tags.items()}
        # Some resources use tags_all
        tags_all = attributes.get("tags_all") or {}
        if isinstance(tags_all, dict):
            return {str(k): str(v) for k, v in tags_all.items()}
        return {}

    def _extract_region(self, attributes: dict[str, Any], provider: str) -> str:
        """Extract region from attributes or ARN."""
        # Direct region attribute
        if "region" in attributes:
            return str(attributes["region"])
        if "location" in attributes:
            return str(attributes["location"])

        # Extract from ARN for AWS
        if provider == "aws" and "arn" in attributes:
            arn = str(attributes.get("arn", ""))
            parts = arn.split(":")
            if len(parts) >= 4:
                return parts[3]

        return ""

    def _filter_attributes(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Filter out ignored and computed attributes."""
        filtered = {}
        for key, value in attributes.items():
            if key in self.ignore_attributes:
                continue
            if key.startswith("_"):
                continue
            # Skip null values
            if value is None:
                continue
            # Skip timeouts block (Terraform internal)
            if key == "timeouts":
                continue
            filtered[key] = value
        return filtered
