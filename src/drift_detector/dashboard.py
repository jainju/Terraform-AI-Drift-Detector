"""Flask-based web dashboard for viewing drift reports."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template_string

from .models import DriftReport
from .output import OutputFormatter
from .providers.base import ProviderRegistry
from .scanner import DriftScanner


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Terraform Drift Detector</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f0f23; color: #ccc; min-height: 100vh; }
        .header { background: #1a1a2e; padding: 1.5rem 2rem; border-bottom: 1px solid #16213e; display: flex; justify-content: space-between; align-items: center; }
        .header h1 { color: #00d4aa; font-size: 1.4rem; }
        .header .actions { display: flex; gap: 1rem; }
        .btn { padding: 0.5rem 1rem; border: none; border-radius: 6px; cursor: pointer; font-size: 0.85rem; font-weight: 600; transition: all 0.2s; }
        .btn-primary { background: #00d4aa; color: #0f0f23; }
        .btn-primary:hover { background: #00f5c4; }
        .btn-secondary { background: #16213e; color: #ccc; border: 1px solid #333; }
        .btn-secondary:hover { background: #1f3460; }
        .container { max-width: 1400px; margin: 0 auto; padding: 2rem; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
        .stat-card { background: #1a1a2e; border-radius: 8px; padding: 1.5rem; border: 1px solid #16213e; }
        .stat-card .value { font-size: 2.5rem; font-weight: bold; margin-bottom: 0.25rem; }
        .stat-card .label { color: #888; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 1px; }
        .stat-card.drift .value { color: #ff6b6b; }
        .stat-card.clean .value { color: #00d4aa; }
        .stat-card.warning .value { color: #ffd93d; }
        .stat-card.info .value { color: #6c5ce7; }
        .section { background: #1a1a2e; border-radius: 8px; border: 1px solid #16213e; margin-bottom: 2rem; overflow: hidden; }
        .section-header { padding: 1rem 1.5rem; border-bottom: 1px solid #16213e; font-weight: 600; display: flex; justify-content: space-between; align-items: center; }
        table { width: 100%; border-collapse: collapse; }
        th { text-align: left; padding: 0.75rem 1.5rem; color: #888; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 1px; border-bottom: 1px solid #16213e; }
        td { padding: 0.75rem 1.5rem; border-bottom: 1px solid #16213e; font-size: 0.9rem; }
        tr:hover { background: #16213e; }
        .badge { display: inline-block; padding: 0.2rem 0.6rem; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
        .badge-deleted { background: rgba(255, 107, 107, 0.2); color: #ff6b6b; }
        .badge-modified { background: rgba(255, 217, 61, 0.2); color: #ffd93d; }
        .badge-tag_changed { background: rgba(108, 92, 231, 0.2); color: #a29bfe; }
        .badge-critical { background: rgba(255, 71, 87, 0.2); color: #ff4757; }
        .badge-high { background: rgba(255, 107, 107, 0.2); color: #ff6b6b; }
        .badge-medium { background: rgba(255, 217, 61, 0.2); color: #ffd93d; }
        .badge-low { background: rgba(0, 212, 170, 0.2); color: #00d4aa; }
        .changes { font-family: 'Fira Code', monospace; font-size: 0.8rem; color: #aaa; max-width: 400px; }
        .empty-state { text-align: center; padding: 4rem 2rem; color: #666; }
        .empty-state .icon { font-size: 3rem; margin-bottom: 1rem; }
        .loading { text-align: center; padding: 2rem; color: #888; }
        #scan-status { margin-left: 0.5rem; font-size: 0.8rem; }
        .report-list { padding: 1rem 1.5rem; }
        .report-item { display: flex; justify-content: space-between; align-items: center; padding: 0.75rem; border-radius: 6px; margin-bottom: 0.5rem; cursor: pointer; transition: background 0.2s; }
        .report-item:hover { background: #16213e; }
    </style>
</head>
<body>
    <div class="header">
        <h1>⚡ Terraform Drift Detector</h1>
        <div class="actions">
            <button class="btn btn-primary" onclick="triggerScan()">Run Scan</button>
            <button class="btn btn-secondary" onclick="refreshReports()">Refresh</button>
        </div>
    </div>
    <div class="container">
        <div class="stats" id="stats">
            <div class="stat-card info">
                <div class="value" id="total-resources">-</div>
                <div class="label">Resources Tracked</div>
            </div>
            <div class="stat-card drift">
                <div class="value" id="total-drifts">-</div>
                <div class="label">Total Drifts</div>
            </div>
            <div class="stat-card warning">
                <div class="value" id="modified-count">-</div>
                <div class="label">Modified</div>
            </div>
            <div class="stat-card drift">
                <div class="value" id="deleted-count">-</div>
                <div class="label">Deleted</div>
            </div>
        </div>

        <div class="section">
            <div class="section-header">
                <span>Drift Findings</span>
                <span id="scan-status" style="color: #888; font-size: 0.85rem;"></span>
            </div>
            <div id="drift-table">
                <div class="empty-state">
                    <div class="icon">🔍</div>
                    <p>No scan results yet. Click "Run Scan" to start.</p>
                </div>
            </div>
        </div>

        <div class="section">
            <div class="section-header">Recent Reports</div>
            <div class="report-list" id="report-list">
                <div class="loading">Loading reports...</div>
            </div>
        </div>
    </div>

    <script>
        async function triggerScan() {
            document.getElementById('scan-status').textContent = 'Scanning...';
            try {
                const resp = await fetch('/api/scan', { method: 'POST' });
                const data = await resp.json();
                displayReport(data);
                refreshReports();
            } catch (err) {
                document.getElementById('scan-status').textContent = 'Error: ' + err.message;
            }
        }

        async function refreshReports() {
            try {
                const resp = await fetch('/api/reports');
                const data = await resp.json();
                displayReportList(data.reports);
                if (data.reports.length > 0) {
                    loadReport(data.reports[0]);
                }
            } catch (err) {
                console.error(err);
            }
        }

        async function loadReport(filename) {
            try {
                const resp = await fetch('/api/reports/' + filename);
                const data = await resp.json();
                displayReport(data);
            } catch (err) {
                console.error(err);
            }
        }

        function displayReport(data) {
            const summary = data.summary || data;
            document.getElementById('total-resources').textContent = summary.total_resources_in_state || 0;
            document.getElementById('total-drifts').textContent = summary.total_drifts || 0;
            document.getElementById('modified-count').textContent = summary.modified || 0;
            document.getElementById('deleted-count').textContent = summary.deleted || 0;
            document.getElementById('scan-status').textContent = 'Last scan: ' + (summary.timestamp || 'N/A');

            const drifts = data.drifts || [];
            if (drifts.length === 0) {
                document.getElementById('drift-table').innerHTML = '<div class="empty-state"><div class="icon">✅</div><p>No drift detected. Infrastructure matches state.</p></div>';
                return;
            }

            let html = '<table><thead><tr><th>Type</th><th>Severity</th><th>Resource</th><th>Name</th><th>Changes</th></tr></thead><tbody>';
            for (const d of drifts) {
                const changesText = Object.entries(d.changes || {}).map(([k, v]) => {
                    if (v && v.expected !== undefined) return k + ': ' + v.expected + ' → ' + v.actual;
                    return k + ': ' + JSON.stringify(v);
                }).join('<br>');
                html += '<tr>';
                html += '<td><span class="badge badge-' + d.drift_type + '">' + d.drift_type.toUpperCase() + '</span></td>';
                html += '<td><span class="badge badge-' + d.severity + '">' + d.severity.toUpperCase() + '</span></td>';
                html += '<td>' + d.resource_type + '</td>';
                html += '<td>' + (d.resource_name || d.resource_id) + '</td>';
                html += '<td class="changes">' + changesText + '</td>';
                html += '</tr>';
            }
            html += '</tbody></table>';
            document.getElementById('drift-table').innerHTML = html;
        }

        function displayReportList(reports) {
            if (!reports || reports.length === 0) {
                document.getElementById('report-list').innerHTML = '<div class="empty-state"><p>No reports saved yet.</p></div>';
                return;
            }
            let html = '';
            for (const r of reports.slice(0, 10)) {
                html += '<div class="report-item" onclick="loadReport(\\'' + r + '\\')">';
                html += '<span>' + r + '</span>';
                html += '</div>';
            }
            document.getElementById('report-list').innerHTML = html;
        }

        // Load on page ready
        refreshReports();
    </script>
</body>
</html>"""


def create_app(config: dict[str, Any]) -> Flask:
    """Create the Flask dashboard application."""
    app = Flask(__name__)
    app.config["DRIFT_CONFIG"] = config

    report_dir = Path(config.get("output", {}).get("report_dir", "./reports"))
    report_dir.mkdir(parents=True, exist_ok=True)

    @app.route("/")
    def index():
        return render_template_string(DASHBOARD_HTML)

    @app.route("/api/scan", methods=["POST"])
    def api_scan():
        """Trigger an on-demand scan and return results."""
        cfg = app.config["DRIFT_CONFIG"]

        registry = ProviderRegistry()
        registry.configure_providers(cfg)

        if not registry.list_providers():
            return jsonify({"error": "No cloud providers configured"}), 500

        scanner = DriftScanner(
            state_path=cfg["state"]["path"],
            registry=registry,
            ignore_attributes=cfg["detection"].get("ignore_attributes", []),
            skip_resources=cfg["detection"].get("skip_resources", []),
            include_resources=cfg["detection"].get("include_resources", []),
        )

        report = scanner.scan()

        # Save report as JSON
        formatter = OutputFormatter(report_dir=str(report_dir))
        formatter._output_json(report)

        return jsonify({
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
                }
                for d in report.drifts
            ],
        })

    @app.route("/api/reports")
    def api_reports():
        """List available drift reports."""
        reports = []
        if report_dir.exists():
            reports = sorted(
                [f.name for f in report_dir.glob("drift_report_*.json")],
                reverse=True,
            )
        return jsonify({"reports": reports})

    @app.route("/api/reports/<filename>")
    def api_report(filename):
        """Get a specific drift report."""
        filepath = report_dir / filename
        if not filepath.exists():
            return jsonify({"error": "Report not found"}), 404

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data)

    @app.route("/api/health")
    def api_health():
        """Health check endpoint."""
        return jsonify({"status": "healthy", "version": "1.0.0"})

    return app
