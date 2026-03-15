"""PagineGialle.it scraper module.

Pagine Gialle (Italian Yellow Pages) - comprehensive Italian business
directory covering all business types including manufacturers.
"""

from __future__ import annotations

import logging
import re
from typing import AsyncIterator
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .engine import ScraperEngine
from .models import Company

logger = logging.getLogger("scraper.paginegialle")

BASE_URL = "https://www.paginegialle.it"
SEARCH_URL = f"{BASE_URL}/ricerca/{{query}}/{{location}}?page={{page}}"
SEARCH_URL_NO_LOC = f"{BASE_URL}/ricerca/{{query}}?page={{page}}"

SEARCH_SELECTORS = {
    "card": "div.vcard, div.search-result, article.result-item, div[data-pag]",
    "name": "h2 a, .org a, .company-name a, [itemprop='name'] a",
    "address": ".adr, .address, [itemprop='address']",
    "city": ".locality, [itemprop='addressLocality']",
    "phone": ".tel a, [itemprop='telephone'], a[href^='tel:']",
    "category": ".category, .sector",
}

DETAIL_SELECTORS = {
    "name": "h1, .org, [itemprop='name']",
    "address": "[itemprop='streetAddress'], .street-address, .adr",
    "city": "[itemprop='addressLocality'], .locality",
    "phone": "[itemprop='telephone'], a[href^='tel:'], .tel",
    "email": "a[href^='mailto:'], [itemprop='email']",
    "website": "a[itemprop='url'], a.website-link, a[data-pag='website']",
    "description": "[itemprop='description'], .company-description",
    "sector": ".category-main, .activity",
    "fax": "[itemprop='faxNumber'], .fax",
}


class PagineGialleScraper:
    """Scrapes company data from Pagine Gialle (Italian Yellow Pages)."""

    def __init__(
        self,
        engine: ScraperEngine,
        locations: list[str] | None = None,
        max_results: int = 0,
    ):
        self.engine = engine
        self.locations = locations or [""]
        self.max_results = max_results

    async def search(self, keyword: str) -> AsyncIterator[Company]:
        """Search PagineGialle for companies matching keyword."""
        count = 0

        locations = self.locations if self.locations else [""]

        for location in locations:
            page = 1

            while True:
                if 0 < self.max_results <= count:
                    return

                if location:
                    url = SEARCH_URL.format(
                        query=quote_plus(keyword),
                        location=quote_plus(location),
                        page=page,
                    )
                else:
                    url = SEARCH_URL_NO_LOC.format(
                        query=quote_plus(keyword),
                        page=page,
                    )

                logger.info(f"Fetching {url}")

                try:
                    resp = await self.engine.get(url)
                except Exception as e:
                    logger.error(f"Failed to fetch PagineGialle: {e}")
                    break

                companies, has_next = self._parse_search(resp.text)

                if not companies:
                    logger.info(f"No more PagineGialle results for '{keyword}' in '{location}' page {page}")
                    break

                for company in companies:
                    if company.source_url:
                        try:
                            detail = await self._fetch_detail(company.source_url)
                            if detail:
                                company.merge(detail)
                        except Exception as e:
                            logger.warning(f"PagineGialle detail failed: {e}")

                    company.source = "paginegialle"
                    company.country = "Italy"
                    yield company
                    count += 1

                    if 0 < self.max_results <= count:
                        return

                if not has_next:
                    break

                page += 1

    def _parse_search(self, html: str) -> tuple[list[Company], bool]:
        """Parse PagineGialle search results page."""
        soup = BeautifulSoup(html, "lxml")
        companies: list[Company] = []

        cards = soup.select(SEARCH_SELECTORS["card"])

        if not cards:
            # Fallback: microdata / schema.org
            for item in soup.find_all(attrs={"itemtype": re.compile(r"schema.org/LocalBusiness|Organization")}):
                name_el = item.find(attrs={"itemprop": "name"})
                if not name_el:
                    continue
                name = name_el.get_text(strip=True)
                if not name:
                    continue

                link = name_el.find_parent("a") or item.find("a")
                href = ""
                if link and link.get("href"):
                    href = link["href"]
                    if href.startswith("/"):
                        href = f"{BASE_URL}{href}"

                city_el = item.find(attrs={"itemprop": "addressLocality"})
                phone_el = item.find(attrs={"itemprop": "telephone"})

                companies.append(Company(
                    name=name,
                    city=city_el.get_text(strip=True) if city_el else "",
                    phone=phone_el.get_text(strip=True) if phone_el else "",
                    country="Italy",
                    source_url=href,
                ))

            has_next = bool(soup.find("a", rel="next") or soup.find("a", class_="next"))
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

                phone_el = card.select_one(SEARCH_SELECTORS["phone"])
                phone = ""
                if phone_el:
                    if phone_el.get("href", "").startswith("tel:"):
                        phone = phone_el["href"].replace("tel:", "").strip()
                    else:
                        phone = phone_el.get_text(strip=True)

                addr_el = card.select_one(SEARCH_SELECTORS["address"])
                address = addr_el.get_text(strip=True) if addr_el else ""

                companies.append(Company(
                    name=name,
                    city=city,
                    phone=phone,
                    address=address,
                    country="Italy",
                    source_url=href,
                ))
            except Exception as e:
                logger.debug(f"Error parsing PagineGialle card: {e}")

        has_next = bool(
            soup.select("a[rel='next'], a.next, li.next a")
            or soup.find("a", string=re.compile(r"avanti|succ|next", re.I))
        )

        return companies, has_next

    async def _fetch_detail(self, url: str) -> Company | None:
        try:
            resp = await self.engine.get(url)
        except Exception:
            return None
        return self._parse_detail(resp.text, url)

    def _parse_detail(self, html: str, url: str) -> Company | None:
        """Parse PagineGialle company detail page."""
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
                "example.com", "paginegialle", "italiaonline", "sentry",
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
            phone=_sel("phone"),
            email=email,
            fax=_sel("fax"),
            website=website,
            description=_sel("description"),
            sector=_sel("sector"),
            country="Italy",
            source="paginegialle",
            source_url=url,
            emails_extra=extra,
        )
