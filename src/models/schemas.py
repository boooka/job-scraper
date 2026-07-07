"""Pydantic schemas for scraped vacancy data."""
from __future__ import annotations
 
from typing import Any
 
from pydantic import BaseModel, Field, field_validator, model_validator


class VacancyData(BaseModel):
    """Parsed vacancy data from any source."""
 
    source: str
    external_id: str
    title: str
    company: str | None = None
    location: str | None = None
    url: str
    description: str | None = None
 
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    salary_period: str | None = None  # "month" | "hour"
    salary_type: str | None = None    # "gross" | "net"
 
    page_html: str | None = None      # raw HTML of the vacancy detail page
    welcome_ukraine: bool = False
 
    extra: dict[str, Any] = Field(default_factory=dict)
 
    @field_validator("title", "company", "location", mode="before")
    @classmethod
    def strip_whitespace(cls, v: str | None) -> str | None:
        return v.strip() if isinstance(v, str) else v
 
    @field_validator("external_id", mode="before")
    @classmethod
    def coerce_external_id(cls, v: Any) -> str:
        return str(v)
 
    @model_validator(mode="after")
    def validate_salary_range(self) -> VacancyData:
        if self.salary_min and self.salary_max:
            if self.salary_min > self.salary_max:
                self.salary_min, self.salary_max = self.salary_max, self.salary_min
        return self
 
    def to_comparable_dict(self) -> dict[str, Any]:
        """Return fields that are tracked for change detection."""
        return {
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "salary_min": self.salary_min,
            "salary_max": self.salary_max,
            "salary_currency": self.salary_currency,
            "salary_period": self.salary_period,
            "description": self.description,
            "welcome_ukraine": self.welcome_ukraine,
        }
 
 
TRACKED_FIELDS: frozenset[str] = frozenset(VacancyData.model_fields) - frozenset(
    {"source", "external_id", "url", "extra"}
)
