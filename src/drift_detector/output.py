"""Output formatters for drift reports (table, JSON, HTML)."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from .models import DriftReport, DriftResult, DriftType


class OutputFormatter:
    """Format drift reports for different output targets."""

    def __init__(self, report_dir: str = "./reports"):
        self.report_dir = Path(report_dir)
        self.console = Console()

    def output(self, report: DriftReport, format: str = "table") -> str:
        """Output report in the specified format.

        Args:
            report: The drift report to output.
            format: Output format - 'table', 'json', or 'html'.

        Returns:
            File path if written to disk, or empty string for console output.
        """
        if format == "json":
            return self._output_json(report)
        elif format == "html":
            return self._output_html(report)
        else:
            self._output_table(report)
            return ""

    def _output_table(self, report: DriftReport) -> None:
        """Output report as a rich table to the console."""
        # Summary panel
        status_color = "red" if report.has_drift() else "green"
        status_text = "DRIFT DETECTED" if report.has_drift() else "NO DRIFT"

        summary = Text()
        summary.append(f"Status: ", style="bold")
        summary.append(f"{status_text}\n", style=f"bold {status_color}")
        summary.append(f"Scan ID: {report.scan_id}\n")
        summary.append(f"State File: {report.state_file}\n")
        summary.append(f"Resources in State: {report.total_resources_in_state}\n")
        summary.append(f"Total Drifts: {report.total_drifts}\n")
        summary.append(f"  Deleted: {report.deleted_count}\n")
        summary.append(f"  Modified: {report.modified_count}\n")
        summary.append(f"  Tag Changes: {report.tag_changed_count}\n")
        summary.append(f"  Unmanaged: {report.unmanaged_count}\n")
        summary.append(f"Duration: {report.scan_duration_seconds:.2f}s")

        self.console.print(Panel(summary, title="Drift Scan Report", border_style="blue"))

        if not report.has_drift():
            self.console.print("\n[green]✓ All resources match expected state[/green]")
            return

        # Drift details table
        table = Table(title="Drift Details", show_lines=True)
        table.add_column("Type", style="bold", width=12)
        table.add_column("Severity", width=10)
        table.add_column("Resource Type", width=25)
        table.add_column("Resource Name", width=25)
        table.add_column("Changes", width=50)

        for drift in report.drifts:
            type_style = self._drift_type_style(drift.drift_type)
            severity_style = self._severity_style(drift.severity)

            changes_text = self._format_changes_text(drift)

            table.add_row(
                Text(drift.drift_type.value.upper(), style=type_style),
                Text(drift.severity.upper(), style=severity_style),
                drift.resource_type,
                drift.resource_name or drift.resource_id,
                changes_text,
            )

        self.console.print(table)

        # Errors
        if report.errors:
            self.console.print(f"\n[yellow]⚠ {len(report.errors)} errors during scan:[/yellow]")
            for error in report.errors:
                self.console.print(f"  • {error}")

    def _output_json(self, report: DriftReport) -> str:
        """Output report as JSON file."""
        self.report_dir.mkdir(parents=True, exist_ok=True)

        filename = f"drift_report_{report.scan_id}.json"
        filepath = self.report_dir / filename

        output_data = {
            "summary": report.to_summary_dict(),
            "drifts": [
                {
                    "drift_type": d.drift_type.value,
                    "resource_type": d.resource_type,
                    "resource_id": d.resource_id,
                    "resource_name": d.resource_name,
                    "provider": d.provider,
                    "severity": d.severity,
                    "changes": d.changes,
                    "expected": d.expected,
                    "actual": d.actual,
                }
                for d in report.drifts
            ],
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, default=str)

        self.console.print(f"[green]Report saved to: {filepath}[/green]")
        return str(filepath)

    def _output_html(self, report: DriftReport) -> str:
        """Output report as HTML file."""
        self.report_dir.mkdir(parents=True, exist_ok=True)

        filename = f"drift_report_{report.scan_id}.html"
        filepath = self.report_dir / filename

        html = self._generate_html(report)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        self.console.print(f"[green]HTML report saved to: {filepath}[/green]")
        return str(filepath)

    def _generate_html(self, report: DriftReport) -> str:
        """Generate HTML report content."""
        status_class = "danger" if report.has_drift() else "success"
        status_text = "DRIFT DETECTED" if report.has_drift() else "NO DRIFT"

        drift_rows = ""
        for d in report.drifts:
            changes_html = "<br>".join(
                f"<strong>{k}:</strong> {self._format_change_value(v)}"
                for k, v in d.changes.items()
            )
            drift_rows += f"""
            <tr class="{d.drift_type.value}">
                <td><span class="badge badge-{d.drift_type.value}">{d.drift_type.value.upper()}</span></td>
                <td><span class="badge badge-{d.severity}">{d.severity.upper()}</span></td>
                <td>{d.resource_type}</td>
                <td>{d.resource_name or d.resource_id}</td>
                <td class="changes">{changes_html}</td>
            </tr>
            """

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Terraform Drift Report - {report.scan_id}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; padding: 2rem; color: #333; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        h1 {{ color: #1a1a2e; margin-bottom: 1rem; }}
        .summary {{ background: white; border-radius: 8px; padding: 1.5rem; margin-bottom: 2rem; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem; margin-top: 1rem; }}
        .summary-item {{ text-align: center; padding: 1rem; background: #f8f9fa; border-radius: 6px; }}
        .summary-item .value {{ font-size: 2rem; font-weight: bold; }}
        .summary-item .label {{ font-size: 0.85rem; color: #666; margin-top: 0.25rem; }}
        .status {{ display: inline-block; padding: 0.25rem 0.75rem; border-radius: 4px; font-weight: bold; }}
        .status.success {{ background: #d4edda; color: #155724; }}
        .status.danger {{ background: #f8d7da; color: #721c24; }}
        table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        th {{ background: #1a1a2e; color: white; padding: 0.75rem 1rem; text-align: left; }}
        td {{ padding: 0.75rem 1rem; border-bottom: 1px solid #eee; vertical-align: top; }}
        tr:hover {{ background: #f8f9fa; }}
        .badge {{ display: inline-block; padding: 0.2rem 0.5rem; border-radius: 3px; font-size: 0.75rem; font-weight: bold; }}
        .badge-deleted {{ background: #f8d7da; color: #721c24; }}
        .badge-modified {{ background: #fff3cd; color: #856404; }}
        .badge-tag_changed {{ background: #d1ecf1; color: #0c5460; }}
        .badge-unmanaged {{ background: #e2e3e5; color: #383d41; }}
        .badge-critical {{ background: #721c24; color: white; }}
        .badge-high {{ background: #dc3545; color: white; }}
        .badge-medium {{ background: #ffc107; color: #333; }}
        .badge-low {{ background: #28a745; color: white; }}
        .changes {{ font-size: 0.85rem; font-family: monospace; }}
        .timestamp {{ color: #666; font-size: 0.9rem; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Terraform Drift Report</h1>
        <p class="timestamp">Scan ID: {report.scan_id} | Generated: {report.timestamp.isoformat()} | Duration: {report.scan_duration_seconds:.2f}s</p>

        <div class="summary">
            <span class="status {status_class}">{status_text}</span>
            <p style="margin-top: 0.5rem;">State: {report.state_file}</p>
            <div class="summary-grid">
                <div class="summary-item">
                    <div class="value">{report.total_resources_in_state}</div>
                    <div class="label">Resources in State</div>
                </div>
                <div class="summary-item">
                    <div class="value" style="color: {'#dc3545' if report.total_drifts > 0 else '#28a745'}">{report.total_drifts}</div>
                    <div class="label">Total Drifts</div>
                </div>
                <div class="summary-item">
                    <div class="value" style="color: #721c24">{report.deleted_count}</div>
                    <div class="label">Deleted</div>
                </div>
                <div class="summary-item">
                    <div class="value" style="color: #856404">{report.modified_count}</div>
                    <div class="label">Modified</div>
                </div>
                <div class="summary-item">
                    <div class="value" style="color: #0c5460">{report.tag_changed_count}</div>
                    <div class="label">Tag Changes</div>
                </div>
            </div>
        </div>

        {"<table><thead><tr><th>Type</th><th>Severity</th><th>Resource Type</th><th>Resource</th><th>Changes</th></tr></thead><tbody>" + drift_rows + "</tbody></table>" if report.has_drift() else "<p style='text-align:center; padding: 2rem; color: #28a745; font-size: 1.2rem;'>✓ All resources match expected state</p>"}
    </div>
</body>
</html>"""
        return html

    def _drift_type_style(self, drift_type: DriftType) -> str:
        """Get rich style for drift type."""
        styles = {
            DriftType.DELETED: "bold red",
            DriftType.MODIFIED: "bold yellow",
            DriftType.TAG_CHANGED: "bold cyan",
            DriftType.UNMANAGED: "bold magenta",
        }
        return styles.get(drift_type, "")

    def _severity_style(self, severity: str) -> str:
        """Get rich style for severity."""
        styles = {
            "critical": "bold red",
            "high": "red",
            "medium": "yellow",
            "low": "green",
        }
        return styles.get(severity, "")

    def _format_changes_text(self, drift: DriftResult) -> str:
        """Format changes for table display."""
        if drift.drift_type == DriftType.DELETED:
            return "Resource no longer exists in cloud"

        parts = []
        for key, value in drift.changes.items():
            if isinstance(value, dict) and "expected" in value and "actual" in value:
                # Check if there are details to show
                if "details" in value and isinstance(value["details"], list):
                    details = value["details"]
                    parts.append(f"{key}: {value['actual']} found (not in terraform)")
                    for item in details[:10]:  # Show up to 10 items
                        if isinstance(item, dict):
                            # Format based on resource type
                            item_desc = self._format_detail_item(key, item)
                            parts.append(f"  • {item_desc}")
                        else:
                            parts.append(f"  • {item}")
                    if len(details) > 10:
                        parts.append(f"  ... and {len(details) - 10} more")
                else:
                    parts.append(f"{key}: {value['expected']} → {value['actual']}")
            elif key == "tags" and isinstance(value, dict):
                tag_parts = []
                if "added" in value:
                    tag_parts.append(f"added: {list(value['added'].keys())}")
                if "removed" in value:
                    tag_parts.append(f"removed: {list(value['removed'].keys())}")
                if "modified" in value:
                    tag_parts.append(f"changed: {list(value['modified'].keys())}")
                parts.append(f"tags: {', '.join(tag_parts)}")
            else:
                parts.append(f"{key}: {value}")

        return "\n".join(parts[:15])  # Limit to 15 lines

    def _format_detail_item(self, indicator_key: str, item: dict) -> str:
        """Format a detail item for display based on the indicator type."""
        if indicator_key == "object_count":
            key = item.get("key", "unknown")
            size = item.get("size_bytes", 0)
            modified = item.get("last_modified", "")
            size_str = self._human_readable_size(size)
            return f"{key} ({size_str}, modified: {modified})"
        elif indicator_key == "subnet_count":
            return f"{item.get('subnet_id', '')} - {item.get('name', 'unnamed')} ({item.get('cidr_block', '')} in {item.get('availability_zone', '')})"
        elif indicator_key == "route_table_count":
            return f"{item.get('route_table_id', '')} - {item.get('name', 'unnamed')}"
        elif indicator_key == "internet_gateway_count":
            return f"{item.get('igw_id', '')} - {item.get('name', 'unnamed')}"
        elif indicator_key == "nat_gateway_count":
            return f"{item.get('nat_gateway_id', '')} - {item.get('name', 'unnamed')} (state: {item.get('state', '')})"
        elif indicator_key == "attached_policy_count":
            return f"{item.get('policy_name', '')} ({item.get('policy_arn', '')})"
        elif indicator_key in ("ingress_rules_count", "egress_rules_count"):
            proto = item.get("protocol", "all")
            from_p = item.get("from_port", 0)
            to_p = item.get("to_port", 0)
            cidr = item.get("cidr", item.get("cidr_ipv6", item.get("source_sg", "")))
            port_str = f"{from_p}-{to_p}" if from_p != to_p else str(from_p)
            return f"{proto} port {port_str} from {cidr}"
        elif indicator_key == "gsi_count":
            return f"{item.get('index_name', '')} (status: {item.get('index_status', '')})"
        elif indicator_key == "lsi_count":
            return f"{item.get('index_name', '')}"
        elif indicator_key == "nodegroup_count":
            return f"{item.get('name', '')} (status: {item.get('status', '')}, types: {item.get('instance_types', [])})"
        elif indicator_key == "subscription_count":
            return f"{item.get('protocol', '')}:{item.get('endpoint', '')}"
        # Generic fallback
        return str(item)

    @staticmethod
    def _human_readable_size(size_bytes: int) -> str:
        """Convert bytes to human readable string."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    def _format_change_value(self, value: Any) -> str:
        """Format a change value for HTML display."""
        if isinstance(value, dict) and "expected" in value and "actual" in value:
            result = f"<code>{value['expected']}</code> → <code>{value['actual']}</code>"
            if "details" in value and isinstance(value["details"], list):
                result += "<ul>"
                for item in value["details"][:10]:
                    if isinstance(item, dict):
                        item_str = ", ".join(f"{k}: {v}" for k, v in item.items())
                        result += f"<li><small>{item_str}</small></li>"
                    else:
                        result += f"<li><small>{item}</small></li>"
                if len(value["details"]) > 10:
                    result += f"<li><small>... and {len(value['details']) - 10} more</small></li>"
                result += "</ul>"
            return result
        return str(value)
