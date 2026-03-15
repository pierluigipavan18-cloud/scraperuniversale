"""IndustryStock.com scraper module.

IndustryStock is a pure industrial/manufacturing B2B directory with
300K+ verified businesses and 3.16M+ products across 17 languages.
All contact information is freely available.
"""

from __future__ import annotations

import logging
import re
from typing import AsyncIterator
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .engine import ScraperEngine
from .models import Company

logger = logging.getLogger("scraper.industrystock")

BASE_URL = "https://www.industrystock.com"
SEARCH_URL = f"{BASE_URL}/html/search/search.aspx?Term={{query}}&Page={{page}}"

# Country-specific domains
COUNTRY_URLS = {
    "italy": "https://www.industrystock.it",
    "germany": "https://www.industrystock.de",
    "france": "https://www.industrystock.fr",
    "spain": "https://www.industrystock.es",
}

SEARCH_SELECTORS = {
    "card": "div.company-result, div.search-result, div.result-item, li.result-item",
    "name": "h2 a, h3 a, .company-name a, .result-title a",
    "city": ".company-city, .location, .address",
    "sector": ".company-sector, .category, .industry",
}

DETAIL_SELECTORS = {
    "name": "h1, .company-name, [itemprop='name']",
    "address": "[itemprop='address'], .company-address, .address-block",
    "city": "[itemprop='addressLocality'], .city",
    "country": "[itemprop='addressCountry'], .country",
    "phone": "[itemprop='telephone'], a[href^='tel:'], .phone",
    "email": "a[href^='mailto:'], [itemprop='email'], .email",
    "website": "a[itemprop='url'], a.website, .company-website a",
    "description": "[itemprop='description'], .company-description, .about-text",
    "employees": ".employees, .company-size, [itemprop='numberOfEmployees']",
    "sector": ".main-activity, .industry-sector, .category",
    "fax": ".fax, [itemprop='faxNumber']",
}


class IndustryStockScraper:
    """Scrapes company data from IndustryStock directory."""

    def __init__(
        self,
        engine: ScraperEngine,
        countries: list[str] | None = None,
        max_results: int = 0,
    ):
        self.engine = engine
        self.countries = countries or ["international"]
        self.max_results = max_results

    def _get_base_url(self, country: str) -> str:
        return COUNTRY_URLS.get(country.lower(), BASE_URL)

    async def search(self, keyword: str) -> AsyncIterator[Company]:
        """Search IndustryStock for companies matching keyword."""
        count = 0

        for country in self.countries:
            base = self._get_base_url(country)
            page = 1

            while True:
                if 0 < self.max_results <= count:
                    return

                url = f"{base}/html/search/search.aspx?Term={quote_plus(keyword)}&Page={page}"
                logger.info(f"Fetching {url}")

                try:
                    resp = await self.engine.get(url)
                except Exception as e:
                    logger.error(f"Failed to fetch IndustryStock search: {e}")
                    break

                companies, has_next = self._parse_search(resp.text, base, country)

                if not companies:
                    logger.info(f"No more IndustryStock results for '{keyword}' page {page}")
                    break

                for company in companies:
                    if company.source_url:
                        try:
                            detail = await self._fetch_detail(company.source_url)
                            if detail:
                                company.merge(detail)
                        except Exception as e:
                            logger.warning(f"IndustryStock detail failed: {e}")

                    company.source = "industrystock"
                    yield company
                    count += 1

                    if 0 < self.max_results <= count:
                        return

                if not has_next:
                    break

                page += 1

    def _parse_search(
        self, html: str, base_url: str, country: str
    ) -> tuple[list[Company], bool]:
        """Parse IndustryStock search results."""
        soup = BeautifulSoup(html, "lxml")
        companies: list[Company] = []

        cards = soup.select(SEARCH_SELECTORS["card"])

        if not cards:
            # Fallback: any links to company pages
            for a in soup.find_all("a", href=re.compile(r"/company/|/supplier/|/html/company")):
                name = a.get_text(strip=True)
                if name and len(name) > 2:
                    href = a.get("href", "")
                    if href.startswith("/"):
                        href = f"{base_url}{href}"
                    companies.append(Company(name=name, country=country, source_url=href))

            has_next = bool(soup.find("a", string=re.compile(r"next|weiter|succ", re.I)))
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
                    href = f"{base_url}{href}"

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
                logger.debug(f"Error parsing IndustryStock card: {e}")

        has_next = bool(
            soup.select("a.next, [rel='next'], a.pagination-next")
            or soup.find("a", string=re.compile(r"next|weiter|succ|suivant", re.I))
        )

        return companies, has_next

    async def _fetch_detail(self, url: str) -> Company | None:
        try:
            resp = await self.engine.get(url)
        except Exception:
            return None
        return self._parse_detail(resp.text, url)

    def _parse_detail(self, html: str, url: str) -> Company | None:
        """Parse IndustryStock company detail page."""
        soup = BeautifulSoup(html, "lxml")

        def _sel(key: str) -> str:
            el = soup.select_one(DETAIL_SELECTORS[key])
            if el:
                if el.name == "a" and el.get("href"):
                    href = el["href"]
                    if href.startswith("mailto:"):
                        return href.replace("mailto:", "").split("?")[0].strip()
                    if href.startswith("tel:"):
                        return href.replace("tel:", "").strip()
                    return href
                return el.get_text(strip=True)
            return ""

        name = _sel("name")
        if not name:
            return None

        email = _sel("email")

        email_re = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
        all_emails = list(set(email_re.findall(soup.get_text())))
        filtered = [
            e for e in all_emails
            if not any(x in e.lower() for x in [
                "example.com", "industrystock", "sentry",
                "noreply", "no-reply", "privacy", "cookie",
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
            source="industrystock",
            source_url=url,
            emails_extra=extra,
        )
