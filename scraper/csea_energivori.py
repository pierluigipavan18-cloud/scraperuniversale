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


class CSEAEnergivoriScraper:
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
        keyword_lower = keyword.lower() if keyword else ""

        text = self._extract_text_from_pdf(content)
        if not text:
            logger.error("Could not extract text from CSEA PDF")
            return []

        companies = self._parse_text(text, keyword_lower)
        logger.info(f"Parsed {len(companies)} companies from CSEA PDF")
        return companies

    def _extract_text_from_pdf(self, content: bytes) -> str:
        """Extract text from PDF bytes using pdfplumber."""
        # Method 1: pdfplumber (best for tables)
        try:
            import pdfplumber
            text_parts: list[str] = []
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages:
                    # Try table extraction first (more structured)
                    tables = page.extract_tables()
                    if tables:
                        for table in tables:
                            for row in table:
                                if row:
                                    cells = [c or "" for c in row]
                                    text_parts.append("\t".join(cells))
                    else:
                        page_text = page.extract_text()
                        if page_text:
                            text_parts.append(page_text)
            result = "\n".join(text_parts)
            if result.strip():
                logger.info(f"Extracted {len(result)} chars via pdfplumber")
                return result
        except ImportError:
            logger.warning("pdfplumber not installed, trying PyPDF2")
        except Exception as e:
            logger.warning(f"pdfplumber failed: {e}, trying PyPDF2")

        # Method 2: PyPDF2 fallback
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(content))
            text_parts = []
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            result = "\n".join(text_parts)
            if result.strip():
                logger.info(f"Extracted {len(result)} chars via PyPDF2")
                return result
        except ImportError:
            logger.warning("PyPDF2 not installed either")
        except Exception as e:
            logger.warning(f"PyPDF2 failed: {e}")

        logger.error(
            "No PDF library available. Install pdfplumber: pip install pdfplumber"
        )
        return ""

    def _parse_text(self, text: str, keyword_lower: str) -> list[Company]:
        """Parse extracted PDF text into Company objects."""
        companies: list[Company] = []
        lines = text.split("\n")
        current_name = ""

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Skip header/footer lines
            if any(skip in line.lower() for skip in [
                "elenco", "energivori", "pagina", "page",
                "ragione sociale", "codice fiscale", "classe",
                "cassa servizi", "autorita", "delibera",
                "partita iva", "n.", "agevolazione",
            ]):
                continue

            # Handle tab-separated rows (from table extraction)
            if "\t" in line:
                cells = [c.strip() for c in line.split("\t") if c.strip()]
                name = ""
                vat = ""
                for cell in cells:
                    piva_match = PIVA_RE.search(cell)
                    if piva_match:
                        vat = piva_match.group()
                    elif len(cell) > 3 and not cell.isdigit():
                        if not name:
                            name = cell
                if name and vat:
                    if not keyword_lower or keyword_lower in name.lower():
                        companies.append(self._make_company(name, vat))
                continue

            # Plain text lines: try to find P.IVA
            piva_match = PIVA_RE.search(line)

            if piva_match:
                vat = piva_match.group()
                name_part = line[:piva_match.start()].strip()

                if name_part and len(name_part) > 3:
                    current_name = name_part
                elif not current_name:
                    continue

                if not keyword_lower or keyword_lower in current_name.lower():
                    companies.append(self._make_company(current_name, vat))
                current_name = ""

            elif not any(c.isdigit() for c in line) and len(line) > 5:
                current_name = line

        return companies

    def _make_company(self, name: str, vat_id: str) -> Company:
        return Company(
            name=name.strip(),
            vat_id=vat_id,
            country="Italy",
            sector="Energy-intensive industry",
            source="csea_energivori",
            source_url=ENERGIVORI_URLS.get(self.year, ""),
        )
