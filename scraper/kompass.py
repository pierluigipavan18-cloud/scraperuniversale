"""Kompass.com scraper module.

Kompass is one of the largest B2B directories (57M+ companies in 70+ countries).
Uses HTML parsing with BeautifulSoup.
"""

from __future__ import annotations

import logging
import re
from typing import AsyncIterator
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .engine import ScraperEngine
from .models import Company

logger = logging.getLogger("scraper.kompass")

BASE_URL = "https://www.kompass.com"

# Country-specific Kompass domains
COUNTRY_DOMAINS = {
    "italy": "it.kompass.com",
    "germany": "de.kompass.com",
    "france": "fr.kompass.com",
    "spain": "es.kompass.com",
}

# Selectors for search results
SEARCH_SELECTORS = {
    "card": "div.product-list--item, div.company-item, li.search-result-item",
    "name": "h2 a, h3 a, .company-name a, a.product-list--item-title",
    "city": ".company-city, .address, .location",
    "sector": ".company-activity, .activity, .sector",
}

# Selectors for company detail pages
DETAIL_SELECTORS = {
    "name": "h1.companyName, h1, [itemprop='name']",
    "address": "[itemprop='address'], .address-block, .company-address",
    "city": "[itemprop='addressLocality']",
    "country": "[itemprop='addressCountry']",
    "phone": "[itemprop='telephone'], a[href^='tel:'], .phone-number",
    "email": "[itemprop='email'], a[href^='mailto:'], .email-link",
    "website": "a[itemprop='url'], a.website-link, a[data-tracking='website']",
    "description": "[itemprop='description'], .company-description, .presentation",
    "employees": ".employees, .company-size, [itemprop='numberOfEmployees']",
    "sector": ".main-activity, .nace-code, .classification",
    "fax": ".fax-number, [itemprop='faxNumber']",
}


class KompassScraper:
    """Scrapes company data from Kompass directory."""

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
        return COUNTRY_DOMAINS.get(country.lower(), "www.kompass.com")

    async def search(self, keyword: str) -> AsyncIterator[Company]:
        """Search Kompass for companies matching keyword."""
        count = 0

        for country in self.countries:
            domain = self._get_domain(country)
            page = 1

            while True:
                if 0 < self.max_results <= count:
                    return

                url = f"https://{domain}/searchCompanies?text={quote_plus(keyword)}&page={page}"
                logger.info(f"Fetching {url}")

                try:
                    resp = await self.engine.get(url)
                except Exception as e:
                    logger.error(f"Failed to fetch Kompass search: {e}")
                    break

                companies, has_next = self._parse_search(resp.text, domain, country)

                if not companies:
                    logger.info(f"No more Kompass results for '{keyword}' on {domain} page {page}")
                    break

                for company in companies:
                    if company.source_url:
                        try:
                            detail = await self._fetch_detail(company.source_url)
                            if detail:
                                company.merge(detail)
                        except Exception as e:
                            logger.warning(f"Kompass detail fetch failed: {e}")

                    company.source = "kompass"
                    yield company
                    count += 1

                    if 0 < self.max_results <= count:
                        return

                if not has_next:
                    break

                page += 1

    def _parse_search(
        self, html: str, domain: str, country: str
    ) -> tuple[list[Company], bool]:
        """Parse Kompass search results."""
        soup = BeautifulSoup(html, "lxml")
        companies: list[Company] = []

        cards = soup.select(SEARCH_SELECTORS["card"])

        # Fallback: look for company links
        if not cards:
            links = soup.find_all("a", href=re.compile(r"/company/"))
            for link in links:
                name = link.get_text(strip=True)
                if name and len(name) > 2:
                    href = link.get("href", "")
                    if href.startswith("/"):
                        href = f"https://{domain}{href}"
                    companies.append(Company(
                        name=name,
                        country=country,
                        source_url=href,
                    ))
            has_next = bool(soup.find("a", class_=re.compile(r"next")))
            return companies, has_next

        for card in cards:
            try:
                name_el = card.select_one(SEARCH_SELECTORS["name"])
                if not name_el:
                    continue

                name = name_el.get_text(strip=True)
                if not name or len(name) < 2:
                    continue

                href = name_el.get("href", "")
                if href.startswith("/"):
                    href = f"https://{domain}{href}"

                city_el = card.select_one(SEARCH_SELECTORS["city"])
                city = city_el.get_text(strip=True) if city_el else ""

                sector_el = card.select_one(SEARCH_SELECTORS["sector"])
                sector = sector_el.get_text(strip=True) if sector_el else ""

                companies.append(Company(
                    name=name,
                    city=city,
                    country=country,
                    sector=sector,
                    source_url=href,
                ))
            except Exception as e:
                logger.debug(f"Error parsing Kompass card: {e}")

        # Check pagination
        has_next = bool(
            soup.select("a.next, a[rel='next'], li.next a")
            or soup.find("a", string=re.compile(r"next|succ|weiter|suivant", re.I))
        )

        return companies, has_next

    async def _fetch_detail(self, url: str) -> Company | None:
        """Fetch company detail page from Kompass."""
        try:
            resp = await self.engine.get(url)
        except Exception:
            return None

        return self._parse_detail(resp.text, url)

    def _parse_detail(self, html: str, url: str) -> Company | None:
        """Parse Kompass company detail page."""
        soup = BeautifulSoup(html, "lxml")

        def _sel(key: str) -> str:
            el = soup.select_one(DETAIL_SELECTORS[key])
            if el:
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

        email = _sel("email")

        # Extract emails from page text
        email_re = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
        all_emails = list(set(email_re.findall(soup.get_text())))
        filtered = [
            e for e in all_emails
            if not any(x in e.lower() for x in [
                "example.com", "kompass", "sentry", "noreply", "no-reply",
                "privacy", "cookie", "webpack",
            ])
        ]
        extra = [e for e in filtered if e != email]

        website = _sel("website")
        if website and not website.startswith("http"):
            website = f"https://{website}"

        return Company(
            name=name,
            address=_sel("address"),
            city=_sel("city"),
            country=_sel("country"),
            phone=_sel("phone"),
            email=email,
            fax=_sel("fax"),
            website=website,
            description=_sel("description"),
            employees=_sel("employees"),
            sector=_sel("sector"),
            source="kompass",
            source_url=url,
            emails_extra=extra,
        )
