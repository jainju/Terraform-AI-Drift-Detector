"""Main drift scanner that orchestrates state parsing, cloud fetching, and comparison."""

from __future__ import annotations

import logging
import time
from typing import Any

from .comparator import DriftComparator
from .models import DriftReport, DriftType, DriftResult, ResourceMetadata
from .providers.base import ProviderRegistry
from .state_parser import StateParser

logger = logging.getLogger(__name__)


class DriftScanner:
    """Orchestrates the complete drift detection scan.

    Workflow:
    1. Parse Terraform state file to get expected resources
    2. For each resource, query cloud provider for actual state
    3. Compare expected vs actual using DriftComparator
    4. Generate DriftReport with all findings
    """

    def __init__(
        self,
        state_path: str,
        registry: ProviderRegistry,
        ignore_attributes: list[str] | None = None,
        skip_resources: list[str] | None = None,
        include_resources: list[str] | None = None,
    ):
        self.state_path = state_path
        self.registry = registry
        self.ignore_attributes = ignore_attributes or []
        self.skip_resources = set(skip_resources or [])
        self.include_resources = set(include_resources or [])

        self.state_parser = StateParser(ignore_attributes=self.ignore_attributes)
        self.comparator = DriftComparator(ignore_attributes=self.ignore_attributes)

    def scan(self) -> DriftReport:
        """Execute a full drift detection scan.

        Returns:
            DriftReport with all detected drifts.
        """
        start_time = time.time()
        report = DriftReport(state_file=self.state_path)

        logger.info(f"Starting drift scan for state: {self.state_path}")

        # Step 1: Parse state file
        try:
            state_resources = self.state_parser.parse_file(self.state_path)
        except FileNotFoundError as e:
            report.errors.append(f"State file not found: {e}")
            logger.error(f"State file not found: {e}")
            return report
        except Exception as e:
            report.errors.append(f"Failed to parse state: {e}")
            logger.error(f"Failed to parse state: {e}")
            return report

        report.total_resources_in_state = len(state_resources)
        logger.info(f"Found {len(state_resources)} resources in state")

        # Step 2: Filter resources
        filtered_resources = self._filter_resources(state_resources)
        logger.info(f"Scanning {len(filtered_resources)} resources after filtering")

        # Step 3: For each resource, fetch actual state and compare
        for resource in filtered_resources:
            try:
                drift = self._check_resource(resource)
                if drift:
                    report.add_drift(drift)
            except Exception as e:
                error_msg = f"Error checking {resource.get_key()}: {e}"
                report.errors.append(error_msg)
                logger.error(error_msg)

        # Finalize report
        report.scan_duration_seconds = time.time() - start_time
        logger.info(
            f"Scan complete: {report.total_drifts} drifts found in "
            f"{report.scan_duration_seconds:.2f}s"
        )

        return report

    def scan_from_state_data(self, state_data: dict[str, Any]) -> DriftReport:
        """Execute drift scan from pre-loaded state data (useful for remote backends)."""
        start_time = time.time()
        report = DriftReport(state_file=self.state_path)

        try:
            state_resources = self.state_parser.parse_state(state_data)
        except Exception as e:
            report.errors.append(f"Failed to parse state data: {e}")
            return report

        report.total_resources_in_state = len(state_resources)
        filtered_resources = self._filter_resources(state_resources)

        for resource in filtered_resources:
            try:
                drift = self._check_resource(resource)
                if drift:
                    report.add_drift(drift)
            except Exception as e:
                report.errors.append(f"Error checking {resource.get_key()}: {e}")

        report.scan_duration_seconds = time.time() - start_time
        return report

    def _filter_resources(self, resources: list[ResourceMetadata]) -> list[ResourceMetadata]:
        """Apply include/skip filters to resource list."""
        filtered = []
        for r in resources:
            if self.skip_resources and r.resource_type in self.skip_resources:
                logger.debug(f"Skipping resource type: {r.resource_type}")
                continue
            if self.include_resources and r.resource_type not in self.include_resources:
                logger.debug(f"Resource type not in include list: {r.resource_type}")
                continue
            filtered.append(r)
        return filtered

    def _check_resource(self, resource: ResourceMetadata) -> DriftResult | None:
        """Check a single resource for drift.

        Args:
            resource: Expected resource from Terraform state.

        Returns:
            DriftResult if drift detected, None otherwise.
        """
        # Find appropriate provider
        provider = self.registry.get_provider_for_resource(resource.resource_type)
        if provider is None:
            logger.debug(
                f"No provider registered for {resource.resource_type}, skipping"
            )
            return None

        # Fetch actual state from cloud
        logger.debug(f"Checking resource: {resource.get_key()}")
        actual = provider.get_resource(resource)

        # Compare
        return self.comparator.compare(resource, actual)
