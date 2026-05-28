from typing import Optional
from pydantic import BaseModel, Field


class SupplierCard(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    honest_assessment: Optional[str] = None
    business_types: Optional[list[str]] = None
    key_products: Optional[list[str]] = None
    years_in_business: Optional[int] = None
    certifications: Optional[list[str]] = None
    website: Optional[str] = None
    address: Optional[str] = None
    fit_score: float = Field(0.0, description="B2B supplier suitability 0-100")
    llm_phones: list[str] = Field(default_factory=list, description="Phone numbers from OFFICIAL website only")
    llm_landlines: list[str] = Field(default_factory=list, description="Landline numbers from OFFICIAL website only")
    llm_emails: list[str] = Field(default_factory=list, description="Emails from OFFICIAL website only")
