"""wlw.de (Wer Liefert Was) scraper module.

wlw is the leading B2B platform for the DACH region (Germany, Austria,
Switzerland) with 600K+ suppliers, manufacturers, and wholesalers.
High email availability on company profiles.
"""

from __future__ import annotations

import logging
import re
from typing import AsyncIterator
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .engine import ScraperEngine
from .models import Company

logger = logging.getLogger("scraper.wlw")

BASE_URL = "https://www.wlw.de"
SEARCH_URL = f"{BASE_URL}/search?q={{query}}&page={{page}}"

# wlw uses structured data and data-test attributes
SEARCH_SELECTORS = {
    "card": "div[data-test='company-card'], article.company-card, div.search-result-item",
    "name": "h2 a, [data-test='company-name'], .company-name a",
    "city": "[data-test='company-location'], .company-location, .location",
    "sector": "[data-test='company-activity'], .main-activity",
}

DETAIL_SELECTORS = {
    "name": "h1, [data-test='company-title'], [itemprop='name']",
    "address": "[itemprop='address'], [data-test='company-address'], .address",
    "city": "[itemprop='addressLocality']",
    "country": "[itemprop='addressCountry']",
    "phone": "[itemprop='telephone'], a[href^='tel:'], [data-test='phone']",
    "email": "a[href^='mailto:'], [data-test='email'], [itemprop='email']",
    "website": "[data-test='company-external-link'] a, a[data-test='website'], [itemprop='url']",
    "description": "[data-test='company-description'], [itemprop='description'], .company-description",
    "employees": "[data-test='company-employees'], .employees, [itemprop='numberOfEmployees']",
    "sector": "[data-test='company-activity'], .main-activity",
    "fax": "[data-test='fax'], [itemprop='faxNumber']",
}


class WLWScraper:
    """Scrapes company data from wlw.de (Wer Liefert Was)."""

    def __init__(
        self,
        engine: ScraperEngine,
        countries: list[str] | None = None,
        max_results: int = 0,
    ):
        self.engine = engine
        self.countries = countries or ["Germany"]
        self.max_results = max_results

    async def search(self, keyword: str) -> AsyncIterator[Company]:
        """Search wlw.de for companies matching keyword."""
        count = 0
        page = 1

        while True:
            if 0 < self.max_results <= count:
                return

            url = SEARCH_URL.format(query=quote_plus(keyword), page=page)
            logger.info(f"Fetching {url}")

            try:
                resp = await self.engine.get(url)
            except Exception as e:
                logger.error(f"Failed to fetch wlw search: {e}")
                break

            companies, has_next = self._parse_search(resp.text)

            if not companies:
                logger.info(f"No more wlw results for '{keyword}' page {page}")
                break

            for company in companies:
                if company.source_url:
                    try:
                        detail = await self._fetch_detail(company.source_url)
                        if detail:
                            company.merge(detail)
                    except Exception as e:
                        logger.warning(f"wlw detail fetch failed: {e}")

                company.source = "wlw"
                if not company.country:
                    company.country = "Germany"
                yield company
                count += 1

                if 0 < self.max_results <= count:
                    return

            if not has_next:
                break

            page += 1

    def _parse_search(self, html: str) -> tuple[list[Company], bool]:
        """Parse wlw search results page."""
        soup = BeautifulSoup(html, "lxml")
        companies: list[Company] = []

        cards = soup.select(SEARCH_SELECTORS["card"])

        if not cards:
            # Fallback: look for company links
            for a in soup.find_all("a", href=re.compile(r"/supplier/|/company/")):
                name = a.get_text(strip=True)
                if name and len(name) > 2:
                    href = a.get("href", "")
                    if href.startswith("/"):
                        href = f"{BASE_URL}{href}"
                    companies.append(Company(name=name, source_url=href))
            has_next = bool(soup.find("a", string=re.compile(r"next|weiter|nächste", re.I)))
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
                    href = f"{BASE_URL}{href}"

                city_el = card.select_one(SEARCH_SELECTORS["city"])
                city = city_el.get_text(strip=True) if city_el else ""

                sector_el = card.select_one(SEARCH_SELECTORS["sector"])
                sector = sector_el.get_text(strip=True) if sector_el else ""

                companies.append(Company(
                    name=name,
                    city=city,
                    sector=sector,
                    source_url=href,
                ))
            except Exception as e:
                logger.debug(f"Error parsing wlw card: {e}")

        has_next = bool(
            soup.select("a[data-test='next-page'], a.next, [rel='next']")
            or soup.find("a", string=re.compile(r"next|weiter|nächste", re.I))
        )

        return companies, has_next

    async def _fetch_detail(self, url: str) -> Company | None:
        """Fetch company detail page from wlw."""
        try:
            resp = await self.engine.get(url)
        except Exception:
            return None

        return self._parse_detail(resp.text, url)

    def _parse_detail(self, html: str, url: str) -> Company | None:
        """Parse wlw company detail page."""
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

        # Extract emails via regex
        email_re = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
        all_emails = list(set(email_re.findall(soup.get_text())))
        filtered = [
            e for e in all_emails
            if not any(x in e.lower() for x in [
                "example.com", "wlw.de", "visable.com", "sentry",
                "noreply", "no-reply", "privacy", "cookie", "webpack",
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
            country=_sel("country") or "Germany",
            phone=_sel("phone"),
            email=email,
            fax=_sel("fax"),
            website=website,
            description=_sel("description"),
            employees=_sel("employees"),
            sector=_sel("sector"),
            source="wlw",
            source_url=url,
            emails_extra=extra,
        )
