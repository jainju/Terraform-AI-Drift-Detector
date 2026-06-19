"""CLI interface for the Terraform drift detector."""

from __future__ import annotations

import json
import logging
import sys

import click
from rich.console import Console

from .config_loader import load_config
from .models import DriftReport
from .output import OutputFormatter
from .providers.base import ProviderRegistry
from .scanner import DriftScanner
from .scheduler import DriftScheduler

console = Console()


def _setup_logging(verbose: bool) -> None:
    """Configure logging based on verbosity."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@click.group()
@click.version_option(version="1.0.0", prog_name="drift-detector")
def cli():
    """Terraform Drift Detector - Identify infrastructure drift without terraform plan."""
    pass


@cli.command()
@click.option("--config", "-c", default=None, help="Path to config.yaml")
@click.option("--state", "-s", default=None, help="Path to terraform.tfstate")
@click.option("--format", "-f", "output_format", default=None,
              type=click.Choice(["table", "json", "html"]),
              help="Output format")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.option("--include", multiple=True, help="Only scan these resource types")
@click.option("--skip", multiple=True, help="Skip these resource types")
def scan(config, state, output_format, verbose, include, skip):
    """Run an on-demand drift scan.

    Examples:
        drift-detector scan
        drift-detector scan --state ./prod.tfstate --format json
        drift-detector scan --include aws_instance --include aws_s3_bucket
    """
    _setup_logging(verbose)

    # Load config
    cfg = load_config(config)

    # CLI overrides
    if state:
        cfg["state"]["path"] = state
    if output_format:
        cfg["output"]["format"] = output_format
    if include:
        cfg["detection"]["include_resources"] = list(include)
    if skip:
        cfg["detection"]["skip_resources"] = list(skip)

    console.print("[blue]Starting drift scan...[/blue]\n")

    # Setup providers
    registry = ProviderRegistry()
    registry.configure_providers(cfg)

    if not registry.list_providers():
        console.print("[yellow]⚠ No cloud providers configured or credentials available.[/yellow]")
        console.print("Configure providers in config.yaml or set cloud credentials.")
        sys.exit(1)

    # Run scan
    scanner = DriftScanner(
        state_path=cfg["state"]["path"],
        registry=registry,
        ignore_attributes=cfg["detection"].get("ignore_attributes", []),
        skip_resources=cfg["detection"].get("skip_resources", []),
        include_resources=cfg["detection"].get("include_resources", []),
    )

    report = scanner.scan()

    # Output results
    formatter = OutputFormatter(report_dir=cfg["output"].get("report_dir", "./reports"))
    formatter.output(report, format=cfg["output"]["format"])

    # Exit code: 1 if drift found, 0 if clean
    sys.exit(1 if report.has_drift() else 0)


@cli.command()
@click.option("--config", "-c", default=None, help="Path to config.yaml")
@click.option("--cron", default=None, help="Cron expression override")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
def watch(config, cron, verbose):
    """Start scheduled drift scanning.

    Runs drift scans on a schedule defined in config or via --cron flag.
    Press Ctrl+C to stop.

    Examples:
        drift-detector watch
        drift-detector watch --cron "0 */2 * * *"
    """
    _setup_logging(verbose)
    cfg = load_config(config)

    cron_expr = cron or cfg["scheduler"].get("cron", "0 */6 * * *")

    console.print(f"[blue]Starting drift watcher with schedule: {cron_expr}[/blue]")
    console.print("Press Ctrl+C to stop.\n")

    # Setup providers
    registry = ProviderRegistry()
    registry.configure_providers(cfg)

    if not registry.list_providers():
        console.print("[yellow]⚠ No cloud providers configured.[/yellow]")
        sys.exit(1)

    def run_scan():
        """Execute a scheduled scan."""
        console.print(f"\n[blue]Running scheduled drift scan...[/blue]")
        scanner = DriftScanner(
            state_path=cfg["state"]["path"],
            registry=registry,
            ignore_attributes=cfg["detection"].get("ignore_attributes", []),
            skip_resources=cfg["detection"].get("skip_resources", []),
            include_resources=cfg["detection"].get("include_resources", []),
        )
        report = scanner.scan()
        formatter = OutputFormatter(report_dir=cfg["output"].get("report_dir", "./reports"))
        # Always save JSON for scheduled scans
        formatter.output(report, format="json")
        formatter.output(report, format="table")

    scheduler = DriftScheduler()
    scheduler.schedule(run_scan, cron_expr)
    scheduler.start()

    console.print(f"[green]Next scan: {scheduler.get_next_run_time()}[/green]")

    try:
        import time
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.stop()
        console.print("\n[yellow]Scheduler stopped.[/yellow]")


@cli.command()
@click.option("--config", "-c", default=None, help="Path to config.yaml")
@click.option("--host", default=None, help="Dashboard host")
@click.option("--port", "-p", default=None, type=int, help="Dashboard port")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
def dashboard(config, host, port, verbose):
    """Start the web dashboard for viewing drift reports.

    Examples:
        drift-detector dashboard
        drift-detector dashboard --port 8080
    """
    _setup_logging(verbose)
    cfg = load_config(config)

    dash_host = host or cfg["dashboard"].get("host", "0.0.0.0")
    dash_port = port or cfg["dashboard"].get("port", 5000)

    console.print(f"[blue]Starting dashboard at http://{dash_host}:{dash_port}[/blue]")

    from .dashboard import create_app
    app = create_app(cfg)
    app.run(host=dash_host, port=dash_port, debug=verbose)


@cli.command()
@click.option("--config", "-c", default=None, help="Path to config.yaml")
def validate(config):
    """Validate configuration and cloud provider credentials.

    Examples:
        drift-detector validate
    """
    cfg = load_config(config)

    console.print("[blue]Validating configuration...[/blue]\n")

    # Check state file
    state_path = cfg["state"]["path"]
    from pathlib import Path
    if Path(state_path).exists():
        console.print(f"  ✓ State file found: {state_path}")
    else:
        console.print(f"  ✗ State file not found: {state_path}", style="red")

    # Check providers
    console.print("\n[blue]Checking providers...[/blue]")
    registry = ProviderRegistry()
    registry.configure_providers(cfg)

    providers = registry.list_providers()
    if providers:
        for p in providers:
            console.print(f"  ✓ Provider '{p}' configured and authenticated")
    else:
        console.print("  ✗ No providers available", style="red")

    console.print("\n[blue]Configuration summary:[/blue]")
    console.print(f"  State backend: {cfg['state']['backend']}")
    console.print(f"  Scheduler: {'enabled' if cfg['scheduler']['enabled'] else 'disabled'}")
    console.print(f"  Dashboard port: {cfg['dashboard']['port']}")
    console.print(f"  Output format: {cfg['output']['format']}")


if __name__ == "__main__":
    cli()
