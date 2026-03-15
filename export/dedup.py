"""Deduplication and validation pipeline."""

from __future__ import annotations

import logging
import re

from scraper.models import Company

logger = logging.getLogger("export.dedup")

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def validate_email(email: str) -> bool:
    """Validate email format."""
    if not email:
        return False
    return bool(EMAIL_RE.match(email.strip()))


def validate_company(company: Company) -> bool:
    """Check if company has minimum required data."""
    if not company.name or len(company.name.strip()) < 2:
        return False
    # Must have at least city or email or phone
    if not any([company.city, company.email, company.phone]):
        return False
    return True


def deduplicate(companies: list[Company]) -> list[Company]:
    """Deduplicate companies by name+city, merging data from duplicates."""
    seen: dict[str, Company] = {}
    result: list[Company] = []

    for company in companies:
        key = company.dedup_key

        if key in seen:
            # Merge into existing
            seen[key].merge(company)
            logger.debug(f"Merged duplicate: {company.name}")
        else:
            seen[key] = company
            result.append(company)

    deduped = len(companies) - len(result)
    if deduped:
        logger.info(f"Deduplicated {deduped} entries ({len(companies)} -> {len(result)})")

    return result


def clean_companies(companies: list[Company]) -> list[Company]:
    """Full cleaning pipeline: validate, clean fields, deduplicate."""
    cleaned: list[Company] = []

    for c in companies:
        # Clean whitespace
        c.name = c.name.strip()
        c.city = c.city.strip()
        c.email = c.email.strip().lower() if c.email else ""
        c.phone = c.phone.strip()
        c.website = c.website.strip()

        # Validate email format
        if c.email and not validate_email(c.email):
            c.emails_extra.insert(0, c.email)
            c.email = ""

        # Clean extra emails
        c.emails_extra = [
            e.strip().lower() for e in c.emails_extra
            if validate_email(e.strip())
        ]

        if validate_company(c):
            cleaned.append(c)
        else:
            logger.debug(f"Skipped invalid company: {c.name}")

    invalid = len(companies) - len(cleaned)
    if invalid:
        logger.info(f"Removed {invalid} invalid entries")

    return deduplicate(cleaned)
