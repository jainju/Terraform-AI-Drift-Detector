"""Common data models for normalized resource representation."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DriftType(str, Enum):
    """Types of drift that can be detected."""
    DELETED = "deleted"          # Resource exists in state but not in cloud
    MODIFIED = "modified"        # Resource attributes differ
    TAG_CHANGED = "tag_changed"  # Only tags differ
    UNMANAGED = "unmanaged"      # Resource exists in cloud but not in state


class ResourceMetadata(BaseModel):
    """Normalized resource representation across all cloud providers."""
    resource_type: str = Field(description="Terraform resource type, e.g., aws_instance")
    resource_id: str = Field(description="Unique resource identifier")
    resource_name: str = Field(default="", description="Human-readable name")
    provider: str = Field(description="Cloud provider: aws, azure, gcp")
    region: str = Field(default="", description="Region/location")
    attributes: dict[str, Any] = Field(default_factory=dict, description="Normalized attributes")
    tags: dict[str, str] = Field(default_factory=dict, description="Resource tags")
    raw: dict[str, Any] = Field(default_factory=dict, description="Original raw attributes")

    def get_key(self) -> str:
        """Return a unique key for this resource."""
        return f"{self.resource_type}.{self.resource_id}"


class DriftResult(BaseModel):
    """A single drift finding."""
    drift_type: DriftType
    resource_type: str
    resource_id: str
    resource_name: str = ""
    provider: str = ""
    expected: dict[str, Any] = Field(default_factory=dict, description="Expected state from Terraform")
    actual: dict[str, Any] = Field(default_factory=dict, description="Actual state from cloud")
    changes: dict[str, Any] = Field(default_factory=dict, description="Detailed differences")
    severity: str = Field(default="medium", description="low, medium, high, critical")

    def summary(self) -> str:
        """Human-readable drift summary."""
        if self.drift_type == DriftType.DELETED:
            return f"DELETED: {self.resource_type}/{self.resource_name or self.resource_id} no longer exists in cloud"
        elif self.drift_type == DriftType.MODIFIED:
            changed_keys = list(self.changes.keys())
            return f"MODIFIED: {self.resource_type}/{self.resource_name or self.resource_id} - changed: {', '.join(changed_keys)}"
        elif self.drift_type == DriftType.TAG_CHANGED:
            return f"TAGS CHANGED: {self.resource_type}/{self.resource_name or self.resource_id}"
        elif self.drift_type == DriftType.UNMANAGED:
            return f"UNMANAGED: {self.resource_type}/{self.resource_id} exists in cloud but not in state"
        return f"{self.drift_type.value}: {self.resource_type}/{self.resource_id}"


class DriftReport(BaseModel):
    """Complete drift scan report."""
    scan_id: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    state_file: str = ""
    total_resources_in_state: int = 0
    total_resources_in_cloud: int = 0
    total_drifts: int = 0
    deleted_count: int = 0
    modified_count: int = 0
    tag_changed_count: int = 0
    unmanaged_count: int = 0
    drifts: list[DriftResult] = Field(default_factory=list)
    scan_duration_seconds: float = 0.0
    errors: list[str] = Field(default_factory=list)

    def add_drift(self, drift: DriftResult) -> None:
        """Add a drift result and update counters."""
        self.drifts.append(drift)
        self.total_drifts += 1
        if drift.drift_type == DriftType.DELETED:
            self.deleted_count += 1
        elif drift.drift_type == DriftType.MODIFIED:
            self.modified_count += 1
        elif drift.drift_type == DriftType.TAG_CHANGED:
            self.tag_changed_count += 1
        elif drift.drift_type == DriftType.UNMANAGED:
            self.unmanaged_count += 1

    def has_drift(self) -> bool:
        """Check if any drift was detected."""
        return self.total_drifts > 0

    def to_summary_dict(self) -> dict[str, Any]:
        """Return a summary dictionary for display."""
        return {
            "scan_id": self.scan_id,
            "timestamp": self.timestamp.isoformat(),
            "state_file": self.state_file,
            "total_resources_in_state": self.total_resources_in_state,
            "total_drifts": self.total_drifts,
            "deleted": self.deleted_count,
            "modified": self.modified_count,
            "tag_changed": self.tag_changed_count,
            "unmanaged": self.unmanaged_count,
            "scan_duration_seconds": round(self.scan_duration_seconds, 2),
            "errors": self.errors,
        }
