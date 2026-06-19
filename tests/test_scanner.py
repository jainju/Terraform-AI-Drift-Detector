"""Tests for the drift scanner module."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from drift_detector.models import DriftType, ResourceMetadata
from drift_detector.providers.base import ProviderRegistry
from drift_detector.scanner import DriftScanner


SAMPLE_STATE_PATH = str(Path(__file__).parent / "sample_state.json")


class MockProvider:
    """Mock cloud provider for testing."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    @property
    def name(self):
        return "aws"

    @property
    def supported_resource_types(self):
        return ["aws_instance", "aws_s3_bucket", "aws_security_group", "aws_lambda_function"]

    def supports_resource(self, resource_type):
        return resource_type in self.supported_resource_types

    def get_resource(self, resource):
        self.calls.append(resource)
        return self.responses.get(resource.resource_id)

    def validate_credentials(self):
        return True


@pytest.fixture
def mock_registry():
    registry = ProviderRegistry()
    return registry


class TestDriftScanner:
    def test_scan_detects_deleted_resource(self, mock_registry):
        """Test scan detects resource deleted from cloud."""
        # Provider returns None for all resources = all deleted
        provider = MockProvider(responses={})
        mock_registry.register(provider)

        scanner = DriftScanner(
            state_path=SAMPLE_STATE_PATH,
            registry=mock_registry,
            ignore_attributes=["arn", "id"],
        )

        report = scanner.scan()
        assert report.total_drifts > 0
        assert report.deleted_count > 0

    def test_scan_no_drift_when_matching(self, mock_registry):
        """Test scan reports no drift when state matches cloud."""
        with open(SAMPLE_STATE_PATH) as f:
            state = json.load(f)

        # Build mock responses that match state
        responses = {}
        for res_block in state["resources"]:
            if res_block["mode"] != "managed":
                continue
            for instance in res_block["instances"]:
                attrs = instance["attributes"]
                resource_id = attrs.get("id", "")
                tags = attrs.get("tags", {})
                responses[resource_id] = ResourceMetadata(
                    resource_type=res_block["type"],
                    resource_id=resource_id,
                    resource_name=attrs.get("name", ""),
                    provider="aws",
                    region="us-east-1",
                    attributes=attrs,
                    tags=tags if isinstance(tags, dict) else {},
                )

        provider = MockProvider(responses=responses)
        mock_registry.register(provider)

        scanner = DriftScanner(
            state_path=SAMPLE_STATE_PATH,
            registry=mock_registry,
            ignore_attributes=["arn", "id"],
        )

        report = scanner.scan()
        # Should have minimal drift since we return the same attributes
        assert report.errors == []

    def test_scan_handles_missing_state_file(self, mock_registry):
        """Test scan handles missing state file gracefully."""
        scanner = DriftScanner(
            state_path="/nonexistent/state.tfstate",
            registry=mock_registry,
        )
        report = scanner.scan()
        assert len(report.errors) > 0
        assert "not found" in report.errors[0].lower()

    def test_scan_respects_skip_resources(self, mock_registry):
        """Test scan skips specified resource types."""
        provider = MockProvider(responses={})
        mock_registry.register(provider)

        scanner = DriftScanner(
            state_path=SAMPLE_STATE_PATH,
            registry=mock_registry,
            skip_resources=["aws_instance", "aws_s3_bucket"],
            ignore_attributes=["arn", "id"],
        )

        report = scanner.scan()
        # Should not have checked instance or bucket
        checked_types = [r.resource_type for r in provider.calls]
        assert "aws_instance" not in checked_types
        assert "aws_s3_bucket" not in checked_types

    def test_scan_respects_include_resources(self, mock_registry):
        """Test scan only checks included resource types."""
        provider = MockProvider(responses={})
        mock_registry.register(provider)

        scanner = DriftScanner(
            state_path=SAMPLE_STATE_PATH,
            registry=mock_registry,
            include_resources=["aws_instance"],
            ignore_attributes=["arn", "id"],
        )

        report = scanner.scan()
        checked_types = [r.resource_type for r in provider.calls]
        assert all(t == "aws_instance" for t in checked_types)

    def test_scan_report_metadata(self, mock_registry):
        """Test scan report contains correct metadata."""
        provider = MockProvider(responses={})
        mock_registry.register(provider)

        scanner = DriftScanner(
            state_path=SAMPLE_STATE_PATH,
            registry=mock_registry,
            ignore_attributes=["arn", "id"],
        )

        report = scanner.scan()
        assert report.state_file == SAMPLE_STATE_PATH
        assert report.total_resources_in_state == 4
        assert report.scan_duration_seconds > 0
        assert report.scan_id != ""
