from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Company:
    name: str
    city: str = ""
    country: str = ""
    address: str = ""
    email: str = ""
    phone: str = ""
    fax: str = ""
    website: str = ""
    sector: str = ""
    description: str = ""
    employees: str = ""
    vat_id: str = ""
    source: str = ""
    source_url: str = ""
    emails_extra: list[str] = field(default_factory=list)

    @property
    def dedup_key(self) -> str:
        return f"{self.name.lower().strip()}|{self.city.lower().strip()}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["emails_extra"] = ";".join(self.emails_extra)
        return d

    def merge(self, other: Company) -> None:
        """Merge data from another Company record (fill blanks)."""
        for fld in [
            "city", "country", "address", "email", "phone", "fax",
            "website", "sector", "description", "employees", "vat_id",
        ]:
            if not getattr(self, fld) and getattr(other, fld):
                setattr(self, fld, getattr(other, fld))
        for e in other.emails_extra:
            if e not in self.emails_extra:
                self.emails_extra.append(e)
        if not self.source_url and other.source_url:
            self.source_url = other.source_url
        if other.source and other.source not in self.source:
            self.source = f"{self.source}+{other.source}" if self.source else other.source
