#!/usr/bin/env python3
"""ScraperUniversale - Enterprise B2B Company Data Scraper.

Extracts company data (name, city, email, phone, website) from
multiple B2B directories with email enrichment.

Sources: Europages, Kompass, wlw.de, IndustryStock, PagineGialle, CSEA Energivori
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
from rich.panel import Panel

from scraper.engine import ScraperEngine
from scraper.europages import EuropagesScraper
from scraper.kompass import KompassScraper
from scraper.wlw import WLWScraper
from scraper.industrystock import IndustryStockScraper
from scraper.paginegialle import PagineGialleScraper
from scraper.csea_energivori import CSEAEnergivoriScraper
from scraper.email_enricher import EmailEnricher
from scraper.models import Company
from export.dedup import clean_companies
from export.exporter import export

console = Console()

ALL_SOURCES = ["europages", "kompass", "wlw", "industrystock", "paginegialle", "csea_energivori"]


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


def _create_scraper(source: str, engine: ScraperEngine, countries: list[str], max_results: int):
    """Factory: create the right scraper for a given source name."""
    if source == "europages":
        return EuropagesScraper(engine, countries, max_results)
    elif source == "kompass":
        return KompassScraper(engine, countries, max_results)
    elif source == "wlw":
        return WLWScraper(engine, countries, max_results)
    elif source == "industrystock":
        return IndustryStockScraper(engine, countries, max_results)
    elif source == "paginegialle":
        # PagineGialle uses Italian city names as locations, not country names.
        # Filter out generic country names and pass only city-like locations.
        locations = [c for c in countries if c.lower() not in (
            "italy", "italia", "germany", "france", "spain", "international",
        )] or [""]
        return PagineGialleScraper(engine, locations=locations, max_results=max_results)
    elif source == "csea_energivori":
        return CSEAEnergivoriScraper(engine, max_results=max_results)
    else:
        raise ValueError(f"Unknown source: {source}")


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

    # Show config summary
    config_table = Table(title="Scraper Configuration", show_header=False)
    config_table.add_column("Key", style="cyan")
    config_table.add_column("Value", style="white")
    config_table.add_row("Sources", ", ".join(sources))
    config_table.add_row("Keywords", ", ".join(keywords))
    config_table.add_row("Countries", ", ".join(countries))
    config_table.add_row("Max results", str(max_results) if max_results > 0 else "unlimited")
    config_table.add_row("Email enrichment", "ON" if email_cfg.get("enabled", True) else "OFF")
    config_table.add_row("Proxies", str(len(proxies)) if proxies else "none")
    console.print(config_table)
    console.print()

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
            scraper = _create_scraper(source, engine, countries, max_results)

            for keyword in keywords:
                task_id = progress.add_task(
                    f"[cyan]{source}[/cyan] - '{keyword}'",
                    total=max_results or None,
                )

                async for company in scraper.search(keyword):
                    all_companies.append(company)
                    progress.update(task_id, advance=1)

                progress.update(task_id, visible=False)

        # Email enrichment
        if email_cfg.get("enabled", True) and not args.no_enrich:
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
    sources_used = set(c.source for c in all_companies)

    stats = Table(title="Results Summary")
    stats.add_column("Metric", style="cyan")
    stats.add_column("Value", style="green")
    stats.add_row("Total companies", str(len(all_companies)))
    stats.add_row("With email", f"{with_email} ({100*with_email//max(len(all_companies),1)}%)")
    stats.add_row("With phone", f"{with_phone} ({100*with_phone//max(len(all_companies),1)}%)")
    stats.add_row("With website", f"{with_website} ({100*with_website//max(len(all_companies),1)}%)")
    stats.add_row("Sources hit", ", ".join(sources_used))
    stats.add_row("HTTP requests", str(engine.request_count))
    console.print(stats)

    # Per-source breakdown
    source_counts = {}
    for c in all_companies:
        source_counts[c.source] = source_counts.get(c.source, 0) + 1
    if len(source_counts) > 1:
        breakdown = Table(title="Per-Source Breakdown")
        breakdown.add_column("Source", style="cyan")
        breakdown.add_column("Companies", style="green")
        breakdown.add_column("With Email", style="yellow")
        for src, cnt in sorted(source_counts.items(), key=lambda x: -x[1]):
            src_email = sum(1 for c in all_companies if c.source == src and c.email)
            breakdown.add_row(src, str(cnt), f"{src_email} ({100*src_email//max(cnt,1)}%)")
        console.print(breakdown)

    # Export
    fmt = args.format or export_cfg.get("format", "csv")
    output_dir = args.output or export_cfg.get("output_dir", "./output")
    filepath = export(all_companies, fmt, output_dir)
    console.print(f"\n[bold green]Exported to:[/bold green] {filepath}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ScraperUniversale - B2B Company Data Scraper (6 sources)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Sources available:
  europages       2.6M+ EU B2B companies (best email coverage)
  kompass         57M+ companies in 70+ countries (NACE classification)
  wlw             600K+ DACH manufacturers/suppliers
  industrystock   300K+ verified industrial companies
  paginegialle    Italian Yellow Pages (full Italy coverage)
  csea_energivori Official Italian energy-intensive companies registry (~4000)

Examples:
  %(prog)s -k manufacturing energy -c Italy Germany
  %(prog)s -k "solar panels" -s europages --max 100 -f excel
  %(prog)s -s csea_energivori -f excel
  %(prog)s -k "steel" -s europages kompass wlw industrystock -c Italy Germany
  %(prog)s -k "produzione" -s paginegialle -c Milano Roma Torino
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
        help="Target countries/locations (e.g., Italy Germany France)",
    )
    parser.add_argument(
        "-s", "--sources",
        nargs="+",
        choices=ALL_SOURCES,
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

    console.print(Panel.fit(
        "[bold blue]ScraperUniversale[/bold blue]\n"
        "[dim]Enterprise B2B Company Data Scraper - 6 Sources[/dim]",
        border_style="blue",
    ))
    console.print()

    try:
        asyncio.run(run_scraper(args, config))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        sys.exit(1)


if __name__ == "__main__":
    main()
