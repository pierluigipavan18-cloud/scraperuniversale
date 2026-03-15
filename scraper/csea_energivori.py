"""CSEA Energivori scraper module.

Downloads and parses the official Italian energy-intensive companies
registry from CSEA (Cassa per i Servizi Energetici e Ambientali).

This is a SEED LIST: it provides company names and P.IVA for ~4000
verified energy-intensive companies. These can then be enriched
with email/phone from other sources.

Source: https://energivori.csea.it
"""

from __future__ import annotations

import io
import logging
import re
from typing import AsyncIterator

from .engine import ScraperEngine
from .models import Company

logger = logging.getLogger("scraper.csea_energivori")

# Official PDF URLs for energivori lists
ENERGIVORI_URLS = {
    "2024": "https://energivori.csea.it/Energivori/pdf/elenco/Elenco_Energivori_2024_del_18-08-24.pdf",
    "2023": "https://energivori.csea.it/Energivori/pdf/elenco/Elenco_Energivori_2023_del_18-01-24.pdf",
}

# Alternative: the main page that lists available PDFs
ENERGIVORI_PAGE = "https://energivori.csea.it"

# Regex for Italian P.IVA (11 digits)
PIVA_RE = re.compile(r"\b\d{11}\b")
# Regex for Codice Fiscale (alphanumeric, 11 or 16 chars)
CF_RE = re.compile(r"\b[A-Z0-9]{11,16}\b")


class CSEAEnerivogiScraper:
    """Scrapes the CSEA energivori registry for energy-intensive companies."""

    def __init__(
        self,
        engine: ScraperEngine,
        year: str = "2024",
        max_results: int = 0,
    ):
        self.engine = engine
        self.year = year
        self.max_results = max_results

    async def search(self, keyword: str = "") -> AsyncIterator[Company]:
        """Download and parse the energivori PDF list.

        The keyword parameter is accepted for interface compatibility
        but the CSEA list is a fixed registry, not a search engine.
        If keyword is provided, it filters results by company name.
        """
        # Try to get the PDF for the requested year
        url = ENERGIVORI_URLS.get(self.year)
        if not url:
            logger.warning(f"No known URL for year {self.year}, trying 2024")
            url = ENERGIVORI_URLS["2024"]

        logger.info(f"Downloading CSEA energivori list: {url}")

        try:
            resp = await self.engine.get(url)
        except Exception as e:
            logger.error(f"Failed to download CSEA PDF: {e}")
            return

        # Parse PDF content
        companies = self._parse_pdf(resp.content, keyword)

        count = 0
        for company in companies:
            yield company
            count += 1
            if 0 < self.max_results <= count:
                return

    def _parse_pdf(self, content: bytes, keyword: str = "") -> list[Company]:
        """Parse the CSEA PDF to extract company data.

        The PDF typically contains a table with:
        - Ragione Sociale (company name)
        - P.IVA / Codice Fiscale
        - Classe di agevolazione (benefit class)
        """
        companies: list[Company] = []
        keyword_lower = keyword.lower() if keyword else ""

        try:
            text = self._extract_text_from_pdf(content)
        except Exception as e:
            logger.error(f"Failed to parse PDF: {e}")
            return companies

        # Split into lines and parse
        lines = text.split("\n")
        current_name = ""
        current_vat = ""

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Skip header/footer lines
            if any(skip in line.lower() for skip in [
                "elenco", "energivori", "pagina", "page", "data",
                "ragione sociale", "codice fiscale", "classe",
                "cassa servizi", "autorita", "delibera",
            ]):
                continue

            # Try to find P.IVA in the line
            piva_match = PIVA_RE.search(line)

            if piva_match:
                # This line likely contains company data
                vat = piva_match.group()
                # The name is usually before the P.IVA
                name_part = line[:piva_match.start()].strip()

                if name_part and len(name_part) > 3:
                    current_name = name_part
                    current_vat = vat
                elif current_name:
                    current_vat = vat
                else:
                    continue

                # Apply keyword filter
                if keyword_lower and keyword_lower not in current_name.lower():
                    current_name = ""
                    current_vat = ""
                    continue

                companies.append(Company(
                    name=current_name,
                    vat_id=current_vat,
                    country="Italy",
                    sector="Energy-intensive industry",
                    source="csea_energivori",
                    source_url=ENERGIVORI_URLS.get(self.year, ""),
                ))
                current_name = ""
                current_vat = ""

            elif not any(c.isdigit() for c in line) and len(line) > 5:
                # Likely a company name on its own line
                current_name = line

        logger.info(f"Parsed {len(companies)} companies from CSEA PDF")
        return companies

    def _extract_text_from_pdf(self, content: bytes) -> str:
        """Extract text from PDF bytes.

        Tries multiple approaches: first a lightweight text extraction
        from the raw PDF, then falls back to basic parsing.
        """
        text_chunks: list[str] = []

        # Basic PDF text extraction without external PDF library
        # PDF text streams are between BT and ET markers
        raw = content.decode("latin-1", errors="ignore")

        # Method 1: Extract text from PDF stream objects
        # Look for text between parentheses in Tj/TJ operators
        tj_pattern = re.compile(r"\(([^)]*)\)\s*Tj")
        for match in tj_pattern.finditer(raw):
            text_chunks.append(match.group(1))

        # Method 2: Look for text in TJ arrays
        tj_array_pattern = re.compile(r"\[((?:\([^)]*\)|[^]]*)*)\]\s*TJ")
        for match in tj_array_pattern.finditer(raw):
            inner = match.group(1)
            parts = re.findall(r"\(([^)]*)\)", inner)
            text_chunks.append("".join(parts))

        if text_chunks:
            return "\n".join(text_chunks)

        # Method 3: Brute-force search for Italian company name patterns
        # Look for strings that look like company names (all caps with SRL, SPA, etc.)
        company_pattern = re.compile(
            r"([A-Z][A-Z\s&.,'-]{5,}(?:S\.?R\.?L\.?|S\.?P\.?A\.?|S\.?N\.?C\.?|S\.?A\.?S\.?|S\.?C\.?A\.?R\.?L\.?))"
        )
        names = company_pattern.findall(raw)
        if names:
            return "\n".join(names)

        logger.warning("Could not extract text from PDF. Consider installing PyPDF2 or pdfplumber.")
        return raw

    async def _try_find_pdf_links(self) -> list[str]:
        """Fallback: try to scrape the main CSEA page for PDF links."""
        links: list[str] = []
        try:
            from bs4 import BeautifulSoup
            resp = await self.engine.get(ENERGIVORI_PAGE)
            soup = BeautifulSoup(resp.text, "lxml")

            for a in soup.find_all("a", href=re.compile(r"\.pdf$", re.I)):
                href = a.get("href", "")
                if "energivori" in href.lower() or "elenco" in href.lower():
                    logger.info(f"Found PDF link: {href}")
                    links.append(href)
        except Exception as e:
            logger.error(f"Page scrape fallback failed: {e}")
        return links
