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
        }

        medium_severity_keys = {
            "memory_size", "timeout", "runtime", "handler", "engine_version",
            "allocated_storage", "monitoring", "versioning_enabled",
            "retention_in_days", "sse_algorithm",
        }

        changed_keys = set(changes.keys())

        if changed_keys & high_severity_keys:
            return "high"
        elif changed_keys & medium_severity_keys:
            return "medium"
        return "low"
