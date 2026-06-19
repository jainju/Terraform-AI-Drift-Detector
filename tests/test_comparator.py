"""Tests for the drift comparator module."""

import pytest

from drift_detector.comparator import DriftComparator
from drift_detector.models import DriftType, ResourceMetadata


@pytest.fixture
def comparator():
    return DriftComparator(ignore_attributes=["arn", "id"])


def make_resource(
    resource_type="aws_instance",
    resource_id="i-123",
    attributes=None,
    tags=None,
):
    """Helper to create ResourceMetadata for testing."""
    return ResourceMetadata(
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name="test-resource",
        provider="aws",
        region="us-east-1",
        attributes=attributes or {},
        tags=tags or {},
    )


class TestDriftComparator:
    def test_no_drift(self, comparator):
        """Test no drift when expected matches actual."""
        expected = make_resource(attributes={"instance_type": "t3.medium"})
        actual = make_resource(attributes={"instance_type": "t3.medium"})
        result = comparator.compare(expected, actual)
        assert result is None

    def test_deleted_resource(self, comparator):
        """Test drift when resource is deleted from cloud."""
        expected = make_resource(attributes={"instance_type": "t3.medium"})
        result = comparator.compare(expected, None)
        assert result is not None
        assert result.drift_type == DriftType.DELETED
        assert result.severity == "critical"

    def test_modified_resource(self, comparator):
        """Test drift when attribute values differ."""
        expected = make_resource(attributes={"instance_type": "t3.medium", "monitoring": True})
        actual = make_resource(attributes={"instance_type": "t3.large", "monitoring": True})
        result = comparator.compare(expected, actual)
        assert result is not None
        assert result.drift_type == DriftType.MODIFIED
        assert "instance_type" in result.changes

    def test_tag_changed(self, comparator):
        """Test drift when only tags differ."""
        expected = make_resource(
            attributes={"instance_type": "t3.medium"},
            tags={"Environment": "prod"},
        )
        actual = make_resource(
            attributes={"instance_type": "t3.medium"},
            tags={"Environment": "staging"},
        )
        result = comparator.compare(expected, actual)
        assert result is not None
        assert result.drift_type == DriftType.TAG_CHANGED
        assert result.severity == "low"

    def test_tag_added(self, comparator):
        """Test drift when tags are added in cloud."""
        expected = make_resource(tags={"Environment": "prod"})
        actual = make_resource(tags={"Environment": "prod", "CostCenter": "123"})
        result = comparator.compare(expected, actual)
        assert result is not None
        assert result.drift_type == DriftType.TAG_CHANGED
        assert "added" in result.changes["tags"]

    def test_tag_removed(self, comparator):
        """Test drift when tags are removed from cloud."""
        expected = make_resource(tags={"Environment": "prod", "Team": "platform"})
        actual = make_resource(tags={"Environment": "prod"})
        result = comparator.compare(expected, actual)
        assert result is not None
        assert "removed" in result.changes["tags"]

    def test_severity_high_for_security_changes(self, comparator):
        """Test high severity for security-related changes."""
        expected = make_resource(attributes={"security_groups": ["sg-111"]})
        actual = make_resource(attributes={"security_groups": ["sg-111", "sg-999"]})
        result = comparator.compare(expected, actual)
        assert result is not None
        assert result.severity == "high"

    def test_severity_medium_for_config_changes(self, comparator):
        """Test medium severity for configuration changes."""
        expected = make_resource(attributes={"memory_size": 256})
        actual = make_resource(attributes={"memory_size": 512})
        result = comparator.compare(expected, actual)
        assert result is not None
        assert result.severity == "medium"

    def test_list_comparison_order_insensitive(self, comparator):
        """Test that list comparison is order-insensitive."""
        expected = make_resource(attributes={"security_groups": ["sg-222", "sg-111"]})
        actual = make_resource(attributes={"security_groups": ["sg-111", "sg-222"]})
        result = comparator.compare(expected, actual)
        assert result is None  # Same elements, different order = no drift

    def test_none_vs_empty_not_drift(self, comparator):
        """Test that None and empty values are treated as equivalent."""
        expected = make_resource(attributes={"description": None})
        actual = make_resource(attributes={"description": ""})
        result = comparator.compare(expected, actual)
        assert result is None
