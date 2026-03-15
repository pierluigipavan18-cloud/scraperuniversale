#!/usr/bin/env python3
"""ScraperUniversale - Enterprise B2B Company Data Scraper.

Extracts company data (name, city, email, phone, website) from
Europages and Kompass directories with email enrichment.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from scraper.engine import ScraperEngine
from scraper.europages import EuropagesScraper
from scraper.kompass import KompassScraper
from scraper.email_enricher import EmailEnricher
from scraper.models import Company
from export.dedup import clean_companies
from export.exporter import export

console = Console()


def load_config(path: str) -> dict:
    """Load YAML configuration file."""
    p = Path(path)
    if not p.exists():
        console.print(f"[yellow]Config file not found: {path}. Using defaults.[/yellow]")
        return {}
    with open(p) as f:
        return yaml.safe_load(f) or {}


def setup_logging(level: str = "INFO") -> None:
    """Configure rich logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )


async def run_scraper(args: argparse.Namespace, config: dict) -> None:
    """Main scraper orchestration."""
    search_cfg = config.get("search", {})
    rate_cfg = config.get("rate_limit", {})
    proxy_cfg = config.get("proxy", {})
    export_cfg = config.get("export", {})
    email_cfg = config.get("email_enrichment", {})

    # Keywords from CLI or config
    keywords = args.keywords or search_cfg.get("keywords", ["manufacturing"])
    countries = args.countries or search_cfg.get("countries", ["international"])
    max_results = args.max_results if args.max_results > 0 else search_cfg.get("max_results", 0)
    sources = args.sources or config.get("sources", ["europages", "kompass"])

    # Proxy setup
    proxies = proxy_cfg.get("list", []) if proxy_cfg.get("enabled") else []

    # Create engine
    engine = ScraperEngine(
        proxies=proxies,
        delay_min=rate_cfg.get("delay_min", 1.0),
        delay_max=rate_cfg.get("delay_max", 3.0),
        backoff_base=rate_cfg.get("backoff_base", 5.0),
        max_retries=rate_cfg.get("max_retries", 3),
    )

    all_companies: list[Company] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        # Scrape each source
        for source in sources:
            for keyword in keywords:
                task_id = progress.add_task(
                    f"[cyan]{source}[/cyan] - '{keyword}'",
                    total=max_results or None,
                )

                if source == "europages":
                    scraper = EuropagesScraper(engine, countries, max_results)
                    async for company in scraper.search(keyword):
                        all_companies.append(company)
                        progress.update(task_id, advance=1)

                elif source == "kompass":
                    scraper = KompassScraper(engine, countries, max_results)
                    async for company in scraper.search(keyword):
                        all_companies.append(company)
                        progress.update(task_id, advance=1)

                progress.update(task_id, completed=True)

        # Email enrichment
        if email_cfg.get("enabled", True):
            companies_without_email = [c for c in all_companies if not c.email and c.website]
            if companies_without_email:
                enricher = EmailEnricher(
                    engine,
                    paths=email_cfg.get("paths"),
                    timeout=email_cfg.get("timeout", 10.0),
                )
                enrich_task = progress.add_task(
                    "[green]Email enrichment[/green]",
                    total=len(companies_without_email),
                )
                for company in companies_without_email:
                    await enricher.enrich(company)
                    progress.update(enrich_task, advance=1)

    await engine.close()

    # Clean and deduplicate
    console.print(f"\n[bold]Raw results:[/bold] {len(all_companies)} companies")
    all_companies = clean_companies(all_companies)
    console.print(f"[bold]After cleaning:[/bold] {len(all_companies)} companies")

    if not all_companies:
        console.print("[red]No companies found. Try different keywords or sources.[/red]")
        return

    # Stats
    with_email = sum(1 for c in all_companies if c.email)
    with_phone = sum(1 for c in all_companies if c.phone)
    with_website = sum(1 for c in all_companies if c.website)

    stats = Table(title="Results Summary")
    stats.add_column("Metric", style="cyan")
    stats.add_column("Value", style="green")
    stats.add_row("Total companies", str(len(all_companies)))
    stats.add_row("With email", f"{with_email} ({100*with_email//max(len(all_companies),1)}%)")
    stats.add_row("With phone", f"{with_phone} ({100*with_phone//max(len(all_companies),1)}%)")
    stats.add_row("With website", f"{with_website} ({100*with_website//max(len(all_companies),1)}%)")
    stats.add_row("HTTP requests", str(engine.request_count))
    console.print(stats)

    # Export
    fmt = args.format or export_cfg.get("format", "csv")
    output_dir = args.output or export_cfg.get("output_dir", "./output")
    filepath = export(all_companies, fmt, output_dir)
    console.print(f"\n[bold green]Exported to:[/bold green] {filepath}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ScraperUniversale - B2B Company Data Scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -k manufacturing energy -c Italy Germany
  %(prog)s -k "solar panels" -s europages --max 100 -f excel
  %(prog)s --config my_config.yaml -k "steel production"
        """,
    )

    parser.add_argument(
        "-k", "--keywords",
        nargs="+",
        help="Search keywords (e.g., 'manufacturing' 'energy' 'steel')",
    )
    parser.add_argument(
        "-c", "--countries",
        nargs="+",
        help="Target countries (e.g., Italy Germany France)",
    )
    parser.add_argument(
        "-s", "--sources",
        nargs="+",
        choices=["europages", "kompass"],
        help="Data sources to use",
    )
    parser.add_argument(
        "--max",
        dest="max_results",
        type=int,
        default=0,
        help="Maximum results per source/keyword (0=unlimited)",
    )
    parser.add_argument(
        "-f", "--format",
        choices=["csv", "json", "excel"],
        help="Export format",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output directory",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Config file path (default: config.yaml)",
    )
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="Skip email enrichment from websites",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose/debug output",
    )

    args = parser.parse_args()

    config = load_config(args.config)

    log_level = "DEBUG" if args.verbose else config.get("logging", {}).get("level", "INFO")
    setup_logging(log_level)

    if args.no_enrich:
        config.setdefault("email_enrichment", {})["enabled"] = False

    console.print("[bold blue]ScraperUniversale[/bold blue] - B2B Company Data Scraper\n")

    try:
        asyncio.run(run_scraper(args, config))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        sys.exit(1)


if __name__ == "__main__":
    main()
