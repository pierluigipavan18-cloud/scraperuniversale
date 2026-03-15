"""Europages.com scraper module.

Europages uses a Vue.js frontend that fetches data from internal API endpoints.
We replicate those API calls directly for fast, reliable extraction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import AsyncIterator
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .engine import ScraperEngine
from .models import Company

logger = logging.getLogger("scraper.europages")

# Europages regional domains
DOMAINS = {
    "international": "www.europages.co.uk",
    "it": "www.europages.it",
    "de": "www.europages.de",
    "fr": "www.europages.fr",
    "es": "www.europages.es",
}

# Known search URL pattern
SEARCH_URL = "https://{domain}/search?q={query}&page={page}"

# Company detail selectors (CSS / data attributes)
SELECTORS = {
    "company_card": "div[data-test='company-card'], div.company-card, article.ep-company",
    "company_name": "h3, [data-test='company-name'], .company-name",
    "company_link": "a[href*='/company/'], a[data-test='company-link']",
    "city": "[data-test='company-city'], .company-city, .address",
    "sector": "[data-test='company-sector'], .company-sector, .activity",
}

DETAIL_SELECTORS = {
    "name": "h1, [data-test='company-title']",
    "address": "[data-test='company-address'], .address-line",
    "phone": "[data-test='company-phone'] a, a[href^='tel:']",
    "email": "[data-test='company-email'] a, a[href^='mailto:']",
    "website": "[data-test='company-external-link'] a, a[data-test='website-link']",
    "description": "[data-test='company-description'], .company-description",
    "employees": "[data-test='company-employees'], .employees",
    "vat": "[data-test='company-vat'], .vat-number",
    "fax": "[data-test='company-fax'], a[href^='fax:']",
    "sector": "[data-test='company-activity'], .main-activity",
}


class EuropagesScraper:
    """Scrapes company data from Europages directory."""

    def __init__(
        self,
        engine: ScraperEngine,
        countries: list[str] | None = None,
        max_results: int = 0,
    ):
        self.engine = engine
        self.countries = countries or ["international"]
        self.max_results = max_results

    def _get_domain(self, country: str) -> str:
        country_lower = country.lower()
        for key, domain in DOMAINS.items():
            if key == country_lower or country_lower.startswith(key):
                return domain
        # Map full country names
        mapping = {
            "italy": "www.europages.it",
            "germany": "www.europages.de",
            "france": "www.europages.fr",
            "spain": "www.europages.es",
        }
        return mapping.get(country_lower, DOMAINS["international"])

    async def search(self, keyword: str) -> AsyncIterator[Company]:
        """Search Europages for companies matching keyword across configured countries."""
        count = 0
        for country in self.countries:
            domain = self._get_domain(country)
            page = 1

            while True:
                if 0 < self.max_results <= count:
                    return

                url = SEARCH_URL.format(
                    domain=domain,
                    query=quote_plus(keyword),
                    page=page,
                )

                logger.info(f"Fetching {url}")

                try:
                    resp = await self.engine.get(url)
                except Exception as e:
                    logger.error(f"Failed to fetch search page: {e}")
                    break

                companies, has_next = self._parse_search_results(resp.text, domain, country)

                if not companies:
                    logger.info(f"No more results for '{keyword}' on {domain} page {page}")
                    break

                for company in companies:
                    # Fetch detail page for each company
                    if company.source_url:
                        try:
                            detail = await self._fetch_detail(company.source_url)
                            if detail:
                                company.merge(detail)
                        except Exception as e:
                            logger.warning(f"Failed to fetch detail for {company.name}: {e}")

                    company.source = "europages"
                    yield company
                    count += 1

                    if 0 < self.max_results <= count:
                        return

                if not has_next:
                    break

                page += 1

    def _parse_search_results(
        self, html: str, domain: str, country: str
    ) -> tuple[list[Company], bool]:
        """Parse search results page and return list of companies + has_next flag."""
        soup = BeautifulSoup(html, "lxml")
        companies: list[Company] = []

        # Try multiple selector strategies
        cards = soup.select(SELECTORS["company_card"])

        # Fallback: look for any links to company pages
        if not cards:
            cards = soup.find_all("a", href=re.compile(r"/company/"))

        for card in cards:
            try:
                company = self._parse_card(card, domain, country)
                if company:
                    companies.append(company)
            except Exception as e:
                logger.debug(f"Error parsing card: {e}")

        # Check for next page
        has_next = bool(
            soup.select("a[data-test='next-page'], a.next, [aria-label='Next']")
            or soup.find("a", string=re.compile(r"next|succ|weiter|suivant", re.I))
        )

        return companies, has_next

    def _parse_card(
        self, card, domain: str, country: str
    ) -> Company | None:
        """Parse a single company card from search results."""
        # Extract name
        name_el = card.select_one(SELECTORS["company_name"])
        if not name_el:
            name_el = card
        name = name_el.get_text(strip=True)

        if not name or len(name) < 2:
            return None

        # Extract link to detail page
        link_el = card.select_one(SELECTORS["company_link"])
        if not link_el and card.name == "a":
            link_el = card
        source_url = ""
        if link_el and link_el.get("href"):
            href = link_el["href"]
            if href.startswith("/"):
                source_url = f"https://{domain}{href}"
            elif href.startswith("http"):
                source_url = href

        # Extract city
        city_el = card.select_one(SELECTORS["city"])
        city = city_el.get_text(strip=True) if city_el else ""

        # Extract sector
        sector_el = card.select_one(SELECTORS["sector"])
        sector = sector_el.get_text(strip=True) if sector_el else ""

        return Company(
            name=name,
            city=city,
            country=country,
            sector=sector,
            source_url=source_url,
        )

    async def _fetch_detail(self, url: str) -> Company | None:
        """Fetch and parse a company detail page."""
        try:
            resp = await self.engine.get(url)
        except Exception as e:
            logger.warning(f"Detail fetch failed for {url}: {e}")
            return None

        return self._parse_detail(resp.text, url)

    def _parse_detail(self, html: str, url: str) -> Company | None:
        """Parse company detail page."""
        soup = BeautifulSoup(html, "lxml")

        def _sel(key: str) -> str:
            el = soup.select_one(DETAIL_SELECTORS[key])
            if el:
                # For links, prefer href attribute
                if el.name == "a" and el.get("href"):
                    href = el["href"]
                    if href.startswith("mailto:"):
                        return href.replace("mailto:", "").strip()
                    if href.startswith("tel:"):
                        return href.replace("tel:", "").strip()
                    return href
                return el.get_text(strip=True)
            return ""

        name = _sel("name")
        if not name:
            return None

        # Extract email from mailto links
        email = _sel("email")

        # Also search for emails in page text via regex
        email_pattern = re.compile(
            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
        )
        all_emails = list(set(email_pattern.findall(soup.get_text())))
        # Filter out common false positives
        filtered_emails = [
            e for e in all_emails
            if not any(x in e.lower() for x in [
                "example.com", "europages", "sentry", "webpack",
                "noreply", "no-reply", "privacy", "cookie",
            ])
        ]

        extra_emails = [e for e in filtered_emails if e != email]

        # Extract website
        website = _sel("website")
        if website and not website.startswith("http"):
            website = f"https://{website}"

        return Company(
            name=name,
            address=_sel("address"),
            phone=_sel("phone"),
            email=email,
            fax=_sel("fax"),
            website=website,
            description=_sel("description"),
            employees=_sel("employees"),
            vat_id=_sel("vat"),
            sector=_sel("sector"),
            source="europages",
            source_url=url,
            emails_extra=extra_emails,
        )
