"""Base cloud provider interface and registry."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from ..models import ResourceMetadata

logger = logging.getLogger(__name__)


class CloudProvider(ABC):
    """Abstract base class for cloud provider adapters.

    Each provider implementation is responsible for:
    1. Accepting a resource from Terraform state
    2. Querying the cloud API to get actual current state
    3. Returning normalized ResourceMetadata for comparison
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier (aws, azure, gcp)."""
        ...

    @property
    @abstractmethod
    def supported_resource_types(self) -> list[str]:
        """List of Terraform resource types this provider can handle."""
        ...

    @abstractmethod
    def get_resource(self, resource: ResourceMetadata) -> ResourceMetadata | None:
        """Fetch the actual state of a resource from the cloud.

        Args:
            resource: The resource metadata from Terraform state.

        Returns:
            ResourceMetadata with actual cloud state, or None if resource not found.
        """
        ...

    def supports_resource(self, resource_type: str) -> bool:
        """Check if this provider can handle the given resource type."""
        return resource_type in self.supported_resource_types

    @abstractmethod
    def validate_credentials(self) -> bool:
        """Validate that cloud credentials are available and valid."""
        ...


class ProviderRegistry:
    """Registry for cloud provider adapters."""

    def __init__(self):
        self._providers: dict[str, CloudProvider] = {}

    def register(self, provider: CloudProvider) -> None:
        """Register a cloud provider adapter."""
        self._providers[provider.name] = provider
        logger.info(f"Registered provider: {provider.name}")

    def get_provider(self, name: str) -> CloudProvider | None:
        """Get a registered provider by name."""
        return self._providers.get(name)

    def get_provider_for_resource(self, resource_type: str) -> CloudProvider | None:
        """Find the appropriate provider for a resource type."""
        for provider in self._providers.values():
            if provider.supports_resource(resource_type):
                return provider
        return None

    def list_providers(self) -> list[str]:
        """List all registered provider names."""
        return list(self._providers.keys())

    def configure_providers(self, config: dict[str, Any]) -> None:
        """Configure providers from config dictionary."""
        from .aws_provider import AWSProvider

        providers_config = config.get("providers", {})

        # AWS
        aws_config = providers_config.get("aws", {})
        if aws_config.get("enabled", False):
            try:
                aws = AWSProvider(
                    region=aws_config.get("region", "us-east-1"),
                    profile=aws_config.get("profile"),
                )
                if aws.validate_credentials():
                    self.register(aws)
                else:
                    logger.warning("AWS credentials validation failed")
            except Exception as e:
                logger.warning(f"Failed to initialize AWS provider: {e}")

        # Azure (placeholder)
        azure_config = providers_config.get("azure", {})
        if azure_config.get("enabled", False):
            logger.info("Azure provider enabled but not yet fully implemented")

        # GCP (placeholder)
        gcp_config = providers_config.get("gcp", {})
        if gcp_config.get("enabled", False):
            logger.info("GCP provider enabled but not yet fully implemented")
