"""Email enrichment module.

For companies without email, visits their website and extracts
email addresses from contact pages, about pages, and page footers.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .engine import ScraperEngine
from .models import Company

logger = logging.getLogger("scraper.email_enricher")

# Email regex pattern
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# Common false-positive email domains to filter out
BLACKLIST_DOMAINS = {
    "example.com", "example.org", "test.com", "sentry.io",
    "wixpress.com", "w3.org", "schema.org", "googleapis.com",
    "googleusercontent.com", "gstatic.com", "facebook.com",
    "twitter.com", "instagram.com", "youtube.com",
}

# Default paths to check for emails
DEFAULT_PATHS = [
    "/contact",
    "/contacts",
    "/contatti",
    "/kontakt",
    "/contacto",
    "/about",
    "/about-us",
    "/chi-siamo",
    "/impressum",
    "/imprint",
    "/legal",
    "/privacy",
]


class EmailEnricher:
    """Visits company websites to find email addresses."""

    def __init__(
        self,
        engine: ScraperEngine,
        paths: list[str] | None = None,
        timeout: float = 10.0,
    ):
        self.engine = engine
        self.paths = paths or DEFAULT_PATHS
        self.timeout = timeout

    async def enrich(self, company: Company) -> Company:
        """Try to find email addresses from the company website."""
        if not company.website:
            return company

        website = company.website
        if not website.startswith("http"):
            website = f"https://{website}"

        # Parse the base URL
        parsed = urlparse(website)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        found_emails: set[str] = set()

        # First, check the homepage
        emails = await self._extract_emails_from_url(website)
        found_emails.update(emails)

        # Then check contact/about pages
        if not found_emails or len(found_emails) < 2:
            for path in self.paths:
                url = urljoin(base_url, path)
                try:
                    emails = await self._extract_emails_from_url(url)
                    found_emails.update(emails)
                except Exception:
                    continue

                # Stop early if we found enough
                if len(found_emails) >= 3:
                    break

        # Apply results to company
        valid_emails = self._filter_emails(found_emails, parsed.netloc)

        if valid_emails:
            if not company.email:
                company.email = valid_emails[0]
                valid_emails = valid_emails[1:]

            for e in valid_emails:
                if e != company.email and e not in company.emails_extra:
                    company.emails_extra.append(e)

        return company

    async def _extract_emails_from_url(self, url: str) -> set[str]:
        """Fetch a URL and extract email addresses from the HTML."""
        try:
            resp = await self.engine.get(url, timeout=self.timeout)
        except Exception as e:
            logger.debug(f"Failed to fetch {url}: {e}")
            return set()

        return self._extract_emails_from_html(resp.text)

    def _extract_emails_from_html(self, html: str) -> set[str]:
        """Extract email addresses from HTML content."""
        emails: set[str] = set()

        soup = BeautifulSoup(html, "lxml")

        # Method 1: mailto links
        for a in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
            href = a["href"]
            email = href.replace("mailto:", "").split("?")[0].strip()
            if EMAIL_RE.match(email):
                emails.add(email.lower())

        # Method 2: Regex on visible text
        text = soup.get_text(separator=" ")
        for match in EMAIL_RE.findall(text):
            emails.add(match.lower())

        # Method 3: Regex on raw HTML (catches obfuscated emails)
        for match in EMAIL_RE.findall(html):
            emails.add(match.lower())

        return emails

    def _filter_emails(self, emails: set[str], company_domain: str) -> list[str]:
        """Filter and prioritize extracted emails."""
        filtered: list[str] = []
        company_domain_clean = company_domain.lower().replace("www.", "")

        for email in emails:
            email_domain = email.split("@")[1] if "@" in email else ""

            # Skip blacklisted domains
            if email_domain in BLACKLIST_DOMAINS:
                continue

            # Skip image file extensions mistaken as emails
            if any(email.endswith(ext) for ext in [".png", ".jpg", ".gif", ".svg", ".css", ".js"]):
                continue

            filtered.append(email)

        # Prioritize: company domain emails first, then generic
        def sort_key(e: str) -> tuple[int, str]:
            domain = e.split("@")[1] if "@" in e else ""
            if company_domain_clean in domain:
                return (0, e)  # Company domain = highest priority
            if any(g in domain for g in ["gmail", "yahoo", "hotmail", "outlook", "libero"]):
                return (2, e)  # Generic = lowest
            return (1, e)  # Other business domains

        filtered.sort(key=sort_key)
        return filtered
