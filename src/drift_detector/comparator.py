"""Compare expected (state) vs actual (cloud) resource metadata to detect drift."""

from __future__ import annotations

import logging
from typing import Any

from deepdiff import DeepDiff

from .models import DriftResult, DriftType, ResourceMetadata

logger = logging.getLogger(__name__)


class DriftComparator:
    """Compare normalized resource metadata to identify configuration drift.

    Detects:
    - Deleted resources: exist in state but not in cloud
    - Modified resources: attribute values differ
    - Tag changes: only tags differ
    """

    def __init__(
        self,
        ignore_attributes: list[str] | None = None,
        significant_only: bool = True,
    ):
        """Initialize comparator.

        Args:
            ignore_attributes: Attribute keys to skip during comparison.
            significant_only: If True, only report meaningful differences.
        """
        self.ignore_attributes = set(ignore_attributes or [])
        self.significant_only = significant_only
        # Attributes that indicate unmanaged changes when they appear in actual
        # but not in expected (or differ from a known baseline)
        self._drift_indicator_attributes = {
            # S3
            "object_count",  # Objects added manually to S3 buckets
            "has_policy",  # Policy added manually
            "acl_public_read",  # ACL changed manually
            "logging_enabled",  # Logging toggled manually
            "lifecycle_rules_count",  # Lifecycle rules added manually
            # EC2
            "attached_volume_count",  # EBS volumes attached manually
            "network_interface_count",  # ENIs attached manually
            "user_data_present",  # User data modified manually
            "iam_instance_profile",  # IAM profile attached manually
            # VPC
            "subnet_count",  # Subnets added manually
            "route_table_count",  # Route tables added manually
            "internet_gateway_count",  # IGW attached manually
            "nat_gateway_count",  # NAT GW added manually
            # Security Group
            "ingress_rules_count",  # Rules added manually
            "egress_rules_count",  # Rules added manually
            # IAM Role
            "attached_policy_count",  # Policies attached manually
            "inline_policy_count",  # Inline policies added manually
            # Lambda
            "event_source_mapping_count",  # Triggers added manually
            # DynamoDB
            "gsi_count",  # GSIs added manually
            "lsi_count",  # LSIs added manually
            # EKS
            "nodegroup_count",  # Node groups added manually
            # SNS
            "subscription_count",  # Subscriptions added manually
            "has_access_policy",  # Access policy added manually
            "has_delivery_policy",  # Delivery policy added manually
            # SQS
            "has_dead_letter_queue",  # DLQ configured manually
            # CloudWatch Logs
            "metric_filter_count",  # Metric filters added manually
            "subscription_filter_count",  # Subscription filters added manually
        }

    def compare(
        self,
        expected: ResourceMetadata,
        actual: ResourceMetadata | None,
    ) -> DriftResult | None:
        """Compare expected state against actual cloud state.

        Args:
            expected: Resource metadata from Terraform state.
            actual: Resource metadata from cloud API, or None if not found.

        Returns:
            DriftResult if drift detected, None otherwise.
        """
        # Resource deleted from cloud
        if actual is None:
            return DriftResult(
                drift_type=DriftType.DELETED,
                resource_type=expected.resource_type,
                resource_id=expected.resource_id,
                resource_name=expected.resource_name,
                provider=expected.provider,
                expected=expected.attributes,
                actual={},
                changes={"status": "Resource no longer exists in cloud"},
                severity="critical",
            )

        # Compare attributes
        attr_changes = self._compare_attributes(expected.attributes, actual.attributes)

        # Compare tags separately
        tag_changes = self._compare_tags(expected.tags, actual.tags)

        # Determine drift type
        if attr_changes and tag_changes:
            # Both attributes and tags changed
            all_changes = {**attr_changes, "tags": tag_changes}
            return DriftResult(
                drift_type=DriftType.MODIFIED,
                resource_type=expected.resource_type,
                resource_id=expected.resource_id,
                resource_name=expected.resource_name,
                provider=expected.provider,
                expected=expected.attributes,
                actual=actual.attributes,
                changes=all_changes,
                severity=self._assess_severity(attr_changes),
            )
        elif attr_changes:
            return DriftResult(
                drift_type=DriftType.MODIFIED,
                resource_type=expected.resource_type,
                resource_id=expected.resource_id,
                resource_name=expected.resource_name,
                provider=expected.provider,
                expected=expected.attributes,
                actual=actual.attributes,
                changes=attr_changes,
                severity=self._assess_severity(attr_changes),
            )
        elif tag_changes:
            return DriftResult(
                drift_type=DriftType.TAG_CHANGED,
                resource_type=expected.resource_type,
                resource_id=expected.resource_id,
                resource_name=expected.resource_name,
                provider=expected.provider,
                expected={"tags": expected.tags},
                actual={"tags": actual.tags},
                changes={"tags": tag_changes},
                severity="low",
            )

        # No drift
        return None

    def _compare_attributes(
        self,
        expected: dict[str, Any],
        actual: dict[str, Any],
    ) -> dict[str, Any]:
        """Compare resource attributes and return differences."""
        changes: dict[str, Any] = {}

        # Only compare keys that exist in both expected and actual
        # This handles the case where cloud API returns different attribute sets
        common_keys = set(expected.keys()) & set(actual.keys())
        common_keys -= self.ignore_attributes
        # Also remove tag-related keys (handled separately)
        common_keys -= {"tags", "tags_all"}

        for key in common_keys:
            exp_val = expected[key]
            act_val = actual[key]

            if self._values_differ(exp_val, act_val):
                changes[key] = {
                    "expected": exp_val,
                    "actual": act_val,
                }

        # Check for drift indicator attributes in actual that are not in expected
        # These represent changes made outside of Terraform (e.g., objects added to S3)
        actual_only = set(actual.keys()) - set(expected.keys()) - self.ignore_attributes
        actual_only -= {"tags", "tags_all"}
        for key in actual_only:
            if key in self._drift_indicator_attributes:
                act_val = actual[key]
                # Flag if the value indicates something was added/changed
                if self._is_meaningful_drift_indicator(key, act_val):
                    change_entry: dict[str, Any] = {
                        "expected": "<not managed by terraform>",
                        "actual": act_val,
                    }
                    # Include the detailed info if available
                    detail_key = self._get_detail_key(key)
                    if detail_key and detail_key in actual:
                        change_entry["details"] = actual[detail_key]
                    changes[key] = change_entry

        # Check for keys in expected but missing from actual (potential issue)
        expected_only = set(expected.keys()) - set(actual.keys()) - self.ignore_attributes
        expected_only -= {"tags", "tags_all"}
        # Don't flag all missing keys - only flag ones that are likely significant
        if not self.significant_only:
            for key in expected_only:
                if expected[key] is not None and expected[key] != "" and expected[key] != []:
                    changes[key] = {
                        "expected": expected[key],
                        "actual": "<not present>",
                    }

        return changes

    def _is_meaningful_drift_indicator(self, key: str, value: Any) -> bool:
        """Determine if a drift indicator value represents an actual change."""
        # Integer counts > 0 indicate something was added manually
        count_keys = {
            "object_count", "lifecycle_rules_count", "attached_volume_count",
            "network_interface_count", "subnet_count", "route_table_count",
            "internet_gateway_count", "nat_gateway_count", "ingress_rules_count",
            "egress_rules_count", "attached_policy_count", "inline_policy_count",
            "event_source_mapping_count", "gsi_count", "lsi_count",
            "nodegroup_count", "subscription_count", "metric_filter_count",
            "subscription_filter_count",
        }
        if key in count_keys:
            return isinstance(value, int) and value > 0

        # Boolean flags that indicate something was added/changed when True
        boolean_keys = {
            "has_policy", "acl_public_read", "logging_enabled",
            "user_data_present", "has_access_policy", "has_delivery_policy",
            "has_dead_letter_queue",
        }
        if key in boolean_keys:
            return value is True

        # String attributes that indicate something was added when non-empty
        if key == "iam_instance_profile":
            return isinstance(value, str) and value != ""

        return False

    def _get_detail_key(self, indicator_key: str) -> str | None:
        """Map a drift indicator count key to its corresponding detail list key."""
        detail_map = {
            "object_count": "unmanaged_objects",
            "subnet_count": "subnet_details",
            "route_table_count": "route_table_details",
            "internet_gateway_count": "internet_gateway_details",
            "nat_gateway_count": "nat_gateway_details",
            "attached_policy_count": "attached_policy_details",
            "inline_policy_count": "inline_policy_names",
            "ingress_rules_count": "ingress_rules",
            "egress_rules_count": "egress_rules",
            "gsi_count": "gsi_details",
            "lsi_count": "lsi_details",
            "nodegroup_count": "nodegroup_details",
            "subscription_count": "subscription_details",
        }
        return detail_map.get(indicator_key)

    def _compare_tags(
        self,
        expected_tags: dict[str, str],
        actual_tags: dict[str, str],
    ) -> dict[str, Any] | None:
        """Compare resource tags and return differences."""
        if expected_tags == actual_tags:
            return None

        changes: dict[str, Any] = {}

        # Tags added in cloud (not in state)
        added = set(actual_tags.keys()) - set(expected_tags.keys())
        if added:
            changes["added"] = {k: actual_tags[k] for k in added}

        # Tags removed from cloud
        removed = set(expected_tags.keys()) - set(actual_tags.keys())
        if removed:
            changes["removed"] = {k: expected_tags[k] for k in removed}

        # Tags with changed values
        common = set(expected_tags.keys()) & set(actual_tags.keys())
        modified = {k: {"expected": expected_tags[k], "actual": actual_tags[k]}
                    for k in common if expected_tags[k] != actual_tags[k]}
        if modified:
            changes["modified"] = modified

        return changes if changes else None

    def _values_differ(self, expected: Any, actual: Any) -> bool:
        """Check if two values are meaningfully different."""
        # Handle None/empty equivalence
        if expected is None and actual is None:
            return False
        if expected is None and actual in ("", [], {}, 0, False):
            return False
        if actual is None and expected in ("", [], {}, 0, False):
            return False

        # Type coercion for string/number comparisons
        if isinstance(expected, (int, float)) and isinstance(actual, str):
            try:
                return expected != type(expected)(actual)
            except (ValueError, TypeError):
                return True
        if isinstance(actual, (int, float)) and isinstance(expected, str):
            try:
                return actual != type(actual)(expected)
            except (ValueError, TypeError):
                return True

        # List comparison (order-insensitive for certain types)
        if isinstance(expected, list) and isinstance(actual, list):
            try:
                return sorted(expected) != sorted(actual)
            except TypeError:
                # Use DeepDiff for complex nested lists
                diff = DeepDiff(expected, actual, ignore_order=True)
                return bool(diff)

        # Dict comparison
        if isinstance(expected, dict) and isinstance(actual, dict):
            diff = DeepDiff(expected, actual, ignore_order=True)
            return bool(diff)

        return expected != actual

    def _assess_severity(self, changes: dict[str, Any]) -> str:
        """Assess the severity of detected changes."""
        high_severity_keys = {
            "security_groups", "vpc_id", "subnet_id", "publicly_accessible",
            "storage_encrypted", "multi_az", "instance_type", "instance_class",
            "role", "role_arn", "policy", "cidr_block", "ingress_rules_count",
            "egress_rules_count", "kms_key_id", "kms_master_key_id",
            "acl_public_read", "has_policy", "ingress_rules", "egress_rules",
            "attached_policy_count", "inline_policy_count", "attached_policy_arns",
            "inline_policy_names", "assume_role_policy", "endpoint_public_access",
            "endpoint_private_access", "deletion_protection", "vpc_security_group_ids",
            "has_access_policy", "source_dest_check", "iam_instance_profile",
        }

        medium_severity_keys = {
            "memory_size", "timeout", "runtime", "handler", "engine_version",
            "allocated_storage", "monitoring", "versioning_enabled",
            "retention_in_days", "sse_algorithm", "object_count",
            "logging_enabled", "lifecycle_rules_count", "attached_volume_count",
            "network_interface_count", "user_data_present", "subnet_count",
            "route_table_count", "internet_gateway_count", "nat_gateway_count",
            "event_source_mapping_count", "gsi_count", "lsi_count",
            "nodegroup_count", "subscription_count", "has_dead_letter_queue",
            "metric_filter_count", "subscription_filter_count",
            "root_volume_size", "root_volume_type", "root_volume_encrypted",
            "backup_retention_period", "auto_minor_version_upgrade",
            "stream_enabled", "ttl_enabled", "point_in_time_recovery_enabled",
            "encryption_enabled", "server_side_encryption_enabled",
            "sqs_managed_sse_enabled", "environment_variables",
            "reserved_concurrency", "layers", "addon_names",
        }

        changed_keys = set(changes.keys())

        if changed_keys & high_severity_keys:
            return "high"
        elif changed_keys & medium_severity_keys:
            return "medium"
        return "low"
